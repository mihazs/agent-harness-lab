#!/usr/bin/env python3
"""Install the release CLI and skills without Git, pip, or third-party packages."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import shlex
import stat
import sys
import tarfile
import tempfile
from urllib.request import Request, urlopen

REPOSITORY = 'mihazs/agent-harness-lab'
SKILLS = ('harness-self-improvement',)
RETIRED_SKILLS = ('harness-evaluation',)  # folded into harness-self-improvement; owned links are removed on upgrade
VERSION = re.compile(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?')
TEMPLATE = 'global-instructions.md'
START_MARKER = '<!-- agent-harness-lab:start version={version} sha256={digest} -->'
END_MARKER = b'<!-- agent-harness-lab:end -->'
START_LINE = re.compile(rb'<!-- agent-harness-lab:start((?: [^\r\n]*?)?) -->')
START_ATTRIBUTES = re.compile(rb' version=(\S+) sha256=([0-9a-f]{64})')
MAX_INSTRUCTION_BYTES = 8 * 1024 * 1024
# Typed instruction outcomes with their exact next step; {path} is the file. agent_harness_lab/exit_guidance.py holds the
# same text with {path} read as "the instruction file", and a test keeps the two tables identical.
REASONS = {
    'instruction_markers_malformed': 'Edit {path} so it has exactly one `<!-- agent-harness-lab:start ... -->` line followed later by one `<!-- agent-harness-lab:end -->` line, or delete both lines, then run: ahl update',
    'instruction_symlink_refused': 'Make {path} a regular file you own, or a symlink to a regular file you own that is not world-writable, then run: ahl update',
    'instruction_target_unsafe': 'Make {path} a regular file you own and under 8 MiB, then run: ahl update',
    'instruction_write_failed': 'Fix the filesystem error reported for {path}, then run: ahl update',
    'instruction_template_invalid': 'This release ships an unusable instruction template, so {path} was left unchanged; install a newer release with: ahl update',
    'instruction_template_missing': 'This release ships no instruction template, so {path} was left unchanged; install a newer release with: ahl update',
    'region_edited_by_user_replaced': 'Text edited inside the managed region of {path} was replaced and the edited text is in the backup; keep your own text outside the markers',
}


def fetch(url, target):
    request = Request(url, headers={'User-Agent': 'agent-harness-lab-installer'})
    with urlopen(request, timeout=60) as response, Path(target).open('wb') as stream:
        size = 0
        while block := response.read(1024 * 1024):
            size += len(block)
            if size > 100 * 1024 * 1024:
                raise ValueError('release download exceeds 100 MiB')
            stream.write(block)


def verify(archive, checksums):
    expected = None
    for line in Path(checksums).read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip('*') == Path(archive).name:
            if expected is not None or not re.fullmatch(r'[a-fA-F0-9]{64}', parts[0]):
                raise ValueError('invalid or duplicate checksum entry')
            expected = parts[0].lower()
    if Path(archive).stat().st_size > 100 * 1024 * 1024:
        raise ValueError('release archive exceeds 100 MiB')
    with Path(archive).open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if expected != actual:
        raise ValueError('release checksum mismatch or missing entry')
    return actual


def extract(archive, destination):
    """Accept one archive root and regular files only; reject links and traversal."""
    with tarfile.open(archive, 'r:gz') as stream:
        members = []
        roots, names = set(), set()
        expanded = 0
        for member in stream:
            expanded += member.size
            if len(members) >= 1000 or member.size < 0 or expanded > 100 * 1024 * 1024:
                raise ValueError('invalid release archive size')
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or '\\' in member.name or not path.parts:
                raise ValueError('unsafe release path')
            if member.name in names or not (member.isfile() or member.isdir()):
                raise ValueError('duplicate or non-regular release member')
            names.add(member.name)
            roots.add(path.parts[0])
            members.append(member)
        if not members or len(roots) != 1:
            raise ValueError('release archive must contain one root')
        for member in members:
            target = Path(destination).joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with stream.extractfile(member) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o755 if target.name == 'ahl' else 0o644)
        return Path(destination) / roots.pop()


def owned_link(link, root, relative):
    if not link.is_symlink():
        return False
    try:
        target = link.resolve()
        version = target.relative_to(root / 'releases').parts[0]
        return bool(VERSION.fullmatch(version) and target == root / 'releases' / version / relative
                    and (root / 'releases' / version / '.release-sha256').is_file())
    except (ValueError, OSError):
        return False


def prior_state(root):
    """The recorded installation receipt, or {} when it is missing or unreadable."""
    path = Path(root) / 'installation.json'
    try:
        state = json.loads(path.read_text()) if path.is_file() and not path.is_symlink() and path.stat().st_size <= 65536 else {}
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def instruction_settings(state, agent=None, enabled=None):
    """(enabled, agent): explicit choices, then the receipt, then the global skill directories a pre-instructions receipt lists."""
    recorded = state.get('instructions') if isinstance(state.get('instructions'), dict) else {}
    if agent is None and recorded.get('agent') in ('codex', 'claude', 'both'):
        agent = recorded['agent']
    if agent is None:
        home = Path.home()
        listed = {str(Path(folder).resolve()) for folder in state.get('skill_dirs') or () if isinstance(folder, str)}
        found = [name for name, folder in (('codex', home / '.agents/skills'), ('claude', home / '.claude/skills'))
                 if str(folder.resolve()) in listed]
        agent = found[0] if len(found) == 1 else 'both'
    return recorded.get('enabled', True) is not False if enabled is None else enabled, agent


def instruction_targets(agent):
    """(label, file, skip reason or None) where the selected agents read user-level instructions.

    Decided before any skill link exists, because linking creates ~/.claude, which must not turn CLAUDE.md into a target.
    AHL never creates another tool's directory, and the shared ~/.agents/AGENTS.md is a target only when it already exists."""
    home = Path.home()
    found = []
    if agent in ('claude', 'both'):
        found.append(('claude', Path(os.environ.get('CLAUDE_CONFIG_DIR') or home / '.claude').expanduser().absolute() / 'CLAUDE.md'))
    if agent in ('codex', 'both'):
        found.append(('codex', Path(os.environ.get('CODEX_HOME') or home / '.codex').expanduser().absolute() / 'AGENTS.md'))
        if os.path.lexists(home / '.agents/AGENTS.md'):
            found.append(('shared', home / '.agents/AGENTS.md'))
    unique = {}
    for label, path in found:
        unique.setdefault(os.path.realpath(path), (label, path, None if path.parent.is_dir() else 'config_directory_missing'))
    return list(unique.values())


