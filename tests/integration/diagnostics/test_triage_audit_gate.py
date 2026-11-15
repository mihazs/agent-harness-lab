"""The monitor step applies the preregistered audit gate to a private local review over synthetic evidence (#75)."""
import json

import pytest

from agent_harness_lab.diagnostics import audit_corpus, audit_decisions as decisions, audit_fixed, audit_gate
from agent_harness_lab.diagnostics import audit_sampling as audit, audit_stratified, audit_summary, triage_policy
from agent_harness_lab.diagnostics.corpus import CorpusStore
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.promotion.monitor import triage_audit_gate, triage_audit_observation
from tests.integration.diagnostics.test_diagnostic_corpus import label, sample
from tests.unit.diagnostics.test_audit_decisions import FIRST, budget, chain
from tests.unit.diagnostics.test_audit_fixed import policy as fixed_plan
from tests.unit.diagnostics.test_audit_gate import requirement
from tests.unit.diagnostics.test_audit_sampling import frame, plan as grid_plan, unit
from tests.unit.diagnostics.test_audit_stratified import policy as stratified_plan
from tests.unit.diagnostics.test_triage_policy_v2 import policy as triage_fixture

DESIGNS = ['grid', 'fixed', 'stratified']


class Audit:
    """One reviewed unit under one design, with its triage policy, private corpus and review state."""

    def __init__(self, tmp_path, design):
        self.triage = triage_fixture(model_sha256='c' * 64)
        policy_sha = triage_policy.policy_sha256(self.triage)
        self.rubric = dict(schema_version=1, profile='audit-corpus-rubric-v1',
                           markers={'audit-clean': audit.NO_INCIDENT, 'audit-incident': 'tool_misuse'})
        incident = sample()
        incident['rubric_sha256'] = audit_corpus.rubric(self.rubric)
        self.frame = frame([unit(incident['root_id'])])
        self.options = {}
        if design == 'grid':
            self.plan = grid_plan(self.frame, rates=dict(random=1), schema_version=2, profile=audit.PLAN_PROFILE_V2,
                                  policy_sha256=policy_sha, model_sha256=self.triage['model_sha256'])
            self.receipt = audit.draw(self.frame, self.plan)
        else:
            declare = dict(policy_sha256=policy_sha, model_sha256=self.triage['model_sha256'])
            if design == 'fixed':
                self.plan = dict(fixed_plan(self.frame, k=1, budget=1), **declare)
                self.receipt = audit_fixed.draw(self.frame, self.plan)
            else:
                self.plan = dict(stratified_plan(self.frame, {'nonalerted': 1}, 1), **declare)
                self.receipt = audit_stratified.draw(self.frame, self.plan)
            self.options['plan'] = self.plan
        self.store = CorpusStore(tmp_path / 'corpus')
        self.case = self.store.enqueue(incident)
        mapping = [dict(unit_sha256=audit.unit_sha256(incident['root_id']), case_id=self.case['case_id'])]
        self.binding = audit_corpus.prepare(self.receipt, self.frame, self.store.root, mapping, self.rubric, **self.options)

    def gate(self, requirement_value=None, **options):
        return triage_audit_gate(self.triage, self.receipt, self.frame, self.store.root, self.binding, self.rubric,
                                 requirement() if requirement_value is None else requirement_value,
                                 **self.options, **options)

    def observation(self):
        return triage_audit_observation(self.triage, self.receipt, self.frame, self.store.root, self.binding, self.rubric,
                                        **self.options)

    def adjudicate(self):
        event = audit_corpus.inspect(self.receipt, self.frame, self.store.root, self.binding, **self.options)
        case = dict(self.case, event_sha256=event['observations'][0]['event_sha256'])
        answer = label()
        answer['symptoms'] = ['audit-incident']
        for reviewer in ('one', 'two'):
            case = self.store.update(case['case_id'], case['event_sha256'], 'claimed', reviewer=reviewer)
            case = self.store.update(case['case_id'], case['event_sha256'], 'submitted', reviewer=reviewer, label=answer)
        self.store.update(case['case_id'], case['event_sha256'], 'adjudicated', reviewer='three', label=answer, reason='synthetic')


@pytest.fixture(params=DESIGNS)
def audited(request, tmp_path):
    return Audit(tmp_path, request.param)


