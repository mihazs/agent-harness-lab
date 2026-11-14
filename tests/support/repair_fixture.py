"""Synthetic harness text and recorded arms for the instruction-repair tests; nothing here is a real transcript."""
from agent_harness_lab.diagnostics import instruction_adherence as adherence
from agent_harness_lab.diagnostics import instruction_repair as repair
from tests.support.adherence_fixture import SOURCE, agent, session, shell, user

RULE = '- Never run `git push --force`.\n'
CONTROL = '- Never run `rm -rf build`.\n'
VIOLATION = shell('git push --force origin main')
COMPLIANT = [user('Publish the change.'), agent('Pushing normally.'), shell('git push origin main')]
BROKEN = [user('Publish the change.'), VIOLATION]
CONTROL_BROKEN = [user('Tidy the build.'), shell('rm -rf build')]
CONTROL_CLEAN = [user('Tidy the build.'), shell('ls build')]


def harness(text, name='AGENTS.md', layer='project'):
    return [dict(layer=layer, name=name, text=text)] if text else []


def arm(roots, turns, *, prefix='s', loaded='loaded', capture='complete'):
    """One recorded session per root, all with the same turns; a root of None leaves the lineage undeclared."""
    return [session(f'{prefix}-{index}', turns, capture=capture, loaded={SOURCE: loaded} if loaded else None, root=root)
            for index, root in enumerate(roots)]


def document(raw):
    return dict(profile=adherence.SESSIONS_PROFILE, sessions=raw)


def sessions(raw):
    return adherence.load_sessions(document(raw))


def report_for(raw, rules, **options):
    harness_files = harness(rules) if isinstance(rules, str) else rules
    return adherence.analyze(sessions(raw), harness_files, **options), harness_files


def plan_for(raw, rules, **options):
    report, harness_files = report_for(raw, rules, **options)
    return repair.diagnose(report, harness_files)


def first(plan):
    return plan['candidates'][0]


def mechanisms(candidate):
    return [row['mechanism'] for row in candidate['hypotheses']]


def target_ref(rules=RULE):
    return adherence.inventory(harness(rules))[0][0]['ref']


def judged(*, baseline=RULE + CONTROL, candidate=RULE + CONTROL, target=None, reproduction=None, preserved='default'):
    """The verdict for synthetic arms: five violating baseline roots against five compliant candidate roots unless overridden."""
    roots = [f'r{n}' for n in range(5)]
    reproduction = reproduction or (arm(roots, BROKEN, prefix='b'), arm(roots, COMPLIANT, prefix='c'))
    if preserved == 'default':
        kept = [f'p{n}' for n in range(3)]
        preserved = (arm(kept, CONTROL_CLEAN, prefix='pb'), arm(kept, CONTROL_CLEAN, prefix='pc'))
    return repair.judge(target or target_ref(), harness(baseline), harness(candidate), reproduction=tuple(map(sessions, reproduction)),
                        preserved=tuple(map(sessions, preserved)) if preserved else None)
