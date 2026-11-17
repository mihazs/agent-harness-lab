"""The skill routes loop requests to `ahl skills invoke` with the right flags, and never to a manual loop (#817).

The routing table is a lexical contract over the skill text, like the scope mapping in tests/unit/docs/test_unified_skill.py: it shows
that each documented cue and command exists and that the commands the skill names are accepted by the real parser. It does not show how
a native agent routes a prompt; that needs a model run in a native host and is reported as not run. DEVELOPMENT was used while writing
the routing text; HELD_OUT was written once, ran once and must not be edited after seeing results: change the skill text or report the
failure instead. Every prompt is synthetic.
"""
import contextlib
import io
from pathlib import Path
import re

import pytest

from agent_harness_lab.cli import main

ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / 'skills/harness-self-improvement'
ENTRY, REFERENCE = (SKILL/'SKILL.md').read_text(), (SKILL/'references/invocation.md').read_text()
ROUTING = REFERENCE.split('## Loop routing', 1)[1].split('\n## ', 1)[0]
BEFORE_ROUTING_BYTES = 13380  # SKILL.md before the loop routing replaced its manual loop; the always-loaded text must not grow (#329)

OBJECTS = ('harness', 'skill', 'instruction')
ACTIONS = ('improv', 'fix', 'maintain', 'evaluat')
SCORE = re.compile(r'\b(until|reach|reaches|target)\b.*(\bscore\b|\d+\s*%|\b0?\.\d+\b)')
EVALUATE_ONLY = ('evaluate only', 'only evaluate', 'evaluate my harness only', "don't change", 'do not change', 'without changing')
PER_AREA = ('ux/ui', 'per area', 'per-area', 'coding area', 'for coding')

# (prompt, route). `none` is a request that must not trigger the skill at all.
DEVELOPMENT = (
    ('Improve my harness until the score is 0.8.', 'accept-score'),
    ('Keep improving the agent instructions until they reach 80%.', 'accept-score'),
    ('Improve my harness.', 'default'),
    ('Fix my agent instructions, the agent keeps skipping the tests.', 'default'),
    ('Evaluate my harness only, do not change anything.', 'analyze'),
    ('How good is my harness for UX/UI work? Evaluate the skill per area.', 'score-area'),
    ('Fix the failing checkout test in the web application.', 'none'),
    ('Improve the loading time of the dashboard page.', 'none'),
    ('Rename this variable in the parser.', 'none'),
)
HELD_OUT = (
    ('Improve my skills until the score reaches 0.9.', 'accept-score'),
    ('Maintain the instructions of this repo and stop at a target of 70%.', 'accept-score'),
    ('Help me improve the harness for this repository.', 'default'),
    ('Fix the harness so the agent stops ignoring the lint rule.', 'default'),
    ('Only evaluate my instructions, without changing them.', 'analyze'),
    ('Evaluate the harness per-area, starting with the coding area.', 'score-area'),
    ('Evaluate which database fits this service.', 'none'),
    ('Fix the flaky unit test in the billing module.', 'none'),
)
COMMAND = {'accept-score': '--accept-score', 'default': 'ahl skills invoke . --scope project', 'analyze': 'ahl analyze .', 'score-area': 'ahl score --area'}


def route(prompt):
    text = prompt.lower()
    if not (any(cue in text for cue in OBJECTS) and any(cue in text for cue in ACTIONS)): return 'none'
    if any(cue in text for cue in PER_AREA): return 'score-area'
    if any(cue in text for cue in EVALUATE_ONLY): return 'analyze'
    return 'accept-score' if SCORE.search(text) else 'default'


@pytest.mark.parametrize(('prompt', 'expected'), DEVELOPMENT + HELD_OUT)
def test_the_phrase_table_routes_each_request_to_its_command_or_to_nothing(prompt, expected):
    assert route(prompt) == expected


@pytest.mark.parametrize('name', sorted(set(COMMAND) - {'none'}))
def test_the_routing_table_names_the_command_each_route_runs(name):
    assert COMMAND[name] in ROUTING


def test_negative_requests_are_named_in_the_table_as_not_to_invoke():
    assert 'Not a harness request: do not invoke' in ROUTING
    assert [prompt for prompt, expected in (*DEVELOPMENT, *HELD_OUT) if expected == 'none' and route(prompt) != 'none'] == []


def test_every_cue_the_routing_relies_on_is_stated_in_the_skill_text():
    text = (ENTRY + REFERENCE).lower()
    for phrase in ('--accept-score', '--max-rounds', 'acceptance_source: default', 'loop.stop_criterion', 'loop.remediation', '--single-pass',
                   'certification: unverified', 'evaluate only', 'per-area', 'never estimate an area by hand', 'ordinary coding or tests'):
        assert phrase in text, phrase


def test_the_entrypoint_routes_to_the_command_and_forbids_a_manual_loop_or_a_retry_that_bypasses_a_result():
    text = ' '.join(ENTRY.split())
    assert '## Route the request to the loop' in text and '## Execute the loop' not in text
    assert 'Never start a manual loop, add `--single-pass` or repeat an invocation to bypass a blocked, failed, exhausted or ambiguous result' in text
    assert 'Report `loop.stop_criterion` and `loop.remediation` verbatim' in text and 'never certification' in text
    assert 'unavailable before any attempt' in text  # the manual loop stays only as the closed assisted fallback


def test_the_manual_loop_moved_to_the_reference_fallback_and_keeps_its_budget_rules():
    fallback = REFERENCE.split('## Assisted fallback before dispatch', 1)[1]
    assert '### The manual loop' in fallback and 'Default shared ceiling: 24 agent trials and 45 minutes' in fallback
    assert 'Snapshot affected files with modes and hashes' in fallback and 'Do not recursively launch unbounded improvement sessions' in fallback


def test_the_always_loaded_entrypoint_does_not_grow():
    assert len(ENTRY.splitlines()) <= 200 and len(ENTRY.encode()) <= BEFORE_ROUTING_BYTES


def test_the_skill_names_no_project_specific_path_or_address():
    for text in (ENTRY, ROUTING):
        assert re.findall(r'(?:/home/|/Users/|[A-Za-z]:\\|@[A-Za-z0-9.-]+\.[a-z]{2,}|https?://(?!claude\.com))', text) == []


def test_the_activation_metadata_is_unchanged_by_the_routing():
    head = ENTRY.split('---', 2)[1]
    assert 'name: harness-self-improvement' in head and 'never runs unsolicited' in head and 'not ordinary coding or application testing' in head


@pytest.mark.parametrize('entry', [('skills', 'invoke'), ('improve',)], ids=['skills-invoke', 'improve'])
def test_every_flag_the_routing_runs_is_accepted_by_the_real_parser(entry):
    with contextlib.redirect_stdout(io.StringIO()) as out, pytest.raises(SystemExit):
        main([*entry, '--help'])
    for flag in ('--accept-score', '--max-rounds', '--single-pass', '--plateau-rounds', '--on-regression'):
        assert flag in out.getvalue(), flag
