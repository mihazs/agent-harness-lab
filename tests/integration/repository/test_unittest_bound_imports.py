"""The unittest-bound runners import their helper modules without pytest installed.

Each case runs the standard-library discovery that a runner in .agents/skills/pytest-patterns/SKILL.md uses, in a
subprocess where pytest cannot be imported, so a pytest-style helper module that imports pytest directly (instead of
from tests.support.optional_pytest) turns into a discovery error.
"""
from pathlib import Path
import json
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = '''import importlib.abc, json, sys, unittest

class BlockPytest(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.partition('.')[0] in {'pytest', '_pytest'}:
            raise ModuleNotFoundError(f'No module named {name!r}', name=name)

sys.meta_path.insert(0, BlockPytest())
repository, pattern = sys.argv[1:]
sys.path.append(repository)
loader = unittest.TestLoader()
suite = loader.discover(start_dir=repository + '/tests', pattern=pattern, top_level_dir=repository)
print(json.dumps(dict(errors=loader.errors, tests=suite.countTestCases(), pytest='pytest' in sys.modules)))
'''


@pytest.mark.parametrize('pattern', [pytest.param('test_bank_qualification*.py', id='bank-qualification'),
                                     pytest.param('test_quickstart.py', id='quickstart')])
def test_unittest_bound_discovery_imports_without_pytest(tmp_path, pattern):
    completed = subprocess.run([sys.executable, '-c', SCRIPT, str(REPO), pattern], cwd=tmp_path,
                               env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': ''}, capture_output=True, text=True,
                               timeout=120)
    assert completed.returncode == 0, completed.stderr[-4000:]
    report = json.loads(completed.stdout.splitlines()[-1])
    assert report['errors'] == [], '\n'.join(report['errors'])[-4000:]
    assert report['tests'] > 0
    assert not report['pytest']
