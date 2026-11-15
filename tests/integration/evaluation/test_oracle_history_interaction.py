"""Saved-history analysis of interaction-contract grades with synthetic grader output, not model behavior."""
from contextlib import ExitStack
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.interaction_contract import contract_sha256
from agent_harness_lab.private_journal import record
from tests.integration.evaluation.test_oracle_history import OracleHistoryTests
from tests.unit.evaluation.test_interaction_contract import ASK, EDIT, REPLY, contract, episode

TASK = 'localization'
SPEC = contract(task_id=TASK, completion=dict(kind='interaction'), required_clarifications=['target'],
                allowed_clarifications=['target'])
PASSING = episode(ASK, REPLY, EDIT)
FAILING = episode(EDIT)  # acts before the required clarification


@pytest.fixture
def cleanups():
    with ExitStack() as stack:
        yield SimpleNamespace(addCleanup=stack.callback)


def bound_history(cleanups, spec=SPEC):
    """A synthetic two-task history whose `localization` grader bundle carries an interaction contract."""
    value = OracleHistoryTests(cleanups.addCleanup)
    folder = value.root / 'export' / 'graders' / TASK
    (folder / 'interaction-contract.json').write_text(json.dumps(spec))
    value.all_bundles[TASK]['digest'] = digest(folder)
    value.install_plan(value.all_tasks)
    return value


def declared(run, evidence_for):
    """Rewrite the typed output of every contract-bound grader execution to declare interaction evidence."""
    def rewrite(*args, **kwargs):
        result = run(*args, **kwargs)
        if args[3] == 'grader' and (Path(args[2]['oracle']) / 'interaction-contract.json').exists():
            envelope = json.loads(result['stdout'])
            evidence = evidence_for(envelope['outcome'])
            if evidence is not None:
                envelope['interaction'] = dict(schema_version=1, profile='interaction-contract-v1',
                                               contract_sha256=contract_sha256(SPEC), evidence=evidence)
            result['stdout'] = json.dumps(envelope)
        return result
    return rewrite


def matching(outcome):
    """Evidence whose recomputed verdict equals the grader's real artifact outcome."""
    return PASSING if outcome else FAILING


def test_history_without_an_interaction_contract_has_no_interaction_section(cleanups):
    value = OracleHistoryTests(cleanups.addCleanup)
    result, _ = value.run_history()
    assert 'interaction' not in value.store.analyze(result['run_id'])


def test_consistent_evidence_reports_interaction_outcomes_per_arm_without_changing_the_decision(cleanups):
    plain = OracleHistoryTests(cleanups.addCleanup)
    baseline_decision = plain.store.analyze(plain.run_history()[0]['run_id'])
    value = bound_history(cleanups)
    result, _ = value.run_history(declared(value.execute, matching))
    analysis = value.store.analyze(result['run_id'])
    arms = analysis['interaction']['families'][TASK]
    assert (arms['baseline']['passed'], arms['candidate']['passed']) == (
        dict(true=0, false=1, unknown=0), dict(true=1, false=0, unknown=0))
    assert (arms['baseline']['findings']['missed_necessary_clarification'],
            arms['candidate']['findings']['missed_necessary_clarification']) == (1, 0)
    assert analysis['interaction']['required_observation_unknown'] is False
    assert analysis['status'] == baseline_decision['status']
    assert (analysis['automatic_promotion'], analysis['publication_allowed']) == (False, False)


def test_grader_that_declares_no_interaction_evidence_blocks_the_recommendation(cleanups):
    value = bound_history(cleanups)
    result, _ = value.run_history(declared(value.execute, lambda outcome: None))
    analysis = value.store.analyze(result['run_id'])
    assert (analysis['status'], analysis['reason']) == ('blocked', 'interaction_observation_incomplete')
    arm = analysis['interaction']['families'][TASK]['candidate']
    assert arm['passed'] == dict(true=0, false=0, unknown=1)
    assert arm['unknowns'] == {'interaction_observation_invalid_or_missing': 1}


def test_grader_outcome_that_contradicts_its_declared_evidence_blocks_the_recommendation(cleanups):
    value = bound_history(cleanups)
    result, _ = value.run_history(declared(value.execute, lambda outcome: FAILING))  # the candidate states a pass
    analysis = value.store.analyze(result['run_id'])
    assert (analysis['status'], analysis['reason']) == ('blocked', 'interaction_observation_incomplete')
    assert analysis['interaction']['families'][TASK]['candidate']['unknowns'] == {'interaction_outcome_inconsistent': 1}


def test_changed_frozen_contract_invalidates_saved_analysis(cleanups):
    value = bound_history(cleanups)
    result, _ = value.run_history(declared(value.execute, matching))
    case = value.store.case(result['run_id'])
    bundle = value.manifest['grader_bundles'][TASK]['digest']
    (case / 'oracles' / bundle / 'interaction-contract.json').write_text(json.dumps(dict(SPEC, endpoint='interaction_boundary')))
    for operation in (value.store.show, value.store.analyze):
        with pytest.raises(ValueError):
            operation(result['run_id'])


def test_contract_that_names_another_task_is_rejected_when_the_run_is_analyzed(cleanups):
    value = bound_history(cleanups, spec=dict(SPEC, task_id='adversarial-input'))
    result, _ = value.run_history()
    with pytest.raises(ValueError, match='invalid frozen interaction contract'):
        value.store.analyze(result['run_id'])


def test_inputs_schema_is_unchanged_by_an_interaction_contract(cleanups):
    value = bound_history(cleanups)
    result, _ = value.run_history(declared(value.execute, matching))
    inputs, _ = record(value.store.case(result['run_id']) / 'inputs.json')
    assert inputs['schema_version'] == 1 and 'interaction' not in json.dumps(inputs)
