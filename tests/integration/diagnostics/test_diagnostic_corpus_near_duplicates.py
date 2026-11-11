"""Synthetic near-duplicate decisions across protected corpus partitions; no real incident text."""
from copy import deepcopy
from hashlib import sha256

import pytest

from agent_harness_lab.diagnostics.corpus import CorpusStore, development_export
from tests.integration.diagnostics.test_diagnostic_corpus import label, sample

BASE = ('The synthetic build job stopped after the cache step reported a missing artifact in the staging area '
        'and the retry step also reported the same missing artifact before the run ended with a nonzero exit code '
        'while the reviewer inspected the terminal record')


def fixture(name, partition, summary):
    value = sample(name, partition); value['summary'] = summary
    value['evidence'] = [dict(id='terminal', content=name+' evidence', sha256=sha256((name+' evidence').encode()).hexdigest())]
    return value


def reviewed(store, value):
    item = store.enqueue(value)
    for reviewer in ('synthetic-reviewer-one', 'synthetic-reviewer-two'):
        item = store.update(item['case_id'], item['event_sha256'], 'claimed', reviewer=reviewer)
        item = store.update(item['case_id'], item['event_sha256'], 'submitted', reviewer=reviewer, label=label())
    return store.update(item['case_id'], item['event_sha256'], 'adjudicated', reviewer='synthetic-adjudicator',
                        label=label(), reason='Synthetic adjudication')


def pair(tmp_path, held_partition='validation'):
    store = CorpusStore(tmp_path/'corpus')
    fit = reviewed(store, fixture('fit', 'fitting', BASE))
    held = store.enqueue(fixture('held', held_partition, BASE.replace('stopped', 'halted')))
    return store, fit['case_id'], held['case_id']


def export(store): return store.export_development(store.snapshot()['revision_sha256'])


def test_near_copy_with_new_root_needs_a_recorded_decision(tmp_path):
    store, _, _ = pair(tmp_path)
    with pytest.raises(ValueError, match='requires a recorded decision'):
        export(store)


def test_distinct_decision_clears_the_export_and_changes_its_revision(tmp_path):
    store, fit, held = pair(tmp_path)
    before = store.snapshot()['revision_sha256']
    decision = store.decide_near_duplicate(fit, held, 'distinct_incident', 'synthetic-near-reviewer', 'Different synthetic failure site.')
    assert decision['review_authentication'] == 'operator_attested'
    assert store.snapshot()['revision_sha256'] != before
    manifest = export(store)
    assert len(manifest['records']) == 1
    development_export(manifest)


def test_same_lineage_decision_keeps_blocking(tmp_path):
    store, fit, held = pair(tmp_path)
    store.decide_near_duplicate(held, fit, 'same_lineage', 'synthetic-near-reviewer', 'Same synthetic incident reworded.')
    with pytest.raises(ValueError, match='same-lineage'):
        export(store)


def test_decision_rules(tmp_path):
    store, fit, held = pair(tmp_path)
    for args, match in [((fit, fit, 'distinct_incident', 'synthetic-near-reviewer', 'r'), 'distinct'),
                        ((fit, held, 'maybe', 'synthetic-near-reviewer', 'r'), 'distinct'),
                        ((fit, held, 'distinct_incident', 'Synthetic-Author', 'r'), 'separate declared reviewer')]:
        with pytest.raises(ValueError, match=match):
            store.decide_near_duplicate(*args)
    store.decide_near_duplicate(fit, held, 'distinct_incident', 'synthetic-near-reviewer', 'r')
    with pytest.raises(ValueError, match='already recorded'):
        store.decide_near_duplicate(held, fit, 'same_lineage', 'synthetic-near-reviewer', 'r')


def test_unrelated_pair_cannot_be_decided_and_never_blocks(tmp_path):
    store = CorpusStore(tmp_path/'corpus')
    a = reviewed(store, fixture('fit', 'fitting', BASE))
    b = store.enqueue(fixture('held', 'validation', 'A wholly different synthetic outage hit the network layer during deployment of the gateway.'))
    with pytest.raises(ValueError, match='not a near duplicate'):
        store.decide_near_duplicate(a['case_id'], b['case_id'], 'distinct_incident', 'synthetic-near-reviewer', 'r')
    assert len(export(store)['records']) == 1


def test_requeue_invalidates_the_decision(tmp_path):
    store, fit, held = pair(tmp_path)
    store.decide_near_duplicate(fit, held, 'distinct_incident', 'synthetic-near-reviewer', 'Distinct.')
    value = fixture('held', 'validation', BASE.replace('stopped', 'halted'))
    value.update(supersedes=held, rubric_sha256='b'*64)
    store.enqueue(value)
    with pytest.raises(ValueError, match='requires a recorded decision'):
        export(store)


def test_forged_manifest_cannot_drop_or_flip_decisions(tmp_path):
    store, fit, held = pair(tmp_path)
    store.decide_near_duplicate(fit, held, 'distinct_incident', 'synthetic-near-reviewer', 'Distinct.')
    assert export(store)['near_duplicate_decisions'] == []  # the protected partner is not exported
    # A development row near-copies the fitting row; the consumer sees both partitions.
    reviewed_held = reviewed(store, fixture('dev', 'development', BASE.replace('stopped', 'ended')))
    store.decide_near_duplicate(fit, reviewed_held['case_id'], 'distinct_incident', 'synthetic-near-reviewer', 'Distinct.')
    store.decide_near_duplicate(held, reviewed_held['case_id'], 'distinct_incident', 'synthetic-near-reviewer', 'Distinct.')
    manifest = export(store); assert len(manifest['records']) == 2 and len(manifest['near_duplicate_decisions']) == 1
    development_export(manifest)

    def resign(m):
        body = {k: v for k, v in m.items() if k != 'manifest_sha256'}
        from agent_harness_lab.diagnostics.corpus import _digest
        return dict(body, manifest_sha256=_digest(body))
    stripped = resign(dict(manifest, near_duplicate_decisions=[]))
    with pytest.raises(ValueError, match='requires a recorded decision'):
        development_export(stripped)
    flipped = deepcopy(manifest); flipped['near_duplicate_decisions'][0]['decision'] = 'same_lineage'
    with pytest.raises(ValueError, match='same-lineage'):
        development_export(resign(flipped))
