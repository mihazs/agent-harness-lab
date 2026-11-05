"""Declared Claude Code memory files and bounded @path imports; never loading proof."""
import bisect
import codecs
from collections import deque
from dataclasses import replace
import fnmatch
import hashlib
import os
import posixpath
import re
import stat
from pathlib import Path, PurePosixPath
from agent_harness_lab.evaluation.integrity import relative
from .instructions import InstructionEdge, InstructionNode
from .schema import digest

# Public memory documentation, checked 2026-11-04 for the declared 2.1.317 profile.
IMPORT_HOPS = 4
SKIPPED_BYTES = 4*1024*1024
# Smallest documented per-file limit for on-demand files and their imports (200K context window); the
# runtime truncates past it rather than skipping, and the session's window is never declared here.
ON_DEMAND_CHARS = 40000
LAUNCH = ('CLAUDE.md', '.claude/CLAUDE.md', 'CLAUDE.local.md')
AGENTS = ('AGENTS.md', '.claude/AGENTS.md')
LOCAL, PRIVATE, ON_DEMAND = 'CLAUDE.local.md', 'private_excluded', 'on_demand_subdirectory'
# Declared "Project instructions" (instructionFiles) values; undeclared keeps the assumed-default labels.
MODES = ('claude-md-or-agents-md', 'claude-md-and-agents-md', 'claude-md', 'managed-only')
MODE_EXCLUDED, EXCLUDES = 'excluded_by_declared_instruction_files', 'claude_md_excludes_match_unverified'
TREE_ENTRIES, TREE_DEPTH = 100000, 64
_DIRECTORY = os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC
CONTAINER = re.compile(r'(?:[ \t]{0,3}>)*')
LIST = re.compile(r'[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+')
FENCE = re.compile(r'[ \t]*(`{3,}|~{3,})')
RUN = re.compile(r'`+')
TOKEN = re.compile(r'(?<!\S)@((?:\\ |\S)+)')
COMMENTED, UNCERTAIN = 'import_inside_html_comment', 'import_markdown_context_uncertain'
TRUNCATED, TRUNCATION = 'import_beyond_on_demand_truncation_unverified', 'on_demand_truncation_unverified'
UNREADABLE = 'instruction_file_unreadable'
# probe() statuses that fail the launch chain closed but only mark an on-demand import unresolved.
CROSSING = {'link': 'instruction imports cannot cross a link or non-directory',
            'nested_repository': 'instruction imports cannot cross a nested repository',
            'special': 'Git input must be a bounded single-link regular file'}
UNRESOLVED = {'missing': 'import_missing', 'directory': 'import_target_not_regular_file',
              'link': 'import_crosses_link_or_repository', 'nested_repository': 'import_crosses_link_or_repository',
              'special': 'import_target_link_not_followed', 'unreadable': 'import_target_unreadable'}


def inside(regions, position):
    index = bisect.bisect_right(regions, (position, float('inf')))-1
    return index >= 0 and regions[index][0] <= position < regions[index][1]


def spans(line, span):
    """Blank closed one-line code spans; spans left open across lines are uncertain."""
    chars, uncertain, start, runs, pending = list(line), [], 0, list(RUN.finditer(line)), {}
    for index, run in enumerate(runs): pending.setdefault(len(run[0]), deque()).append(index)
    index = 0
    while index < len(runs):
        run = runs[index]; size = len(run[0])
        if span:
            if size == span: uncertain.append((start, run.end())); span = None
            index += 1; continue
        queue = pending[size]
        while queue and queue[0] <= index: queue.popleft()
        if not queue:
            span, start = size, run.start(); index += 1; continue
        close = runs[queue[0]]
        chars[run.start():close.end()] = ' '*(close.end()-run.start()); index = queue.popleft()+1
    if span: uncertain.append((start, len(line)))
    return ''.join(chars), uncertain, span


