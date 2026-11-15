"""Synthetic declared-validity fixtures; they prove the as-of contract, not factual accuracy."""
from dataclasses import replace
import json
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources.execution import RecordRef
from agent_harness_lab.sources.schema import EvidenceRecord, SourceSpec, digest
from agent_harness_lab.sources.store import SourceStore
from agent_harness_lab.sources.temporal import TemporalFacet, resolve, resolve_store

SOURCE = digest('synthetic-source')


def rec(name, revision='r1'):
    return RecordRef(SOURCE, digest(name), digest(name+':'+revision))


def facet(name, revision='r1', **values):
    values.setdefault('recorded_at', '2026-01-01T00:00:00Z')
    values.setdefault('valid_from', '2026-01-01T00:00:00Z')
    if 'valid_until' not in values: values.setdefault('open_ended', True)
    return TemporalFacet(rec(name, revision), **values)


def statuses(result):
    return {(r['record']['native_id_sha256'], r['record']['revision_sha256']): (r['status'], r['reasons'])
            for r in result['records']}


def status(result, name, revision='r1'):
    return statuses(result)[rec(name, revision).key[1:]]


class TestDeterminismAndTie:
    def test_edges_to_other_records_do_not_order_revisions_of_one_record(self):
        facets = [facet('a', 'r1', superseded_by=(rec('x'),)), facet('a', 'r2'), facet('x')]
        result = resolve(facets, '2026-02-01T00:00:00Z')
        assert status(result, 'a', 'r1')[0] == 'unknown'
        assert status(result, 'a', 'r2') == ('unknown', ['ambiguous_revisions'])
        assert 'ambiguous_revisions' in {row['kind'] for row in result['conflicts']}

    def test_result_is_identical_across_input_order_and_hash_seeds(self):
        import random, subprocess, sys
        facets = [facet(name, revision) for name in 'xybcza' for revision in ('r1', 'r2')]
        baseline = json.dumps(resolve(facets, '2026-02-01T00:00:00Z'), sort_keys=False)
        for seed in range(20):
            shuffled = facets[:]; random.Random(seed).shuffle(shuffled)
            assert json.dumps(resolve(shuffled, '2026-02-01T00:00:00Z'), sort_keys=False) == baseline
        script = ('import json,sys;sys.path.insert(0,%r);import test_source_temporal as t;'
                  'f=[t.facet(n,r) for n in "xybcza" for r in ("r1","r2")];'
                  'print(json.dumps(t.resolve(f,"2026-02-01T00:00:00Z")))') % str(Path(__file__).parent)
        outputs = {subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, check=True,
                                  env={'PYTHONHASHSEED': str(seed), 'PATH': '/usr/bin:/bin'}).stdout for seed in (1, 2, 3)}
        assert len(outputs) == 1


