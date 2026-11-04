"""Declared Claude Code memory files and bounded @path imports; never loading proof."""
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
FENCE = re.compile(r' {0,3}(`{3,}|~{3,})')
SPAN = re.compile(r'(`+)(?!`).*?(?<!`)\1(?!`)')
TOKEN = re.compile(r'(?<!\S)@((?:\\ |\S)+)')


def imports(text):
    """Return (token, commented) pairs outside code spans and fenced code blocks."""
    found, fence, commented = [], None, False
    for line in text.splitlines():
        opening = FENCE.match(line)
        if fence:
            if opening and opening[1][0] == fence[0] and len(opening[1]) >= len(fence) and not line.strip().strip(fence[0]):
                fence = None
            continue
        if opening:
            fence = opening[1]; continue
        line, regions, start, position = SPAN.sub(lambda match: ' '*len(match[0]), line), [], 0 if commented else None, 0
        while (position := line.find('-->' if commented else '<!--', position)) >= 0:
            if commented: regions.append((start, position)); position += 3
            else: start = position; position += 4
            commented = not commented
        if commented: regions.append((start, len(line)))
        for match in TOKEN.finditer(line):
            token = match[1].replace('\\ ', ' ')
            if token[0] not in '"\'':
                found.append((token, any(a <= match.start() < b for a, b in regions)))
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


def capture(cwd, scopes, selected, folder, checkout):
    nodes, raw, path_of, depth, queue, edges, children = {}, {}, {}, {}, [], {}, {}
    def add(edge):
        edges.setdefault((edge.kind, edge.source_sha256, edge.target_sha256), edge)
        if edge.kind in ('imports', 'import_cycle'): children.setdefault(edge.source_sha256, set()).add(edge.target_sha256)
    def keep(value, data, path):
        nodes[value.path_sha256], raw[value.path_sha256] = value, data
        if path is not None: path_of[value.path_sha256] = path
    def read(path, applicability, reason):
        value, data = read_node(folder, checkout, path, applicability, reason); keep(value, data, path)
        return value, data
    def admit(key, hops):
        nodes[key] = replace(nodes[key], applicability='declared_applicable', reason='import' if hops else 'directory_concatenation')
        depth[key] = hops; queue.append(key)
    def reaches(start, goal):
        stack, visited = [start], set()
        while stack:
            item = stack.pop()
            if item == goal: return True
            if item not in visited: visited.add(item); stack.extend(children.get(item, ()))
        return False
    previous, claude_files, agent_files = [], [], []
    for scope in scopes:
        current = []
        for name in LAUNCH+AGENTS:
            value, data = read(str(Path(scope)/name), 'unknown', 'missing')
            if data is None: continue
            data.decode('utf-8')
            key = value.path_sha256
            if name in AGENTS:
                agent_files.append(key); nodes[key] = replace(value, reason='instruction_files_setting_unknown')
            elif len(data) > SKIPPED_BYTES:
                claude_files.append(key); nodes[key] = replace(value, reason='runtime_size_limit_unverified')
            else:
                claude_files.append(key); admit(key, 0); current.append(key)
        for target in current:
            for source in previous: add(InstructionEdge('parent', source, target))
        previous = current or previous
    index = 0
    while index < len(queue):
        source = queue[index]; index += 1
        source_path = path_of[source]; parent = digest(str(Path(source_path).parent))
        for token, commented in imports(raw[source].decode('utf-8')):
            target, reason = resolve(source_path, token, cwd)
            key = digest(target) if target is not None else digest([reason, token])
            if key not in nodes:
                if target is None:
                    external = reason == 'external_import_not_captured'
                    keep(InstructionNode(key, parent, None, None, 'unknown', 'unknown' if external else 'unsupported', reason,
                                         origin='external_reference' if external else 'project'), None, None)
                else: unread(target, key, folder, checkout, read, keep)
            add(InstructionEdge('import_cycle' if reaches(key, source) else 'imports', source, key))
            data, current = raw[key], nodes[key]
            if data is None or current.applicability == 'declared_applicable': continue
            hops = depth[source]+1
            blocked = (('import_inside_html_comment' if commented else reason)
                       or ('import_depth_limit' if hops > IMPORT_HOPS else None)
                       or ('runtime_size_limit_unverified' if len(data) > SKIPPED_BYTES else None))
            if blocked is None:
                try: data.decode('utf-8')
                except UnicodeDecodeError: blocked = 'non_utf8_import'
            if blocked is None: admit(key, hops)
            elif current.reason == 'import_pending': nodes[key] = replace(current, reason=blocked)
    for key in agent_files:
        if nodes[key].applicability != 'declared_applicable' and claude_files:
            nodes[key] = replace(nodes[key], applicability='shadowed', reason='default_instruction_files_setting')
            add(InstructionEdge('shadows', claude_files[0], key))
    for path in sorted(set(selected)-set(path_of.values())):
        scope, parts = scope_of(path), Path(path).parts
        if any(parts[i:i+2] == ('.claude', 'rules') for i in range(len(parts)-1)): read(path, 'unknown', 'rules_not_modeled')
        elif scope is not None and scope not in scopes and (cwd == '.' or scope.startswith(cwd+'/')):
            read(path, 'unknown', 'on_demand_subdirectory')
        elif str(Path(path).parent) in scopes: read(path, 'unsupported', 'unrecognized_filename')
        else: read(path, 'out_of_scope', 'outside_task_scope')
    return tuple(nodes.values()), tuple(edges.values())


def unread(target, key, folder, checkout, read, keep):
    """Capture a new in-project import target without following links or nested repositories."""
    try:
        for ancestor in Path(target).parents:
            if str(ancestor) != '.' and folder.inspect(str(Path(checkout)/ancestor/'.git')) is not None:
                raise ValueError('instruction imports cannot cross a nested repository')
        info = folder.inspect(str(Path(checkout)/target))
    except NotADirectoryError: info = None
    if info is not None and not stat.S_ISDIR(info.st_mode):
        read(target, 'unknown', 'import_pending'); return
    scope = digest(str(Path(target).parent))
    keep(InstructionNode(key, scope, None, None, 'missing' if info is None else 'unknown', 'unknown' if info is None else 'unsupported',
                         'import_missing' if info is None else 'import_target_not_regular_file'), None, target)