def imports(text, limit=None):
    """Return (token, uncertainty) pairs; fenced code is skipped, unclear Markdown stays uncertain.

    A token ending past `limit` characters is uncertain: the runtime may truncate the file before it."""
    found, fence, commented, span, offset = [], None, False, None, 0
    for whole in text.splitlines(True):
        line, base = whole.splitlines()[0], offset; offset += len(whole)
        body = line[CONTAINER.match(line).end():]
        listed = LIST.match(body)
        rest = body[listed.end():] if listed else body
        opening = FENCE.match(rest)
        if opening and opening[1][0] == '`' and '`' in rest[opening.end():]: opening = None
        if fence:
            if opening and opening[1][0] == fence[0] and len(opening[1]) >= len(fence) and not body.strip().strip(fence[0]):
                fence = None
            continue
        if opening and span is None:
            fence = opening[1]; continue
        if not body.strip(): span = None; continue
        indented = body.startswith(('    ', '\t'))
        line, uncertain, span = spans(line, span)
        regions, start, position = [], 0, 0
        while (position := line.find('-->' if commented else '<!--', position)) >= 0:
            if commented: regions.append((start, position)); position += 3
            else: start = position; position += 4
            commented = not commented
        if commented: regions.append((start, len(line)))
        for match in TOKEN.finditer(line):
            token = match[1].replace('\\ ', ' ')
            if token[0] in '"\'': continue
            where = match.start()
            found.append((token, COMMENTED if inside(regions, where) else
                          UNCERTAIN if indented or inside(uncertain, where) else
                          TRUNCATED if limit is not None and base+match.end() > limit else None))
    return found


def resolve(source, token, cwd):
    """Classify an import without touching the filesystem."""
    if token.startswith(('/', '~')): return None, 'external_import_not_captured'
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(source), token))
    if joined == '..' or joined.startswith('../'): return None, 'external_import_not_captured'
    try: relative(joined)
    except ValueError: return None, 'invalid_import_path'
    if '.git' in Path(joined).parts: return None, 'unsafe_import_target'
    inside = cwd == '.' or joined == cwd or joined.startswith(cwd+'/')
    return joined, None if inside else 'external_import_requires_approval'


def scope_of(path):
    for name in sorted(LAUNCH+AGENTS, key=len, reverse=True):
        if path == name or path.endswith('/'+name): return path[:-len(name)].rstrip('/') or '.'
    return None


def cyclic(pairs):
    """Edges whose endpoints share a strongly connected component (iterative Tarjan)."""
    graph = {}
    for source, target in pairs: graph.setdefault(source, []).append(target); graph.setdefault(target, [])
    index, low, component, stack, on_stack, counter = {}, {}, {}, [], set(), 0
    for root in graph:
        if root in index: continue
        work = [(root, iter(graph[root]))]; index[root] = low[root] = counter; counter += 1
        stack.append(root); on_stack.add(root)
        while work:
            item, children = work[-1]
            child = next(children, None)
            if child is not None:
                if child not in index:
                    index[child] = low[child] = counter; counter += 1
                    stack.append(child); on_stack.add(child); work.append((child, iter(graph[child])))
                elif child in on_stack: low[item] = min(low[item], index[child])
                continue
            work.pop()
            if work: low[work[-1][0]] = min(low[work[-1][0]], low[item])
            if low[item] == index[item]:
                while True:
                    member = stack.pop(); on_stack.discard(member); component[member] = item
                    if member == item: break
    return {(s, t) for s, t in pairs if component[s] == component[t]}


def probe(folder, checkout, target):
    """'file' (single-link regular), 'missing', 'directory', or a CROSSING status; nothing is followed."""
    parts = Path(target).parts
    for size in range(1, len(parts)):
        prefix = str(Path(checkout, *parts[:size]))
        info = folder.inspect(prefix)
        if info is None: return 'missing'
        if not stat.S_ISDIR(info.st_mode): return 'link'
        if folder.inspect(prefix+'/.git') is not None: return 'nested_repository'
    info = folder.inspect(str(Path(checkout)/target))
    if info is None: return 'missing'
    if stat.S_ISDIR(info.st_mode): return 'directory'
    return 'file' if stat.S_ISREG(info.st_mode) and info.st_nlink == 1 else 'special'


def scan(folder, name):
    """Stream one file into (sha256, mode, (bytes, utf8, blank)) without keeping it; None when missing."""
    with folder.open(name) as opened:
        if opened is None: return None
        descriptor, info = opened
        hashed, decoder, utf8, blank = hashlib.sha256(), codecs.getincrementaldecoder('utf-8')(), True, True
        for block in folder.blocks(descriptor, info, None, False, 65536):
            hashed.update(block)
            if not utf8: continue
            try: blank = not decoder.decode(block).strip() and blank
            except UnicodeDecodeError: utf8 = False
        if utf8:
            try: decoder.decode(b'', True)
            except UnicodeDecodeError: utf8 = False
        return hashed.hexdigest(), stat.S_IMODE(info.st_mode), (info.st_size, utf8, blank and utf8)


