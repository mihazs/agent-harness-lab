"""A second `ahl improve --incremental` reuses the verified evidence stage that is still valid and reruns the rest (#806).

The same fake agent as `test_incremental_runs` plays proposer, construction and trials; the real evidence stage reads a
synthetic capture and a memory store made under a temporary directory. Nothing reads a real project, history or model.
"""
import json
from contextlib import ExitStack
from pathlib import Path

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.memory.store import MemoryStore
from agent_harness_lab.workflow import improve
from tests.integration.workflow import test_before_after_guard as guard_fixture
from tests.integration.workflow import test_workflow as workflow_fixture
from tests.integration.workflow.test_incremental_analyze import events_text


@pytest.fixture
def world():
    """Run the real improve entry repeatedly on one project, one capture and one store; `go` returns (receipt, run folder)."""
    with ExitStack() as cleanups:
        base = workflow_fixture.WorkflowTests(cleanups.callback)
        base.collect.stop()  # the real evidence stage runs on the synthetic capture below
        base.agent.write_text(guard_fixture.WORSE)
        events = base.root / 'events.jsonl'
        events.write_text(events_text())
        store = MemoryStore.init(base.root / 'memory', excluded=[base.project])

        def go(mode='ok', *, harness=None, incremental=True, explain=False):
            if harness is not None: (base.project / 'AGENTS.md').write_text(harness)
            (base.project / 'mode').write_text(mode)
            args = base.args()
            args.events, args.source_version, args.incremental, args.explain_reuse = events, None, incremental, explain
            args.memory_store = store.root if incremental else None
            result = improve(args)
            return result, Path(result['output'])
        go.base, go.events, go.store = base, events, store
        yield go


def evidence(result):
    return result['incremental']['stages']['evidence_collection']


def test_a_first_incremental_run_executes_and_commits_the_stage_and_has_no_delta_block(world):
    result, _ = world('ok', harness='bad')
    assert 'incremental' not in result
    assert (result['reuse']['outcome'], result['reuse']['reason'], result['reuse']['commit']) == ('executed', 'miss_no_prior', 'committed')
    assert result['reuse']['publication_allowed'] is False


def test_an_ignored_candidate_reruns_the_trials_but_reuses_the_unchanged_evidence_stage(world):
    first, first_output = world('ok', harness='bad')
    second, output = world('ok')
    assert evidence(second) == dict(action='reused', reason='hit_verified')
    assert (second['reuse']['outcome'], second['reuse']['commit']) == ('reused', 'not_needed')
    assert second['reuse']['counters']['historical_reuse'] == dict(reused_live_trials=0)
    stages = second['incremental']['stages']
    assert {name: row['action'] for name, row in stages.items() if name != 'evidence_collection'} == dict(
        candidate_preparation='rerun', evaluation_construction='rerun', trials='rerun')
    assert (output / 'evaluation-0/results.json').is_file()  # a fresh evaluation still ran
    for name in ('analysis.json', 'report.md'):
        assert (first_output / name).read_bytes() == (output / name).read_bytes()


def test_an_applied_candidate_changes_the_harness_so_the_evidence_stage_is_rerun_and_says_why(world):
    world('ok', harness='bad')
    second, _ = world('worse', harness='good\n')  # the candidate's exact bytes, applied by hand
    assert second['incremental']['disposition'] == 'applied'
    assert evidence(second) == dict(action='rerun', reason='miss_input_changed')
    assert (second['reuse']['outcome'], second['reuse']['commit']) == ('executed', 'committed')


def test_a_harness_edited_by_hand_between_runs_reruns_the_evidence_stage(world):
    world('ok', harness='bad')
    second, _ = world('ok', harness='edited by hand\n')
    assert second['incremental']['disposition'] == 'edited'
    assert evidence(second) == dict(action='rerun', reason='miss_input_changed')


def test_a_changed_capture_reruns_the_evidence_stage(world):
    world('ok', harness='bad')
    world.events.write_text(events_text(cases=('a', 'b', 'c', 'd')))
    second, _ = world('ok')
    assert second['incremental']['disposition'] == 'ignored'  # the harness is untouched; only the capture differs
    assert evidence(second) == dict(action='rerun', reason='miss_input_changed')


def test_a_regressed_candidate_is_stopped_before_evaluation_and_still_reuses_the_evidence_stage(world):
    first, _ = world('worse', harness='good')
    assert first['reason'] == 'candidate_regressed'
    second, output = world('worse')
    assert (second['status'], second['reason']) == ('blocked', 'candidate_already_regressed')
    stages = second['incremental']['stages']
    assert evidence(second) == dict(action='reused', reason='hit_verified')
    assert stages['evaluation_construction']['action'] == stages['trials']['action'] == 'skipped'
    assert not (output / 'evaluation-0').exists()


def test_without_the_flags_the_stage_is_not_reused_and_the_receipt_keeps_its_reason(world):
    world('ok', harness='bad')
    second, _ = world('ok', incremental=False)
    assert 'reuse' not in second
    assert evidence(second) == dict(action='rerun', reason='no_verified_prior_output')


def test_the_explained_receipt_names_the_stage_decision_and_the_inputs(world):
    world('ok', harness='bad')
    second, _ = world('ok', explain=True)
    assert second['reuse']['receipt']['reused_live_trials'] == 0
    assert sorted(second['reuse']['inputs']) == ['events_sha256', 'harness_sha256']
    assert json.loads((Path(second['output']) / 'workflow.json').read_text())['reuse'] == second['reuse']


@pytest.mark.parametrize('flags, message', [
    pytest.param(['--incremental'], 'explicit --memory-store', id='incremental_without_a_store'),
    pytest.param(['--memory-store', '{store}'], 'require --incremental', id='store_without_incremental'),
    pytest.param(['--explain-reuse'], 'require --incremental', id='explain_without_incremental'),
    pytest.param(['--incremental', '--memory-store', '{store}'], 'explicit --events capture', id='incremental_without_a_capture'),
    pytest.param(['--incremental', '--memory-store', '{store}', '--events', '{events}', '--history'], '--history', id='incremental_with_history'),
], )
def test_improve_refuses_incomplete_reuse_flags_before_any_run_folder_exists(world, capsys, flags, message):
    base = world.base
    argv = ['improve', str(base.project), '--agent', 'codex', '--codex', str(base.agent), '--timeout', '30',
            *(flag.format(store=world.store.root, events=world.events) for flag in flags)]
    with pytest.raises(SystemExit) as stop:
        main(argv)
    assert stop.value.code == 2 and message in capsys.readouterr().err
    assert not (base.root / 'state' / 'runs').exists()