class TestInterval:
    def test_declared_interval_is_half_open_and_offsets_compare_as_instants(self):
        page = facet('page', valid_from='2026-02-01T03:00:00+03:00', valid_until='2026-03-01T00:00:00Z', open_ended=False)
        for at, expected in (('2026-01-31T23:59:59Z', 'not_yet_valid'), ('2026-02-01T00:00:00Z', 'in_force'),
                             ('2026-02-28T21:00:00-03:00', 'expired'), ('2026-03-01T00:00:00Z', 'expired')):
            assert status(resolve([page], at, knowledge='as_known_now'), 'page')[0] == expected, at

    def test_missing_unparseable_and_timezone_less_times_stay_unknown(self):
        cases = {'valid_from_missing': dict(valid_from=None), 'valid_from_unparseable': dict(valid_from='last spring'),
                 'valid_from_timezone_missing': dict(valid_from='2026-01-01'),
                 'valid_until_timezone_missing': dict(valid_until='2026-06-01T00:00:00', open_ended=False),
                 'valid_until_missing': dict(open_ended=False),
                 'conflicting_end_declaration': dict(valid_until='2026-06-01T00:00:00Z', open_ended=True),
                 'empty_or_inverted_interval': dict(valid_until='2025-06-01T00:00:00Z', open_ended=False)}
        for reason, values in cases.items():
            result = resolve([facet('page', **values)], '2026-02-01T00:00:00Z', knowledge='as_known_now')
            assert status(result, 'page') == ('unknown', [reason])
        for knowledge_time, reason in ((None, 'recorded_at_missing'), ('2026-01-01T00:00', 'recorded_at_timezone_missing'),
                                       ('soon', 'recorded_at_unparseable')):
            result = resolve([facet('page', recorded_at=knowledge_time)], '2026-02-01T00:00:00Z')
            assert status(result, 'page') == ('unknown', [reason])
            assert (status(resolve([facet('page', recorded_at=knowledge_time)], '2026-02-01T00:00:00Z',
                                            knowledge='as_known_now'), 'page')[0] == 'in_force')

    def test_query_instant_and_contract_inputs_are_rejected_not_guessed(self):
        for at in ('2026-02-01T00:00:00', '2026-02-01', None, 'now'):
            with pytest.raises(ValueError): resolve([facet('page')], at)
        with pytest.raises(ValueError): resolve([facet('page')], '2026-02-01T00:00:00Z', knowledge='latest')
        with pytest.raises(ValueError): resolve([facet('page'), facet('page')], '2026-02-01T00:00:00Z')
        with pytest.raises(ValueError): TemporalFacet(rec('page'), decision_status='final')
        with pytest.raises(ValueError): TemporalFacet(rec('page'), valid_from='2026\n')
        with pytest.raises(ValueError): TemporalFacet(rec('page'), supersedes=(rec('a'), rec('a')))
        with pytest.raises(ValueError): resolve([facet('page')], '2026-02-01T00:00:00Z', revisions=[['x']*4])

    def test_decisions_and_self_declared_lifecycle(self):
        at = '2026-02-01T00:00:00Z'
        for decision, expected in (('proposed', 'not_adopted'), ('rejected', 'not_adopted'), ('accepted', 'in_force'),
                                   ('deprecated', 'expired'), ('superseded', 'superseded')):
            assert status(resolve([facet('adr', decision_status=decision)], at), 'adr')[0] == expected
        later = facet('adr', recorded_at='2026-03-01T00:00:00Z', decision_status='deprecated')
        assert status(resolve([later], at, knowledge='as_known_now'), 'adr') == ('unknown', ['lifecycle_effective_time_unknown'])

    def test_facet_round_trip_and_order_independent_input_fingerprint(self):
        first = facet('a', supersedes=(rec('b'),)); second = facet('b')
        assert TemporalFacet.parse(json.loads(json.dumps(first.to_dict()))) == first
        at = '2026-02-01T00:00:00Z'
        assert resolve([first, second], at)['inputs_sha256'] == resolve([second, first], at)['inputs_sha256']
        assert resolve([first, second], at)['inputs_sha256'] != resolve([first, second], at, knowledge='as_known_now')['inputs_sha256']


