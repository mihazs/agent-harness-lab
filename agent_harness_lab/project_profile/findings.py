"""Findings from comparing the project profile with the harness text: contradictions first, then gaps.

A contradiction is a harness line the checkout does not support (a script, target or path that is not there, a toolchain
the lockfile does not use, instruction files that name different tools). A gap is a fact the checkout declares and the
harness never states (a non-default toolchain, a verification command). Both are hypotheses with a proposed harness update
and a stable id, never a diagnosis. Whatever the reader could not settle stays silent: an unparsed manifest, an included
Makefile, an unread instruction file, an ignored or example path never becomes a finding.
"""
import fnmatch
import os
import re

from agent_harness_lab.project_profile import commands, harness_text
from agent_harness_lab.sources.schema import digest

PROFILE = 'ahl-harness-findings-v1'
MAX_PER_KIND = 8
MAX_WHERE = 3
# kind -> (class, proposed update action). A wrong line is followed literally, so contradictions list before gaps.
KINDS = dict(
    command_target_missing=('contradiction', 'correct_or_remove_line'),
    path_reference_missing=('contradiction', 'correct_or_remove_line'),
    toolchain_contradicted=('contradiction', 'correct_or_remove_line'),
    test_runner_contradicted=('contradiction', 'correct_or_remove_line'),
    harness_files_disagree=('contradiction', 'resolve_disagreement'),
    toolchain_not_stated=('gap', 'state_toolchain'),
    verification_command_unnamed=('gap', 'name_verification_command'))
MANAGERS = dict(node=('npm', 'pnpm', 'yarn', 'bun'), python=('uv', 'poetry', 'pipenv', 'pdm'))
DEFAULT_MANAGER = dict(node='npm', python='pip')
RUNNER_FILE = dict(npm='package.json', pnpm='package.json', yarn='package.json', bun='package.json', make='make', task='task', just='just')
JS_TEST_RUNNERS = frozenset('jest vitest mocha ava'.split())
META = frozenset('verify check ci all validate presubmit precommit pre-commit'.split())
GENERIC_TOOLS = frozenset('npm pnpm yarn bun make task just uv poetry pipenv pdm cargo go pip'.split())
# A word that is also plain English counts as a mention only inside a code span.
AMBIGUOUS = frozenset('bun poetry uv make task just go'.split())
GENERATED = frozenset('node_modules dist build target out coverage htmlcov tmp temp log logs venv env artifacts vendor __pycache__ '
                      'site-packages'.split())
PLACEHOLDER = frozenset('path foo bar baz your example examples dir folder xxx name file project module'.split())
PLACEHOLDER_TARGET = frozenset('x y z foo bar baz name target script cmd command task recipe something'.split())
_EXAMPLE = re.compile(r"\b(?:e\.g\.|i\.e\.|for (?:example|instance)|such as|example|todo|planned|creat\w+|generat\w+|writes?|written|"
                      r"output\w*|produc\w+|will|might|may|your|replace|placeholder)\b", re.I)
_PATH = re.compile(r'(?:\./)?[\w@.+-]+(?:/[\w@.+-]+)+/?')
_LINK = re.compile(r'\]\(([^)\s#]+)(?:#[^)]*)?\)')
_TLD = frozenset('com org io net dev app ai co xyz'.split())
_SAFE = re.compile(r'[A-Za-z0-9_][\w:.-]{0,63}')


def finding(kind, subject, scope, evidence, where, update=None):
    """One finding with its stable id; `scope` keeps two findings with one subject in different places apart."""
    cls, action = KINDS[kind]
    return dict(id=f'hp/{kind}/{digest([kind, subject, scope])[:10]}', kind=kind, finding_class=cls, subject=subject,
                evidence=evidence, where=sorted(where, key=lambda row: (row['file'], row['line']))[:MAX_WHERE],
                proposed_update=dict(action=action, **(update or {})), status='candidate_claim', source='static')


def _where(rows):
    return [dict(file=row['path'], line=row['line']) for row in rows]


def _ignored(repo):
    patterns = [line.strip().rstrip('/').lstrip('/') for line in (repo.read('.gitignore') or '').splitlines()
                if line.strip() and line[0] not in '#!']

    def match(path):
        parts = path.strip('/').split('/')
        return any(fnmatch.fnmatchcase(part, pattern) for pattern in patterns for part in parts) or any(
            fnmatch.fnmatchcase(path.strip('/'), pattern) or '/' in pattern and path.startswith(pattern + '/') for pattern in patterns)
    return match


