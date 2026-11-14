"""Native acceptance cases shared by source, wheel and archive layouts."""
import json
import os
from pathlib import Path
import subprocess
import sys

from agent_harness_lab.cli import version
import agent_harness_lab


def test_application_is_loaded_from_the_selected_layout():
    expected = Path(os.environ.get('AHL_EXPECTED_PACKAGE_ROOT', Path(__file__).resolve().parents[3])).resolve()
    assert Path(agent_harness_lab.__file__).resolve().is_relative_to(expected)


def invoke(*args, **extra):
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')}
    env['AHL_SYNTHETIC_TOKEN'] = 'SYNTHETIC_SECRET_CANARY'
    env.update(extra)
    result = subprocess.run([sys.executable, '-m', 'agent_harness_lab', *args],
        capture_output=True, text=True, timeout=30, env=env)
    assert 'SYNTHETIC_SECRET_CANARY' not in result.stdout+result.stderr
    return result


def test_cli_help_and_version_are_available():
    assert invoke('--help').returncode == 0
    result = invoke('--version')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == version()


def test_default_source_discovery_enables_nothing():
    result = invoke('sources')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['enabled_sources'] == 0


def test_synthetic_research_requires_no_model_packages():
    result = invoke('research-simulate', '--tasks', '10', '--scenario', 'delayed')
    assert result.returncode == 0, result.stderr
    assert isinstance(json.loads(result.stdout), dict)


def test_packaged_bank_does_not_claim_isolated_certification():
    result = invoke('bank', '--starter', '--audit', '--cooperative-controls')
    assert result.returncode == 3, result.stderr


def test_packaged_adversarial_controls_remain_exposed_and_ineligible():
    result = invoke('bank', '--adversarial', '--audit', '--cooperative-controls')
    assert result.returncode == 3, result.stderr
    value = json.loads(result.stdout)
    assert not value['certification_eligible']
    assert value['tasks'] == 14
    assert value['grader_audit']['status'] == 'controls_passed'
    assert len(value['grader_audit']['checks']) == 84
    assert sum(c['control']=='no_op' and c['status']=='passed' for c in value['grader_audit']['checks']) == 14


def test_offline_onboarding_preview_and_exclusive_creation(tmp_path):
    root = tmp_path; folder = str(root)
    (root/'.auth').write_text('SYNTHETIC_SECRET_CANARY')
    result = invoke('doctor', folder, '--json')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['status'] == 'ready'
    assert invoke('init', folder, '--non-interactive', '--json').returncode == 0
    assert sorted(p.name for p in root.iterdir()) == ['.auth']
    assert invoke('init', folder, '--non-interactive', '--write').returncode == 0
    path = root/'ahl.json'; before = path.read_bytes()
    assert invoke('init', folder, '--non-interactive', '--write').returncode == 2
    assert path.read_bytes() == before
    assert b'SYNTHETIC_SECRET_CANARY' not in before


def test_review_and_apply_of_a_saved_plan_reach_the_same_target_bytes_in_every_layout(tmp_path):
    """#77: the public improve, rollback and apply entrypoints write, refuse and roll back identically in each layout."""
    valid = b'---\nname: example\ndescription: Use for synthetic checks.\n---\nKeep the body intact.\n'
    skill = tmp_path / 'project/.agents/skills/example/SKILL.md'
    skill.parent.mkdir(parents=True)
    skill.write_bytes(b'\xef\xbb\xbf' + valid)
    skill.chmod(0o640)
    # no host marker, and a state directory of its own, so no layout depends on the machine that runs it
    env = {name: '' for name in ('CLAUDECODE', 'CODEX_THREAD_ID', 'CODEX_CI', 'CODEX_SANDBOX')}
    env.update(AHL_STATE_HOME=str(tmp_path / 'state'), HOME=str(tmp_path / 'home'))
    project = str(tmp_path / 'project')

    def run(*args, code=0):
        result = invoke(*args, **env)
        assert result.returncode == code, result.stderr
        return json.loads(result.stdout) if result.stdout.strip().startswith('{') else result.stderr

    first = run('improve', project, '--repair-contracts')
    assert first['status'] == 'applied' and skill.read_bytes() == valid
    run('rollback', first['deployment'], '--project', project)
    assert skill.read_bytes() == b'\xef\xbb\xbf' + valid
    plan, authorize = first['plan_sha256'], ['--project', project, '--authorize-plan', first['plan_sha256']]

    review = run('apply', first['output'], '--project', project)
    assert review['status'] == 'awaiting_review' and review['plan_sha256'] == plan and review['blockers'] == []
    assert set(review['binding'].values()) == {'matched'} and skill.read_bytes() == b'\xef\xbb\xbf' + valid
    assert 'different plan' in run('apply', first['output'], '--project', project, '--authorize-plan', '0' * 64, code=2)
    assert skill.read_bytes() == b'\xef\xbb\xbf' + valid

    applied = run('apply', first['output'], *authorize)
    assert applied['status'] == 'applied_verified' and applied['activation'] == 'unknown'
    assert skill.read_bytes() == valid and skill.stat().st_mode & 0o777 == 0o640
    assert run('apply', first['output'], *authorize, code=3)['status'] == 'stale' and skill.read_bytes() == valid
    run('rollback', applied['receipt']['deployment'], '--project', project)
    assert skill.read_bytes() == b'\xef\xbb\xbf' + valid
