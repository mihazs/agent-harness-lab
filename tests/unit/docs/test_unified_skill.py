"""One owned skill replaces the former evaluation and self-improvement pair; synthetic prompts only.

The routing table is a lexical contract over the activation metadata. It shows that no former trigger phrase was
dropped and that the exclusions remain. It does not show how any native agent routes a prompt; that needs a
model run in a native host and is reported as not run.
"""
import contextlib
import io
import os
from pathlib import Path
import re

import pytest

from agent_harness_lab import scope as scopes
from agent_harness_lab.cli import main
from agent_harness_lab.sources import frontmatter

ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / 'skills/harness-self-improvement'
RETIRED = 'harness-evaluation'

# Frozen from the two descriptions before they were merged; not to be edited after seeing results.
FORMER_PHRASES = (
    'Evaluate and improve agent instructions and skills',
    'explicitly asks to improve, fix, or maintain a harness',
    'natural-language requests and named invocation',
    'never runs unsolicited',
    'ordinary coding',
    'Build and run baseline/candidate evaluations',
    'diagnose evaluation failures',
    'measure supported improvements',
    'harness maintenance',
    'application testing',
)

# A prompt routes here when it names a harness object and one of the actions the description lists. The cue
# stems must all occur in the description, so dropping a trigger word from it fails the contract below.
ACTIONS = ('improv', 'fix', 'maintain', 'maintenance', 'evaluat', 'diagnos', 'measur', 'baseline', 'candidate')
OBJECTS = ('harness', 'skill', 'instruction')

# (prompt, routes). DEVELOPMENT was used while writing the description; HELD_OUT was written once, ran once and
# must not be edited after seeing results: change the description or report the failure instead.
DEVELOPMENT = (
    ('Please improve my harness.', True),
    ('Fix the harness instructions that the agent keeps ignoring.', True),
    ('Maintain the harness for this repository.', True),
    ('Evaluate and improve agent instructions and skills in this project.', True),
    ('Build and run baseline/candidate evaluations for my skill.', True),
    ('Diagnose evaluation failures in the last paired run of the instructions.', True),
    ('Measure supported improvements of this instruction change.', True),
    ('Rename this variable in the parser.', False),
    ('Add unit tests for the application login form.', False),
    ('Refactor the billing module during ordinary coding.', False),
)
HELD_OUT = (
    ('Can you fix my harness so it stops skipping the test step?', True),
    ('Run a baseline versus candidate comparison for this skill edit.', True),
    ('Diagnose why the evaluation of my instructions failed.', True),
    ('Help me maintain the agent skills in this repo.', True),
    ('Measure whether the new instruction improves the harness.', True),
    ('Fix the failing checkout test in the web application.', False),
    ('Evaluate which database fits this service.', False),
    ('Improve the loading time of the dashboard page.', False),
    ('Write a candidate list of names for the project.', False),
)


def routes(prompt):
    text = prompt.lower()
    return any(a in text for a in ACTIONS) and any(o in text for o in OBJECTS)


def description():
    text = (SKILL / 'SKILL.md').read_text()
    return re.search(r'^description: (.+)$', text, re.M).group(1)


def test_the_frontmatter_parses_with_the_readers_the_hosts_and_the_audit_use():
    """Regression, fixed in the unified skill: an unquoted ': ' inside the merged description made the header invalid YAML
    (the static audit reported parse_failed), so a host would have dropped the skill."""
    fields = frontmatter.texts((SKILL / 'SKILL.md').read_bytes())
    assert sorted(fields) == ['description', 'name'] and fields['name'] == 'harness-self-improvement'
    assert fields['description'] == description()


def test_exactly_one_skill_directory_remains():
    assert sorted(p.name for p in (ROOT / 'skills').iterdir() if p.is_dir()) == ['harness-self-improvement']
    assert re.search(r'^name: harness-self-improvement$', (SKILL / 'SKILL.md').read_text(), re.M)


@pytest.mark.parametrize('phrase', FORMER_PHRASES)
def test_every_former_trigger_phrase_survives_in_the_description(phrase):
    assert phrase.lower() in description().lower()


@pytest.mark.parametrize(('prompt', 'expected'), DEVELOPMENT + HELD_OUT)
def test_prompt_table_routes_to_the_unified_skill_or_not(prompt, expected):
    assert routes(prompt) is expected


def test_the_description_carries_every_routing_cue():
    text = description().lower()
    assert [cue for cue in (*ACTIONS, *OBJECTS) if cue not in text] == []


def test_exclusions_and_implicit_invocation_are_kept():
    text = description().lower()
    assert 'not ordinary coding or application testing' in text and 'never runs unsolicited' in text
    assert 'allow_implicit_invocation: true' in (SKILL / 'agents/openai.yaml').read_text()


def test_entrypoint_stays_within_the_soft_instruction_budget():
    assert len((SKILL / 'SKILL.md').read_text().splitlines()) <= 200


def test_relative_links_in_the_skill_resolve():
    broken = []
    for path in SKILL.rglob('*.md'):
        for target in re.findall(r'\]\(([^)#\s]+)(?:#[^)]*)?\)', path.read_text()):
            if '://' not in target and not (path.parent / target).resolve().exists():
                broken.append(f'{path.relative_to(ROOT)} -> {target}')
    assert broken == []


