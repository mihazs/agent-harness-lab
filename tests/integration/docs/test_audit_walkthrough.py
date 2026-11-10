"""Run the documented offline walkthrough against its synthetic fixture."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

from tests.support import harness_surface_fixture

ROOT = Path(__file__).resolve().parents[3]


def test_documented_audit_command_runs_offline_and_leaves_fixture_unchanged(tmp_path):
    document = (ROOT / 'docs/audit-walkthrough.md').read_text()
    command = shlex.split(document.split('```sh\n', 1)[1].split('\n```', 1)[0])
    assert command == ['python', '-m', 'agent_harness_lab', 'audit', 'examples/flawed-audit', '--runtime', 'claude-code', '--json']
    fixture = ROOT / 'examples/flawed-audit'
    before = harness_surface_fixture.tree_state(fixture)
    (tmp_path / 'sitecustomize.py').write_text(
        "import sys\n"
        "def deny(event, args):\n"
        "    if event in ('subprocess.Popen', 'os.system', 'socket.__new__', 'socket.connect'):\n"
        "        raise RuntimeError('unexpected process or network')\n"
        "sys.addaudithook(deny)\n")
    env = {**os.environ, 'PYTHONPATH': str(tmp_path) + os.pathsep + str(ROOT),
           'XDG_STATE_HOME': str(tmp_path / 'state'), 'PYTHONDONTWRITEBYTECODE': '1'}
    result = subprocess.run([sys.executable, *command[1:]], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['status'] == 'completed'
    assert report['performance_evidence'] is False and report['publication_allowed'] is False
    findings = {row['reason']: row for row in report['findings']}
    for reason, dimension in (('hook_unknown_event', 'enforcement'), ('instruction_empty', 'instructions')):
        finding = findings[reason]
        assert finding['dimension'] == dimension
        assert finding['source'] and finding['evidence_kind'] and finding['cost_class'] and finding['remediation']
        assert finding['notice'] == 'convention_check_not_performance_evidence'
    assert any(row['status'] == 'not_checked' for row in report['coverage'])
    assert 'ahl improve' in report['next_action']
    assert json.loads(Path(result.stderr.removeprefix('Private report: ').strip()).read_text()) == report
    assert harness_surface_fixture.tree_state(fixture) == before