def render(release, version):
    """(sha256, body bytes with LF line endings) of the block this release ships; FileNotFoundError when it has none."""
    raw = (Path(release) / TEMPLATE).read_bytes()
    if len(raw) > 2048 or b'\r' in raw or b'agent-harness-lab:' in raw or not raw.strip():
        raise ValueError('unusable instruction template')
    body = raw.decode().replace('{version}', version).rstrip('\n').encode() + b'\n'
    return hashlib.sha256(body).hexdigest(), body


def inspect_target(path, version, digest, body):
    """Read-only decision for one file: current, stale, absent or refused with a typed reason, plus the bytes a write needs."""
    path, link = Path(path), Path(path).is_symlink()
    unsafe = 'instruction_symlink_refused' if link else 'instruction_target_unsafe'
    refused = dict(state='refused', reason=unsafe, resolved=path)
    block = lambda eol: eol.join([START_MARKER.format(version=version, digest=digest).encode(), *body.splitlines(), END_MARKER])
    try:
        resolved = path.resolve(strict=True)
        descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return refused if link else dict(state='absent', resolved=path, old=None, new=block(b'\n') + b'\n', mode=0o600)
    except (OSError, RuntimeError):
        return refused
    info = os.fstat(descriptor)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or (link and info.st_mode & 0o002)
            or info.st_size > MAX_INSTRUCTION_BYTES):
        os.close(descriptor)
        return dict(refused, resolved=resolved)
    with os.fdopen(descriptor, 'rb') as stream:
        data = stream.read()
    found = dict(resolved=resolved, old=data, mode=stat.S_IMODE(info.st_mode), group=info.st_gid,
                 stamp=(info.st_ino, info.st_size, info.st_mtime_ns))
    marks, offset = [], 0
    for line in data.splitlines(keepends=True):
        content = line.rstrip(b'\r\n')
        if content == END_MARKER or START_LINE.fullmatch(content):
            marks.append((content != END_MARKER, offset, offset + len(content), offset + len(line), START_LINE.fullmatch(content)))
        offset += len(line)
    starts, ends = [m for m in marks if m[0]], [m for m in marks if not m[0]]
    # A mangled marker (indented, padded), a duplicate, an unpaired, a nested or a reversed pair is refused, never repaired.
    if (len(re.findall(rb'<!--\s*agent-harness-lab:(?:start|end)', data)) != len(marks) or len(starts) != len(ends)
            or len(starts) > 1 or starts and starts[0][1] > ends[0][1]):
        return dict(found, state='refused', reason='instruction_markers_malformed')
    eol = b'\r\n' if data.count(b'\r\n') * 2 > data.count(b'\n') else b'\n'
    if not starts:
        return dict(found, state='absent', new=data + (eol if data and not data.endswith(b'\n') else b'') + block(eol) + eol)
    (_, begin, _, inner, head), (_, closing, end, _, _) = starts[0], ends[0]
    recorded = START_ATTRIBUTES.match(head[1])
    # The start marker records the hash of the text its release wrote; a different hash (or none) means a person edited it.
    edited = recorded is None or hashlib.sha256(data[inner:closing].replace(b'\r\n', b'\n')).hexdigest() != recorded[2].decode()
    if not edited and (recorded[1].decode(), recorded[2].decode()) == (version, digest):
        return dict(found, state='current')
    return dict(found, state='stale', edited=edited, new=data[:begin] + block(eol) + data[end:])


