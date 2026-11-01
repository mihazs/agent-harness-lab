"""Explicit offline Git metadata; no source config, hooks, filters or remotes run."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat
import time
from agent_harness_lab.config import read_json
from agent_harness_lab.evaluation.integrity import contained, relative
from .git_view import Folder, GitView, MAX_BYTES
from .instructions import capture as capture_instructions, validate as validate_instruction_profile
from .schema import EvidenceRecord, digest, fingerprint
from .snapshot import LocalExport, Snapshot

OID = re.compile(rb'(?:[0-9a-f]{40}|[0-9a-f]{64})')


def reference(name):
    if (not name.startswith('refs/') or len(name) > 4096 or name.endswith(('/', '.'))
            or any(c in name for c in ('..', '@{', '//', '\\', ' ', '~', '^', ':', '?', '*', '['))
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or any(p.startswith('.') or p.endswith('.lock') for p in name.split('/'))):
        raise ValueError('invalid Git reference')
    return name


@dataclass(frozen=True)
class HarnessFile:
    path_sha256: str
    content_sha256: str | None
    mode: int | None
    committed_sha256: str | None
    committed_mode: int | None

    def __post_init__(self):
        fingerprint(self.path_sha256)
        for value in (self.content_sha256, self.committed_sha256):
            if value is not None: fingerprint(value)
        if ((self.content_sha256 is None) != (self.mode is None)
                or self.mode is not None and (type(self.mode) is not int or not 0 <= self.mode <= 0o7777)
                or (self.committed_sha256 is None) != (self.committed_mode is None)
                or self.committed_mode not in (None, 0o100644, 0o100755, 0o120000)):
            raise ValueError('invalid immutable harness file metadata')


@dataclass(frozen=True)
class HarnessSnapshot:
    head_sha256: str
    files: tuple
    effective_settings: str = 'unknown'
    observed_loading: str = 'unknown'
    configuration_source: str = 'declared_project_files'
    schema_version: int = 1

    def __post_init__(self):
        fingerprint(self.head_sha256)
        if (type(self.files) is not tuple or len(self.files) > 100 or not all(isinstance(f, HarnessFile) for f in self.files)
                or len({f.path_sha256 for f in self.files}) != len(self.files)
                or self.effective_settings != 'unknown' or self.observed_loading != 'unknown'
                or self.configuration_source != 'declared_project_files'
                or type(self.schema_version) is not int or self.schema_version != 1):
            raise ValueError('filesystem capture requires immutable files and unknown effective state')

    @property
    def sha256(self): return digest(asdict(self))


class GitExport(LocalExport):
    def read(self, source):
        if (source.kind, source.provider, source.version) != ('local_git', 'git', 'v1'):
            raise ValueError('unsupported Git source version')
        current, declaration = self.declaration(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
        if current != source or not source.enabled: raise ValueError('Git source changed or is disabled')
        descriptor = contained(self.manifest.parent, declaration['data'])
        selection = read_json(descriptor)
        fields = {'schema_version', 'checkout', 'git_dir', 'common_dir', 'harness_paths', 'max_commits'}
        if (not isinstance(selection, dict) or set(selection) not in (fields, fields|{'instruction_profile'})
                or type(selection['schema_version']) is not int or selection['schema_version'] != 1):
            raise ValueError('invalid Git selection descriptor')
        if digest(selection) != source.scope_sha256: raise ValueError('Git selection scope mismatch')
        for key in ('checkout', 'git_dir', 'common_dir'): relative(selection[key])
        paths = selection['harness_paths']
        if not isinstance(paths, list) or len(paths) > 100 or not all(isinstance(p, str) for p in paths) or len(set(paths)) != len(paths):
            raise ValueError('invalid Git harness allowlist')
        for path in paths:
            relative(path)
            if len(path) > 4096 or len(Path(path).parts) > 64 or '.git' in Path(path).parts:
                raise ValueError('unsafe Git harness path')
        count = selection['max_commits']
        if type(count) is not int or not 1 <= count <= 100: raise ValueError('invalid Git history bound')
        profile = selection.get('instruction_profile')
        if 'instruction_profile' in selection: validate_instruction_profile(profile)
        deadline = time.monotonic()+10
        self.warnings, self.trees, self.algorithm = set(), {}, 'sha1'
        with Folder(self.manifest.parent, deadline) as folder, GitView(deadline) as view:
            self.folder, self.view = folder, view
            checkout, git_dir, common = (selection[k] for k in ('checkout', 'git_dir', 'common_dir'))
            self.layout(checkout, git_dir, common)
            config = folder.read(common+'/config')
            if config is None: raise ValueError('missing Git format declaration')
            worktree_config = folder.read(git_dir+'/config.worktree')
            (view.root/'source-config').write_bytes(config)
            self.configuration()
            refs = self.references(common, git_dir)
            head_raw = folder.read(git_dir+'/HEAD', 4096)
            head, branch = self.resolve(head_raw, refs)
            self.oid(head)
            view.configure(self.algorithm)
            (view.root/'HEAD').write_bytes(head+b'\n')
            self.objects(common)
            shallow = folder.read(common+'/shallow')
            boundaries = set(shallow.splitlines()) if shallow else set()
            for oid in boundaries: self.oid(oid)
            if shallow:
                (view.root/'shallow').write_bytes(shallow); self.warnings.add('shallow_history')
            view.run('fsck', '--full', '--strict', '--no-reflogs', '--no-dangling', '--no-progress', head.decode())
            commits = view.run('rev-list', '--topo-order', '--max-count='+str(count+1), head.decode()).splitlines()
            if len(commits) > count: self.warnings.add('history_limit_reached')
            rows, graph = [], []
            now = datetime.now(timezone.utc).isoformat()
            def record(kind, native, revision, content, original_at=None):
                return EvidenceRecord(source.source_key, digest((kind, native)), revision, content,
                    'development_artifact', kind, now, original_at=original_at,
                    provenance='native_field', capture_completeness='partial')
            for oid in commits[:count]:
                self.oid(oid)
                raw = self.object('commit', oid)
                header, message = raw.split(b'\n\n', 1)
                parents = [line[7:] for line in header.splitlines() if line.startswith(b'parent ')]
                for parent in parents: self.oid(parent)
                original = None
                stamp = re.search(rb'^committer .* (-?\d+) [+-]\d{4}$', header, re.M)
                if stamp:
                    try: original = datetime.fromtimestamp(int(stamp[1]), timezone.utc).isoformat()
                    except (ValueError, OverflowError, OSError): pass
                changes, patch = (), None
                if oid not in boundaries:
                    pair = [parents[0].decode(), oid.decode()] if parents else [oid.decode()]
                    flags = ('diff-tree', '--root', '--no-commit-id', '-r', '--no-renames', '--no-ext-diff', '--no-textconv')
                    numbers = view.run(*flags, '--numstat', '-z', *pair)
                    changes = tuple(self.change(row) for row in numbers.split(b'\0') if row)
                    patch = hashlib.sha256(view.run(*flags, '--patch', '--binary', '--full-index', '--no-color', *pair)).hexdigest()
                node = dict(commit_sha256=digest(oid.decode()), parents_sha256=[digest(p.decode()) for p in parents],
                            message_sha256=hashlib.sha256(message).hexdigest(), changes=changes, diff_sha256=patch,
                            comparison='first_parent', original_at=original)
                graph.append(node)
                rows.append(record('git_commit', oid.decode(), digest(oid.decode()), hashlib.sha256(raw).hexdigest(), original))
            valid_refs = self.valid_refs(refs)
            for name, target, kind in valid_refs:
                value = (digest(target.decode()), kind)
                rows.append(record('git_ref', name, digest(value), digest(value)))
            files = []
            for path in sorted(paths):
                committed, mode = self.harness_file(head, checkout, path)
                name = str(Path(checkout)/path)
                data = folder.read(name, 1024*1024)
                info = folder.inspect(name)
                files.append(HarnessFile(digest(path), hashlib.sha256(data).hexdigest() if data is not None else None,
                    stat.S_IMODE(info.st_mode) if info else None,
                    hashlib.sha256(committed).hexdigest() if committed is not None else None, mode))
            harness = HarnessSnapshot(digest(head.decode()), tuple(files))
            for file in files:
                value = asdict(file)
                rows.append(record('harness_file', file.path_sha256, digest(value), digest(value)))
            instructions = None
            if profile is not None:
                self.harness_file(head, checkout, str(Path(profile['task_cwd'])/'AGENTS.md'))
                instructions = capture_instructions(profile, folder, checkout, paths, git_harness_sha256=harness.sha256)
                rows.append(record('instruction_inventory', 'project', instructions.sha256, instructions.sha256))
            folder.verify()
            if Folder.stamp(view.binary.stat()) != view.binary_stamp: raise ValueError('trusted Git binary changed')
            if read_json(descriptor) != selection or self.declaration(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)[0] != source:
                raise ValueError('Git selection changed during capture')
            self.harness = harness
            self.instructions = instructions
            self.summary = dict(head_sha256=harness.head_sha256, branch_sha256=digest(branch) if branch else None,
                commits=graph, refs=[dict(name_sha256=digest(n), target_sha256=digest(t.decode()), object_type=k) for n, t, k in valid_refs],
                harness=asdict(harness), harness_sha256=harness.sha256, git_version=view.version,
                git_binary_sha256=view.binary_sha256, source_git_config_sha256=hashlib.sha256(config).hexdigest(),
                source_worktree_config_sha256=hashlib.sha256(worktree_config).hexdigest() if worktree_config is not None else None,
                warnings=sorted(self.warnings|{'source_configuration_ignored', 'effective_settings_unknown', 'selected_paths_only'}),
                network_used=False, capture_completeness='partial', source_identity='caller_declared')
            if instructions is not None:
                self.summary.update(instruction_inventory=asdict(instructions), instruction_inventory_sha256=instructions.sha256)
            return Snapshot(digest(self.summary), tuple(rows))

    def harness_file(self, head, checkout, path):
        for ancestor in Path(path).parents:
            if str(ancestor) != '.' and self.folder.inspect(str(Path(checkout)/ancestor/'.git')) is not None:
                raise NotImplementedError('nested repositories are excluded from Git harness capture')
        return self.committed(head, path)

    def layout(self, checkout, git_dir, common):
        folder = self.folder
        for name in (checkout, git_dir, common):
            descriptor = folder.directory(name); os.close(descriptor)
        marker = folder.inspect(checkout+'/.git')
        if marker is None: raise NotImplementedError('explicit Git checkout required; bare repositories are unsupported')
        def target(base, raw):
            if raw is None or b'\0' in raw or len(raw.splitlines()) != 1: raise ValueError('invalid worktree pointer')
            value = Path(os.fsdecode(raw.strip()))
            return Path(os.path.abspath(self.manifest.parent/base/value))
        if stat.S_ISDIR(marker.st_mode):
            if git_dir != checkout+'/.git' or common != git_dir: raise ValueError('Git directory selection mismatch')
            if folder.read(git_dir+'/commondir') is not None: raise NotImplementedError('unexpected common Git directory redirection')
        elif stat.S_ISREG(marker.st_mode):
            raw = folder.read(checkout+'/.git', 4096)
            if not raw.startswith(b'gitdir: '): raise ValueError('invalid Git worktree marker')
            expected = Path(os.path.abspath(self.manifest.parent/git_dir))
            if target(checkout, raw[8:]) != expected: raise ValueError('Git worktree directory mismatch')
            if target(git_dir, folder.read(git_dir+'/gitdir', 4096)) != Path(os.path.abspath(self.manifest.parent/checkout/'.git')):
                raise ValueError('Git worktree backlink mismatch')
            if target(git_dir, folder.read(git_dir+'/commondir', 4096)) != Path(os.path.abspath(self.manifest.parent/common)):
                raise ValueError('Git common directory mismatch')
        else: raise ValueError('unsafe Git checkout marker')

    def configuration(self):
        settings = {}
        for entry in self.view.run('config', '--file', str(self.view.root/'source-config'), '--no-includes', '--null', '--list').split(b'\0'):
            if entry:
                key, _, value = entry.partition(b'\n'); settings.setdefault(key.lower(), []).append(value)
        if settings.get(b'core.repositoryformatversion') not in ([b'0'], [b'1']): raise NotImplementedError('unsupported Git repository format')
        allowed = (b'extensions.objectformat', b'extensions.worktreeconfig', b'extensions.partialclone', b'extensions.preciousobjects', b'extensions.refstorage')
        if any(key.startswith(b'extensions.') and (key not in allowed or len(values) != 1) for key, values in settings.items()):
            raise NotImplementedError('unsupported Git repository extension')
        algorithm = settings.get(b'extensions.objectformat', [b'sha1'])
        if len(algorithm) != 1 or algorithm[0] not in (b'sha1', b'sha256') or settings.get(b'extensions.refstorage', [b'files']) != [b'files']:
            raise NotImplementedError('unsupported Git object/reference format')
        self.algorithm = algorithm[0].decode()
        if b'extensions.partialclone' in settings: self.warnings.add('partial_clone')

    def oid(self, value):
        if OID.fullmatch(value or b'') is None or len(value) != (40 if self.algorithm == 'sha1' else 64):
            raise ValueError('invalid Git object identity')

    def references(self, common, git_dir):
        result = {}
        private = ('refs/bisect/', 'refs/worktree/', 'refs/rewritten/')
        packed = self.folder.read(common+'/packed-refs', 1024*1024)
        for line in (packed or b'').splitlines():
            if line.startswith((b'#', b'^')): continue
            oid, separator, raw = line.partition(b' ')
            name = os.fsdecode(raw)
            if not separator: raise ValueError('malformed packed Git refs')
            reference(name); self.oid(oid)
            if git_dir != common and name.startswith(private): continue
            if name in result: raise ValueError('duplicate packed Git ref')
            result[name] = oid
        if self.folder.inspect(common+'/refs') is not None:
            excluded = tuple(common+'/'+prefix.rstrip('/') for prefix in private) if git_dir != common else ()
            for name in self.folder.walk(common+'/refs', excluded=excluded):
                ref = str(Path(name).relative_to(common)); reference(ref)
                result[ref] = self.folder.read(name, 4096).strip()
        if git_dir != common:
            for prefix in private:
                folder = git_dir+'/'+prefix.rstrip('/')
                if self.folder.inspect(folder) is not None:
                    for name in self.folder.walk(folder):
                        ref = str(Path(name).relative_to(git_dir)); reference(ref)
                        result[ref] = self.folder.read(name, 4096).strip()
        if len(result) > 1000: raise ValueError('Git ref budget exceeded')
        return result

    def resolve(self, value, refs):
        branch, visited = None, set()
        for _ in range(32):
            value = (value or b'').strip()
            if not value.startswith(b'ref: '): return value, branch
            name = reference(os.fsdecode(value[5:]))
            branch = branch or name
            if name in visited: raise ValueError('cyclic symbolic Git ref')
            visited.add(name); value = refs.get(name)
        raise ValueError('Git ref depth exceeded')

    def objects(self, common):
        for name in ('alternates', 'http-alternates'):
            if self.folder.read(common+'/objects/info/'+name): raise NotImplementedError('alternate object stores are outside Git source scope')
        for name in self.folder.walk(common+'/objects'):
            suffix = str(Path(name).relative_to(common+'/objects'))
            if not (re.fullmatch(r'[0-9a-f]{2}/[0-9a-f]{38}(?:[0-9a-f]{24})?', suffix)
                    or re.fullmatch(r'pack/pack-[0-9a-f]{40}(?:[0-9a-f]{24})?\.(?:pack|idx|rev)', suffix)):
                continue
            raw = self.folder.read(name, MAX_BYTES)
            output = self.view.root/'objects'/suffix; output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(raw)

    def object(self, kind, oid):
        self.oid(oid)
        raw = self.view.run('cat-file', kind, oid.decode())
        actual = hashlib.new(self.algorithm, kind.encode()+b' '+str(len(raw)).encode()+b'\0'+raw).hexdigest().encode()
        if actual != oid: raise ValueError('Git object checksum mismatch')
        return raw

    def valid_refs(self, refs):
        selected = []
        for name, raw in sorted(refs.items()):
            oid, _ = self.resolve(raw, refs)
            if OID.fullmatch(oid or b'') is None: self.warnings.add('dangling_refs'); continue
            self.oid(oid); selected.append((name, oid))
        if not selected: return ()
        raw = self.view.run('cat-file', '--batch-check', data=b''.join(oid+b'\n' for _, oid in selected))
        lines = raw.splitlines()
        if len(lines) != len(selected): raise ValueError('incomplete Git ref verification')
        valid = []
        for (name, oid), line in zip(selected, lines):
            fields = line.split()
            if fields == [oid, b'missing']: self.warnings.add('dangling_refs'); continue
            if len(fields) != 3 or fields[0] != oid or fields[1] not in (b'blob', b'tree', b'commit', b'tag'):
                raise ValueError('invalid Git ref object response')
            valid.append((name, oid, fields[1].decode()))
        return tuple(valid)

    def committed(self, head, path):
        header = self.object('commit', head).split(b'\n\n', 1)[0]
        roots = [line[5:] for line in header.splitlines() if line.startswith(b'tree ')]
        if len(roots) != 1: raise ValueError('invalid commit tree identity')
        tree = roots[0]
        parts = Path(path).parts
        for position, part in enumerate(parts):
            if tree not in self.trees:
                raw, entries, offset = self.object('tree', tree), {}, 0
                width = 20 if self.algorithm == 'sha1' else 32
                while offset < len(raw):
                    end = raw.index(b'\0', offset)
                    mode, name = raw[offset:end].split(b' ', 1)
                    if name in entries or b'/' in name or name in (b'.', b'..') or end+1+width > len(raw):
                        raise ValueError('invalid Git tree entry')
                    entries[name] = (int(mode, 8), raw[end+1:end+1+width].hex().encode()); offset = end+1+width
                self.trees[tree] = entries
            mode, oid = self.trees[tree].get(os.fsencode(part), (None, None))
            if mode is None: return None, None
            if mode == 0o160000: raise NotImplementedError('submodule contents are outside Git harness scope')
            if position == len(parts)-1:
                if mode not in (0o100644, 0o100755, 0o120000): raise ValueError('harness path is not a Git file')
                return self.object('blob', oid), mode
            if mode != 0o40000: return None, None
            tree = oid
        raise ValueError('empty Git harness path')

    @staticmethod
    def change(row):
        added, removed, name = row.split(b'\t', 2)
        return dict(path_sha256=digest(os.fsdecode(name)),
                    added=None if added == b'-' else int(added), removed=None if removed == b'-' else int(removed))
