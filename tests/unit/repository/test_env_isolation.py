"""The shared fixture keeps a caller's AHL_* environment from changing or writing through the suite."""
import ast
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from tests.conftest import AHL_AMBIENT_VARIABLES

ROOT = Path(__file__).resolve().parents[3]
PRODUCT = ('agent_harness_lab', 'skills', 'scripts', 'tools', 'install.py')
# Set only for sandboxed children and compared with a fixed value; it names no path and is never ambient input.
NOT_AMBIENT = {'AHL_SANDBOX_ROLE'}
AFFECTED = ('tests/integration/cli/test_static_audit_cli.py',
            'tests/integration/workflow/test_history_input.py',
            'tests/unit/memory/test_store.py::test_cli_requires_an_explicit_location_and_valid_flags',
            'tests/integration/cli/test_cli_help_boundary.py')  # module-scoped fixture builds its subprocess env from os.environ


def product_files():
    for name in PRODUCT:
        base = ROOT / name
        for path in ([base] if base.is_file() else sorted(base.rglob('*.py'))):
            if not path.name.startswith('test_') and 'node_modules' not in path.parts and '.venv' not in path.parts: yield path


def code_variables():
    found = set()
    for path in product_files():
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.fullmatch(r'AHL_[A-Z0-9_]+', node.value):
                found.add(node.value)
    return found


def test_every_ahl_variable_the_code_names_is_neutralized_by_the_fixture():
    found = code_variables()
    assert {'AHL_STATE_HOME', 'AHL_MEMORY_STORE'} <= found  # the scan itself still sees known reads
    assert found - NOT_AMBIENT - set(AHL_AMBIENT_VARIABLES) == set()


def listing(root):
    return None if not root.exists() else sorted((str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob('*'))


@pytest.mark.parametrize('kind', ['nonexistent', 'existing'])
def test_exported_overrides_pass_the_affected_suites_and_are_left_untouched(tmp_path, kind):
    state = tmp_path/'real-looking'/'agent-harness-lab'
    if kind == 'existing':
        (state/'runs').mkdir(parents=True); (state/'sentinel.txt').write_text('keep')
    store = tmp_path/'memory-store'
    before = listing(state)
    env = dict(os.environ, AHL_STATE_HOME=str(state), AHL_MEMORY_STORE=str(store), AHL_MANAGED_PREPARATION='1')
    env.pop('PYTEST_ADDOPTS', None); env.pop('PYTEST_XDIST_WORKER', None)
    run = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *AFFECTED], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=600)
    assert run.returncode == 0, run.stdout[-2000:]
    assert listing(state) == before and not store.exists()