class TestSupersession:
    def test_future_correction_cannot_rewrite_a_historical_replay(self):
        original = facet('rule')
        correction = facet('fix', recorded_at='2026-03-01T00:00:00Z', valid_from='2026-01-15T00:00:00Z',
                           supersedes=(rec('rule'),))
        at = '2026-02-01T00:00:00Z'
        replay = resolve([original, correction], at)
        assert status(replay, 'rule') == ('in_force', ['open_ended_by_declaration'])
        assert replay['not_known_at_instant'] == [rec('fix').to_dict()]
        assert not replay['retrospective']
        retrospective = resolve([original, correction], at, knowledge='as_known_now')
        assert status(retrospective, 'rule') == ('superseded', ['superseded_by_effective_successor'])
        assert status(retrospective, 'fix')[0] == 'in_force'
        assert retrospective['retrospective']; assert retrospective['not_known_at_instant'] == []

    def test_one_sided_declarations_and_successors_not_yet_effective(self):
        old = facet('adr-1', superseded_by=(rec('adr-2'),)); new = facet('adr-2', valid_from='2026-03-01T00:00:00Z')
        assert status(resolve([old, new], '2026-04-01T00:00:00Z'), 'adr-1')[0] == 'superseded'
        early = resolve([old, new], '2026-02-01T00:00:00Z')
        assert status(early, 'adr-1') == ('in_force', ['open_ended_by_declaration', 'successor_not_adopted_or_not_yet_effective'])
        assert status(early, 'adr-2')[0] == 'not_yet_valid'
        proposed = replace(new, valid_from='2026-01-01T00:00:00Z', decision_status='proposed')
        assert status(resolve([old, proposed], '2026-04-01T00:00:00Z'), 'adr-1')[0] == 'in_force'
        vague = replace(new, valid_from=None)
        assert status(resolve([old, vague], '2026-04-01T00:00:00Z'), 'adr-1') == ('unknown', ['successor_effective_time_unknown'])

    def test_predecessor_declaring_a_successor_unknown_at_the_instant_is_not_in_force(self):
        old = facet('adr-1', superseded_by=(rec('adr-2'),)); new = facet('adr-2', recorded_at='2026-03-01T00:00:00Z')
        assert status(resolve([old, new], '2026-02-01T00:00:00Z'), 'adr-1') == ('unknown', ['successor_not_known_at_instant'])

    def test_cycles_are_reported_unknown_and_never_resolved(self):
        a = facet('a', supersedes=(rec('b'),)); b = facet('b', supersedes=(rec('c'),)); c = facet('c', supersedes=(rec('a'),))
        older = facet('older', superseded_by=(rec('a'),)); loop = facet('loop', supersedes=(rec('loop'),))
        result = resolve([a, b, c, older, loop], '2026-02-01T00:00:00Z')
        for name in 'abc': assert status(result, name) == ('unknown', ['supersession_cycle'])
        assert status(result, 'loop') == ('unknown', ['supersession_cycle'])
        assert status(result, 'older') == ('unknown', ['successor_status_unknown'])
        cycles = [c for c in result['conflicts'] if c['kind'] == 'supersession_cycle']
        assert sorted(len(c['members']) for c in cycles) == [1, 3]
        assert 'in_force' not in result['counts']; assert 'superseded' not in result['counts']

    def test_unknown_knowledge_time_of_a_correction_blocks_both_views_differently(self):
        rule = facet('rule'); fix = facet('fix', recorded_at=None, supersedes=(rec('rule'),))
        replay = resolve([rule, fix], '2026-02-01T00:00:00Z')
        assert status(replay, 'rule') == ('unknown', ['successor_status_unknown'])
        assert status(resolve([rule, fix], '2026-02-01T00:00:00Z', knowledge='as_known_now'), 'rule')[0] == 'superseded'

    def test_revision_ties_need_explicit_edges_not_arrival_order(self):
        first = facet('page', 'r1'); second = facet('page', 'r2', recorded_at='2026-01-10T00:00:00Z')
        at = '2026-02-01T00:00:00Z'
        tied = resolve([first, second], at)
        assert list(statuses(tied).values()) == [('unknown', ['ambiguous_revisions'])]*2
        assert [c['kind'] for c in tied['conflicts']] == ['ambiguous_revisions']
        edge = [SOURCE, digest('page'), rec('page', 'r1').revision_sha256, rec('page', 'r2').revision_sha256]
        ordered = resolve([first, second], at, revisions=[edge])
        assert (status(ordered, 'page', 'r1')[0], status(ordered, 'page', 'r2')[0]) == ('superseded', 'in_force')
        before = resolve([first, second], '2026-01-05T00:00:00Z', revisions=[edge])
        assert status(before, 'page', 'r1')[0] == 'in_force'
        assert before['not_known_at_instant'] == [rec('page', 'r2').to_dict()]

    def test_competing_successors_stay_unknown_until_explicitly_ordered(self):
        old = facet('old'); left = facet('left', supersedes=(rec('old'),)); right = facet('right', supersedes=(rec('old'),))
        result = resolve([old, left, right], '2026-02-01T00:00:00Z')
        assert status(result, 'old')[0] == 'superseded'
        for name in ('left', 'right'): assert status(result, name) == ('unknown', ['competing_successors'])
        ordered = resolve([old, left, replace(right, supersedes=(rec('old'), rec('left')))], '2026-02-01T00:00:00Z')
        assert [status(ordered, n)[0] for n in ('old', 'left', 'right')] == ['superseded', 'superseded', 'in_force']
        assert ordered['conflicts'] == []

    def test_dangling_references_remain_observable(self):
        result = resolve([facet('a', superseded_by=(rec('gone'),)), facet('b', supersedes=(rec('missing'),))], '2026-02-01T00:00:00Z')
        assert status(result, 'a') == ('unknown', ['successor_without_temporal_facet'])
        assert status(result, 'b')[0] == 'in_force'
        assert sorted(d['reason'] for d in result['dangling']) == ['missing_predecessor', 'missing_successor']

    def test_output_is_descriptive_private_and_blocked_from_public_export(self):
        with patch.object(socket, 'socket', side_effect=AssertionError('no network')):
            result = resolve([facet('a')], '2026-02-01T00:00:00Z')
        assert (result['authority'], result['truth_established'], result['validity_source']) == ('none', False, 'author_declared')
        assert not (result['task_outcomes_verified'] or result['execution_authorized'] or result['publication_allowed'])
        provenance = dict(source_kind='synthetic', approved_publication=True, source_ref='fixture', derived_from_private=False)
        assert not public_export_allowed([dict(result, provenance=provenance)])
        assert not public_export_allowed([dict(result, publication_allowed=True, provenance=provenance)])