class _Bound(Exception):
    """The whole-tree enumeration passed TREE_ENTRIES or TREE_DEPTH."""


def tree(folder, checkout):
    """Instruction-named entries as {path: is single-link regular file}, completeness and unreadable directories.

    Directories are opened by name beneath an opened directory with O_NOFOLLOW; links are listed, never followed.
    .git and nested repositories (a directory holding .git) are skipped: their instructions are their own.
    A subdirectory that cannot be opened, listed or stat'ed is reported, not fatal; the checkout root still is."""
    found, seen, unreadable = {}, [0], []
    def visit(descriptor, prefix, depth):
        folder.check(); rows = []
        try:
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    seen[0] += 1
                    if seen[0] > TREE_ENTRIES: raise _Bound
                    rows.append((entry.name, entry.stat(follow_symlinks=False)))
        except PermissionError:
            if not prefix: raise
            unreadable.append(prefix[:-1]); return
        if prefix and any(name == '.git' for name, _ in rows): return
        for name, info in sorted(rows):
            path = prefix+name
            if name == '.git': continue
            if stat.S_ISDIR(info.st_mode):
                if depth >= TREE_DEPTH: raise _Bound
                try: nested = os.open(name, _DIRECTORY, dir_fd=descriptor)
                except PermissionError: unreadable.append(path); continue
                try: visit(nested, path+'/', depth+1)
                finally: os.close(nested)
            elif scope_of(path) is not None: found[path] = stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    descriptor = folder.directory(checkout)
    try:
        visit(descriptor, '', 0); return found, True, unreadable
    except _Bound: return found, False, unreadable
    finally: os.close(descriptor)


