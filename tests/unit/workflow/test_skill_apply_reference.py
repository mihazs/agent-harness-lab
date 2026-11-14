"""The bundled skill's instructions for reviewing and applying a saved plan match the command they name (#77)."""
import contextlib
import io
from pathlib import Path
import re

from agent_harness_lab.cli import main

SKILLS = Path(__file__).resolve().parents[3] / 'skills' / 'harness-self-improvement'
FIRST_RELEASE_WITH_APPLY = (0, 198, 0)


def section():
    text = (SKILLS / 'references' / 'invocation.md').read_text()
    return text.split('## Review and apply a saved plan', 1)[1].split('\n## ', 1)[0]


def test_every_option_the_skill_names_is_an_option_of_ahl_apply():
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.suppress(SystemExit):
        main(['apply', '--help'])
    options = set(re.findall(r'`(--[a-z-]+)', section()))
    assert '--authorize-plan' in options and options <= set(re.findall(r'--[a-z-]+', output.getvalue()))
    assert 'ahl apply RUN --project PROJECT' in section()


def test_the_skill_requires_a_release_that_has_the_command_it_documents():
    declared = re.search(r'Requires `ahl` (\d+)\.(\d+)\.(\d+) or later', (SKILLS / 'SKILL.md').read_text())
    assert tuple(map(int, declared.groups())) >= FIRST_RELEASE_WITH_APPLY

