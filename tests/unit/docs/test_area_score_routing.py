"""The skill routes a per-area question to `ahl score` with the inputs that command takes, and never to an estimate (#800).

A lexical contract over the skill text, like the scope mapping in tests/unit/docs/test_unified_skill.py: it shows that each cue
and flag the routing names exists, that the real parser accepts the command and that the documented command prints the states
the routing tells the agent to quote. It does not show how a native agent routes a prompt; that needs a model run in a native
host and is reported as not run. DEVELOPMENT shaped the routing text; HELD_OUT was written once, ran once and must not be edited
after seeing results: change the skill text or report the failure instead. Every prompt is synthetic.
"""
import contextlib
import io
from pathlib import Path
import re
import shlex

import pytest

from agent_harness_lab import cli
from tests.support import score_fixture as fixture

ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / 'skills/harness-self-improvement'
ENTRY, REFERENCE = (SKILL / 'SKILL.md').read_text(), (SKILL / 'references/invocation.md').read_text()
ROUTING = REFERENCE.split('## Loop routing', 1)[1].split('\n## ', 1)[0]
SECTION = ROUTING.split('### Per-area questions', 1)[1] if '### Per-area questions' in ROUTING else ''
STATES = ('scored', 'provisional', 'no_evidence', 'blocked')

OBJECTS = ('harness', 'skill', 'instruction')
MEASURE = ('how good', 'score', 'per-area')
CHANGE = ('improv', 'fix', 'until')

# (prompt, routes to `ahl score`). A request to change the harness belongs to the loop routing of test_skill_loop_routing.
DEVELOPMENT = (
    ('How good is this harness for UX/UI work?', True),
    ('What is the coding score of my harness?', True),
    ('Score my harness for the docs area.', True),
    ('Show the per-area scores of my agent instructions.', True),
    ('Improve my harness until the score is 0.8.', False),
    ('Fix the failing score display in the dashboard.', False),
    ('How good is this database for coding workloads?', False),
)
HELD_OUT = (
    ('How good are my skills for review work?', True),
    ('Give me the per-area score of the harness.', True),
    ('What score does my harness get for coding?', True),
    ('Improve the harness for UX/UI.', False),
    ('Fix my instructions so the agent stops skipping tests.', False),
    ('Rename the score variable in the parser.', False),
)


def asks_for_an_area_score(prompt):
    text = prompt.lower().replace('per area', 'per-area')
    return any(o in text for o in OBJECTS) and any(m in text for m in MEASURE) and not any(c in text for c in CHANGE)


def command():
    """The tokens of the one shell block of the per-area section."""
    (block,) = re.findall(r'```sh\n(.*?)\n```', SECTION, re.S)
    return shlex.split(block)


def test_the_invocation_reference_has_a_per_area_section_under_the_loop_routing():
    assert SECTION, 'no "### Per-area questions" section under "## Loop routing"'


@pytest.mark.parametrize(('prompt', 'expected'), DEVELOPMENT + HELD_OUT)
def test_the_phrase_table_routes_an_area_question_to_ahl_score_and_a_change_request_elsewhere(prompt, expected):
    assert asks_for_an_area_score(prompt) is expected


def test_every_cue_the_phrase_table_relies_on_is_stated_in_the_skill_text():
    text = (ENTRY + REFERENCE).lower()
    for cue in (*MEASURE, 'ux/ui', 'coding'):
        assert cue in text, cue


def test_the_entrypoint_routes_the_question_to_the_command_quotes_the_state_and_forbids_an_estimate():
    (bullet,) = re.findall(r'^- A per-area question.*?(?=\n- |\n\n)', ENTRY, re.S | re.M)
    text = ' '.join(bullet.split())
    assert '`ahl score --area X`' in text and '`state`' in text and 'never estimate an area by hand' in text


def test_the_routing_no_longer_hedges_on_a_help_listing_for_a_command_that_exists():
    """Regression, fixed in #800: `ahl score` shipped in 0.220.0, but the routing still told the agent to look for it in `ahl --help`
    and otherwise to call it unavailable, which sent an up-to-date install to a dead end instead of to the real failure handling."""
    assert 'when `ahl --help` lists `score`' not in ENTRY + REFERENCE
    assert 'ahl score --help' in SECTION and 'ahl update' in SECTION


def test_the_command_names_the_project_and_the_two_recorded_files_and_every_flag_is_accepted_by_the_real_parser():
    tokens = command()
    assert tokens[:3] == ['ahl', 'score', '.'] and {'--spec', '--records', '--area'} <= set(tokens)
    with contextlib.redirect_stdout(io.StringIO()) as out, pytest.raises(SystemExit):
        cli.main(['score', '--help'])
    for flag in (token for token in tokens if token.startswith('--')):
        assert flag in out.getvalue(), flag


def test_the_documented_command_prints_the_states_the_routing_tells_the_agent_to_quote(tmp_path, capsys):
    root, spec, records = fixture.four_states(tmp_path / 'project')
    substitutions = {'EXPERIMENT.json': str(spec), 'TRIALS.json': str(records), '.': str(root)}
    arguments = [substitutions.get(token, token) for token in command()[1:]]
    assert cli.main(arguments) == 0
    assert '| UX/UI (`ux-ui`) | provisional |' in capsys.readouterr().out


def test_the_section_names_every_state_the_exit_code_and_the_limits():
    text = ' '.join(SECTION.split()).lower()
    for state in STATES:
        assert f'`{state}`' in text, state
    for phrase in ('exit 2', 'typed reason', 'never turn', 'never average', 'overall', 'aesthetics', 'wide intervals', 'never run a trial'):
        assert phrase in text, phrase


def test_the_section_says_what_to_do_without_recorded_files():
    text = ' '.join(SECTION.split()).lower()
    assert 'has nothing to read' in text and 'never invent' in text


def test_an_improvement_aimed_at_an_area_adds_area_to_the_invoke_line_the_parser_accepts():
    assert '`--area X`' in SECTION and 'ahl skills invoke' in SECTION
    with contextlib.redirect_stdout(io.StringIO()) as out, pytest.raises(SystemExit):
        cli.main(['skills', 'invoke', '--help'])
    assert '--area' in out.getvalue()
