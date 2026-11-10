"""Synthetic bank-backed history similarity and stale-binding controls."""
from contextlib import ExitStack
from dataclasses import replace
import json
from unittest.mock import patch

from agent_harness_lab.evaluation.integrity import digest as fixture_digest
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.evaluation.root_similarity import controller_evidence, project_evidence
from agent_harness_lab.evaluation.task_bank import inspect
from agent_harness_lab.private_journal import encoded
from agent_harness_lab.sources.schema import digest
from tests.integration.evaluation.test_evaluation_foundation import TaskBank
from tests.unit.evaluation.test_precision_solver import spec
from tests.unit.evaluation.test_preservation_analysis import records


def test_bank_backed_final_analysis_rechecks_authority_and_detects_stale_bank(tmp_path):
    with ExitStack() as cleanup:
        bank = TaskBank(cleanup.callback)
        bank.tasks[1]['partition'] = 'development'
        prompts = ['Set customer from records then return customer.',
                   'Set account from records then return account.']
        for index, prompt in enumerate(prompts):
            folder = bank.root/f'fixture{index}'
            (folder/'task.json').write_text(json.dumps({'prompt': prompt}))
            bank.tasks[index]['fixture_digest'] = fixture_digest(folder)
        bank.write()
        full = inspect(bank.path)['content_grouping']['root_similarity']['evidence']
        selected = [(task['task_id'], task['root_id']) for task in bank.tasks]
        snapshot = project_evidence(full, selected)
        original = spec({'a': 2})
        plan = replace(original, tasks=tuple((task, root, 'a') for task, root in selected),
                       root_similarity_sha256=digest(snapshot))
        source = {'tasks': [dict(id=task, root=root, prompt=prompt)
                            for (task, root), prompt in zip(selected, prompts)],
                  'bank_authority': {'bank_path': str(bank.path)}}
        case = tmp_path/'case'; case.mkdir()
        manifest = case/'manifest.json'; manifest.write_bytes(encoded(source))
        history = HistoryStore(tmp_path/'history')
        trials = records(plan, lambda row: row['version'] == 'candidate')
        checked = (case, [], {'run_id':'synthetic'}, plan, {}, 'current')
        # Authority validation has separate admission tests; use an explicit test double here.
        with patch('agent_harness_lab.evaluation.oracle_history.bank_authority', return_value={}) as authority, \
                patch.object(history, 'trials', return_value=trials):
            actual, location = controller_evidence(plan, source, manifest)
            assert actual == snapshot and location == str(bank.path)
            assert authority.called
            result = history._analyze_checked(checked)
            assert result['status'] == 'inconclusive'
            assert result['reason'] == 'insufficient_independent_roots'
            changed = bank.root/'fixture1'/'task.json'
            changed.write_text(json.dumps({'prompt':'Changed synthetic prompt with distinct behavior.'}))
            bank.tasks[1]['fixture_digest'] = fixture_digest(changed.parent)
            bank.write()
            result = history._analyze_checked(checked)
            assert result['status'] == 'blocked'
            assert result['reason'] == 'root_similarity_binding_missing_or_invalid'


def test_runner_preflight_accepts_current_bank_evidence_and_blocks_twins(trajectory):
    from agent_harness_lab.evaluation import responses

    fx = trajectory
    with ExitStack() as cleanup:
        bank = TaskBank(cleanup.callback)
        bank.tasks[1]['partition'] = 'development'
        prompts = ['Set customer from records then return customer.',
                   'Set account from records then return account.']
        source_tasks = fx.fixture.manifest['tasks']
        for index, (task, prompt) in enumerate(zip(source_tasks, prompts)):
            task['prompt'] = prompt
            bank.tasks[index].update(task_id=task['id'], root_id=task['root'], family=task['family'])
            folder = bank.root/f'fixture{index}'
            (folder/'task.json').write_text(json.dumps({'prompt': prompt}))
            bank.tasks[index]['fixture_digest'] = fixture_digest(folder)
        bank.write()
        full = inspect(bank.path)['content_grouping']['root_similarity']['evidence']
        selected = [(task['id'], task['root']) for task in source_tasks]
        snapshot = project_evidence(full, selected)
        fx.fixture.manifest['bank_authority'] = {'bank_path': str(bank.path)}
        fx.plan = replace(fx.plan, root_similarity_sha256=digest(snapshot))
        fx.freeze()
        with patch('agent_harness_lab.evaluation.oracle_history.bank_authority', return_value={}), \
                patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=fx.f.backend), \
                patch.object(responses, 'read_credential', side_effect=AssertionError('no credential read')), \
                patch.object(responses, 'request', side_effect=AssertionError('no provider dispatch')):
            assert fx.f.runner.evaluate(fx.f.args) == 3
        report = fx.fixture.results()
        assert report['reason'] == 'precision_target_infeasible'
        assert report['precision_preflight']['effective_roots'] == 1
        assert report['trials'] == []