def _checkable(text, link):
    """The checkout-relative path a code span or link names, or None when it is not clearly a repository path."""
    text = text.strip().rstrip('.,:;)')
    if '://' in text or text.startswith(('/', '~', '@', '#', 'mailto:')) or any(mark in text for mark in '*?<>{}$|\\=: ') or '..' in text:
        return None
    if not _PATH.fullmatch(text): return None
    parts = text.removeprefix('./').rstrip('/').split('/')
    first = parts[0]
    if first in GENERATED or first in PLACEHOLDER or first.startswith('.venv') or '.' in first and first.rsplit('.', 1)[1] in _TLD:
        return None
    if text.endswith('/') or link or re.search(r'\.\w{1,8}$', parts[-1]): return '/'.join(parts)
    return None


def stale_paths(repo, entries):
    ignored, seen = _ignored(repo), {}
    for entry in entries:
        if entry['scope'] == 'skill': continue
        base, lines = os.path.dirname(entry['path']), entry['text'].splitlines()
        found = [(number, span, False) for number, span, fenced in harness_text.spans(entry['text']) if not fenced]
        found += [(number, target, True) for number, line in enumerate(lines, 1) for target in _LINK.findall(line)]
        for number, text, link in found:
            path = _checkable(text, link)
            if path is None or _EXAMPLE.search(lines[number - 1]) or ignored(path): continue
            if repo.kind(path) is None and (not base or repo.kind(f'{base}/{path}') is None):
                seen.setdefault(path, []).append(dict(path=entry['path'], line=number))
    return [finding('path_reference_missing', path, '', dict(mentions=len(rows)), _where(rows)) for path, rows in sorted(seen.items())][:MAX_PER_KIND]


def stale_targets(profile, mentions):
    """`npm run x`, `make x`, `task x` and `just x` whose runner file is read, complete and declares no x anywhere."""
    seen = {}
    for mention in mentions:
        pair = commands.runner_target(mention['command'])
        if pair is None or mention['scope'] != 'root' or not _SAFE.fullmatch(pair[1]) or pair[0] == 'npm' and pair[1] != 'test': continue
        if pair[1].lower() in PLACEHOLDER_TARGET or pair[1].isupper(): continue  # `make NAME` and `just x` name a slot, not a target
        runner = profile['runners'][RUNNER_FILE[pair[0]]]
        if not runner['complete'] or pair[1] in runner['targets'] or pair[1] in runner.get('nested_targets', ()): continue
        if not runner['present'] and runner.get('nested'): continue  # a nested package may declare it
        seen.setdefault(pair, []).append(mention)
    return [finding('command_target_missing', ' '.join(pair), '', dict(runner=pair[0], target=pair[1], mentions=len(rows),
            state='target_not_declared' if profile['runners'][RUNNER_FILE[pair[0]]]['present'] else 'runner_file_absent'), _where(rows))
            for pair, rows in sorted(seen.items())][:MAX_PER_KIND]


def named_managers(mentions, ecosystem):
    """{manager: [mentions]} of the ecosystem's package managers the root harness commands run."""
    found = {}
    for mention in mentions:
        program = mention['command']['program']
        if mention['scope'] == 'root' and program in MANAGERS[ecosystem]: found.setdefault(program, []).append(mention)
    return found


def detected_managers(profile, ecosystem):
    return sorted({row['name'] for row in profile['package_managers'] if row['ecosystem'] == ecosystem})


def toolchain_contradictions(profile, mentions):
    found = []
    for ecosystem in MANAGERS:
        have, says = detected_managers(profile, ecosystem), named_managers(mentions, ecosystem)
        # Several lockfiles or a mix of named managers is a disagreement the repository itself has, not a stale line.
        if not have or set(says) & set(have): continue
        found += [finding('toolchain_contradicted', name, ecosystem, dict(ecosystem=ecosystem, named=name, detected=have,
                  mentions=len(rows)), _where(rows)) for name, rows in sorted(says.items())]
    return found


