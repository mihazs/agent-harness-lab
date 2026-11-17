"""Run the synthetic probe through the actual paired evaluator with a stub cooperative agent."""
import json
import stat
import sys

import pytest

from agent_harness_lab.evaluation.hypothesis_probe import materialize


@pytest.mark.parametrize('follow_up', ['same_call_retry', 'same_tool_changed_input'])
def test_real_paired_runner_grades_the_same_synthetic_task_for_both_snapshots(tmp_path, monkeypatch, capsys, follow_up):
    from agent_harness_lab.cli import main
    project, baseline, candidate = (tmp_path / name for name in ('project', 'baseline', 'candidate'))
    for folder in (project, baseline, candidate): folder.mkdir()
    (baseline / 'AGENTS.md').write_text('leave the synthetic state unchanged\n')
    (candidate / 'AGENTS.md').write_text('repair ' + follow_up + '\n')
    agent = tmp_path / 'codex'
    agent.write_text('''#!%s
import json
from pathlib import Path
import subprocess
import sys
if '--version' in sys.argv:
    print('synthetic-agent'); raise SystemExit(0)
sys.stdin.read()
subprocess.run([sys.executable, 'synthetic_worker.py'], capture_output=True)
if 'repair' in Path('AGENTS.md').read_text():
    if 'same_call_retry' in Path('AGENTS.md').read_text():
        Path('config.json').write_text('{"ready": true}')
        args = []
    else:
        args = ['--recover']
    subprocess.run([sys.executable, 'synthetic_worker.py', *args], check=True, capture_output=True)
print(json.dumps({'type': 'turn.completed', 'usage': {'output_tokens': 1}}))
''' % sys.executable)
    agent.chmod(agent.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex-home'))
    mechanism = dict(status='observed_pattern', tool_class='exec_command', error_category='tool_reported_error',
                     next_action={follow_up: 2})
    brief = tmp_path / 'brief.json'
    brief.write_text(json.dumps({'mechanisms': [mechanism]}))
    output = tmp_path / 'probe'
    code = main(['hypothesis-probe', '--brief', str(brief), '--mechanism', '0', '--project', str(project),
                 '--baseline', str(baseline), '--candidate', str(candidate), '--output', str(output),
                 '--agent', 'codex', '--binary', str(agent), '--harness-path', 'AGENTS.md'])
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result['verdict'] == 'supported_hypothesis'
    assert result['scope'] == 'one_synthetic_pair' and result['source_session_outcome'] == 'unknown'
    assert result['independence'] == 'unverified' and result['automatic_promotion'] is False
    recorded = json.loads((output / 'evaluation/results.json').read_text())
    assert [(row['version'], row['task'], row['split'], row['status']) for row in recorded['trials']] == [
        ('baseline', 'hypothesis-probe', 'development', 'failed'),
        ('candidate', 'hypothesis-probe', 'development', 'passed')]
    assert json.loads((output / 'probe-result.json').read_text()) == result


@pytest.mark.parametrize('remove_marker', [False, True])
def test_runner_rejects_a_probe_relabelled_as_heldout_before_dispatch(tmp_path, remove_marker):
    from agent_harness_lab.cli import evaluator
    from agent_harness_lab.loop.adapters import ExecutorRunner
    project, baseline, candidate = (tmp_path / name for name in ('project', 'baseline', 'candidate'))
    for folder in (project, baseline, candidate): folder.mkdir()
    mechanism = dict(status='observed_pattern', tool_class='exec_command', error_category='tool_reported_error',
                     next_action={'same_call_retry': 2})
    output = tmp_path / 'probe'
    manifest_path = materialize(mechanism, output, project=project, harness_paths=[])
    if remove_marker:
        (output / 'fixture/ahl-synthetic-probe.json').unlink()
    manifest = json.loads(manifest_path.read_text())
    manifest['tasks'][0].update(id='renamed', split='heldout')
    manifest_path.write_text(json.dumps(manifest))
    runner = ExecutorRunner(evaluator().evaluate, 'codex', sys.executable, inherit_env=False)
    with pytest.raises(ValueError, match='synthetic probes cannot be held-out'):
        runner.run(('baseline', 'candidate'), {'baseline': baseline, 'candidate': candidate}, manifest_path, output / 'evaluation')
    assert not (output / 'evaluation').exists()
