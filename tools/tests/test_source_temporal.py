"""Synthetic declared-validity fixtures; they prove the as-of contract, not factual accuracy."""
from dataclasses import replace
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


class DeterminismAndTieTests(unittest.TestCase):
    def test_edges_to_other_records_do_not_order_revisions_of_one_record(self):
        facets = [facet('a', 'r1', superseded_by=(rec('x'),)), facet('a', 'r2'), facet('x')]
        result = resolve(facets, '2026-02-01T00:00:00Z')
        self.assertEqual(status(result, 'a', 'r1')[0], 'unknown')
        self.assertEqual(status(result, 'a', 'r2'), ('unknown', ['ambiguous_revisions']))
        self.assertIn('ambiguous_revisions', {row['kind'] for row in result['conflicts']})

    def test_result_is_identical_across_input_order_and_hash_seeds(self):
        import random, subprocess, sys
        facets = [facet(name, revision) for name in 'xybcza' for revision in ('r1', 'r2')]
        baseline = json.dumps(resolve(facets, '2026-02-01T00:00:00Z'), sort_keys=False)
        for seed in range(20):
            shuffled = facets[:]; random.Random(seed).shuffle(shuffled)
            self.assertEqual(json.dumps(resolve(shuffled, '2026-02-01T00:00:00Z'), sort_keys=False), baseline)
        script = ('import json,sys;sys.path.insert(0,%r);import test_source_temporal as t;'
                  'f=[t.facet(n,r) for n in "xybcza" for r in ("r1","r2")];'
                  'print(json.dumps(t.resolve(f,"2026-02-01T00:00:00Z")))') % str(Path(__file__).parent)
        outputs = {subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, check=True,
                                  env={'PYTHONHASHSEED': str(seed), 'PATH': '/usr/bin:/bin'}).stdout for seed in (1, 2, 3)}
        self.assertEqual(len(outputs), 1)


class IntervalTests(unittest.TestCase):
    def test_declared_interval_is_half_open_and_offsets_compare_as_instants(self):
        page = facet('page', valid_from='2026-02-01T03:00:00+03:00', valid_until='2026-03-01T00:00:00Z', open_ended=False)
        for at, expected in (('2026-01-31T23:59:59Z', 'not_yet_valid'), ('2026-02-01T00:00:00Z', 'in_force'),
                             ('2026-02-28T21:00:00-03:00', 'expired'), ('2026-03-01T00:00:00Z', 'expired')):
            self.assertEqual(status(resolve([page], at, knowledge='as_known_now'), 'page')[0], expected, at)

    def test_missing_unparseable_and_timezone_less_times_stay_unknown(self):
        cases = {'valid_from_missing': dict(valid_from=None), 'valid_from_unparseable': dict(valid_from='last spring'),
                 'valid_from_timezone_missing': dict(valid_from='2026-01-01'),
                 'valid_until_timezone_missing': dict(valid_until='2026-06-01T00:00:00', open_ended=False),
                 'valid_until_missing': dict(open_ended=False),
                 'conflicting_end_declaration': dict(valid_until='2026-06-01T00:00:00Z', open_ended=True),
                 'empty_or_inverted_interval': dict(valid_until='2025-06-01T00:00:00Z', open_ended=False)}
        for reason, values in cases.items():
            result = resolve([facet('page', **values)], '2026-02-01T00:00:00Z', knowledge='as_known_now')
            self.assertEqual(status(result, 'page'), ('unknown', [reason]))
        for knowledge_time, reason in ((None, 'recorded_at_missing'), ('2026-01-01T00:00', 'recorded_at_timezone_missing'),
                                       ('soon', 'recorded_at_unparseable')):
            result = resolve([facet('page', recorded_at=knowledge_time)], '2026-02-01T00:00:00Z')
            self.assertEqual(status(result, 'page'), ('unknown', [reason]))
            self.assertEqual(status(resolve([facet('page', recorded_at=knowledge_time)], '2026-02-01T00:00:00Z',
                                            knowledge='as_known_now'), 'page')[0], 'in_force')

    def test_query_instant_and_contract_inputs_are_rejected_not_guessed(self):
        for at in ('2026-02-01T00:00:00', '2026-02-01', None, 'now'):
            with self.assertRaises(ValueError): resolve([facet('page')], at)
        with self.assertRaises(ValueError): resolve([facet('page')], '2026-02-01T00:00:00Z', knowledge='latest')
        with self.assertRaises(ValueError): resolve([facet('page'), facet('page')], '2026-02-01T00:00:00Z')
        with self.assertRaises(ValueError): TemporalFacet(rec('page'), decision_status='final')
        with self.assertRaises(ValueError): TemporalFacet(rec('page'), valid_from='2026\n')
        with self.assertRaises(ValueError): TemporalFacet(rec('page'), supersedes=(rec('a'), rec('a')))
        with self.assertRaises(ValueError): resolve([facet('page')], '2026-02-01T00:00:00Z', revisions=[['x']*4])

    def test_decisions_and_self_declared_lifecycle(self):
        at = '2026-02-01T00:00:00Z'
        for decision, expected in (('proposed', 'not_adopted'), ('rejected', 'not_adopted'), ('accepted', 'in_force'),
                                   ('deprecated', 'expired'), ('superseded', 'superseded')):
            self.assertEqual(status(resolve([facet('adr', decision_status=decision)], at), 'adr')[0], expected)
        later = facet('adr', recorded_at='2026-03-01T00:00:00Z', decision_status='deprecated')
        self.assertEqual(status(resolve([later], at, knowledge='as_known_now'), 'adr'),
                         ('unknown', ['lifecycle_effective_time_unknown']))

    def test_facet_round_trip_and_order_independent_input_fingerprint(self):
        first = facet('a', supersedes=(rec('b'),)); second = facet('b')
        self.assertEqual(TemporalFacet.parse(json.loads(json.dumps(first.to_dict()))), first)
        at = '2026-02-01T00:00:00Z'
        self.assertEqual(resolve([first, second], at)['inputs_sha256'], resolve([second, first], at)['inputs_sha256'])
        self.assertNotEqual(resolve([first, second], at)['inputs_sha256'],
                            resolve([first, second], at, knowledge='as_known_now')['inputs_sha256'])


