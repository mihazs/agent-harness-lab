"""Declared Claude Code memory files and bounded @path imports; never loading proof."""
import bisect
from collections import deque
from dataclasses import replace
import posixpath
import re
import stat
from pathlib import Path
from agent_harness_lab.evaluation.integrity import relative
from .instructions import InstructionEdge, InstructionNode, read_node
from .schema import digest

# Public memory documentation, checked 2026-11-04 for the declared 2.1.317 profile.
IMPORT_HOPS = 4
SKIPPED_BYTES = 4*1024*1024
LAUNCH = ('CLAUDE.md', '.claude/CLAUDE.md', 'CLAUDE.local.md')
AGENTS = ('AGENTS.md', '.claude/AGENTS.md')
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


def capture(cwd, scopes, selected, folder, checkout):
    nodes, raw, path_of, depth, queue, links, edges, probed = {}, {}, {}, {}, [], {}, [], {}
    def keep(value, data, path):
        nodes[value.path_sha256], raw[value.path_sha256] = value, data
        if path is not None: path_of[value.path_sha256] = path
    def read(path, applicability, reason):
        value, data = read_node(folder, checkout, path, applicability, reason); keep(value, data, path)
        return value, data
    def admit(key, hops):
        nodes[key] = replace(nodes[key], applicability='declared_applicable', reason='import' if hops else 'directory_concatenation')
        depth[key] = hops; queue.append(key)
    def unresolved(source_path, reason):
        key = digest(['unresolved_imports', source_path, reason])
        if key not in nodes:
            external, missing = reason == 'external_import_not_captured', reason == 'import_missing'
            keep(InstructionNode(key, digest(str(Path(source_path).parent)), None, None, 'missing' if missing else 'unknown',
                                 'unknown' if external or missing else 'unsupported', reason,
                                 origin='external_reference' if external else 'project'), None, None)
        return key
    previous, claude_files, agent_files = [], [], []
    for scope in scopes:
        current = []
        for name in LAUNCH+AGENTS:
            value, data = read(str(Path(scope)/name), 'unknown', 'missing')
            if data is None: continue
            text, key = data.decode('utf-8'), value.path_sha256
            if name in AGENTS:
                agent_files.append(key); nodes[key] = replace(value, reason='instruction_files_setting_unknown'); continue
            claude_files.append(key)
            if not text.strip(): nodes[key] = replace(value, reason='empty')
            elif len(data) > SKIPPED_BYTES: nodes[key] = replace(value, reason='runtime_size_limit_unverified')
            else: admit(key, 0); current.append(key)
        edges.extend(InstructionEdge('parent', source, target) for target in current for source in previous)
        previous = current or previous
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
            if data is None or current.applicability == 'declared_applicable': continue
            hops = depth[source]+1
            blocked = (uncertainty or reason or ('import_depth_limit' if hops > IMPORT_HOPS else None)
                       or ('runtime_size_limit_unverified' if len(data) > SKIPPED_BYTES else None))
            if blocked is None:
                try: data.decode('utf-8')
                except UnicodeDecodeError: blocked = 'non_utf8_import'
            if blocked is None: admit(key, hops)
            elif current.reason == 'import_pending': nodes[key] = replace(current, reason=blocked)
    cycles = cyclic(list(links))
    edges.extend(InstructionEdge('import_cycle' if pair in cycles else 'imports', *pair) for pair in links)
    for key in agent_files:
        if nodes[key].applicability != 'declared_applicable' and claude_files:
            nodes[key] = replace(nodes[key], applicability='shadowed', reason='assumed_default_instruction_files_setting')
            edges.append(InstructionEdge('shadows', claude_files[0], key))
    for path in sorted(set(selected)-set(path_of.values())):
        scope, parts = scope_of(path), Path(path).parts
        if any(parts[i:i+2] == ('.claude', 'rules') for i in range(len(parts)-1)): read(path, 'unknown', 'rules_not_modeled')
        elif scope is not None and scope not in scopes and (cwd == '.' or scope.startswith(cwd+'/')):
            read(path, 'unknown', 'on_demand_subdirectory')
        elif str(Path(path).parent) in scopes: read(path, 'unsupported', 'unrecognized_filename')
        else: read(path, 'out_of_scope', 'outside_task_scope')
    return tuple(nodes.values()), tuple(edges)