class TestStaleAfter:
    AT = '2026-02-01T00:00:00Z'

    def freshness(self, result, name='page', revision='r1'):
        row, = [r for r in result['records'] if r['record']['revision_sha256'] == rec(name, revision).revision_sha256]
        return row['freshness'], row['freshness_reason']

    @pytest.mark.parametrize('at, expected', [
        pytest.param('2026-01-31T23:59:59Z', ('current', 'before_declared_stale_after'), id='one-second-before'),
        pytest.param('2026-02-01T00:00:00Z', ('stale', 'at_or_after_declared_stale_after'), id='at-the-instant'),
        pytest.param('2026-02-01T00:00:01Z', ('stale', 'at_or_after_declared_stale_after'), id='after'),
        pytest.param('2026-01-31T21:00:00-03:00', ('stale', 'at_or_after_declared_stale_after'), id='offset-compares-as-instant'),
    ])
    def test_declared_stale_after_is_a_half_open_instant(self, at, expected):
        page = facet('page', stale_after='2026-02-01T00:00:00Z')
        assert self.freshness(resolve([page], at, knowledge='as_known_now')) == expected

    @pytest.mark.parametrize('declared, reason', [
        pytest.param(None, 'stale_after_missing', id='missing'),
        pytest.param('after the next review', 'stale_after_unparseable', id='unparseable'),
        pytest.param('90d', 'stale_after_unparseable', id='duration-is-not-an-instant'),
        pytest.param('2026-06-01', 'stale_after_timezone_missing', id='date-without-offset'),
        pytest.param('2026-06-01T00:00:00', 'stale_after_timezone_missing', id='time-without-offset'),
    ])
    def test_unestablished_staleness_stays_unknown_never_current(self, declared, reason):
        result = resolve([facet('page', stale_after=declared)], self.AT)
        assert self.freshness(result) == ('unknown', reason)
        assert result['freshness_counts'] == {'unknown': 1}

    def test_a_stale_declaration_does_not_change_the_validity_status(self):
        stale = facet('page', stale_after='2026-01-15T00:00:00Z')
        result = resolve([stale], self.AT)
        assert status(result, 'page') == ('in_force', ['open_ended_by_declaration'])
        assert self.freshness(result)[0] == 'stale'

    def test_a_future_stale_after_cannot_make_an_expired_record_in_force(self):
        expired = facet('page', valid_until='2026-01-15T00:00:00Z', stale_after='2027-01-01T00:00:00Z')
        result = resolve([expired], self.AT)
        assert status(result, 'page')[0] == 'expired' and self.freshness(result)[0] == 'current'

    def test_a_reverification_first_known_after_the_instant_does_not_refresh_a_replay(self):
        first = facet('page', 'r1', stale_after='2026-02-01T00:00:00Z')
        second = facet('page', 'r2', recorded_at='2026-03-01T00:00:00Z', stale_after='2026-09-01T00:00:00Z')
        edge = [SOURCE, digest('page'), rec('page', 'r1').revision_sha256, rec('page', 'r2').revision_sha256]
        at = '2026-02-15T00:00:00Z'
        replay = resolve([first, second], at, revisions=[edge])
        assert self.freshness(replay) == ('stale', 'at_or_after_declared_stale_after')
        assert replay['not_known_at_instant'] == [rec('page', 'r2').to_dict()]
        retrospective = resolve([first, second], at, revisions=[edge], knowledge='as_known_now')
        assert self.freshness(retrospective, revision='r2')[0] == 'current'

    def test_freshness_counts_follow_the_rows(self):
        facets = [facet('a', stale_after='2026-01-15T00:00:00Z'), facet('b', stale_after='2026-06-01T00:00:00Z'),
                  facet('c'), facet('d', stale_after='2026-01-01T00:00:00Z')]
        assert resolve(facets, self.AT)['freshness_counts'] == {'current': 1, 'stale': 2, 'unknown': 1}

    def test_stale_after_round_trips_and_changes_the_input_fingerprint(self):
        page = facet('page', stale_after='2026-06-01T00:00:00Z')
        assert TemporalFacet.parse(json.loads(json.dumps(page.to_dict()))) == page
        other = replace(page, stale_after='2026-07-01T00:00:00Z')
        assert resolve([page], self.AT)['inputs_sha256'] != resolve([other], self.AT)['inputs_sha256']

    @pytest.mark.parametrize('value', [
        pytest.param('2026\n', id='control-character'), pytest.param('x'*65, id='oversized'),
        pytest.param('', id='empty'), pytest.param(20260601, id='number')])
    def test_an_unreadable_stale_after_is_rejected_at_construction(self, value):
        with pytest.raises(ValueError): TemporalFacet(rec('page'), stale_after=value)


