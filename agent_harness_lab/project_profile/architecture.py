"""Recorded decisions and cheap architectural patterns of a checkout (#1136).

Decision records are Markdown files named `NNNN-slug.md` in the conventional ADR folders (adr-tools `doc/adr`, MADR
`docs/decisions`, the folder `.adr-dir` names), with a status read from YAML front matter (`status:`), a `## Status` section
or a `Status:` line. Only `accepted` and `superseded` count; any other or unreadable status is skipped, never guessed. Patterns
are only what the tree settles without parsing code: internal edges between nested `package.json` packages, and folders named
`generated`. Read-only and bounded, like the rest of the profile; names and titles are never stored, only counts and paths.
"""
import json
import re

from agent_harness_lab.project_profile import detect

DECISION_DIRS = ('doc/adr', 'docs/adr', 'adr', 'docs/decisions', 'doc/decisions', 'decisions', 'docs/architecture/decisions',
                 'docs/architecture/adr', 'architecture/decisions')
MAX_RECORDS = 200
HEAD_LINES = 80
GENERATED_DIRS = frozenset({'generated', '__generated__'})
# Decisions are only compared on tools that name one choice of a closed group: a manager of an ecosystem or a JS test runner.
GROUPS = dict(node=('npm', 'pnpm', 'yarn', 'bun'), python=('uv', 'poetry', 'pipenv', 'pdm'), jsrunner=('jest', 'vitest', 'mocha', 'ava'))
_RECORD = re.compile(r'(\d{2,5})-[\w.-]+\.md')
_PICK = re.compile(r'\b(?:use|using|adopt|adopting|switch(?:ing)? to|migrat(?:e|ing) to|mov(?:e|ing) to|standardi[sz]e on|replace (?:\w+) with)'
                   r'\s+(?:the\s+)?([a-z0-9]+)', re.I)
_NEGATED = re.compile(r"\b(?:never|don'?t|do not|avoid|not|no longer|without|instead of|rather than)\b", re.I)
_BY = re.compile(r'superseded by[\s:\[\(]*(?:adr[\s-]*)?0*(\d+)')


def group_of(tool):
    return next((name for name, tools in GROUPS.items() if tool in tools), None)


def _status(lines):
    if lines and lines[0].strip() == '---':
        for line in lines[1:40]:
            if line.strip() == '---': break
            if found := re.match(r'status:\s*(.*)$', line, re.I): return found.group(1)
    for index, line in enumerate(lines[:HEAD_LINES]):
        if re.match(r'#{1,6}\s*status\s*$', line, re.I):
            block = []
            for row in lines[index + 1:index + 8]:
                if row.startswith('#'): break
                if row.strip(): block.append(row.strip())
            return ' '.join(block)
        if found := re.match(r'\**status\**\s*:\**\s*(.+)$', line, re.I): return found.group(1)
    return None


def _classify(raw):
    """(status, number of the superseding record) of a status text; status is None unless it is plainly accepted or superseded."""
    text = re.sub(r'[*_`"\']', '', raw or '').lower().strip()
    if re.match(r'superseded\b', text) or 'superseded by' in text:
        by = _BY.search(text)
        return 'superseded', int(by.group(1)) if by else None
    return ('accepted', None) if re.match(r'accepted\b', text) else (None, None)


def _tool(lines):
    """The one tool of a closed group the record's title decides on ("Use pnpm", "Replace npm with pnpm"), else None."""
    title = next((line.lstrip('# ').strip() for line in lines[:HEAD_LINES] if line.startswith('# ')), '')
    if _NEGATED.search(title): return None
    vocabulary = {tool for group in GROUPS.values() for tool in group}
    picked = {match.group(1).lower() for match in _PICK.finditer(title)} & vocabulary
    named = set(re.findall(r'[a-z0-9]+', title.lower())) & vocabulary
    replaced = {match.group(1).lower() for match in re.finditer(r'replace (\w+) with', title, re.I)}
    return picked.pop() if len(picked) == 1 and named <= picked | replaced else None  # a second tool in the title is ambiguous


def _dirs(repo):
    names = list(DECISION_DIRS)
    text = (repo.read('.adr-dir') or '').strip()
    if text and re.fullmatch(r'[\w.-]+(?:/[\w.-]+)*', text) and '..' not in text: names.insert(0, text.strip('/'))
    return list(dict.fromkeys(names))


def decisions(repo):
    """One dict per accepted or superseded decision record: path, number, status, superseded_by, tool."""
    found = []
    for folder in _dirs(repo):
        for entry in repo.listing(folder):
            match = _RECORD.fullmatch(entry.name)
            if not match or len(found) >= MAX_RECORDS or not entry.is_file(follow_symlinks=False): continue
            lines = (repo.read(f'{folder}/{entry.name}') or '').splitlines()[:400]
            status, by = _classify(_status(lines))
            if status: found.append(dict(path=f'{folder}/{entry.name}', folder=folder, number=int(match.group(1)), status=status,
                                         superseded_by=by, tool=_tool(lines)))
    return found


def _generated(repo):
    found, queue, seen = [], [('', 0)], 0
    while queue:
        rel, depth = queue.pop(0)
        seen += 1
        if seen > detect.MAX_DIRS:
            repo.unknown.add('walk_bound')
            break
        for entry in repo.listing(rel):
            if not entry.is_dir(follow_symlinks=False) or entry.name in detect.SKIP_DIRS or entry.name.startswith('.'): continue
            path = f'{rel}/{entry.name}' if rel else entry.name
            if entry.name in GENERATED_DIRS: found.append(path)
            elif depth < detect.MAX_DEPTH: queue.append((path, depth + 1))
    return found


def _workspace(repo):
    """Counts of nested packages that name each other as a dependency: the internal dependency edges of a workspace."""
    parsed = [repo.parse(rel, json.loads) for rel in repo.find({'package.json'})[:detect.MAX_PACKAGES] if rel != 'package.json']
    packages = [data for data in parsed if data and isinstance(data.get('name'), str)]
    names = {data['name'] for data in packages}
    edges = {(data['name'], dep) for data in packages for key in ('dependencies', 'devDependencies', 'peerDependencies')
             if isinstance(data.get(key), dict) for dep in data[key] if dep in names and dep != data['name']}
    return dict(packages=len(names), internal_edges=len(edges))


def observe(repo):
    records = decisions(repo)
    return dict(decisions=records, decision_dirs=sorted({row['folder'] for row in records}), workspace=_workspace(repo),
                generated_dirs=_generated(repo))
