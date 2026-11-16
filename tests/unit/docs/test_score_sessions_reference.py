"""docs/score-reference.md states the signals, limits and reasons of the advisory session placement that the code defines (#799)."""
from pathlib import Path

import pytest

from agent_harness_lab.evaluation import session_areas as areas

ROOT = Path(__file__).resolve().parents[3]
REFERENCE = (ROOT/'docs/score-reference.md').read_text()
RULES = REFERENCE.split('### Rules')[1].split('### Fields')[0]


def row(area):
    return next(line for line in RULES.splitlines() if line.startswith(f'| `{area}` |'))


@pytest.mark.parametrize('area', areas.RULED)
def test_each_rule_area_documents_exactly_its_extensions_and_command_families(area):
    cells = [cell.strip(' `').split() for cell in row(area).split('|')[2:4]]
    assert cells[0] == (areas.EXTENSIONS.get(area, '').split() or ['none']) and cells[1] == areas.COMMANDS[area].split()


@pytest.mark.parametrize('reason', areas.REASONS)
def test_each_unclassified_reason_has_a_row(reason):
    assert f'| `{reason}` |' in RULES


def test_the_file_changing_tools_and_the_thresholds_are_stated():
    assert all(f'`{tool}`' in RULES for tool in areas.CHANGE_TOOLS)
    assert f'at least {areas.MIN_SIGNALS} recognized' in RULES and 'three fifths' in RULES and areas.MIN_SHARE == (3, 5)


def test_the_input_limits_are_stated():
    assert f'at most {areas.MAX_SESSIONS:,} objects' in REFERENCE and f'at most {areas.MAX_KINDS} names' in REFERENCE
    assert f'from 1 to {areas.MAX_COUNT:,}' in REFERENCE