def test_a_pending_review_leaves_the_gate_inconclusive_with_no_limit_verdicts(audited):
    result = audited.gate(requirement(max_missed_incident_rate=1))
    assert result['status'] == 'inconclusive' and result['criteria'] is None
    assert result['reviewed'] == 0 and result['reasons']


def test_a_reviewed_sample_is_judged_on_the_summary_of_the_same_observation(audited):
    audited.adjudicate()
    demanded = requirement(max_missed_incident_rate=1)
    result = audited.gate(demanded)
    summary = audit_summary.summarize(audited.observation())
    assert summary['status'] == 'estimated' and result['reviewed'] == 1
    assert result == audit_gate.evaluate(summary, audited.triage, demanded)
    assert result['summary_sha256'] == summary['summary_sha256']
    assert (result['policy_sha256'], result['model_sha256']) == (summary['policy_sha256'], audited.triage['model_sha256'])
    assert result['audit_receipt_sha256'] == audited.receipt['receipt_sha256']
    assert result['status'] == 'requirement_met' and result['reasons'] == []


def test_a_broken_limit_is_reported_as_not_met_and_a_changed_requirement_changes_its_hash(audited):
    audited.adjudicate()
    met = audited.gate(requirement(max_missed_incident_rate=1))
    strict = audited.gate(requirement(max_missed_incident_rate=0))
    assert strict['status'] == 'requirement_not_met' and strict['reasons'] == ['missed_incident_rate_not_met']
    assert strict['requirement_sha256'] != met['requirement_sha256']


def test_the_gate_result_is_observation_only_and_private(audited):
    audited.adjudicate()
    result = audited.gate()
    assert (result['action'], result['promotion_authority'], result['certification']) == ('observe_only', 'none', 'not_issued')
    assert not result['automatic_policy_update'] and not result['automatic_rollback'] and not result['publication_allowed']
    assert not public_export_allowed([result])
    assert audit.unit_sha256(audited.frame['units'][0]['unit_id']) not in json.dumps(result)


def test_a_policy_for_another_model_is_refused_before_the_corpus_is_read(audited, tmp_path):
    wrong = triage_fixture(model_sha256='d' * 64)
    with pytest.raises(ValueError, match='policy or model declaration'):
        triage_audit_gate(wrong, audited.receipt, audited.frame, tmp_path / 'missing', audited.binding, audited.rubric,
                          requirement(), **audited.options)


@pytest.mark.parametrize('damage', [
    pytest.param(lambda value: value.update(min_reviewed=0), id='zero-minimum'),
    pytest.param(lambda value: value.update(max_missed_incident_rate=2), id='oversized-maximum'),
    pytest.param(lambda value: value.update(extra=1), id='unknown-field'),
    pytest.param(lambda value: value.pop('require_decision_log'), id='missing-field'),
])
def test_an_invalid_requirement_is_refused_and_yields_no_verdict(audited, damage):
    audited.adjudicate()
    broken = requirement()
    damage(broken)
    with pytest.raises(ValueError, match='audit requirement'):
        audited.gate(broken)


def test_a_decision_log_after_the_first_label_makes_the_gate_invalid_and_a_logged_one_satisfies_it(tmp_path):
    case = Audit(tmp_path, 'grid')
    case.adjudicate()
    needs_log = requirement(max_missed_incident_rate=1, require_decision_log=True)
    assert case.gate(needs_log)['reasons'] == ['decision_log_not_supplied']
    logged = decisions.check(case.receipt, [], FIRST)
    assert case.gate(needs_log, decision_report=logged)['status'] == 'requirement_met'
    late = decisions.check(case.receipt, chain(budget(FIRST, case.receipt['review_budget'], case.receipt['review_budget'])), FIRST)
    result = case.gate(needs_log, decision_report=late)
    assert (result['status'], result['reasons'], result['criteria']) == ('invalid', ['decision_at_or_after_first_label'], None)


def test_a_decision_report_for_another_receipt_is_refused(tmp_path):
    case = Audit(tmp_path, 'grid')
    report = decisions.check(case.receipt, [], FIRST)
    report['receipt_sha256'] = 'e' * 64
    with pytest.raises(ValueError, match='another receipt'):
        case.gate(decision_report=report)
