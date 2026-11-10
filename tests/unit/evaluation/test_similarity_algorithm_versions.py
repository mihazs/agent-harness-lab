"""Synthetic variable-renaming controls and frozen v1 compatibility."""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
import json
import shutil

import pytest

from agent_harness_lab.evaluation.integrity import digest as fixture_digest
from agent_harness_lab.evaluation.plan import schedule
from agent_harness_lab.evaluation.precision import preflight
from agent_harness_lab.evaluation.root_similarity import (
    ALGORITHM, LEGACY_ALGORITHM, binding, controller_evidence, manifest_evidence, prepare, project_evidence, suspected_components,
)
from agent_harness_lab.evaluation.task_bank import inspect
from agent_harness_lab.sources.schema import digest
from tests.integration.evaluation.test_evaluation_foundation import TaskBank
from tests.unit.evaluation.test_precision_solver import spec


FIRST = 'Set the value of `customer` to the selected record and return `customer`.'
SECOND = 'Set the value of `account` to the selected record and return `account`.'
UNRELATED = 'Reject malformed calendar dates before storing the normalized result.'


def test_backticked_variable_twins_and_unrelated_control():
    prompts = [('ra', FIRST), ('rb', SECOND), ('rc', UNRELATED)]
    assert suspected_components(prompts, algorithm=LEGACY_ALGORITHM) == ()
    assert suspected_components(prompts) == (('ra', 'rb'),)
    literals = [('ra', 'Return status `pass` when finished.'),
                ('rb', 'Return status `fail` when finished.')]
    assert suspected_components(literals) == ()


def test_bank_v1_pin_revalidates_and_v2_preparation_counts_twins():
    with ExitStack() as cleanup:
        bank = TaskBank(cleanup.callback)
        bank.tasks[1]['partition'] = 'development'
        third = deepcopy(bank.tasks[0])
        third.update(task_id='t2',root_id='r2',family='a',project_group='p2',time_group='time2',fixture='fixture2')
        shutil.copytree(bank.root/'fixture0', bank.root/'fixture2')
        bank.tasks.append(third)
        for index,prompt in enumerate((FIRST, SECOND, UNRELATED)):
            folder = bank.root/f'fixture{index}'
            (folder/'task.json').write_text(json.dumps({'prompt':prompt}))
            bank.tasks[index].update(family='a',fixture_digest=fixture_digest(folder))
        bank.write()
        selected = [(t['task_id'],t['root_id']) for t in bank.tasks]
        old = inspect(bank.path, similarity_algorithm=LEGACY_ALGORITHM)['content_grouping']['root_similarity']
        current = inspect(bank.path)['content_grouping']['root_similarity']
        assert old['effective_roots'] == 3 and current['effective_roots'] == 2
        assert old['evidence']['algorithm'] == LEGACY_ALGORITHM
        assert current['evidence']['algorithm'] == ALGORITHM
        original = replace(spec({'a':3}), tasks=tuple((t['task_id'],t['root_id'],'a') for t in bank.tasks))
        old_evidence = project_evidence(old['evidence'], selected)
        old_plan = replace(original, root_similarity_sha256=digest(old_evidence))
        old_hash, old_trials = old_plan.digest, schedule(old_plan)
        assert binding(old_plan, old_evidence, bank.path) == {}
        assert preflight(old_plan, root_similarity_evidence=old_evidence, root_similarity_bank=bank.path)['effective_roots'] == 3
        assert old_plan.digest == old_hash and schedule(old_plan) == old_trials
        prepared = prepare(original, bank.path)
        assert prepared['root_similarity_evidence']['algorithm'] == ALGORITHM
        assert prepared['root_similarity_evidence']['groups'] == [['r0','r1']]
        changed = bank.root/'fixture1'/'task.json'
        changed.write_text(json.dumps({'prompt':'Changed synthetic input with unrelated text.'}))
        bank.tasks[1]['fixture_digest'] = fixture_digest(changed.parent); bank.write()
        with pytest.raises(ValueError):
            binding(old_plan, old_evidence, bank.path)


def test_manifest_history_recovers_exact_v1_and_v2_pins_without_rewriting_ids(tmp_path):
    source = {'tasks':[dict(id='t0',root='r0',prompt=FIRST),dict(id='t1',root='r1',prompt=SECOND)]}
    path = tmp_path/'manifest.json'; path.write_text(json.dumps(source))
    base = replace(spec({'a':2}), tasks=(('t0','r0','a'),('t1','r1','a')))
    old = manifest_evidence(source, algorithm=LEGACY_ALGORITHM)
    new = manifest_evidence(source)
    assert old['groups'] == [] and new['groups'] == [['r0','r1']]
    for snapshot in (old,new):
        plan = replace(base, root_similarity_sha256=digest(snapshot))
        prior_hash, prior_ids = plan.digest, schedule(plan)
        actual, location = controller_evidence(plan, source, path)
        assert actual == snapshot and location == path
        assert plan.digest == prior_hash and schedule(plan) == prior_ids
    assert digest(old) != digest(new)
    source['tasks'][1]['prompt'] = 'Changed synthetic task content that is unrelated.'
    path.write_text(json.dumps(source))
    with pytest.raises(ValueError):
        controller_evidence(replace(base, root_similarity_sha256=digest(old)), source, path)


def test_live_bank_controller_recovers_both_versions_and_refuses_stale_authority():
    from agent_harness_lab.evaluation.plan import ExperimentSpec
    from tests.integration.evaluation.test_oracle_bank_authority import OracleBankAuthority

    with ExitStack() as cleanup:
        fixture = OracleBankAuthority(cleanup.callback)
        selected = [(task['id'], task['root']) for task in fixture.manifest['tasks']]
        original = ExperimentSpec.parse(fixture.manifest['experiment'])
        plans = []
        for algorithm in (LEGACY_ALGORITHM, ALGORITHM):
            full = inspect(fixture.bank, similarity_algorithm=algorithm)['content_grouping']['root_similarity']['evidence']
            snapshot = project_evidence(full, selected)
            plan = replace(original, root_similarity_sha256=digest(snapshot))
            fixture.manifest['experiment'] = plan.to_dict(); fixture.write_manifest()
            actual, location = controller_evidence(plan, fixture.manifest, fixture.export/'manifest.json', export_root=fixture.export)
            assert actual == snapshot and location == str(fixture.bank)
            plans.append(plan)
        mismatch = replace(original, root_similarity_sha256='f'*64)
        fixture.manifest['experiment'] = mismatch.to_dict(); fixture.write_manifest()
        with pytest.raises(ValueError):
            controller_evidence(mismatch, fixture.manifest, fixture.export/'manifest.json', export_root=fixture.export)
        fixture.revoke()
        for plan in plans:
            fixture.manifest['experiment'] = plan.to_dict(); fixture.write_manifest()
            with pytest.raises(ValueError):
                controller_evidence(plan, fixture.manifest, fixture.export/'manifest.json', export_root=fixture.export)
