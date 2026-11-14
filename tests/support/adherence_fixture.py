"""Synthetic sessions and harness text for the instruction-adherence tests; nothing here is a real transcript."""
from agent_harness_lab.diagnostics import instruction_adherence as adherence

SOURCE = 'project:AGENTS.md'


def user(text): return dict(role='user', text=text)
def agent(text): return dict(role='agent', text=text)
def call(tool, **fields): return dict(role='tool_call', tool=tool, **fields)
def shell(command): return call('Bash', command=command)
def edit(path): return call('Edit', path=path)


def session(name, turns, *, capture='complete', loaded=None, root=None, **extra):
    raw = dict(session=name, capture=capture, turns=turns, **extra)
    if loaded is not None:
        raw['loaded'] = loaded
    if root is not None:
        raw['root'] = root
    return raw


def analyze(sessions, rules='', *, loading=None, **options):
    """The report for raw sessions and one project AGENTS.md text; loading names the state of that source in every session."""
    harness = [dict(layer='project', name='AGENTS.md', text=rules)] if rules else []
    raw = [dict(one, loaded={SOURCE: loading}) if loading and 'loaded' not in one else one for one in sessions]
    return adherence.analyze(adherence.load_sessions(dict(profile=adherence.SESSIONS_PROFILE, sessions=raw)), harness, **options)


def statuses(report):
    return sorted(finding['status'] for finding in report['findings'])
