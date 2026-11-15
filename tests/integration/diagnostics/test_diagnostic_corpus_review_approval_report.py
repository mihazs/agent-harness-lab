"""Synthetic key-approval status in the corpus queue, export and benchmark reports; an approval is never a gold label."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from agent_harness_lab.diagnostics.corpus import CorpusStore, development_export
from tests.integration.diagnostics.test_diagnostic_corpus import label, sample
from tests.integration.diagnostics.test_diagnostic_corpus_benchmark import Corpus, resign, case
from tests.integration.diagnostics.test_diagnostic_corpus_review_authentication import Approvals

needs_verifier = pytest.mark.skipif(not Path('/usr/bin/ssh-keygen').is_file(), reason='optional OpenSSH verifier unavailable')
RECORDED, PARTIAL, PLAIN = 'key_approvals_recorded', 'partial_key_approvals_recorded', 'operator_attested'


def status(store):
    return {row['case_id']: row['review_approval'] for row in store.snapshot()['items']}


def exported(store):
    return {row['case_id']: row['review_approval'] for row in store.export_development(store.snapshot()['revision_sha256'])['records']}


def plain_review(store, value=None):
    """A pending case taken through two plain submissions and an adjudication without key approvals."""
    value = value or sample(); item = store.enqueue(value)
    for reviewer in ('synthetic-reviewer-one', 'synthetic-reviewer-two'):
        item = store.update(item['case_id'], item['event_sha256'], 'claimed', reviewer=reviewer)
        item = store.update(item['case_id'], item['event_sha256'], 'submitted', reviewer=reviewer, label=label())
    return store.update(item['case_id'], item['event_sha256'], 'adjudicated', reviewer='synthetic-adjudicator', label=label(), reason='Synthetic adjudication basis')


def test_a_case_without_review_events_reports_no_approval_status(tmp_path):
    store = CorpusStore(tmp_path/'corpus'); item = store.enqueue(sample())
    assert status(store) == {item['case_id']: 'no_review_events'}


def test_plain_reviews_report_operator_attestation_in_the_queue_and_the_export(tmp_path):
    store = CorpusStore(tmp_path/'corpus'); item = plain_review(store)
    assert status(store) == {item['case_id']: PLAIN}
    assert exported(store) == {item['case_id']: PLAIN}


@needs_verifier
@pytest.mark.parametrize(('signed', 'expected'), [((True, True, True), RECORDED), ((False, True, True), PARTIAL), ((True, True, False), PARTIAL),
    ((False, False, False), PLAIN)], ids=['all-signed', 'first-unsigned', 'adjudication-unsigned', 'none-signed'])
def test_recorded_key_approvals_are_reported_per_case_and_never_make_a_gold_label(tmp_path, signed, expected):
    approvals = Approvals(tmp_path); approvals.trust()
    item = approvals.review(approvals.store.enqueue(sample()), signed)
    assert status(approvals.store) == {item['case_id']: expected}
    row, = approvals.store.export_development(approvals.store.snapshot()['revision_sha256'])['records']
    assert (row['review_approval'], row['gold_label'], row['label_provenance']) == (expected, False, 'operator_attested_adjudication')


@needs_verifier
def test_benchmark_report_counts_approval_status_per_split_and_states_it_was_not_reverified(tmp_path):
    (tmp_path/'signed').mkdir(); approvals = Approvals(tmp_path/'signed'); approvals.trust()
    approvals.review(approvals.store.enqueue(case('signed-fit', 'fitting')[0]))
    plain_review(approvals.store, case('plain-fit', 'fitting')[0]); plain_review(approvals.store, case('plain-development')[0])
    env = Corpus(tmp_path); manifest = approvals.store.export_development(approvals.store.snapshot()['revision_sha256'])
    completed = env.run_cli(env.write(manifest))
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report['review_approval_counts'] == dict(train={RECORDED: 1, PLAIN: 1}, development={PLAIN: 1}, calibration={}, heldout={})
    assert report['review_approval_verification'] == 'not_performed'
    assert (report['gold_labels'], report['classifier_selection_allowed'], report['certification']) == (0, False, 'not_issued')
    assert any('not re-verified' in text for text in report['corpus_limitations'])


def test_benchmark_report_counts_plain_reviews_as_operator_attested(tmp_path):
    env = Corpus(tmp_path); env.adjudicate(*case('plain-fit', 'fitting'))
    completed = env.run_cli(env.write(env.store.export_development(env.store.snapshot()['revision_sha256'])))
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)['review_approval_counts']['train'] == {PLAIN: 1}


def _remove(row):
    del row['review_approval']


@pytest.mark.parametrize('forge', [pytest.param(_remove, id='missing'), pytest.param(lambda row: row.update(review_approval='verified'), id='unknown-value'),
    pytest.param(lambda row: row.update(review_approval='no_review_events'), id='no-review-events'),
    pytest.param(lambda row: row.update(review_approval=True), id='boolean'), pytest.param(lambda row: row.update(review_approval=None), id='null')])
def test_an_export_row_with_a_missing_or_forged_approval_status_is_rejected(tmp_path, forge):
    store = CorpusStore(tmp_path/'corpus'); plain_review(store)
    manifest = deepcopy(store.export_development(store.snapshot()['revision_sha256'])); forge(manifest['records'][0])
    with pytest.raises(ValueError):
        development_export(resign(manifest))
