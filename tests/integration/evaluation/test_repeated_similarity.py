"""Synthetic repeated-workflow root similarity; no workers or providers."""
from contextlib import ExitStack
from dataclasses import replace
import json
import time
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation import repeated_workflow
from agent_harness_lab.evaluation.integrity import digest as tree_digest
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.evaluation.root_similarity import manifest_evidence
from agent_harness_lab.private_journal import encoded, record
from agent_harness_lab.sources.schema import digest
from tests.integration.evaluation.test_repeated_access_companion import RepeatedAccessAdmission
from tests.unit.evaluation.test_precision_solver import spec
from tests.unit.evaluation.test_preservation_analysis import records


@pytest.fixture
def setup_workflow():
    with ExitStack() as cleanup:
        yield RepeatedAccessAdmission(cleanup.callback)


def test_default_detects_duplicate_prompts_before_dispatch(setup_workflow):
    host = setup_workflow
    source = record(host.manifest)[0]
    source['tasks'][0]['prompt'] = 'Set customer from records then return customer.'
    source['tasks'][1]['prompt'] = 'Set account from records then return account.'
    source['tasks'][2]['prompt'] = 'Repair the calendar parser and reject invalid leap years.'
    host.manifest.write_bytes(encoded(source))
    host.request_value.pop('root_similarity')
    host.request_value.update(manifest_sha256=record(host.manifest)[1], bank_sha256=tree_digest(host.bank))
    host.request.write_bytes(encoded(host.request_value))
    output = host.root/'prepared'; output.mkdir()
    request = repeated_workflow.Request(host.request_args(host.selected), output, time.monotonic()+30)
    assert request.template.root_similarity_sha256 is not None
    assert request.precision['declared_roots'] == 3
    assert request.precision['effective_roots'] == 2
    assert not request.precision['execution_allowed']
    assert host.calls == [] and host.key_reads == 0


def test_explicit_legacy_request_does_not_claim_similarity_check(setup_workflow):
    host = setup_workflow
    output = host.root/'legacy'; output.mkdir()
    request = repeated_workflow.Request(host.request_args(host.selected), output, time.monotonic()+30)
    assert request.template.root_similarity_sha256 is None
    assert request.precision['effective_roots'] is None
    assert request.precision['root_independence'] == 'unverified'


def test_stored_final_analysis_recomputes_snapshot_and_blocks_tampering(tmp_path):
    original = spec({'a': 4})
    prompts = ['Set customer from records then return customer.',
               'Set account from records then return account.',
               'Reject invalid leap years in the calendar parser.',
               'Sort timestamps and discard duplicate event records.']
    source = {'tasks': [dict(id=task, root=root, prompt=prompt)
                        for (task, root, _), prompt in zip(original.tasks, prompts)]}
    evidence = manifest_evidence(source)
    plan = replace(original, root_similarity_sha256=digest(evidence))
    case = tmp_path/'case'; case.mkdir()
    (case/'manifest.json').write_bytes(encoded(source))
    data = records(plan, lambda trial: trial['version'] == 'candidate')
    store = HistoryStore(tmp_path/'history')
    checked = (case, [], {'schema_version':1, 'run_id':'synthetic'}, plan, {}, 'current')
    with patch.object(store, 'trials', return_value=data):
        result = store._analyze_checked(checked)
        assert result['primary']['independent_roots'] == 3
        source['tasks'][1]['prompt'] = 'Changed unrelated synthetic task content.'
        (case/'manifest.json').write_bytes(encoded(source))
        blocked = store._analyze_checked(checked)
        assert blocked['status'] == 'blocked'
        assert blocked['reason'] == 'root_similarity_binding_missing_or_invalid'
    assert 'customer' not in json.dumps(evidence)


def test_bound_manifest_reconstructs_same_similarity_without_exposing_evidence(setup_workflow):
    host = setup_workflow
    source = record(host.manifest)[0]
    for task, prompt in zip(source['tasks'], [
            'Set customer from records then return customer.',
            'Set account from records then return account.',
            'Repair the calendar parser and reject invalid leap years.']):
        task['prompt'] = prompt
    host.manifest.write_bytes(encoded(source))
    host.request_value.update(root_similarity=True, manifest_sha256=record(host.manifest)[1], bank_sha256=tree_digest(host.bank))
    host.request.write_bytes(encoded(host.request_value))
    output = host.root/'prepared'; output.mkdir()
    request = repeated_workflow.Request(host.request_args(host.selected), output, time.monotonic()+30)
    candidate = host.root/'candidate'; candidate.mkdir(); (candidate/'AGENTS.md').write_text('synthetic candidate')
    request.freeze_baseline(host.project)
    bound = record(request.bind(host.project, candidate, 'a'*64))[0]
    assert digest(manifest_evidence(bound)) == bound['experiment']['root_similarity_sha256']
    assert 'root_similarity_evidence' not in bound


def test_real_runner_rechecks_pinned_manifest_before_any_worker(trajectory):
    from agent_harness_lab.evaluation import responses

    fx = trajectory
    tasks = fx.fixture.manifest['tasks']
    tasks[0]['prompt'] = 'Set customer from records then return customer.'
    tasks[1]['prompt'] = 'Set account from records then return account.'
    evidence = manifest_evidence(fx.fixture.manifest)
    fx.plan = replace(fx.plan, root_similarity_sha256=digest(evidence))
    fx.freeze()
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=fx.f.backend), \
            patch.object(responses, 'read_credential', side_effect=AssertionError('no credential read')), \
            patch.object(responses, 'request', side_effect=AssertionError('no provider dispatch')):
        assert fx.f.runner.evaluate(fx.f.args) == 3
    report = fx.fixture.results()
    assert report['reason'] == 'precision_target_infeasible'
    assert report['precision_preflight']['effective_roots'] == 1
    assert report['trials'] == []
    fx.f.backend.capability.assert_not_called()


