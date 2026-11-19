"""The installation guide's CI claim follows the actual release and test workflows."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('workflow', ['tests.yml', 'release.yml', 'evaluation-packaging.yml'])
def test_documented_ci_platform_matches_each_workflow(workflow):
    document = (ROOT / 'docs/installation.md').read_text()
    row = re.search(r'^\| CI tested \| `([^`]+)` \| `([^`]+)` \|$', document, re.MULTILINE)
    assert row, 'installation guide must contain its CI-tested runner and Python version'
    source = (ROOT / '.github/workflows' / workflow).read_text()
    runners = re.findall(r'^\s*runs-on:\s*([^\s#]+)\s*$', source, re.MULTILINE)
    versions = re.findall(r'^\s*python-version:\s*[\'"]?([\d.]+)[\'"]?\s*$', source, re.MULTILINE)
    assert runners == [row.group(1)] and versions == [row.group(2)]
