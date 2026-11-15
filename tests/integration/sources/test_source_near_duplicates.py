"""Lexical near-duplicate candidates are review leads: never links, root merges or independence claims."""
import json

import pytest

from agent_harness_lab.sources.execution import RecordRef
from agent_harness_lab.sources.near_duplicates import queue
from agent_harness_lab.sources.schema import digest

BODY = ('The nightly import job failed after the schema migration because the column order changed, '
        'so the loader read the timestamp field as a user identifier and rejected every row in the batch.')


def ref(name, source='one', revision=None):
    return RecordRef(digest('source-'+source), digest('record-'+name), digest(revision or 'revision-'+name))


def pairs(report): return {frozenset((p['left']['native_id_sha256'], p['right']['native_id_sha256'])): p for p in report['pairs']}


def lineage(components=(), independent=(), revision=digest('grouping')):
    return dict(grouping_revision_sha256=revision, components=[dict(members=[r.to_dict() for r in c]) for c in components],
        independence_assertions=[dict(left=a.to_dict(), right=b.to_dict()) for a, b in independent])


class TestQueue:
    def test_copies_across_sources_are_candidates_with_their_content_relation_and_no_text(self):
        a, b, c, d = ref('a'), ref('b', 'two'), ref('c', 'three'), ref('d', 'four')
        report = queue([(a, BODY), (b, BODY), (c, '  '.join(BODY.split())), (d, BODY.replace('rejected', 'dropped', 1))])
        found = pairs(report)
        assert found[frozenset((a.native_id_sha256, b.native_id_sha256))]['content_relation'] == 'exact'
        assert found[frozenset((a.native_id_sha256, c.native_id_sha256))]['content_relation'] == 'normalized'
        near = found[frozenset((a.native_id_sha256, d.native_id_sha256))]
        assert near['content_relation'] == 'near' and 800 <= near['similarity_permille'] < 1000
        assert all(p['cross_source'] and p['evidence_class'] == 'heuristic_candidate' and p['queue_status'] == 'pending_review'
                   for p in report['pairs'])
        assert report['counts']['candidate_pairs'] == 6 and report['counts']['exposed_items'] == 4
        assert 'nightly' not in json.dumps(report) and report['text_in_report'] is False

    def test_a_queued_pair_never_creates_a_link_a_root_count_or_authority(self):
        report = queue([(ref('a'), BODY), (ref('b', 'two'), BODY)])
        assert report['independent_roots'] is None and report['task_outcomes_verified'] is False
        assert not report['execution_authorized'] and not report['publication_allowed'] and not report['network_used']
        assert report['lineage_grouping_revision_sha256'] is None and report['text_binding'] == 'caller_declared'
        assert 'links' not in report and 'components' not in report

    def test_negated_and_renumbered_near_matches_stay_queued_but_flagged(self):
        a, b, c = ref('a'), ref('b', 'two'), ref('c', 'three')
        long = BODY+' Operators reran the pipeline twice and confirmed the same outcome on both attempts in the staging cluster.'
        (negated,) = queue([(a, long), (b, long.replace('failed', 'did not fail', 1))])['pairs']
        assert negated['queue_status'] == 'pending_review' and negated['cautions'] == ['negation_differs']
        (renumbered,) = queue([(a, long+' See issue 12.'), (c, long+' See issue 13.')])['pairs']
        assert renumbered['cautions'] == ['identifiers_differ']

    def test_unrelated_boilerplate_and_short_unrelated_text_are_not_queued(self):
        report = queue([(ref('a'), BODY), (ref('b', 'two'), 'Reviewed the changelog entry and approved the release notes.'),
            (ref('c', 'three'), 'ok'), (ref('d', 'four'), 'yes')])
        assert report['pairs'] == [] and report['counts']['candidate_pairs'] == 0

    def test_short_identical_text_is_a_candidate_and_different_short_text_is_not(self):
        same = queue([(ref('a'), 'merged'), (ref('b', 'two'), 'merged'), (ref('c', 'three'), 'closed')])
        assert [p['similarity_permille'] for p in same['pairs']] == [1000]

    def test_revisions_of_one_native_record_are_history_not_duplicates(self):
        report = queue([(ref('a', revision='one'), BODY), (ref('a', revision='two'), BODY), (ref('b', 'two'), BODY)])
        assert report['counts']['same_record_revision_pairs_skipped'] == 1
        assert report['counts']['candidate_pairs'] == 2 and all(p['cross_source'] for p in report['pairs'])

    def test_same_source_pairs_are_marked_not_cross_source(self):
        (pair,) = queue([(ref('a'), BODY), (ref('b'), BODY)])['pairs']
        assert pair['cross_source'] is False

    def test_the_queue_is_independent_of_input_order(self):
        items = [(ref('a'), BODY), (ref('b', 'two'), BODY), (ref('c', 'three'), BODY+' Also noted.')]
        assert queue(items)['queue_sha256'] == queue(list(reversed(items)))['queue_sha256']

    @pytest.mark.parametrize('items, message', [
        pytest.param([(ref('a'), BODY), (ref('a'), BODY)], 'distinct', id='duplicate-revision'),
        pytest.param([(ref('a'), '')], 'token', id='empty-text'),
        pytest.param([(ref('a'), '  \n ')], 'token', id='blank-text'),
        pytest.param([(ref('a'), None)], 'text', id='not-text'),
        pytest.param([(ref('a'), '\ud800')], 'UTF-8', id='lone-surrogate'),
        pytest.param([('a'*64, BODY)], 'exact source revisions', id='not-a-reference')])
    def test_unusable_items_are_rejected_not_skipped(self, items, message):
        with pytest.raises(ValueError, match=message): queue(items)

    @pytest.mark.parametrize('threshold', [0, 1001, 0.8, True, None])
    def test_threshold_must_be_a_permille(self, threshold):
        with pytest.raises(ValueError): queue([(ref('a'), BODY)], threshold_permille=threshold)

    def test_a_lower_declared_threshold_admits_looser_pairs_and_is_recorded(self):
        loose = BODY.replace('rejected every row in the batch', 'stopped')
        items = [(ref('a'), BODY), (ref('b', 'two'), loose)]
        assert queue(items)['pairs'] == []
        report = queue(items, threshold_permille=300)
        assert len(report['pairs']) == 1 and report['threshold_permille'] == 300


