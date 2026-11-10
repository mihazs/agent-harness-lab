"""Public synthetic pre-trial preparation; no execution or private histories."""
from contextlib import ExitStack, redirect_stdout
from dataclasses import replace
import io
import json

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.integrity import digest as tree_digest
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
from agent_harness_lab.evaluation.precision import preflight
from tests.integration.evaluation.test_evaluation_foundation import TaskBank
from tests.unit.evaluation.test_precision_solver import spec


def synthetic_bank(cleanup):
    bank = TaskBank(cleanup)
    bank.tasks[1]['partition'] = 'development'
    for index, prompt in enumerate(('Set customer from records then return customer.',
                                    'Set account from records then return account.')):
        folder = bank.root/f'fixture{index}'
        (folder/'task.json').write_text(json.dumps({'prompt': prompt}))
        bank.tasks[index]['fixture_digest'] = tree_digest(folder)
        bank.tasks[index]['family'] = 'a'
    bank.write()
    return bank


def test_prepare_prints_private_new_plan_without_mutating_bank_or_old_plan(tmp_path):
    with ExitStack() as cleanup:
        bank = synthetic_bank(cleanup.callback)
        old = replace(spec({'a': 2}), tasks=tuple((t['task_id'], t['root_id'], t['family']) for t in bank.tasks))
        path = tmp_path/'plan.json'; path.write_text(json.dumps(old.to_dict()))
        original = path.read_bytes(); bank_before = tree_digest(bank.root)
        output = io.StringIO()
        with redirect_stdout(output):
            assert main(['experiment', '--spec', str(path), '--prepare-root-similarity', '--root-bank', str(bank.path)]) == 0
        result = json.loads(output.getvalue())
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        assert not public_export_allowed([result])
        assert path.read_bytes() == original and tree_digest(bank.root) == bank_before
        assert result['status'] == 'prepared_not_frozen' and not result['execution_authorized']
        assert result['root_independence'] == 'unverified' and not result['publication_allowed']
        assert result['original_plan_sha256'] == old.digest
        new = ExperimentSpec.parse(result['prepared_plan'])
        assert result['prepared_plan_sha256'] == new.digest != old.digest
        assert {row['trial_id'] for row in schedule(old)}.isdisjoint(row['trial_id'] for row in schedule(new))
        assert not result['previous_trial_ids_reusable']
        report = preflight(new, root_similarity_evidence=result['root_similarity_evidence'], root_similarity_bank=bank.path)
        assert report['declared_roots'] == 2 and report['effective_roots'] == 1
        assert not report['execution_allowed']
        assert 'customer' not in output.getvalue() and 'account' not in output.getvalue()


def test_prepare_refuses_incomplete_mismatched_and_already_pinned_inputs():
    from agent_harness_lab.evaluation.root_similarity import prepare
    with ExitStack() as cleanup:
        bank = synthetic_bank(cleanup.callback)
        plan = replace(spec({'a': 2}), tasks=tuple((t['task_id'], t['root_id'], t['family']) for t in bank.tasks))
        with pytest.raises(ValueError, match='already pins'):
            prepare(replace(plan, root_similarity_sha256='a'*64), bank.path)
        with pytest.raises(ValueError, match='roots and families'):
            prepare(replace(plan, tasks=((plan.tasks[0][0], 'wrong', 'a'), plan.tasks[1])), bank.path)
        (bank.root/'fixture1'/'task.json').unlink()
        bank.tasks[1]['fixture_digest'] = tree_digest(bank.root/'fixture1')
        bank.write()
        with pytest.raises(ValueError, match='complete'):
            prepare(plan, bank.path)
