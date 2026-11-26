"""Findings that compare what the sessions did with what the harness says (#1118).

Counts are separate sessions, never occurrences and never independent roots: native captures carry no verified lineage, so a
finding states `lineage: unknown`. A threshold of several sessions keeps one long session from standing for a practice.
Unknown or partial capture produces no finding at all (the section says `not_run`), which is not a clean result. A finding is a
hypothesis that the harness is stale, silent or ignored; only the paired evaluation decides what to change.
"""
from agent_harness_lab.diagnostics import instruction_rules as rules
from agent_harness_lab.project_profile import commands, findings, harness_text

MIN_SESSIONS = 3
DOMINANCE = 2
MAX_RULES = 8
ECOSYSTEMS = dict(node=commands.NODE_MANAGERS, python=(*commands.PYTHON_MANAGERS, 'pip'))
LINEAGE = 'unknown'
# A harness that already tells the agent to run the category has recorded the practice; what sessions add there is only the exact tool.
CATEGORY_WORDS = dict(test=('test', 'tests', 'testing'), lint=('lint', 'linter', 'linting'),
                      typecheck=('typecheck', 'typechecking', 'type-check', 'type-checking'), build=('build', 'builds', 'building'))
RUN_KINDS = ('forbidden', 'gated', 'required')
VOCABULARY = frozenset((*commands.RUNNERS, *commands.KIND, *commands.TOOLS, 'git', 'gh', 'rm', 'curl', 'docker', 'sudo', 'pip', 'python', 'node'))


def _where(rows):
    return findings._where(rows)


def _stated(row, mentions, words):
    """Whether the harness names a command key: a declared script or target by its runner and name, a tool by its name."""
    if row['program'] in commands.RUNNERS:
        pairs = {commands.runner_target(mention['command'], any_directory=True) for mention in mentions}
        node = row['program'] in commands.NODE_MANAGERS
        return any(pair and pair[1] == row['target'] and (pair[0] == row['program'] or node and pair[0] in commands.NODE_MANAGERS) for pair in pairs)
    return row['program'] in words


def command_findings(found, entries, mentions, summary):
    """A verification category run in several sessions that no instruction file names: a new finding, or the evidence on the
    static `verification_command_unnamed` finding of the same category (one problem is one finding, counted once)."""
    named, words, prose = findings.named_categories(mentions), findings._words(entries), harness_text.words(entries)[0]
    static = {row['subject']: row for row in found if row['kind'] == 'verification_command_unnamed'}
    added = []
    for name in commands.VERIFICATION:
        seen = summary['categories'].get(name)
        keys = [row for row in summary['commands'] if row['category'] == name]
        if not seen or seen['sessions'] < MIN_SESSIONS or name in named or any(_stated(row, mentions, words) for row in keys): continue
        if name not in static and set(CATEGORY_WORDS[name]) & prose: continue
        observed = dict(sessions=seen['sessions'], uses=seen['uses'], lineage=LINEAGE)
        if name in static: static[name]['evidence']['observed_in_sessions'] = observed
        else:
            added.append(findings.finding('command_in_use_unrecorded', name, name, dict(category=name, **observed), [],
                                          dict(category=name, candidates=[row['key'] for row in keys[:findings.MAX_WHERE]])))
    return added


def practice_findings(profile, mentions, summary):
    """The harness names a package manager and several sessions use another one far more than any it names."""
    added = []
    for ecosystem, programs in ECOSYSTEMS.items():
        says = {}
        for mention in mentions:
            name = 'pip' if mention['command']['program'] == 'pip3' else mention['command']['program']
            if mention['scope'] == 'root' and name in programs: says.setdefault(name, []).append(mention)
        used = {row['name']: row['sessions'] for row in summary['managers'] if row['ecosystem'] == ecosystem}
        if not says or not used: continue
        top = max(sorted(used), key=lambda name: used[name])
        rival = max((used.get(name, 0) for name in says), default=0)
        if top in says or used[top] < MIN_SESSIONS or used[top] <= DOMINANCE * rival: continue
        added.append(findings.finding('practice_diverges_from_harness', ecosystem, ecosystem, dict(
            ecosystem=ecosystem, named=sorted(says), used=top, sessions=used[top], named_sessions=rival, lineage=LINEAGE,
            lockfile=findings.detected_managers(profile, ecosystem)), _where([row for rows in says.values() for row in rows])))
    return added


def _operand(rule):
    """The program a run-shaped rule names when it is in the closed vocabulary, else None: a path or an unknown command is user text."""
    match = rule.get('match') or rule.get('first') or {}
    command = match.get('command') or []
    parsed = commands.simple_commands(' '.join(command)) if command else []
    return parsed[0]['program'] if parsed and parsed[0]['program'] in VOCABULARY else None


def _run_rules(text, user):
    """{(kind, program)} of the run-shaped rules in a text, for a program of the closed vocabulary."""
    return {(row['rule']['kind'], program) for row in rules.extract(text, user=user)
            if row['rule'] and row['rule']['kind'] in RUN_KINDS and (program := _operand(row['rule']))}


def rule_findings(entries, sessions):
    """A run-shaped rule (`never run X`, `always run X`, `ask before running X`) the user stated in several sessions for a program
    of the closed vocabulary, which no instruction file states. The subject is the rule kind and that program, never user text."""
    try:
        recorded = {key for entry in entries if entry['scope'] in ('root', 'nested') for key in _run_rules(entry['text'], False)}
    except ValueError:
        return []  # a harness file past the rule bound cannot be compared; stay silent
    seen = {}
    for number, session in enumerate(sessions):
        for turn in session['turns']:
            if turn['role'] == 'user':
                for key in _run_rules(turn['text'], True): seen.setdefault(key, set()).add(number)
    ranked = sorted((key for key, owners in seen.items() if len(owners) >= MIN_SESSIONS and key not in recorded), key=lambda key: (-len(seen[key]), key))
    return [findings.finding('user_rule_unrecorded', ' '.join(key), '', dict(sessions=len(seen[key]), lineage=LINEAGE), [], dict(rule_kind=key[0]))
            for key in ranked[:MAX_RULES]]


def apply(found, profile, entries, mentions, summary, sessions):
    """The findings plus those the session history adds; none unless the read sessions ran shell commands."""
    added = rule_findings(entries, sessions)
    if summary.get('status') == 'completed':
        added += command_findings(found, entries, mentions, summary) + practice_findings(profile, mentions, summary)
    return [*found, *added]