def runner_contradictions(profile, mentions):
    have = {row['name'] for row in profile['technologies'] if row['kind'] == 'test_runner'} & JS_TEST_RUNNERS
    says = {}
    for mention in mentions:
        program = commands.unwrapped(mention['command'])['program']
        if mention['scope'] == 'root' and program in JS_TEST_RUNNERS: says.setdefault(program, []).append(mention)
    return [finding('test_runner_contradicted', name, '', dict(named=name, detected=sorted(have), mentions=len(rows)), _where(rows))
            for name, rows in sorted(says.items()) if have and name not in have]


def disagreements(mentions):
    found = []
    for ecosystem in MANAGERS:
        by_file = {}
        for name, rows in named_managers(mentions, ecosystem).items():
            for row in rows: by_file.setdefault(row['path'], {}).setdefault(name, []).append(row)
        paths = sorted(by_file)
        if any(not set(by_file[a]) & set(by_file[b]) for index, a in enumerate(paths) for b in paths[index + 1:]):
            rows = [row for path in paths for group in by_file[path].values() for row in group]
            found.append(finding('harness_files_disagree', ecosystem, ecosystem, dict(ecosystem=ecosystem, files=paths,
                         names=sorted({name for path in paths for name in by_file[path]})), _where(rows)))
    return found


def _words(entries):
    prose, code = harness_text.words(entries)
    return {word for word in prose if word not in AMBIGUOUS} | code


def toolchain_gaps(profile, entries):
    words, found = _words(entries), []
    for ecosystem, managers in MANAGERS.items():
        have = detected_managers(profile, ecosystem)
        # The default manager needs no line; any mention of a manager of the ecosystem states the toolchain.
        if not set(have) - {DEFAULT_MANAGER[ecosystem]} or words & set(managers): continue
        via = sorted({item for row in profile['package_managers'] if row['ecosystem'] == ecosystem for item in row['evidence']})
        found.append(finding('toolchain_not_stated', ecosystem, ecosystem, dict(ecosystem=ecosystem, detected=have, via=via), []))
    return found


def declare(profile, row):
    """The command line that runs one declared script or target."""
    if row['runner'] == 'package.json': return f"{(detected_managers(profile, 'node') or ['npm'])[0]} run {row['target']}"
    return f"{row['runner']} {row['target']}"


def named_categories(mentions):
    """The verification categories the harness commands work in; a meta target (`make check`) or `pre-commit` names them all."""
    named = {commands.category_of(mention['command']) for mention in mentions} - {None}
    for mention in mentions:
        pair = commands.runner_target(mention['command'], any_directory=True)
        if pair and pair[1] in META or mention['command']['program'] == 'pre-commit': named |= set(commands.VERIFICATION)
    return named


def verification_gaps(profile, entries, mentions):
    words, named, found = _words(entries), named_categories(mentions), []
    for name in commands.VERIFICATION:
        declared = [row for row in profile['commands'] if row['category'] == name]
        ci = [row for row in profile['ci_commands'] if row['category'] == name]
        tools = {tool for row in (*declared, *ci) for tool in row['tools']} - GENERIC_TOOLS
        if name in named or not (declared or ci) or tools & words: continue
        best = sorted(declared, key=lambda row: (row['target'] != name, len(row['target']), row['target']))
        examples = [declare(profile, row) for row in best][:MAX_WHERE] or [row['command'] for row in ci[:MAX_WHERE]]
        found.append(finding('verification_command_unnamed', name, name, dict(category=name, declared=len(declared), ci=len(ci)),
                             [], dict(category=name, candidates=examples)))
    return found


def compare(profile, repo, entries):
    """The static findings of one profile and its instruction files, contradictions first; none without a readable file.

    A skill file informs the gap checks (it may state the command) but is not checked for stale lines. When any candidate
    instruction file could not be read, the gap checks stay silent: the missing file may hold the fact."""
    if not entries: return []
    root = [entry for entry in entries if entry['scope'] != 'skill']
    mentions = [row for entry in root for row in harness_text.mentions(entry)]
    found = [*stale_targets(profile, mentions), *stale_paths(repo, root), *toolchain_contradictions(profile, mentions),
             *runner_contradictions(profile, mentions), *disagreements(mentions)]
    if not any(item.startswith('unread:') for item in repo.unknown):
        mentions += [row for entry in entries if entry['scope'] == 'skill' for row in harness_text.mentions(entry)]
        found += [*toolchain_gaps(profile, entries), *verification_gaps(profile, entries, mentions)]
    return found
