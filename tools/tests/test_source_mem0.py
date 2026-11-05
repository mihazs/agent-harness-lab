"""Synthetic Mem0 OSS v2.2.1 exports; memories and their history are scoped contextual claims, never task outcomes."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources import mem0
from agent_harness_lab.sources.hardened import HostileReport
from agent_harness_lab.sources.mem0 import Mem0Claim, Mem0Export
from agent_harness_lab.sources.schema import SourceSpec, digest

SECRET = 'SYNTHETIC_SECRET_TOKEN'
AT = '2031-04-05T06:07:08+00:00'


def item(identity, body, user='user-aaa', **fields):
    return dict(dict(id=identity, memory=body, hash='0'*32, metadata=None, created_at=AT, updated_at=None, user_id=user),
                **fields)


def event(identity, memory, kind, old, new, updated, deleted=False):
    return dict(id=identity, memory_id=memory, old_memory=old, new_memory=new, event=kind, created_at=AT, updated_at=updated,
                is_deleted=deleted, actor_id=None, role='user')


EXPORT = dict(
    exported_at='2031-06-01T00:00:00+00:00', filters=dict(user_id='user-aaa'), show_expired=True,
    memories=dict(results=[
        item('mem-edited', 'invented fact revised '+SECRET, agent_id='agent-aaa', run_id='run-aaa', role='user',
             metadata=dict(category='invented'), updated_at='2031-04-06T00:00:00+00:00'),
        item('mem-tests', 'all tests pass in the invented repo', run_id='run-aaa'),
        item('mem-expired', 'invented expired fact', expiration_date='2031-05-01'),
        item('mem-other', 'another user invented fact', user='user-bbb')]),
    history={
        'mem-edited': [event('h1', 'mem-edited', 'ADD', None, 'invented fact '+SECRET, None),
                       event('h2', 'mem-edited', 'UPDATE', 'invented fact '+SECRET, 'invented fact revised '+SECRET,
                             '2031-04-06T00:00:00+00:00')],
        'mem-tests': [event('h3', 'mem-tests', 'ADD', None, 'all tests pass in the invented repo', None)],
        'mem-expired': [event('h4', 'mem-expired', 'ADD', None, 'invented expired fact', None)],
        'mem-gone': [event('h5', 'mem-gone', 'ADD', None, 'invented deleted fact', None),
                     event('h6', 'mem-gone', 'DELETE', 'invented deleted fact', None, '2031-04-07T00:00:00+00:00', True)],
        'mem-other': [event('h7', 'mem-other', 'ADD', None, 'another user invented fact', None)]})


class Mem0SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'exports').mkdir()
        self.write(EXPORT)

    def write(self, value, raw=None, **selection):
        (self.root/'exports/mem0.json').write_text(raw if raw is not None else json.dumps(value))
        self.selection = dict(dict(schema_version=1, mem0_version='2.2.1', instance='fixture-instance',
                                   export='exports/mem0.json'), **selection)
        (self.root/'selection.json').write_text(json.dumps(self.selection))
        self.source = SourceSpec('local_file', 'mem0-oss-export', 'v1', digest(str(self.root.resolve())),
            digest('synthetic-project'), digest(self.selection), identity_provenance='source_declared_id', enabled=True)
        self.manifest = self.root/'source.json'
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='selection.json')))
        self.connector = Mem0Export(self.manifest)

    def read(self, value=None, **selection):
        if value is not None or selection: self.write(EXPORT if value is None else value, **selection)
        return self.connector.read(self.source)

    def memory(self, identity):
        native = digest(['mem0', 'fixture-instance', 'memory', identity])
        return [c for c in self.connector.claims if c.memory_native_id_sha256 == native]

    def invoke(self, operation):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = main(['sources', operation, '--manifest', str(self.manifest), '--project-sha256', self.source.project_sha256,
                '--scope-sha256', self.source.scope_sha256, '--cache', str(self.root/'private/cache.json')])
        for text in ('SYNTHETIC_SECRET', 'user-aaa', 'agent-aaa', 'run-aaa', 'mem-edited', 'invented'):
            self.assertNotIn(text, output.getvalue()+error.getvalue())
        return code, output.getvalue()

    def test_history_becomes_versions_and_superseded_events_stay_historical(self):
        snapshot = self.read()
        summary = self.connector.summary
        edited = self.memory('mem-edited')
        self.assertEqual([(c.claim_type, c.event, c.sequence, c.status, c.currently_asserted) for c in edited], [
            ('history_event', 'ADD', 0, 'superseded', False), ('history_event', 'UPDATE', 1, 'current', True),
            ('memory', None, None, 'asserted', True)])
        add, update, fact = edited
        self.assertEqual((update.old_sha256, fact.body_sha256), (add.new_sha256, update.new_sha256))
        self.assertEqual((summary['claims'], summary['currently_asserted']), (dict(memory=3, history_event=4), 2))
        self.assertEqual(summary['memories'], dict(asserted=2, expired=1, expiry_unverified=0, deleted=0))
        # An expired memory stays in evidence, marked historical, and leaves the currently-asserted count.
        (head, expired), = [self.memory('mem-expired')]
        self.assertEqual((expired.status, expired.currently_asserted, head.status), ('expired', False, 'historical'))
        self.assertEqual({r.record_type for r in snapshot.records}, {'mem0_memory', 'mem0_history_event', 'mem0_export'})
        self.assertEqual(summary['warnings'], ['edits_not_independent', 'export_version_unstated',
            'history_for_unlisted_memory_excluded', 'memories_are_contextual_claims', 'memory_outside_filter_scope',
            'run_id_not_task_root'])
        for record in snapshot.records:
            self.assertEqual((record.plane, record.causal_status, record.independence, record.capture_completeness),
                             ('contextual_assertion', 'unknown', 'unknown', 'partial'))

    def test_filters_keep_other_scopes_and_unlisted_history_out_by_default(self):
        self.read()
        self.assertEqual((self.memory('mem-other'), self.memory('mem-gone')), ([], []))
        self.assertEqual(self.connector.summary['excluded'],
                         dict(memories_outside_filter=1, history_outside_filter=1, history_unlisted=2))
        for value in (dict(EXPORT, filters=None), dict(EXPORT, filters={}), dict(EXPORT, filters=dict(user_id=' ')),
                      dict(EXPORT, filters=dict(project_id='p')), {k: v for k, v in EXPORT.items() if k != 'filters'}):
            with self.subTest(filters=value.get('filters')):
                with self.assertRaisesRegex(ValueError, 'filters'): self.read(value)
        # A narrower declared filter drops rows its listing could not have returned.
        self.read(dict(EXPORT, filters=dict(user_id='user-aaa', run_id='run-aaa')))
        self.assertEqual((self.connector.summary['excluded']['memories_outside_filter'], self.memory('mem-expired')), (2, []))

    def test_deletions_become_tombstones_only_when_unlisted_history_is_selected(self):
        snapshot = self.read(unlisted_history='include_unverified')
        add, delete = self.memory('mem-gone')
        self.assertEqual([(c.status, c.tombstone, c.scope_status) for c in (add, delete)],
                         [('superseded', False, 'unlisted_unverified'), ('tombstone', True, 'unlisted_unverified')])
        states = {r.native_id_sha256: r.state for r in snapshot.records}
        self.assertEqual((states[add.native_id_sha256], states[delete.native_id_sha256]), ('present', 'tombstone'))
        self.assertEqual((self.connector.summary['tombstones'], self.connector.summary['excluded']['history_outside_filter']), (1, 1))
        self.assertIn('history_scope_unverified', self.connector.summary['warnings'])
        # A listed memory whose history ends in DELETE is marked deleted, never currently asserted.
        value = copy.deepcopy(EXPORT)
        value['history']['mem-tests'].append(event('h8', 'mem-tests', 'DELETE', 'all tests pass in the invented repo', None,
                                                   '2031-04-08T00:00:00+00:00', True))
        self.read(value)
        fact = self.memory('mem-tests')[-1]
        self.assertEqual((fact.status, fact.currently_asserted), ('deleted', False))
        self.assertIn('history_head_mismatch', self.connector.summary['warnings'])

    def test_memories_never_become_outcomes_roots_or_independent_cases(self):
        self.read()
        for claim in self.connector.claims:
            self.assertEqual((claim.run_is_task_root, claim.independent_case, claim.evidence_of_use, claim.task_outcome_verified),
                             (False,)*4)
        fact = self.memory('mem-tests')[-1]
        for field in ('task_outcome_verified', 'run_is_task_root', 'independent_case'):
            with self.subTest(field=field):
                with self.assertRaises(ValueError): Mem0Claim(**dict(fact.to_dict(), **{field: True}))
        with self.assertRaises(ValueError): Mem0Claim(**dict(fact.to_dict(), status='expired'))
        summary = self.connector.summary
        self.assertEqual((summary['task_outcomes_verified'], summary['independent_trials'], summary['compatibility'],
                          summary['platform_v3'], summary['upstream_commit']),
                         (False, None, 'unverified', 'unsupported', '94c3fe9f238f3dbf29c9ce98643bd71eb13077cd'))
        self.assertFalse(public_export_allowed([summary]))
        self.assertFalse(public_export_allowed([dict(summary, publication_allowed=True,
            provenance=dict(source_kind='synthetic', approved_publication=True, source_ref='x', derived_from_private=False))]))

    def test_identity_is_deterministic_hashed_and_instance_scoped(self):
        self.read()
        first = [c.to_dict() for c in self.connector.claims]
        self.read()
        self.assertEqual([c.to_dict() for c in self.connector.claims], first)
        fact = self.memory('mem-edited')[-1]
        self.assertEqual((fact.user_sha256, fact.run_sha256), (digest(['mem0', 'fixture-instance', 'user', 'user-aaa']),
                                                               digest(['mem0', 'fixture-instance', 'run', 'run-aaa'])))
        self.assertEqual(self.memory('mem-tests')[-1].run_sha256, fact.run_sha256)
        self.read(instance='other-instance')
        self.assertFalse({c['native_id_sha256'] for c in first} & {c.native_id_sha256 for c in self.connector.claims})

    def test_unpinned_releases_and_platform_shapes_are_refused(self):
        for version in ('2.2.0', '3.0.0', '', None, 2.2):
            with self.subTest(version=version):
                with self.assertRaisesRegex(ValueError, 'unsupported Mem0 release'): self.read(EXPORT, mem0_version=version)
        with self.assertRaisesRegex(ValueError, 'version mismatch'): self.read(dict(EXPORT, mem0_version='2.1.0'))
        self.read(dict(EXPORT, mem0_version='2.2.1'))
        self.assertEqual(self.connector.summary['version_attestation'], 'export_declared')
        # Platform v3 pagination and a bare list are not the pinned OSS get_all response.
        for memories in (dict(EXPORT['memories'], count=4, next=None), EXPORT['memories']['results']):
            with self.subTest(memories=type(memories).__name__):
                with self.assertRaisesRegex(ValueError, 'pinned OSS'): self.read(dict(EXPORT, memories=memories))
        with self.assertRaises(ValueError): self.connector.read(SourceSpec(**dict(self.source.to_dict(), version='v2')))
        with self.assertRaisesRegex(ValueError, 'selection'): self.read(EXPORT, unlisted_history='include')

    def test_malformed_rows_are_counted_and_chain_breaks_flagged(self):
        value = copy.deepcopy(EXPORT)
        value['memories']['results'] += [dict(id=None, memory='x'), dict(id='mem-tests', memory='duplicate'),
                                          dict(item('mem-new', 'invented new'), extra=1)]
        value['history']['mem-tests'] += [dict(event('h3', 'mem-tests', 'UPDATE', 'x', 'y', None)),
                                          event('h9', 'mem-other', 'UPDATE', 'x', 'y', None)]
        value['history']['mem-edited'][1].update(old_memory='unrelated', updated_at='2030-01-01T00:00:00+00:00', event='MERGE')
        self.read(value)
        summary = self.connector.summary
        self.assertEqual((summary['rows_rejected'], summary['rows_with_unknown_fields']),
                         (dict(memories=2, history=2), dict(memories=1, history=0)))
        self.assertTrue({'duplicate_memory_id', 'memory_without_identity_or_text', 'history_row_rejected',
            'history_chain_break', 'history_out_of_order', 'unrecognized_history_event', 'memory_without_history',
            'unknown_memory_fields'} <= set(summary['warnings']))
        for raw in ('[]', '{"memories": {"results": []}}', '{"a": 1, "a": 2}', '{"x": NaN}',
                    json.dumps(dict(EXPORT, history=dict(m='x'))), json.dumps(dict(EXPORT, show_expired='yes'))):
            with self.subTest(raw=raw[:30]):
                self.write(None, raw=raw)
                with self.assertRaises(ValueError): self.connector.read(self.source)

    def test_bounds_abort_and_limits_only_lower(self):
        # Four memories plus seven history rows plus the export record make twelve admitted rows.
        self.assertEqual(len(self.read(EXPORT, limits=dict(max_rows=12)).records), 8)
        with self.assertRaisesRegex(HostileReport, 'row bound'): self.read(EXPORT, limits=dict(max_rows=11))
        with self.assertRaisesRegex(HostileReport, 'field bound'): self.read(EXPORT, limits=dict(max_fields=4))
        with self.assertRaises(HostileReport): self.read(None, raw='{"x": '+'['*40+']'*40+'}')
        for bound in (dict(max_rows=0), dict(max_rows=10**9), dict(unknown=1)):
            with self.subTest(bound=bound):
                with self.assertRaises(ValueError): self.read(EXPORT, limits=bound)
        self.write(EXPORT)
        with patch.object(mem0, 'regular_sha256', lambda path, limit: '0'*64):
            with self.assertRaisesRegex(ValueError, 'changed during capture'): self.connector.read(self.source)

    def test_cli_preview_import_reimport_and_edit_keep_hashes_only(self):
        code, preview = self.invoke('preview')
        result = json.loads(preview)
        self.assertEqual((code, result['records'], result['mem0_summary']['profile']), (0, 8, 'mem0-oss-export-v1'))
        code, receipt = self.invoke('import')
        self.assertEqual((code, json.loads(receipt)['imported']), (0, 8))
        raw = (self.root/'private/cache.json').read_text()
        for text in (SECRET, 'user-aaa', 'run-aaa', 'mem-edited', 'invented', 'exports/mem0.json'):
            self.assertNotIn(text, raw)
        self.assertEqual(json.loads(self.invoke('import')[1])['skipped'], 8)
        # An edited memory body is a new revision of the same native memory, plus the changed export record.
        value = copy.deepcopy(EXPORT)
        value['memories']['results'][1]['memory'] = 'revised invented'
        (self.root/'exports/mem0.json').write_text(json.dumps(value))
        receipt = json.loads(self.invoke('import')[1])
        self.assertEqual((receipt['imported'], receipt['skipped']), (2, 6))


if __name__ == '__main__':
    unittest.main()