class TestReviewState:
    def test_reviewed_components_and_independent_assertions_stay_visible_in_the_queue(self):
        a, b, c, d = ref('a'), ref('b', 'two'), ref('c', 'three'), ref('d', 'four')
        report = queue([(a, BODY), (b, BODY), (c, BODY), (d, BODY)],
            lineage=lineage(components=[(a, b)], independent=[(c, d), (a, c)]))
        status = {k: p['queue_status'] for k, p in pairs(report).items()}
        by = lambda x, y: status[frozenset((x.native_id_sha256, y.native_id_sha256))]
        assert by(a, b) == 'covered_by_reviewed_link' and by(c, d) == 'adjudicated_independent_case'
        assert by(a, c) == 'adjudicated_independent_case' and by(a, d) == by(b, d) == 'pending_review'
        assert report['counts']['statuses'] == dict(pending_review=3, covered_by_reviewed_link=1,
            adjudicated_independent_case=2, conflicting_review=0)
        assert report['counts']['exposed_items'] == 4 and report['lineage_grouping_revision_sha256'] == digest('grouping')

    def test_a_pair_both_linked_and_asserted_independent_is_a_conflicting_review(self):
        a, b = ref('a'), ref('b', 'two')
        (pair,) = queue([(a, BODY), (b, BODY)], lineage=lineage(components=[(a, b)], independent=[(a, b)]))['pairs']
        assert pair['queue_status'] == 'conflicting_review'

    def test_a_review_of_another_revision_does_not_cover_the_current_one(self):
        old, current, other = ref('a', revision='old'), ref('a', revision='new'), ref('b', 'two')
        (pair,) = queue([(current, BODY), (other, BODY)], lineage=lineage(components=[(old, other)]))['pairs']
        assert pair['queue_status'] == 'pending_review'

    def test_review_decisions_change_neither_the_candidate_set_nor_its_similarity(self):
        a, b = ref('a'), ref('b', 'two')
        before = queue([(a, BODY), (b, BODY)])
        after = queue([(a, BODY), (b, BODY)], lineage=lineage(independent=[(a, b)]))
        strip = lambda r: [{k: v for k, v in p.items() if k != 'queue_status'} for p in r['pairs']]
        assert strip(before) == strip(after) and before['queue_sha256'] != after['queue_sha256']