def test_entrypoint_links_are_plain_files_an_installed_link_can_open():
    """Regression, fixed in the unified skill: a '#fragment' link in SKILL.md is not a file under the installed skill
    link, which the installed-skill contract (test_skill_invocation) opens one by one."""
    targets = re.findall(r'\]\(([^)\s]+)\)', (SKILL / 'SKILL.md').read_text())
    assert targets and [t for t in targets if '://' not in t and not (SKILL / t).is_file()] == []


def test_nothing_in_the_repository_points_at_the_retired_skill():
    skip = {'.git', 'node_modules', 'artifacts', '__pycache__', '.venv', 'dist'}
    allowed = {'install.py', 'tools/link_skills.py', 'scripts/build_release.py', 'CHANGELOG.md', 'docs/installation.md', 'docs/single-skill-rationale.md',
               'tests/unit/docs/test_unified_skill.py'}
    found = []
    for folder, directories, files in os.walk(ROOT):
        directories[:] = [d for d in directories if d not in skip]
        for name in files:
            path = Path(folder) / name
            relative = path.relative_to(ROOT).as_posix()
            if relative in allowed or relative.startswith('.changeset/') or path.suffix not in ('.py', '.md', '.toml', '.yml', '.yaml', '.json'):
                continue
            if RETIRED in path.read_text(errors='ignore'):
                found.append(relative)
    assert found == []


# Scope mapping: the user's words choose the `--scope` value. The cues are the ones the skill text lists; a prompt that
# carries none of the global cues must never map to global. DEVELOPMENT shaped the wording; HELD_OUT was written once
# and is not edited after seeing results.
GLOBAL_CUES = ('global', 'user-level')
BOTH_CUES = ('both', 'global and project', 'project and global', 'everywhere')
EITHER_LAYER_CUES = ('my skills', 'my setup')
DEVELOPMENT_SCOPES = (
    ('Improve my harness.', 'project'),
    ('Fix the harness in this project.', 'project'),
    ('Improve the skills in my repo.', 'project'),
    ('Improve my global harness.', 'global'),
    ('Fix my user-level skills.', 'global'),
    ('Improve both the global and project harness.', 'project+global'),
    ('Improve my skills.', 'ask-or-project'),
)
HELD_OUT_SCOPES = (
    ('Maintain the harness for this repository.', 'project'),
    ('Evaluate and fix my global harness.', 'global'),
    ('Improve the harness everywhere.', 'project+global'),
    ('Please improve my setup.', 'ask-or-project'),
    ('Measure whether this skill edit helps the project harness.', 'project'),
)


def scope_for(prompt):
    text = prompt.lower()
    if any(cue in text for cue in BOTH_CUES) or ('global' in text and 'project' in text):
        return 'project+global'
    if any(cue in text for cue in GLOBAL_CUES):
        return 'global'
    if any(cue in text for cue in EITHER_LAYER_CUES):
        return 'ask-or-project'
    return 'project'


def mapping_text():
    return (SKILL / 'SKILL.md').read_text() + (SKILL / 'references/invocation.md').read_text()


@pytest.mark.parametrize(('prompt', 'expected'), DEVELOPMENT_SCOPES + HELD_OUT_SCOPES)
def test_scope_mapping_table_resolves_each_request_without_widening(prompt, expected):
    assert scope_for(prompt) == expected


def test_no_prompt_without_a_global_cue_maps_to_global():
    for prompt, _ in DEVELOPMENT_SCOPES + HELD_OUT_SCOPES:
        if not any(cue in prompt.lower() for cue in (*GLOBAL_CUES, *BOTH_CUES)):
            assert 'global' not in scope_for(prompt)


def test_the_skill_text_lists_every_scope_cue_and_flag_the_table_relies_on():
    text = mapping_text().lower()
    for cue in (*GLOBAL_CUES, 'both', 'global and project', 'everywhere', *EITHER_LAYER_CUES):
        assert cue in text, cue
    for flag in ('--scope global --agent', '--scope project+global --agent', '--repair-contracts', '[--scope scope]'):
        assert flag in text, flag
    assert 'never guess toward global' in text and 'omit `--scope`' in (SKILL / 'SKILL.md').read_text()


def test_every_scope_the_skill_names_is_accepted_by_the_command_it_runs():
    with contextlib.redirect_stdout(io.StringIO()) as out, pytest.raises(SystemExit):
        main(['skills', 'invoke', '--help'])
    help_text = out.getvalue()
    for value in re.findall(r'--scope ([\w+]+) --agent', mapping_text()):
        assert value in help_text, value
    assert '--scope' in help_text and '--repair-contracts' in help_text and '--remember-runtime' in help_text


def test_the_blocked_global_result_the_skill_relays_is_the_one_the_command_returns():
    assert scopes.BLOCKED_REASON in (SKILL / 'references/invocation.md').read_text()


def test_the_manual_fallback_is_closed_once_the_cli_path_exists():
    skill = (SKILL / 'SKILL.md').read_text()
    assert 'never edit a global file by hand instead of its `--scope` path' in ' '.join(skill.split())
    assert 'unavailable before any attempt' in skill
    assert 'Automated global scope is limited' not in (SKILL / 'references/invocation.md').read_text()
