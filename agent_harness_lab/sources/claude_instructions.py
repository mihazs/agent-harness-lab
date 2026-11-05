"""Declared Claude Code memory files and bounded @path imports; never loading proof."""
import bisect
from collections import deque
from dataclasses import replace
import fnmatch
import os
import posixpath
import re
import stat
from pathlib import Path, PurePosixPath
from agent_harness_lab.evaluation.integrity import relative
from .instructions import InstructionEdge, InstructionNode, read_node
from .schema import digest

# Public memory documentation, checked 2026-11-04 for the declared 2.1.317 profile.
IMPORT_HOPS = 4
SKIPPED_BYTES = 4*1024*1024
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


def imports(text):
    """Return (token, uncertainty) pairs; fenced code is skipped, unclear Markdown stays uncertain."""
    found, fence, commented, span = [], None, False, None
    for line in text.splitlines():
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
                          UNCERTAIN if indented or inside(uncertain, where) else None))
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
    """Existing regular file, missing path or directory; links and nested repositories fail closed."""
    parts = Path(target).parts
    for size in range(1, len(parts)):
        prefix = str(Path(checkout, *parts[:size]))
        info = folder.inspect(prefix)
        if info is None: return 'missing'
        if not stat.S_ISDIR(info.st_mode): raise ValueError('instruction imports cannot cross a link or non-directory')
        if folder.inspect(prefix+'/.git') is not None: raise ValueError('instruction imports cannot cross a nested repository')
    info = folder.inspect(str(Path(checkout)/target))
    return 'missing' if info is None else 'directory' if stat.S_ISDIR(info.st_mode) else 'file'


class _Bound(Exception):
    """The whole-tree enumeration passed TREE_ENTRIES or TREE_DEPTH."""


def tree(folder, checkout):
    """Instruction-named entries in the checkout as {path: is single-link regular file}, plus completeness.

    Directories are opened by name beneath an opened directory with O_NOFOLLOW; links are listed, never followed.
    .git and nested repositories (a directory holding .git) are skipped: their instructions are their own."""
    found, seen = {}, [0]
    def visit(descriptor, prefix, depth):
        folder.check(); rows = []
        with os.scandir(descriptor) as entries:
            for entry in entries:
                seen[0] += 1
                if seen[0] > TREE_ENTRIES: raise _Bound
                rows.append((entry.name, entry.stat(follow_symlinks=False)))
        if prefix and any(name == '.git' for name, _ in rows): return
        for name, info in sorted(rows):
            path = prefix+name
            if name == '.git': continue
            if stat.S_ISDIR(info.st_mode):
                if depth >= TREE_DEPTH: raise _Bound
                nested = os.open(name, _DIRECTORY, dir_fd=descriptor)
                try: visit(nested, path+'/', depth+1)
                finally: os.close(nested)
            elif scope_of(path) is not None: found[path] = stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    descriptor = folder.directory(checkout)
    try:
        visit(descriptor, '', 0); return found, True
    except _Bound: return found, False
    finally: os.close(descriptor)