def excluded(path, patterns):
    """Declared claudeMdExcludes match. The runtime matches absolute paths; this relative match is unverified."""
    return any(fnmatch.fnmatchcase('/'+path, pattern) or fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def capture(profile, scopes, selected, folder, checkout):
    """Declared Claude memory graph of the whole checkout; returns (nodes, edges, resolution status).

    Files are hashed while streamed and never kept; an expanded file is re-read, at most SKIPPED_BYTES of it."""
    cwd, mode = profile['task_cwd'], profile.get('instruction_files')
    patterns, known = profile.get('claude_md_excludes', ()), mode is None or mode in MODES
    agents_mode = 'declared_instruction_files_setting' if mode else 'assumed_default_instruction_files_setting'
    nodes, facts, path_of, depth, links, edges, probed, hidden = {}, {}, {}, {}, {}, [], {}, []
    def region(path):  # where an unreadable entry can hide applicable or on-demand instructions
        return cwd == '.' or path == cwd or path.startswith(cwd+'/') or cwd.startswith(path+'/')
    def keep(value, info, path):
        nodes[value.path_sha256], facts[value.path_sha256] = value, info
        if path is not None: path_of[value.path_sha256] = path
    def read(path, applicability, reason, tolerant=False):
        # CLAUDE.local.md is personal: hashed, never admitted, expanded or relabelled.
        private = PurePosixPath(path).name == LOCAL
        try: scanned = scan(folder, str(Path(checkout)/path))
        except PermissionError:
            if not tolerant: raise
            scanned, reason = False, UNREADABLE
            if not private and region(path): hidden.append(path)
        sha, permissions, info = scanned or (None, None, None)
        value = InstructionNode(digest(path), digest(str(Path(path).parent)), sha, permissions,
                                'present' if scanned else 'missing' if scanned is None else 'unknown', applicability, reason)
        if private: value = replace(value, applicability=PRIVATE, reason=PRIVATE if scanned else reason
                                    if scanned is False else 'missing')
        keep(value, info, path)
        return value, info
    def load(key):
        size, data = facts[key][0], folder.read(str(Path(checkout)/path_of[key]), SKIPPED_BYTES, prefix=True)
        if data is None or size <= SKIPPED_BYTES and hashlib.sha256(data).hexdigest() != nodes[key].content_sha256:
            raise ValueError('Git input changed during reading')
        return codecs.getincrementaldecoder('utf-8')().decode(data, size <= SKIPPED_BYTES)
    def locate(target, conditional):
        if target not in probed:
            try: probed[target] = probe(folder, checkout, target)
            except PermissionError:
                if not conditional: raise
                probed[target] = 'unreadable'
        status = probed[target]
        if status in CROSSING and not conditional: raise ValueError(CROSSING[status])
        return status
    def relabel(key, applicability, reason):
        if nodes[key].applicability != PRIVATE: nodes[key] = replace(nodes[key], applicability=applicability, reason=reason)
    def admit(key, hops, queue, conditional, reason=None):
        if conditional:
            if hops: relabel(key, 'unknown', 'on_demand_import')
        else: relabel(key, 'declared_applicable', reason or ('import' if hops else 'directory_concatenation'))
        depth[key] = hops; queue.append(key)
    def unresolved(source_path, reason):
        key = digest(['unresolved_imports', source_path, reason])
        if key not in nodes:
            external, missing = reason == 'external_import_not_captured', reason == 'import_missing'
            keep(InstructionNode(key, digest(str(Path(source_path).parent)), None, None, 'missing' if missing else 'unknown',
                                 'unknown' if external or missing else 'unsupported', reason,
                                 origin='external_reference' if external else 'project'), None, None)
        return key
    def expand(queue, conditional):
        index = 0
        while index < len(queue):
            folder.check()
            source = queue[index]; index += 1; source_path = path_of[source]
            text = load(source)
            # On demand, the runtime truncates rather than skips; past the smallest documented limit nothing is certain.
            if conditional and len(text) > ON_DEMAND_CHARS: nodes[source] = replace(nodes[source], reason=TRUNCATION)
            for token, uncertainty in imports(text, ON_DEMAND_CHARS if conditional else None):
                target, reason = resolve(source_path, token, cwd)
                key = digest(target) if target is not None else unresolved(source_path, reason)
                # A launch import is probed even when the walk recorded it, so links and unreadable files fail closed.
                if target is not None and (key not in nodes or not conditional):
                    status = locate(target, conditional)
                    if status == 'file' and (key not in nodes or facts[key] is None and nodes[key].reason == UNREADABLE):
                        read(target, 'unknown', 'import_pending', conditional)
                    elif key not in nodes: key = unresolved(source_path, UNRESOLVED[status])
                links.setdefault((source, key), None)
                info, current = facts[key], nodes[key]
                if info is None or key in depth or current.applicability in ('declared_applicable', PRIVATE): continue
                hops = depth[source]+1
                blocked = (uncertainty or reason or ('import_depth_limit' if hops > IMPORT_HOPS else None)
                           or ('runtime_size_limit_unverified' if not conditional and info[0] > SKIPPED_BYTES else None)
                           or (None if info[1] else 'non_utf8_import'))
                if blocked is None: admit(key, hops, queue, conditional)
                elif current.reason in ('import_pending', 'outside_task_scope'):
                    nodes[key] = replace(current, applicability='unknown', reason=blocked)
    found, complete, unreadable = tree(folder, checkout)
    previous, claude_files, agent_files, launch = [], [], [], []
    for scope in scopes:
        current = []
        for name in LAUNCH+AGENTS:
            path = str(Path(scope)/name)
            value, info = read(path, 'unknown', 'missing')
            if info is None: continue
            key = value.path_sha256
            if name == LOCAL: claude_files.append(key); continue
            if not info[1]: raise ValueError('launch instruction file is not UTF-8')
            if name in AGENTS:
                agent_files.append(key); nodes[key] = replace(value, reason='instruction_files_setting_unknown'); continue
            claude_files.append(key)
            if not known: nodes[key] = replace(value, reason='instruction_files_setting_unknown')
            elif mode == 'managed-only': nodes[key] = replace(value, applicability='out_of_scope', reason=MODE_EXCLUDED)
            elif excluded(path, patterns): nodes[key] = replace(value, reason=EXCLUDES)
            elif info[2]: nodes[key] = replace(value, reason='empty')
            elif info[0] > SKIPPED_BYTES: nodes[key] = replace(value, reason='runtime_size_limit_unverified')
            else: admit(key, 0, launch, False); current.append(key)
        edges.extend(InstructionEdge('parent', source, target) for target in current for source in previous)
        previous = current or previous
    for key in agent_files:
        if not known: continue
        if excluded(path_of[key], patterns): nodes[key] = replace(nodes[key], reason=EXCLUDES)
        elif mode == 'claude-md-and-agents-md' or mode == 'claude-md-or-agents-md' and not claude_files:
            if facts[key][2]: nodes[key] = replace(nodes[key], reason='empty')
            elif facts[key][0] > SKIPPED_BYTES: nodes[key] = replace(nodes[key], reason='runtime_size_limit_unverified')
            else: admit(key, 0, launch, False, 'declared_instruction_files_setting')
    claude_scopes = {}  # a subdirectory's first CLAUDE.md, .claude/CLAUDE.md or CLAUDE.local.md, in LAUNCH order
    for name in LAUNCH:
        for path in sorted(found):
            if path == str(Path(scope_of(path))/name): claude_scopes.setdefault(scope_of(path), path)
    on_demand, shadows = [], []
    for path, regular in sorted(found.items()):
        if digest(path) in nodes: continue
        scope, name = scope_of(path), PurePosixPath(path).name
        below = scope not in scopes and (cwd == '.' or scope.startswith(cwd+'/'))
        if not regular:
            private = name == LOCAL
            keep(InstructionNode(digest(path), digest(str(Path(path).parent)), None, None, 'unknown',
                                 PRIVATE if private else 'unsupported', PRIVATE if private else 'instruction_link_not_followed'), None, path)
        elif name == LOCAL or not below: read(path, 'out_of_scope', 'outside_task_scope', True)
        elif not known: read(path, 'unknown', 'instruction_files_setting_unknown', True)
        elif excluded(path, patterns): read(path, 'unknown', EXCLUDES, True)
        elif name == 'AGENTS.md' and mode in ('claude-md', 'managed-only'): read(path, 'out_of_scope', MODE_EXCLUDED, True)
        elif name == 'AGENTS.md' and mode != 'claude-md-and-agents-md' and scope in claude_scopes:
            read(path, 'shadowed', agents_mode, True); shadows.append((digest(claude_scopes[scope]), digest(path)))
        else:
            value, info = read(path, 'unknown', ON_DEMAND, True)
            if info is not None and info[1] and not info[2]: on_demand.append(value.path_sha256)
    expand(launch, False)
    on_demand = [key for key in on_demand if key not in depth]
    for key in on_demand: depth[key] = 0
    expand(on_demand, True)
    cycles = cyclic(list(links))
    edges.extend(InstructionEdge('import_cycle' if pair in cycles else 'imports', *pair) for pair in links)
    edges.extend(InstructionEdge('shadows', *pair) for pair in shadows if nodes[pair[1]].applicability == 'shadowed')
    for key in agent_files:
        if nodes[key].applicability != 'unknown' or nodes[key].reason != 'instruction_files_setting_unknown' or not known: continue
        if mode in ('claude-md', 'managed-only'): nodes[key] = replace(nodes[key], applicability='out_of_scope', reason=MODE_EXCLUDED)
        elif claude_files:
            nodes[key] = replace(nodes[key], applicability='shadowed', reason=agents_mode)
            edges.append(InstructionEdge('shadows', claude_files[0], key))
    for path in sorted(set(selected)-set(path_of.values())):
        scope, parts = scope_of(path), Path(path).parts
        if any(parts[i:i+2] == ('.claude', 'rules') for i in range(len(parts)-1)): read(path, 'unknown', 'rules_not_modeled')
        elif scope is not None and scope not in scopes and (cwd == '.' or scope.startswith(cwd+'/')):
            read(path, 'unknown', ON_DEMAND)
        elif str(Path(path).parent) in scopes: read(path, 'unsupported', 'unrecognized_filename')
        else: read(path, 'out_of_scope', 'outside_task_scope')
    for path in unreadable:
        keep(InstructionNode(digest(['unreadable_directory', path]), digest(path), None, None, 'unknown', 'unknown',
                             'instruction_directory_unreadable'), None, None)
        if region(path): hidden.append(path)
    if not complete:
        keep(InstructionNode(digest(['tree_enumeration', checkout]), digest('.'), None, None, 'unknown', 'unknown',
                             'tree_enumeration_bound_exceeded'), None, None)
    return tuple(nodes.values()), tuple(edges), 'declared' if complete and not hidden else 'blocked'
