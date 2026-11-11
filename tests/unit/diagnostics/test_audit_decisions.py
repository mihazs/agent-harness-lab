"""Synthetic contracts for the pre-label audit decision log (#75); timing stays self-declared."""
import pytest

from agent_harness_lab.diagnostics import audit_decisions as decisions, audit_sampling as audit
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from tests.unit.diagnostics.test_audit_sampling import frame, plan, unit

FIRST = '2026-01-09T00:00:00+00:00'


@pytest.fixture(scope='module')
def receipt():
    value = frame([unit(f'u{i}') for i in range(20)])
    return audit.draw(value, plan(value, budget=30))


def chain(*rows):
    out, prev = [], None
    for row in rows:
        out.append(dict(row, prev_sha256=prev)); prev = decisions.entry_sha256(out[-1])
    return out


def budget(time, a, b): return dict(kind='budget_change', time=time, from_budget=a, to_budget=b)
def adapt(time, a, b): return dict(kind='adaptive_sampling', time=time, from_plan_sha256=a, to_plan_sha256=b)


def test_history_ending_at_the_receipt_before_labels_is_logged_and_private(receipt):
    log = chain(budget('2026-01-08T00:30:00+00:00', 10, 20), budget('2026-01-08T00:40:00+00:00', 20, 30),
                adapt('2026-01-08T00:50:00+00:00', 'b' * 64, receipt['plan_sha256']))
    report = decisions.check(receipt, log, FIRST)
    assert (report['status'], report['budget_changes'], report['adaptive_decisions']) == ('logged_before_labels', 2, 1)
    assert report['design_based_estimates_supported'] and report['head_sha256'] == decisions.entry_sha256(log[-1])
    assert not report['publication_allowed'] and not report['automatic_policy_update'] and not public_export_allowed([report])
    assert 'log_completeness_unverified' in report['limitations']


def test_decision_at_or_after_the_first_label_invalidates_design_based_inference(receipt):
    log = chain(budget('2026-01-08T00:30:00+00:00', 10, 30), budget(FIRST, 30, 30))
    report = decisions.check(receipt, log, FIRST)
    assert (report['status'], report['late_entries'], report['design_based_estimates_supported']) == ('invalid', [1], False)


def test_empty_log_claims_only_what_it_records(receipt):
    report = decisions.check(receipt, [], FIRST)
    assert report['entries'] == 0 and 'log_completeness_unverified' in report['limitations']


@pytest.mark.parametrize('mutate', [
    lambda log: log[1].update(prev_sha256='c' * 64),
    lambda log: log[0].update(to_budget=31),
    lambda log: log.reverse(),
    lambda log: log[1].update(time='2026-01-08T00:00:00+00:00'),
    lambda log: log[1].update(from_budget=11),
    lambda log: log[1].update(to_budget=29),
    lambda log: log[0].update(extra=1),
    lambda log: log[0].update(kind='other'),
    lambda log: log[0].update(from_budget=0),
    lambda log: log[0].update(time='2026-01-08T00:30:00'),
])
def test_broken_discontinuous_or_wrong_ending_logs_are_refused(receipt, mutate):
    log = chain(budget('2026-01-08T00:30:00+00:00', 10, 20), budget('2026-01-08T00:40:00+00:00', 20, 30))
    mutate(log)
    with pytest.raises(ValueError):
        decisions.check(receipt, log, FIRST)


def test_plan_history_must_end_at_the_receipt_plan_and_be_continuous(receipt):
    with pytest.raises(ValueError, match='receipt'):
        decisions.check(receipt, chain(adapt('2026-01-08T00:30:00+00:00', 'a' * 64, 'b' * 64)), FIRST)
    with pytest.raises(ValueError, match='discontinuous'):
        decisions.check(receipt, chain(adapt('2026-01-08T00:30:00+00:00', 'a' * 64, 'b' * 64),
                                       adapt('2026-01-08T00:40:00+00:00', 'c' * 64, receipt['plan_sha256'])), FIRST)


def test_tampered_receipt_and_bad_inputs_are_refused(receipt):
    with pytest.raises(ValueError):
        decisions.check(dict(receipt, review_budget=1), [], FIRST)
    with pytest.raises(ValueError):
        decisions.check(receipt, [], 'yesterday')
    with pytest.raises(ValueError):
        decisions.check(receipt, 'log', FIRST)
