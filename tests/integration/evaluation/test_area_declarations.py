"""A configured evaluation checks the bank's area tags against the project's declared areas before any trial."""
import hashlib
import json

import pytest

from agent_harness_lab import config
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.bank_tools import export_manifest
from agent_harness_lab.evaluation.manifest_contract import PROJECT, ContractError
from tests.support.area_bank import REGISTRY, two_area_bank


def configured(tmp_path, registry):
    """A project whose pinned bank is the synthetic two-area bank; returns a call that prepares the evaluation."""
    project = tmp_path/'project'; project.mkdir()
    bank = two_area_bank(project)
    value = config.prepare(dict(bank=dict(path='bank/bank.json', sha256=hashlib.sha256(bank.read_bytes()).hexdigest()),
                                harness_paths=['AGENTS.md']))
    if registry is not None: value['areas'] = registry
    (project/'ahl.json').write_text(json.dumps(value))
    manifest = export_manifest(bank, tmp_path/'export')
    return lambda: config.evaluation_settings(project/'ahl.json', tmp_path/'export/manifest.json', manifest, {}, 'offline', None)


def test_declared_areas_pass_and_are_frozen_in_the_effective_configuration(tmp_path):
    prepared = configured(tmp_path, REGISTRY)()
    assert prepared['blocked_reason'] is None
    assert prepared['configuration']['areas'] == REGISTRY


@pytest.mark.parametrize('registry, field', [
    pytest.param(None, 'tasks[0].areas[0]', id='project-declares-no-areas'),
    pytest.param([], 'tasks[0].areas[0]', id='empty-registry'),
    pytest.param(REGISTRY[:1], 'tasks[2].areas[0]', id='one-of-two-areas-declared'),
])
def test_undeclared_task_area_stops_the_evaluation_with_a_typed_reason(tmp_path, registry, field):
    with pytest.raises(ContractError) as caught:
        configured(tmp_path, registry)()
    error = caught.value
    assert (error.reason, error.origin, error.file, error.field) == ('task_area_undeclared', PROJECT, 'bank.json', field)


def test_bank_command_lists_the_task_count_per_area_and_counts_a_multi_area_task_in_each(tmp_path, capsys):
    code = main(['bank', '--manifest', str(two_area_bank(tmp_path))])
    result = json.loads(capsys.readouterr().out)
    assert (code, result['tasks'], result['areas']) == (3, 7, {'coding': 4, 'ux-ui': 3})  # descriptive banks never exit 0


def test_bank_command_rejects_a_malformed_tag_with_the_typed_reason(tmp_path, capsys):
    with pytest.raises(SystemExit) as exited:
        main(['bank', '--manifest', str(two_area_bank(tmp_path, tags={'regression': ['coding', 'ux-ui', 'docs', 'review']}))])
    assert exited.value.code == 2
    assert 'task_areas_too_many: stage=bank_validation file=bank.json field=tasks[1].areas' in capsys.readouterr().err