class SupersessionTests(unittest.TestCase):
    def test_future_correction_cannot_rewrite_a_historical_replay(self):
        original = facet('rule')
        correction = facet('fix', recorded_at='2026-03-01T00:00:00Z', valid_from='2026-01-15T00:00:00Z',
                           supersedes=(rec('rule'),))
        at = '2026-02-01T00:00:00Z'
        replay = resolve([original, correction], at)
        self.assertEqual(status(replay, 'rule'), ('in_force', ['open_ended_by_declaration']))
        self.assertEqual(replay['not_known_at_instant'], [rec('fix').to_dict()])
        self.assertFalse(replay['retrospective'])
        retrospective = resolve([original, correction], at, knowledge='as_known_now')
        self.assertEqual(status(retrospective, 'rule'), ('superseded', ['superseded_by_effective_successor']))
        self.assertEqual(status(retrospective, 'fix')[0], 'in_force')
        self.assertTrue(retrospective['retrospective']); self.assertEqual(retrospective['not_known_at_instant'], [])

    def test_one_sided_declarations_and_successors_not_yet_effective(self):
        old = facet('adr-1', superseded_by=(rec('adr-2'),)); new = facet('adr-2', valid_from='2026-03-01T00:00:00Z')
        self.assertEqual(status(resolve([old, new], '2026-04-01T00:00:00Z'), 'adr-1')[0], 'superseded')
        early = resolve([old, new], '2026-02-01T00:00:00Z')
        self.assertEqual(status(early, 'adr-1'), ('in_force', ['open_ended_by_declaration', 'successor_not_adopted_or_not_yet_effective']))
        self.assertEqual(status(early, 'adr-2')[0], 'not_yet_valid')
        proposed = replace(new, valid_from='2026-01-01T00:00:00Z', decision_status='proposed')
        self.assertEqual(status(resolve([old, proposed], '2026-04-01T00:00:00Z'), 'adr-1')[0], 'in_force')
        vague = replace(new, valid_from=None)
        self.assertEqual(status(resolve([old, vague], '2026-04-01T00:00:00Z'), 'adr-1'),
                         ('unknown', ['successor_effective_time_unknown']))

    def test_predecessor_declaring_a_successor_unknown_at_the_instant_is_not_in_force(self):
        old = facet('adr-1', superseded_by=(rec('adr-2'),)); new = facet('adr-2', recorded_at='2026-03-01T00:00:00Z')
        self.assertEqual(status(resolve([old, new], '2026-02-01T00:00:00Z'), 'adr-1'),
                         ('unknown', ['successor_not_known_at_instant']))

    def test_cycles_are_reported_unknown_and_never_resolved(self):
        a = facet('a', supersedes=(rec('b'),)); b = facet('b', supersedes=(rec('c'),)); c = facet('c', supersedes=(rec('a'),))
        older = facet('older', superseded_by=(rec('a'),)); loop = facet('loop', supersedes=(rec('loop'),))
        result = resolve([a, b, c, older, loop], '2026-02-01T00:00:00Z')
        for name in 'abc': self.assertEqual(status(result, name), ('unknown', ['supersession_cycle']))
        self.assertEqual(status(result, 'loop'), ('unknown', ['supersession_cycle']))
        self.assertEqual(status(result, 'older'), ('unknown', ['successor_status_unknown']))
        cycles = [c for c in result['conflicts'] if c['kind'] == 'supersession_cycle']
        self.assertEqual(sorted(len(c['members']) for c in cycles), [1, 3])
        self.assertNotIn('in_force', result['counts']); self.assertNotIn('superseded', result['counts'])

    def test_unknown_knowledge_time_of_a_correction_blocks_both_views_differently(self):
        rule = facet('rule'); fix = facet('fix', recorded_at=None, supersedes=(rec('rule'),))
        replay = resolve([rule, fix], '2026-02-01T00:00:00Z')
        self.assertEqual(status(replay, 'rule'), ('unknown', ['successor_status_unknown']))
        self.assertEqual(status(resolve([rule, fix], '2026-02-01T00:00:00Z', knowledge='as_known_now'), 'rule')[0], 'superseded')

    def test_revision_ties_need_explicit_edges_not_arrival_order(self):
        first = facet('page', 'r1'); second = facet('page', 'r2', recorded_at='2026-01-10T00:00:00Z')
        at = '2026-02-01T00:00:00Z'
        tied = resolve([first, second], at)
        self.assertEqual(list(statuses(tied).values()), [('unknown', ['ambiguous_revisions'])]*2)
        self.assertEqual([c['kind'] for c in tied['conflicts']], ['ambiguous_revisions'])
        edge = [SOURCE, digest('page'), rec('page', 'r1').revision_sha256, rec('page', 'r2').revision_sha256]
        ordered = resolve([first, second], at, revisions=[edge])
        self.assertEqual((status(ordered, 'page', 'r1')[0], status(ordered, 'page', 'r2')[0]), ('superseded', 'in_force'))
        before = resolve([first, second], '2026-01-05T00:00:00Z', revisions=[edge])
        self.assertEqual(status(before, 'page', 'r1')[0], 'in_force')
        self.assertEqual(before['not_known_at_instant'], [rec('page', 'r2').to_dict()])

    def test_competing_successors_stay_unknown_until_explicitly_ordered(self):
        old = facet('old'); left = facet('left', supersedes=(rec('old'),)); right = facet('right', supersedes=(rec('old'),))
        result = resolve([old, left, right], '2026-02-01T00:00:00Z')
        self.assertEqual(status(result, 'old')[0], 'superseded')
        for name in ('left', 'right'): self.assertEqual(status(result, name), ('unknown', ['competing_successors']))
        ordered = resolve([old, left, replace(right, supersedes=(rec('old'), rec('left')))], '2026-02-01T00:00:00Z')
        self.assertEqual([status(ordered, n)[0] for n in ('old', 'left', 'right')], ['superseded', 'superseded', 'in_force'])
        self.assertEqual(ordered['conflicts'], [])

    def test_dangling_references_remain_observable(self):
        result = resolve([facet('a', superseded_by=(rec('gone'),)), facet('b', supersedes=(rec('missing'),))], '2026-02-01T00:00:00Z')
        self.assertEqual(status(result, 'a'), ('unknown', ['successor_without_temporal_facet']))
        self.assertEqual(status(result, 'b')[0], 'in_force')
        self.assertEqual(sorted(d['reason'] for d in result['dangling']), ['missing_predecessor', 'missing_successor'])

    def test_output_is_descriptive_private_and_blocked_from_public_export(self):
        with patch.object(socket, 'socket', side_effect=AssertionError('no network')):
            result = resolve([facet('a')], '2026-02-01T00:00:00Z')
        self.assertEqual((result['authority'], result['truth_established'], result['validity_source']), ('none', False, 'author_declared'))
        self.assertFalse(result['task_outcomes_verified'] or result['execution_authorized'] or result['publication_allowed'])
        provenance = dict(source_kind='synthetic', approved_publication=True, source_ref='fixture', derived_from_private=False)
        self.assertFalse(public_export_allowed([dict(result, provenance=provenance)]))
        self.assertFalse(public_export_allowed([dict(result, publication_allowed=True, provenance=provenance)]))


class StoreBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.source = SourceSpec('local_file', 'synthetic', 'v1', digest('root'), digest('project'), digest('scope'),
                                 identity_provenance='source_declared_id', enabled=True)
        self.store = SourceStore(Path(self.temp.name)/'cache.json', project_sha256=self.source.project_sha256,
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
        self.assertEqual(by_revision[digest('r1')][0], 'superseded')
        self.assertEqual(by_revision[digest('r2')], ('unknown', ['successor_without_temporal_facet']))
        self.assertEqual(len(result['cache_sha256']), 64); self.assertEqual(self.store.path.read_bytes(), self.before)

    def test_facets_must_bind_present_journal_revisions(self):
        for target in (self.refs[2], RecordRef(self.source.source_key, digest('page'), digest('absent'))):
            with self.assertRaises(ValueError):
                resolve_store(self.store, [TemporalFacet(target, valid_from='2026-01-01T00:00:00Z')], '2026-02-01T00:00:00Z')


if __name__ == '__main__':
    unittest.main()