SCOPE_A, SCOPE_B = digest('scope-a'), digest('scope-b')


class TestDeclaredScope:
    AT = '2026-02-01T00:00:00Z'

    def seen(self, result):
        return {n: statuses(result)[rec(n).key[1:]] for n in ('rule', 'fix') if rec(n).key[1:] in statuses(result)}

    def pair(self):
        rule = facet('rule', scopes=(SCOPE_A, SCOPE_B))
        fix = facet('fix', scopes=(SCOPE_B,), supersedes=(rec('rule'),))
        return [rule, fix]

    def test_a_rule_superseded_for_one_scope_stays_in_force_for_the_other(self):
        for_a = resolve(self.pair(), self.AT, scope=SCOPE_A)
        assert self.seen(for_a) == {'rule': ('in_force', ['open_ended_by_declaration'])}
        assert for_a['out_of_scope'] == [rec('fix').to_dict()]
        for_b = resolve(self.pair(), self.AT, scope=SCOPE_B)
        assert self.seen(for_b) == {'rule': ('superseded', ['superseded_by_effective_successor']),
                                    'fix': ('in_force', ['open_ended_by_declaration'])}
        assert for_b['out_of_scope'] == []

    def test_a_record_outside_the_requested_scope_is_listed_and_never_counted(self):
        result = resolve([facet('rule', scopes=(SCOPE_A,))], self.AT, scope=SCOPE_B)
        assert result['records'] == [] and result['counts'] == {} and result['freshness_counts'] == {}
        assert result['out_of_scope'] == [rec('rule').to_dict()] and result['scope_requested'] is True

    @pytest.mark.parametrize('query', [pytest.param(SCOPE_A, id='scope-requested'), pytest.param(None, id='no-scope-requested')])
    def test_a_record_that_declares_no_scope_is_unknown_for_a_scoped_query_and_unchanged_otherwise(self, query):
        result = resolve([facet('rule')], self.AT, scope=query)
        expected = ('unknown', ['scope_undeclared']) if query else ('in_force', ['open_ended_by_declaration'])
        assert self.seen(result) == {'rule': expected} and result['out_of_scope'] == []

    def test_a_scoped_record_is_unknown_when_no_scope_is_requested(self):
        result = resolve(self.pair(), self.AT)
        assert self.seen(result) == {'rule': ('unknown', ['scope_not_requested']), 'fix': ('unknown', ['scope_not_requested'])}
        assert result['scope_requested'] is False

    def test_a_successor_with_an_undeclared_scope_leaves_the_predecessor_unknown(self):
        rule = facet('rule', scopes=(SCOPE_A,)); fix = facet('fix', supersedes=(rec('rule'),))
        assert self.seen(resolve([rule, fix], self.AT, scope=SCOPE_A)) == {
            'rule': ('unknown', ['successor_status_unknown']), 'fix': ('unknown', ['scope_undeclared'])}

    def test_an_edit_that_changes_the_scope_leaves_the_earlier_revision_unknown(self):
        first = facet('page', 'r1', scopes=(SCOPE_A, SCOPE_B)); second = facet('page', 'r2', scopes=(SCOPE_A,))
        edge = [SOURCE, digest('page'), rec('page', 'r1').revision_sha256, rec('page', 'r2').revision_sha256]
        result = resolve([first, second], self.AT, revisions=[edge], scope=SCOPE_B)
        assert status(result, 'page', 'r1') == ('unknown', ['revision_scope_changed'])
        assert result['out_of_scope'] == [rec('page', 'r2').to_dict()]
        kept = resolve([first, second], self.AT, revisions=[edge], scope=SCOPE_A)
        assert (status(kept, 'page', 'r1')[0], status(kept, 'page', 'r2')[0]) == ('superseded', 'in_force')

    def test_scope_does_not_let_a_later_correction_into_a_historical_replay(self):
        rule = facet('rule', scopes=(SCOPE_B,))
        fix = facet('fix', recorded_at='2026-03-01T00:00:00Z', scopes=(SCOPE_B,), supersedes=(rec('rule'),))
        replay = resolve([rule, fix], self.AT, scope=SCOPE_B)
        assert self.seen(replay) == {'rule': ('in_force', ['open_ended_by_declaration'])}
        assert replay['not_known_at_instant'] == [rec('fix').to_dict()] and replay['out_of_scope'] == []

    def test_a_dangling_reference_from_an_out_of_scope_record_is_not_reported(self):
        result = resolve([facet('rule', scopes=(SCOPE_A,), supersedes=(rec('gone'),))], self.AT, scope=SCOPE_B)
        assert result['dangling'] == [] and result['out_of_scope'] == [rec('rule').to_dict()]

    def test_scope_changes_the_input_fingerprint_and_the_result_is_order_independent(self):
        facets = self.pair()
        assert resolve(facets, self.AT, scope=SCOPE_A)['inputs_sha256'] != resolve(facets, self.AT, scope=SCOPE_B)['inputs_sha256']
        assert resolve(facets, self.AT, scope=SCOPE_B) == resolve(facets[::-1], self.AT, scope=SCOPE_B)

    def test_facet_scopes_round_trip_and_change_the_input_fingerprint(self):
        page = facet('page', scopes=(SCOPE_A, SCOPE_B))
        assert TemporalFacet.parse(json.loads(json.dumps(page.to_dict()))) == page
        assert resolve([page], self.AT, scope=SCOPE_A)['inputs_sha256'] != \
            resolve([replace(page, scopes=(SCOPE_A,))], self.AT, scope=SCOPE_A)['inputs_sha256']

    @pytest.mark.parametrize('value', [
        pytest.param(['a'], id='text-not-a-fingerprint'), pytest.param([SCOPE_A, SCOPE_A], id='duplicate'),
        pytest.param([digest(n) for n in range(33)], id='oversized'), pytest.param([''], id='empty-text'),
        pytest.param(SCOPE_A, id='bare-text-not-a-tuple'), pytest.param([None], id='null-entry'),
        pytest.param([SCOPE_A.upper()], id='upper-case-digest')])
    def test_unreadable_scopes_are_rejected_at_construction(self, value):
        with pytest.raises(ValueError): TemporalFacet(rec('page'), scopes=tuple(value) if isinstance(value, list) else value)

    @pytest.mark.parametrize('query', [pytest.param('scope-a', id='name-not-a-fingerprint'), pytest.param(3, id='number'),
                                       pytest.param('', id='empty'), pytest.param([SCOPE_A], id='list')])
    def test_an_unreadable_scope_query_is_rejected_not_guessed(self, query):
        with pytest.raises(ValueError): resolve([facet('rule')], self.AT, scope=query)