def write_atomic(path, data, mode, group=None, create=False):
    """Temp file in the target directory, fsync, then rename (an exclusive link when creating); the mode is never widened."""
    path = Path(path)
    descriptor, name = tempfile.mkstemp(prefix='.ahl-instructions-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            if group is not None:
                try:
                    os.fchown(stream.fileno(), -1, group)
                except OSError:
                    pass  # shortcut: keeping the group is best effort when the user is not a member of it
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(name, path) if create else os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
    folder = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)


def instruction_report(targets, release, version, enabled=True, write=None):
    """One typed row per target. Without `write` nothing changes (ahl doctor); the installer passes `write(info, row)`.

    A refusal or a failure becomes a row with the exact fix and never an exception, so links are never left half updated."""
    try:
        digest, body = render(release, version)
        fault = None
    except FileNotFoundError:
        fault = 'instruction_template_missing'
    except (OSError, ValueError):
        fault = 'instruction_template_invalid'
    rows = []
    for label, path, skip in targets:
        row = dict(agent=label, path=str(path), state='skipped', reason=None if enabled else 'instructions_opted_out', notes=[])
        rows.append(row)
        if not enabled or skip:
            row['reason'] = row['reason'] or skip
            continue
        try:
            info = dict(state='refused', reason=fault, resolved=path) if fault else inspect_target(path, version, digest, body)
            row.update(state=info['state'], reason=info.get('reason'), resolved=str(info['resolved']))
            if fault == 'instruction_template_missing':
                row['state'] = 'skipped'
            if info['state'] == 'stale' and info.get('edited'):
                row['notes'].append('region_edited_by_user_replaced')
            if write and info['state'] in ('stale', 'absent') and not fault:
                write(info, row)
                row['state'] = 'created' if info['old'] is None else 'updated'
        except Exception as exc:  # one unreadable or unwritable file must not stop the others or the update
            row.update(state='failed', reason='instruction_write_failed', detail=str(exc))
            row['notes'].clear()
        if row['reason'] in REASONS:
            row['fix'] = REASONS[row['reason']].format(path=path)
    return rows


