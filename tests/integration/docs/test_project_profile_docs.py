"""The profile reference and the taxonomy name exactly the finding kinds the code can emit (#1120); no private input."""
from pathlib import Path
import re

from agent_harness_lab.project_profile import findings

DOCS = Path(__file__).resolve().parents[3] / 'docs'


def named(page):
    return set(re.findall(r'`([a-z_]+)`', (DOCS / page).read_text())) & set(findings.KINDS)


def test_the_reference_lists_every_kind_the_code_defines():
    assert named('project-profile.md') == set(findings.KINDS)


def test_the_taxonomy_names_a_detector_for_each_kind():
    assert named('harness-problem-taxonomy.md') == set(findings.KINDS)


def test_the_proposed_updates_in_the_reference_match_the_code():
    text = (DOCS / 'project-profile.md').read_text()
    for kind, (_, action) in findings.KINDS.items():
        row = next(line for line in text.splitlines() if line.startswith(f'| `{kind}` |'))
        assert f'`{action}`' in row, kind


def test_both_pages_are_reachable_from_the_documentation_index():
    index = (DOCS / 'README.md').read_text()
    assert 'project-profile.md' in index and 'harness-problem-taxonomy.md' in index