class TestStoreBinding:
    @pytest.fixture(autouse=True)
    def _store(self, tmp_path):
        self.source = SourceSpec('local_file', 'synthetic', 'v1', digest('root'), digest('project'), digest('scope'),
                                 identity_provenance='source_declared_id', enabled=True)
        self.store = SourceStore(tmp_path/'cache.json', project_sha256=self.source.project_sha256,
                                 scope_sha256=self.source.scope_sha256)
        key = self.source.source_key
        self.refs = [RecordRef(key, digest('page'), digest(r)) for r in ('r1', 'r2', 'r3')]
        records = [EvidenceRecord(key, digest('page'), digest(r), digest('body-'+r), 'contextual_assertion', 'page',
                                  '2026-01-01T00:00:00Z', state='tombstone' if r == 'r3' else 'present') for r in ('r1', 'r2', 'r3')]
        self.store.ingest(self.source, records, supersessions=[
            [digest('page'), digest('r1'), digest('r2')], [digest('page'), digest('r2'), digest('r3')]])
        self.before = self.store.path.read_bytes()

    def test_store_revision_edges_apply_and_a_deletion_cannot_leave_a_current_fact(self):
        first = TemporalFacet(self.refs[0], '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', open_ended=True)
        second = TemporalFacet(self.refs[1], '2026-01-10T00:00:00Z', '2026-01-10T00:00:00Z', open_ended=True)
        result = resolve_store(self.store, [first, second], '2026-02-01T00:00:00Z')
        by_revision = {r['record']['revision_sha256']: (r['status'], r['reasons']) for r in result['records']}
        assert by_revision[digest('r1')][0] == 'superseded'
        assert by_revision[digest('r2')] == ('unknown', ['successor_without_temporal_facet'])
        assert len(result['cache_sha256']) == 64; assert self.store.path.read_bytes() == self.before

    def test_facets_must_bind_present_journal_revisions(self):
        for target in (self.refs[2], RecordRef(self.source.source_key, digest('page'), digest('absent'))):
            with pytest.raises(ValueError):
                resolve_store(self.store, [TemporalFacet(target, valid_from='2026-01-01T00:00:00Z')], '2026-02-01T00:00:00Z')

    def test_the_requested_scope_reaches_the_store_bound_resolver(self):
        scope = digest('scope-a')
        first = TemporalFacet(self.refs[0], '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', open_ended=True, scopes=(scope,))
        assert resolve_store(self.store, [first], '2026-02-01T00:00:00Z', scope=scope)['records'][0]['reasons'] == ['successor_without_temporal_facet']
        assert resolve_store(self.store, [first], '2026-02-01T00:00:00Z')['records'][0]['reasons'] == ['scope_not_requested']
        assert resolve_store(self.store, [first], '2026-02-01T00:00:00Z', scope=digest('scope-b'))['out_of_scope']
