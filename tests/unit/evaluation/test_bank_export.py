"""Relative bank exports retain frozen oracles and reject unsafe destinations."""
# fixed-in: e8ded782 -k quarantine
from contextlib import chdir
import json
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest

import agent_harness_lab
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.bank_tools import audit_graders, export_manifest, frozen_grader


@pytest.fixture
def root(tmp_path):
    source = Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1'
    shutil.copytree(source,tmp_path/'input')
    return tmp_path


def test_relative_source_and_nested_destination_produce_usable_frozen_manifest(root):
    with chdir(root):
        manifest = export_manifest('input/bank.json','nested/export')
    output = root/'nested/export'
    assert json.loads((output/'manifest.json').read_text()) == manifest
    for task in manifest['tasks']:
        bundle,allowed = frozen_grader(output,task,manifest['grader_bundles'][task['id']])
        assert bundle.is_dir(); assert allowed
        assert (output/task['fixture']).is_dir()
    for control in manifest['grader_self_tests']:
        assert (output/control['fixture']).is_dir()


def test_cli_relative_destination_exports_without_upgrading_independence(root):
    with chdir(root), patch('builtins.print') as printed:
        code = main(['bank','--manifest','input/bank.json','--export','export'])
    assert code == 3
    result = json.loads(printed.call_args.args[0])
    assert not result['certification_eligible']
    assert result['export']['tasks'] == 7
    assert (root/'export/manifest.json').is_file()


def test_parent_traversal_cannot_hide_destination_inside_source(root):
    (root/'alias').mkdir()
    with chdir(root), pytest.raises(ValueError):
        export_manifest('input/bank.json','alias/../input/export')
    assert not (root/'input/export').exists()


def test_explicit_adversarial_export_ceiling_preserves_the_default(root):
    with chdir(root), patch('builtins.print'):
        with pytest.raises(SystemExit) as refused:
            main(['bank','--adversarial','--export','too-small'])
        assert refused.value.code == 2
        assert not (root/'too-small').exists()
        code=main(['bank','--adversarial','--export','adversarial','--max-trials','28'])
    assert code == 3
    manifest=json.loads((root/'adversarial/manifest.json').read_text())
    assert (len(manifest['tasks']),manifest['max_trials']) == (14,28)


@pytest.mark.parametrize('limit',[
    pytest.param(True,id='bool'),pytest.param(0,id='zero'),pytest.param(-1,id='negative'),
    pytest.param(1.5,id='fractional'),pytest.param(10001,id='above-maximum')])
def test_invalid_export_ceilings_refuse_before_writing(root,limit):
    with pytest.raises(ValueError):
        export_manifest(root/'input/bank.json',root/'invalid',limit)
    assert not (root/'invalid').exists()


@pytest.mark.parametrize('output',[
    pytest.param('link/export',id='through-symlink'),pytest.param('input/export',id='inside-source'),
    pytest.param('existing',id='existing-output')])
def test_symlink_source_overlap_and_existing_output_refuse_without_writes(root,output):
    (root/'link').symlink_to(root,target_is_directory=True)
    existing = root/'existing'; existing.mkdir(); (existing/'keep').write_text('preserve')
    with chdir(root), pytest.raises((ValueError,FileExistsError)):
        export_manifest('input/bank.json',output)
    assert not (root/'export').exists()
    assert not (root/'input/export').exists()
    assert [p.name for p in existing.iterdir()] == ['keep']
    assert (existing/'keep').read_text() == 'preserve'


def _quarantine(root, *task_ids):
    path = root/'input/bank.json'
    value = json.loads(path.read_text())
    for task in value['tasks']:
        if task['task_id'] in task_ids: task['review_status'] = 'quarantined'
    path.write_text(json.dumps(value))
    return path


def test_quarantined_task_is_listed_and_flagged_but_still_inspectable(root):
    from agent_harness_lab.evaluation.task_bank import inspect, validate
    bank = _quarantine(root, 'localization')
    for result in (validate(bank), inspect(bank)):
        assert result['quarantined_tasks'] == ['localization'] and result['tasks'] == 7
        assert {'task': 'localization', 'status': 'task_quarantined'} in result['issues']
        assert not result['review_declarations_complete'] and not result['certification_eligible']


def test_quarantined_tasks_never_enter_an_executable_manifest(root):
    bank = _quarantine(root, 'localization', 'resumption')
    manifest = export_manifest(bank, root/'export', max_trials=10)
    kept = {'regression', 'tool-recovery', 'instruction-conflict', 'skill-routing', 'adversarial-input'}
    assert {t['id'] for t in manifest['tasks']} == kept
    assert set(manifest['grader_bundles']) == kept
    assert {c['task'] for c in manifest['grader_self_tests']} <= kept
    assert sorted(p.name for p in (root/'export/fixtures').iterdir()) == sorted(kept)
    assert sorted(p.name for p in (root/'export/graders').iterdir()) == sorted(kept)


