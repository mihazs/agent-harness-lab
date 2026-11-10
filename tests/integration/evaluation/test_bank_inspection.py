"""Public bank inspection remains complete and cannot issue execution authority."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.task_bank import validate
from tests.integration.evaluation import test_evaluation_foundation as fixtures


@pytest.fixture
def bank():
    """The shared synthetic task bank, written by the foundation fixture."""
    with ExitStack() as cleanups:
        yield fixtures.TaskBank(cleanups.callback)


def invoke(bank, *options):
    output, errors = io.StringIO(), io.StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        code = main(['bank', '--manifest', str(bank.path), *options])
    return code, json.loads(output.getvalue())


def assert_analysis(result):
    assert result['analysis_only']
    assert not result['certification_eligible']
    assert result['certification_status'] == 'unverified'
    assert not result['admission_authority']
    assert not result['execution_authorized']
    assert 'admission' not in result
    assert 'bank_qualification' not in result


def test_public_list_reads_large_manifest_all_tasks_and_long_declared_identifiers(bank):
    f = bank; original = f.tasks[0]
    f.tasks = [dict(original, task_id='task.'+str(index)+'x'*129,
        root_id='root.'+str(index), source_ref='synthetic '*100) for index in range(1001)]
    f.write(); assert f.path.stat().st_size > 1024*1024
    code, result = invoke(bank, '--list')
    assert code == 3; assert_analysis(result)
    assert result['tasks'] == 1001
    assert len(result['task_index']) == 1001
    assert result['task_index'][-1]['task_id'] == f.tasks[-1]['task_id']
    with pytest.raises(ValueError): validate(f.path)
    for option in ('--audit', '--export'):
        args = [option] if option == '--audit' else [option, str(f.root/'export')]
        with (patch('agent_harness_lab.evaluation.bank_tools.audit_graders', side_effect=AssertionError('no audit')),
              patch('agent_harness_lab.evaluation.bank_tools.export_manifest', side_effect=AssertionError('no export'))):
            with pytest.raises(SystemExit) as error: invoke(bank, *args)
        assert error.value.code == 2
    assert not (f.root/'export').exists()


def test_public_inspection_reads_large_fixture_tree_without_weakening_execution_validation(bank):
    f = bank; f.tasks = f.tasks[:1]; folder = f.root/'fixture0'
    for index in range(1001): (folder/str(index)).write_bytes(b'')
    (folder/'large').write_bytes(b'x'*(11*1024*1024))
    limits = dict(max_files=None, max_bytes=None, max_entries=None, max_depth=None)
    f.tasks[0]['fixture_digest'] = digest(folder, **limits); f.write()
    code, result = invoke(bank)
    assert code == 3; assert_analysis(result)
    assert result['contents_verified']
    assert result['content_grouping']['input_bytes'] > 10*1024*1024
    assert 'task_index' not in result
    with pytest.raises(ValueError): validate(f.path)


def test_controller_profile_is_only_a_declaration_during_inspection(bank):
    from agent_harness_lab.evaluation.bank_qualification import PROFILE
    f = bank; f.write(); value = json.loads(f.path.read_text())
    value['controller_profile'] = PROFILE; f.path.write_text(json.dumps(value))
    with patch('agent_harness_lab.evaluation.bank_revisions.validate_admission',
            side_effect=ValueError('controller receipt required')) as admission:
        _, result = invoke(bank, '--list')
        assert_analysis(result); admission.assert_not_called()
        assert result['controller_profile']['status'] == 'declared_unverified'
        with pytest.raises(ValueError, match='controller receipt required'): validate(f.path)
        admission.assert_called_once_with(f.path)


@pytest.mark.parametrize('change', [
    pytest.param(dict(extra='unknown'), id='unknown-field'),
    pytest.param(dict(fixture='../escape'), id='escaping-fixture-path'),
    pytest.param(dict(fixture_digest='a'*64), id='wrong-fixture-digest'),
    pytest.param('duplicate-root', id='duplicate-root'),
    pytest.param(dict(partition='unsupported'), id='unsupported-partition'),
])
def test_inspection_keeps_schema_path_hash_and_split_checks(bank, change):
    f = bank; original = deepcopy(f.tasks)
    if change == 'duplicate-root':
        change = dict(root_id=original[1]['root_id'])
    f.tasks = deepcopy(original); f.tasks[0].update(change); f.write()
    with pytest.raises(SystemExit) as error: invoke(bank)
    assert error.value.code == 2