def test_unrelated_default_prompts_do_not_enter_proposer_input(setup_workflow):
    host = setup_workflow
    source = record(host.manifest)[0]
    prompts = ['Return the first valid calendar date from the input.',
               'Sort the invoice totals by descending amount and identifier.',
               'Reject malformed network addresses before opening a connection.']
    for task, prompt in zip(source['tasks'], prompts):
        task['prompt'] = prompt
    host.manifest.write_bytes(encoded(source))
    host.request_value.pop('root_similarity')
    host.request_value.update(manifest_sha256=record(host.manifest)[1], bank_sha256=tree_digest(host.bank))
    host.request.write_bytes(encoded(host.request_value))
    pin = digest(manifest_evidence(source))
    from tests.integration.evaluation.test_repeated_workflow import RepeatedWorkflowAdmission
    code, result = RepeatedWorkflowAdmission.supported_removal(host)
    assert code == 3 and result['status'] == 'inconclusive' and host.calls
    sent = json.dumps(host.calls)
    assert pin not in sent and 'workflow-prompt-similarity-v1' not in sent
    assert all(prompt not in sent for prompt in prompts)


def test_similarity_snapshot_and_pinned_results_are_not_public_export_records():
    from agent_harness_lab.evaluation.benchmark import MATCH_FIELDS, public_export_allowed

    runtime = {name: 'synthetic' for name in MATCH_FIELDS}
    public = dict(baseline_runtime=runtime, candidate_runtime=runtime,
                  provenance=dict(source_kind='synthetic', approved_publication=True,
                                  source_ref='synthetic:control', derived_from_private=False))
    assert public_export_allowed([public])
    snapshot = manifest_evidence({'tasks': [dict(id='one', root='root', prompt='synthetic prompt text')]})
    assert not public_export_allowed([dict(public, root_similarity_evidence=snapshot)])
    assert not public_export_allowed([dict(public, root_similarity_sha256=digest(snapshot))])


def test_synthetic_workers_receive_only_their_task_not_similarity_evidence():
    """Inspect the real runner's worker requests and seeded states using synthetic transport."""
    from copy import deepcopy
    import shutil
    from agent_harness_lab.evaluation import responses
    from agent_harness_lab.evaluation.oracle import identity
    from agent_harness_lab.evaluation.plan import ExperimentSpec
    from tests.integration.sandbox.test_sandbox_inference import ResponsesEvaluation

    with ExitStack() as cleanup:
        run = ResponsesEvaluation(cleanup.callback)
        source = run.fixture.manifest
        original = next(task for task in source['tasks'] if task['split'] == 'development')
        oracle = deepcopy(source['grader_bundles'][original['id']])
        source_fixture = run.root/'export'/original['fixture']
        prompts = ['Inspect the calendar and return the earliest valid date.',
                   'Order invoice totals by decreasing numeric amount.',
                   'Reject malformed network addresses before opening a socket.',
                   'Normalize postal codes and preserve leading zeroes.',
                   'Parse the chess position and report the active player.',
                   'Find duplicate article titles after trimming whitespace.',
                   'Count unique tree leaves in the forest inventory.']
        tasks = []
        for index, prompt in enumerate(prompts):
            path = f'synthetic-privacy-fixture-{index}'
            shutil.copytree(source_fixture, run.root/'export'/path)
            tasks.append(dict(original, id=f'synthetic-{index}', root=f'private-root-{index}',
                              family='synthetic', fixture=path, prompt=prompt, split='development'))
        source['tasks'] = tasks
        source['grader_bundles'] = {task['id']: deepcopy(oracle) for task in tasks}
        source['max_trials'] = 14
        value = manifest_evidence(source)
        pin = digest(value)
        plan = ExperimentSpec('synthetic-private-similarity',
            tuple((task['id'], task['root'], task['family']) for task in tasks),
            1, 13, 14, 2, 0, .1, .99, (('synthetic', 1.),),
            run.runner.digest(run.root/'baseline'), run.runner.digest(run.root/'candidate'),
            identity(run.root/'export', tasks, source['grader_bundles']), run.args.image_sha256,
            root_similarity_sha256=pin)
        source['experiment'] = plan.to_dict()
        run.fixture.write_manifest()
        payloads = []
        original_execute = run.execute

        def transport(key, body, *args):
            payloads.append(json.loads(body))
            return run.transport(key, body, *args)

        def execute(argv, workspace, inputs, role, limits, **options):
            if role == 'executor':
                state = options['working_state']
                raw = b''.join(content for _, _, content in state.snapshot.files)
                assert pin.encode() not in raw
                assert b'workflow-prompt-similarity-v1' not in raw
                assert all(task['root'].encode() not in raw for task in tasks)
            return original_execute(argv, workspace, inputs, role, limits, **options)

        run.backend.run.side_effect = execute
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=run.backend), \
                patch.object(responses, 'request', side_effect=transport), \
                patch.object(run.runner, 'execute', side_effect=AssertionError('no native CLI')):
            assert run.runner.evaluate(run.args) == 1
        assert len(payloads) == 28 and run.commands == 14
        for payload in payloads:
            raw = json.dumps(payload)
            assert pin not in raw and 'workflow-prompt-similarity-v1' not in raw
            assert all(task['root'] not in raw for task in tasks)
            # A worker receives at most its own task prompt, never the comparison bank.
            assert sum(prompt in raw for prompt in prompts) == 1