def test_trial_ceiling_counts_only_executable_tasks(root):
    bank = _quarantine(root, 'localization')
    with pytest.raises(ValueError, match='trial ceiling'):
        export_manifest(bank, root/'too-small', max_trials=11)
    assert len(export_manifest(bank, root/'fits', max_trials=12)['tasks']) == 6


def test_explicit_selection_of_a_quarantined_task_is_refused_before_writing(root):
    bank = _quarantine(root, 'localization')
    with pytest.raises(ValueError, match='quarantined tasks cannot be selected: localization'):
        export_manifest(bank, root/'export', task_ids=['regression', 'localization'])
    assert not (root/'export').exists()
    assert [t['id'] for t in export_manifest(bank, root/'ok', task_ids=['regression'])['tasks']] == ['regression']


def test_a_bank_with_only_quarantined_tasks_has_nothing_to_export_or_audit(root):
    ids = [t['task_id'] for t in json.loads((root/'input/bank.json').read_text())['tasks']]
    bank = _quarantine(root, *ids)
    with pytest.raises(ValueError, match='no executable task'):
        export_manifest(bank, root/'export')
    assert not (root/'export').exists()
    with pytest.raises(ValueError, match='no executable task'):
        audit_graders(bank, cooperative=True)


def test_cli_export_reports_the_quarantined_exclusion(root):
    bank = _quarantine(root, 'localization')
    with chdir(root), patch('builtins.print') as printed:
        code = main(['bank', '--manifest', str(bank), '--export', 'export'])
    assert code == 3
    result = json.loads(printed.call_args.args[0])
    assert result['export'] == {'tasks': 6, 'profile': 'descriptive_smoke', 'quarantined_excluded': ['localization']}
    assert not (root/'export/fixtures/localization').exists()


def test_quarantine_isolates_a_broken_grader_from_the_audit(root):
    from agent_harness_lab.evaluation.integrity import digest
    bank = root/'input/bank.json'
    healthy = audit_graders(bank, cooperative=True)['checks']
    own = [c for c in healthy if c['task'] == 'regression']
    assert own and len(own) < len(healthy)
    value = json.loads(bank.read_text())
    broken = next(t for t in value['tasks'] if t['task_id'] == 'regression')
    bundle = bank.parent/broken['grader_bundle']
    # A grader that rejects even the complete control cannot be calibrated.
    (bundle/'grade.py.txt').write_text('import json,sys\nprint(json.dumps(dict(schema_version=1,valid=True,outcome=False,grader_id="x",grader_version="v1")))\nsys.exit(1)\n')
    broken['grader_digest'] = digest(bundle)
    bank.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='falsification failed: regression'):
        audit_graders(bank, cooperative=True)
    _quarantine(root, 'regression')
    audited = audit_graders(bank, cooperative=True)
    assert audited['status'] == 'controls_passed' and audited['quarantined_skipped'] == ['regression']
    assert 'regression' not in {c['task'] for c in audited['checks']}
    assert len(audited['checks']) == len(healthy) - len(own)


@pytest.mark.parametrize('status', ['Quarantined', 'rejected', ''], ids=['wrong-case', 'unknown-name', 'empty'])
def test_a_review_status_outside_the_closed_vocabulary_never_reaches_an_executable_manifest(root, status):
    bank = root/'input/bank.json'
    value = json.loads(bank.read_text())
    value['tasks'][0]['review_status'] = status
    bank.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        export_manifest(bank, root/'export')
    assert not (root/'export').exists()


def test_cli_export_with_no_quarantined_task_reports_an_empty_exclusion(root):
    with chdir(root), patch('builtins.print') as printed:
        main(['bank', '--manifest', 'input/bank.json', '--export', 'export'])
    assert json.loads(printed.call_args.args[0])['export']['quarantined_excluded'] == []


def test_cli_grader_audit_reports_the_quarantined_skip_and_never_audits_that_task(root):
    bank = _quarantine(root, 'localization')
    with chdir(root), patch('builtins.print') as printed:
        main(['bank', '--manifest', str(bank), '--audit', '--cooperative-controls'])
    audit = json.loads(printed.call_args.args[0])['grader_audit']
    assert audit['quarantined_skipped'] == ['localization']
    assert 'localization' not in {check['task'] for check in audit['checks']}


def test_cli_refuses_to_export_a_bank_whose_every_task_is_quarantined(root):
    bank = _quarantine(root, *(task['task_id'] for task in json.loads((root/'input/bank.json').read_text())['tasks']))
    with chdir(root), patch('builtins.print'), pytest.raises(SystemExit) as refused:
        main(['bank', '--manifest', str(bank), '--export', 'export'])
    assert refused.value.code == 2 and not (root/'export').exists()