def sha256_of(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def undo(manifest, folder):
    """Restore what one install recorded, but only when nothing it wrote has changed since; otherwise change nothing."""
    files = [*([manifest['receipt']] if manifest.get('receipt') else []), *manifest['files']]
    for item in (*files, *manifest['links']):
        if not os.path.isabs(item['path']) or (item['saved'] and Path(item['saved']).name != item['saved']):
            raise ValueError('invalid installation backup manifest')
    changed = [i['path'] for i in files if Path(i['path']).is_symlink() or sha256_of(i['path']) != i['sha256']]
    changed += [i['path'] for i in manifest['links'] if os.path.lexists(i['path'])
                and not (Path(i['path']).is_symlink() and os.readlink(i['path']) == i['target'])]
    if changed:
        raise ValueError('changed since the installation, so nothing was restored: ' + ', '.join(changed))
    for item in files:
        if item['saved']:
            write_atomic(item['path'], (folder / item['saved']).read_bytes(), item['mode'])
        else:
            Path(item['path']).unlink()
    for item in manifest['links']:
        if Path(item['path']).is_symlink():
            Path(item['path']).unlink()
        if item['saved']:
            (folder / item['saved']).rename(item['path'])


def locked(root, action):
    """Run `action()` while holding the exclusive installation lock of `root`."""
    try:
        import fcntl
    except ImportError:
        raise ValueError('release installation requires POSIX advisory file locking') from None
    if not hasattr(os, 'O_NOFOLLOW'):
        raise ValueError('release installation requires no-follow file support')
    root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(root / '.install.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
            raise ValueError('installation lock must be an owned regular file')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('another installation or update is in progress for this root') from None
        return action()
    finally:
        os.close(descriptor)


def install(archive, checksums, root, bin_dir, skill_dirs, instructions=None, report=None):
    """Install or update. `instructions` is (enabled, agent) to manage the global instruction files, None to leave them alone;
    one typed row per instruction target is appended to `report`."""
    root, bin_dir = Path(root).resolve(), Path(bin_dir).resolve()
    archive_hash = verify(archive, checksums)
    return locked(root, lambda: _install_locked(archive, archive_hash, root, bin_dir, skill_dirs, instructions,
                                                report))


def rollback(root, name):
    """Restore the launcher, skill links, receipt and instruction files saved by installation backup `name`."""
    root = Path(root).resolve()
    def restore():
        folders = [found.parent for found in (root / 'backups').glob('install-*/manifest.json') if found.parent.name == name]
        if not folders:
            raise ValueError(f'{name} has no rollback manifest; it is unknown, was already restored or is not an installation backup')
        manifest = json.loads((folders[0] / 'manifest.json').read_text())
        undo(manifest, folders[0])
        (folders[0] / 'manifest.json').rename(folders[0] / 'manifest.restored.json')
        return manifest
    return locked(root, restore)


def _install_locked(archive, archive_hash, root, bin_dir, skill_dirs, instructions=None, report=None):
    receipt = root / 'installation.json'
    if receipt.is_symlink() or (receipt.exists() and not receipt.is_file()):
        raise ValueError('installation receipt must be a regular file')
    skill_dirs = [str(Path(folder).expanduser().resolve()) for folder in skill_dirs]
    targets = instruction_targets(instructions[1]) if instructions else []
    with tempfile.TemporaryDirectory(prefix='.stage-', dir=root) as temporary:
        unpacked = extract(archive, temporary)
        version = json.loads((unpacked / 'package.json').read_text())['version']
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            raise ValueError('invalid release version')
        for relative in ('ahl', 'agent_harness_lab/cli.py', *(f'skills/{name}/SKILL.md' for name in SKILLS)):
            if not (unpacked / relative).is_file():
                raise ValueError('release is missing required files')
        if unpacked.name != f'agent-harness-lab-{version}' or Path(archive).name != f'agent-harness-lab-{version}.tar.gz':
            raise ValueError('release filename, root and version must agree')
        destination = root / 'releases' / version
        links = [(bin_dir / 'ahl', Path('ahl'))]
        links += [(Path(folder).expanduser().resolve() / name, Path('skills') / name)
                  for folder in skill_dirs for name in SKILLS]
        links = list(dict(links).items())
        for link, relative in links:
            if link.is_symlink() and not owned_link(link, root, relative):
                raise ValueError(f'refusing to replace a foreign symlink: {link}')
            if link.exists() and not link.is_symlink() and (relative == Path('ahl') or not link.is_dir()):
                raise ValueError(f'refusing to replace a non-skill directory: {link}')
        # An installer-owned link to a retired skill is backed up and not recreated; foreign content stays.
        retired = [Path(folder) / name for folder in skill_dirs for name in RETIRED_SKILLS
                   if owned_link(Path(folder) / name, root, Path('skills') / name)]
        created = False
        if destination.exists():
            marker = destination / '.release-sha256'
            if not marker.is_file() or marker.read_text().strip() != archive_hash:
                raise ValueError('installed version differs; refusing to overwrite it')
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            (unpacked / '.release-sha256').write_text(archive_hash + '\n')
            unpacked.rename(destination)
            created = True
        backup = None
        def backup_dir():
            nonlocal backup
            if backup is None:
                (root / 'backups').mkdir(exist_ok=True)
                backup = Path(tempfile.mkdtemp(prefix='install-', dir=root / 'backups'))
            return backup
        moved, installed, files, saved_receipt, written = [], [], [], None, None
        try:
            for link, relative in [*links, *((link, None) for link in retired)]:
                target = destination / relative if relative else None
                if target and link.is_symlink() and link.resolve() == target:
                    continue
                link.parent.mkdir(parents=True, exist_ok=True)
                if link.exists() or link.is_symlink():
                    saved = backup_dir() / str(len(moved))
                    link.rename(saved)
                    moved.append((saved, link))
                if target:
                    link.symlink_to(target, target_is_directory=relative != Path('ahl'))
                    installed.append(link)
            # Keep update settings in the same rollback boundary as launcher links.
            document = dict(schema_version=1, version=version, bin_dir=str(bin_dir), skill_dirs=skill_dirs)
            if instructions:
                document['instructions'] = dict(enabled=instructions[0], agent=instructions[1])
            written = (json.dumps(document) + '\n').encode()
            state = Path(temporary) / 'installation.json'
            state.write_bytes(written)
            state.chmod(0o600)
            if receipt.exists() and receipt.read_bytes() == written:
                written = None  # unchanged, so the repeat run neither writes nor backs up
            else:
                if receipt.exists():
                    saved_receipt = backup_dir() / 'installation.json'
                    saved_receipt.write_bytes(receipt.read_bytes())
                    saved_receipt.chmod(0o600)
                state.replace(receipt)
        except BaseException:
            for link in reversed(installed):
                link.unlink()
            for saved, link in reversed(moved):
                saved.rename(link)
            if created:
                shutil.rmtree(destination)
            raise
    # The launcher, links and receipt are committed above; from here a problem is a warning, never a rollback.
    if instructions:
        def write(info, row):
            if info['old'] is not None:
                now = os.stat(info['resolved'])
                if (now.st_ino, now.st_size, now.st_mtime_ns) != info['stamp']:
                    raise OSError('the file changed while it was being updated; rerun the update')
                copy = backup_dir() / f'instruction-{len(files)}'
                copy.write_bytes(info['old'])
                copy.chmod(0o600)
                row['backup'] = str(copy)
            item = dict(path=str(info['resolved']), saved=copy.name if info['old'] is not None else None, mode=info['mode'],
                        sha256=hashlib.sha256(info['new']).hexdigest())
            files.append(item)  # recorded first: an interrupted write is still restorable
            try:
                write_atomic(info['resolved'], info['new'], info['mode'], info.get('group'), create=info['old'] is None)
            except BaseException:
                if sha256_of(info['resolved']) != item['sha256']:
                    files.remove(item)
                raise
            if str(info['resolved']) != row['path']:
                row['notes'].append('symlink_followed')
        report = [] if report is None else report
        report.extend(instruction_report(targets, destination, version, instructions[0], write))
    if backup:
        manifest = dict(schema_version=1, version=version, files=files,
                        links=[dict(path=str(link), target=str(destination / dict(links)[link]),
                                    saved=next((saved.name for saved, original in moved if original == link), None))
                               for link in installed],
                        receipt=written and dict(path=str(receipt), saved=saved_receipt and saved_receipt.name, mode=0o600,
                                                 sha256=hashlib.sha256(written).hexdigest()))
        (backup / 'manifest.json').write_text(json.dumps(manifest) + '\n')
        (backup / 'manifest.json').chmod(0o600)
    return destination, backup


def main(argv=None, *, skill_dirs=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Requires Python 3.11+. Downloads release metadata/assets over HTTPS unless --archive and --checksums are supplied.\n'
        'Checks the archive SHA-256 against SHA256SUMS; obtain both files from a trusted release source.\n'
        'Writes a versioned installation, ahl launcher, selected global skill links and a private installation receipt.\n'
        'Also keeps one marked region of the global instruction files (Claude CLAUDE.md, Codex AGENTS.md) in step with the\n'
        'release, leaving every byte outside the markers untouched; see docs/global-instructions-reference.md.\n'
        'Replaced links/directories and edited instruction files are backed up; prints installation paths and shell PATH guidance.\n'
        'Exit 0: installed; 2: invalid arguments, download or installation error; 3: installed, but an instruction file was\n'
        'refused or could not be written (the warning names the file and the exact fix; launcher and skill links are complete).\n'
        'Examples: python install.py --agent codex; python install.py --archive release.tar.gz --checksums SHA256SUMS')
    parser.add_argument('--version', default='latest', help='Release version to download when --archive is omitted, with optional v prefix (default: %(default)s)')
    parser.add_argument('--root', type=Path, default=Path.home() / '.local/share/agent-harness-lab', help='Installation root for versioned releases, backups and receipt (default: ~/.local/share/agent-harness-lab)')
    parser.add_argument('--bin-dir', type=Path, default=Path.home() / '.local/bin', help='Directory for the ahl launcher symlink; add it to PATH (default: ~/.local/bin)')
    parser.add_argument('--agent', choices=('codex', 'claude', 'both'), help='Global skill-link and instruction-file targets: ~/.agents/skills and AGENTS.md for codex, ~/.claude/skills and CLAUDE.md for claude (default: both; an update keeps the recorded choice)')
    parser.add_argument('--instructions', action=argparse.BooleanOptionalAction, help='Write or refresh the managed region of the global instruction files; --no-instructions opts out and the choice is recorded in the installation receipt for later updates (default: the recorded choice, otherwise on)')
    parser.add_argument('--archive', type=Path, help='Existing local release .tar.gz for offline installation; requires matching --checksums and skips downloads')
    parser.add_argument('--checksums', type=Path, help='Existing SHA256SUMS containing the exact --archive filename and digest; required together with --archive')
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11):
        parser.error('Python 3.11+ is required')
    if bool(args.archive) != bool(args.checksums):
        parser.error('--archive and --checksums must be supplied together')
    if args.version != 'latest' and not VERSION.fullmatch(args.version.removeprefix('v')):
        parser.error('invalid release version')
    directories = []
    if args.agent in ('codex', 'both', None):
        directories.append(Path.home() / '.agents/skills')
    if args.agent in ('claude', 'both', None):
        directories.append(Path.home() / '.claude/skills')
    if skill_dirs is not None:
        directories = skill_dirs
    state = dict(prior_state(args.root), skill_dirs=[str(folder) for folder in directories])
    settings = instruction_settings(state, args.agent, args.instructions)
    report = []
    try:
        with tempfile.TemporaryDirectory(prefix='ahl-download-') as temporary:
            archive, checksums = args.archive, args.checksums
            if archive is None:
                endpoint = 'latest' if args.version == 'latest' else 'tags/v' + args.version.removeprefix('v')
                request = Request(f'https://api.github.com/repos/{REPOSITORY}/releases/{endpoint}', headers={'User-Agent': 'agent-harness-lab-installer'})
                with urlopen(request, timeout=60) as response:
                    release = json.load(response)
                version = release['tag_name'].removeprefix('v')
                if not VERSION.fullmatch(version):
                    raise ValueError('invalid release tag')
                base = f'https://github.com/{REPOSITORY}/releases/download/v{version}/'
                archive = Path(temporary) / f'agent-harness-lab-{version}.tar.gz'
                checksums = Path(temporary) / 'SHA256SUMS'
                fetch(base + archive.name, archive)
                fetch(base + 'SHA256SUMS', checksums)
            destination, backup = install(archive, checksums, args.root, args.bin_dir, directories, settings, report)
        print(f'Installed {destination.name}: {args.bin_dir / "ahl"}')
        if backup:
            print(f'Previous installations backed up at {backup}')
            if (backup / 'manifest.json').is_file():
                print(f'Undo this installation with: ahl rollback {backup.name}')
        for row in report:
            if row['state'] in ('created', 'updated'):
                where = f' (symlink resolved to {row["resolved"]})' if 'symlink_followed' in row['notes'] else ''
                print(f'Instructions {row["state"]}: {row["path"]}{where}' + (f'; previous bytes saved at {row["backup"]}' if 'backup' in row else ''))
                for note in row['notes']:
                    if note in REASONS:
                        print(f'  {note}: {REASONS[note].format(path=row["path"])}')
        bin_dir = args.bin_dir.expanduser().resolve()
        if str(bin_dir) not in os.environ.get('PATH', '').split(os.pathsep):
            print('For this POSIX shell, run: export PATH=' + shlex.quote(str(bin_dir)) + ':"$PATH"')
        print(f'Check: {shlex.quote(str(bin_dir / "ahl"))} --help')
        print('Update later: ahl update (after a release containing this command is installed)')
    except (ValueError, OSError, KeyError, tarfile.TarError) as exc:
        parser.exit(2, f'installation error: {exc}\n')
    problems = [row for row in report if row['state'] in ('refused', 'failed')]
    if problems:
        parser.exit(3, 'warning: installed, but the global instruction files below were not updated; the launcher and skill links are complete\n'
                    + ''.join(f'  {row["path"]}: {row["reason"]}' + (f' ({row["detail"]})' if 'detail' in row else '') + f'. Fix: {row["fix"]}\n' for row in problems))


if __name__ == '__main__':
    main()