def excluded(path, patterns):
    """Declared claudeMdExcludes match. The runtime matches absolute paths; this relative match is unverified."""
    return any(fnmatch.fnmatchcase('/'+path, pattern) or fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def capture(profile, scopes, selected, folder, checkout):
    """Declared Claude memory graph of the whole checkout; returns (nodes, edges, resolution status)."""
    cwd, mode = profile['task_cwd'], profile.get('instruction_files')
    patterns, known = profile.get('claude_md_excludes', ()), mode is None or mode in MODES
    agents_mode = 'declared_instruction_files_setting' if mode else 'assumed_default_instruction_files_setting'
    nodes, raw, path_of, depth, links, edges, probed = {}, {}, {}, {}, {}, [], {}
    def keep(value, data, path):
        nodes[value.path_sha256], raw[value.path_sha256] = value, data
        if path is not None: path_of[value.path_sha256] = path
    def read(path, applicability, reason):
        # CLAUDE.local.md is personal: hashed, never admitted, expanded or relabelled.
        value, data = read_node(folder, checkout, path, applicability, reason)
        if PurePosixPath(path).name == LOCAL: value = replace(value, applicability=PRIVATE, reason=PRIVATE if data else 'missing')
        keep(value, data, path)
        return value, data
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
            for token, uncertainty in imports(raw[source].decode('utf-8')):
                target, reason = resolve(source_path, token, cwd)
                key = digest(target) if target is not None else unresolved(source_path, reason)
                if target is not None and key not in nodes:
                    if target not in probed: probed[target] = probe(folder, checkout, target)
                    status = probed[target]
                    if status == 'file': read(target, 'unknown', 'import_pending')
                    else: key = unresolved(source_path, 'import_missing' if status == 'missing' else 'import_target_not_regular_file')
                links.setdefault((source, key), None)
                data, current = raw[key], nodes[key]
                if data is None or key in depth or current.applicability in ('declared_applicable', PRIVATE): continue
                hops = depth[source]+1
                blocked = (uncertainty or reason or ('import_depth_limit' if hops > IMPORT_HOPS else None)
                           or ('runtime_size_limit_unverified' if len(data) > SKIPPED_BYTES else None))
                if blocked is None:
                    try: data.decode('utf-8')
                    except UnicodeDecodeError: blocked = 'non_utf8_import'
                if blocked is None: admit(key, hops, queue, conditional)
                elif current.reason in ('import_pending', 'outside_task_scope'):
                    nodes[key] = replace(current, applicability='unknown', reason=blocked)
    found, complete = tree(folder, checkout)
    previous, claude_files, agent_files, launch = [], [], [], []
    for scope in scopes:
        current = []
        for name in LAUNCH+AGENTS:
            path = str(Path(scope)/name)
            value, data = read(path, 'unknown', 'missing')
            if data is None: continue
            key = value.path_sha256
            if name == LOCAL: claude_files.append(key); continue
            text = data.decode('utf-8')
            if name in AGENTS:
                agent_files.append(key); nodes[key] = replace(value, reason='instruction_files_setting_unknown'); continue
            claude_files.append(key)
            if not known: nodes[key] = replace(value, reason='instruction_files_setting_unknown')
            elif mode == 'managed-only': nodes[key] = replace(value, applicability='out_of_scope', reason=MODE_EXCLUDED)
            elif excluded(path, patterns): nodes[key] = replace(value, reason=EXCLUDES)
            elif not text.strip(): nodes[key] = replace(value, reason='empty')
            elif len(data) > SKIPPED_BYTES: nodes[key] = replace(value, reason='runtime_size_limit_unverified')
            else: admit(key, 0, launch, False); current.append(key)
        edges.extend(InstructionEdge('parent', source, target) for target in current for source in previous)
        previous = current or previous
    for key in agent_files:
        if not known: continue
        if excluded(path_of[key], patterns): nodes[key] = replace(nodes[key], reason=EXCLUDES)
        elif mode == 'claude-md-and-agents-md' or mode == 'claude-md-or-agents-md' and not claude_files:
            admit(key, 0, launch, False, 'declared_instruction_files_setting')
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
        elif name == LOCAL or not below: read(path, 'out_of_scope', 'outside_task_scope')
        elif not known: read(path, 'unknown', 'instruction_files_setting_unknown')
        elif excluded(path, patterns): read(path, 'unknown', EXCLUDES)
        elif name == 'AGENTS.md' and mode in ('claude-md', 'managed-only'): read(path, 'out_of_scope', MODE_EXCLUDED)
        elif name == 'AGENTS.md' and mode != 'claude-md-and-agents-md' and scope in claude_scopes:
            read(path, 'shadowed', agents_mode); shadows.append((digest(claude_scopes[scope]), digest(path)))
        else:
            value, data = read(path, 'unknown', ON_DEMAND)
            try: usable = bool(data.decode('utf-8').strip()) and len(data) <= SKIPPED_BYTES
            except UnicodeDecodeError: usable = False
            if usable: on_demand.append(value.path_sha256)
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
    if not complete:
        keep(InstructionNode(digest(['tree_enumeration', checkout]), digest('.'), None, None, 'unknown', 'unknown',
                             'tree_enumeration_bound_exceeded'), None, None)
    return tuple(nodes.values()), tuple(edges), 'declared' if complete else 'blocked'
