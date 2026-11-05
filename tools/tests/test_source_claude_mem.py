"""Synthetic Claude-Mem v15.9.0 exports; memory rows are separate contextual claims, never task outcomes."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources import claude_mem, hardened
from agent_harness_lab.sources.claude_mem import ClaudeMemExport, MemoryClaim
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.test_results import HostileReport, LIMITS, screen_json

SECRET = 'SYNTHETIC_SECRET_TOKEN'
AT, EPOCH = '2031-04-05T06:07:08.000Z', 1933135628000
EXPORT = dict(
    exportedAt='2031-04-06T00:00:00.000Z', exportedAtEpoch=1933200000000, query='fixture-query', project='fixture-project',
    totalObservations=2, totalSessions=1, totalSummaries=1, totalPrompts=1,
    sessions=[dict(id=7, content_session_id='content-aaa', memory_session_id='memory-aaa', project='fixture-project',
        platform_source='claude', user_prompt='invented opening '+SECRET, started_at=AT, started_at_epoch=EPOCH,
        completed_at=None, completed_at_epoch=None, status='completed')],
    observations=[
        dict(id=11, memory_session_id='memory-aaa', project='fixture-project', text=None, type='discovery',
             title='invented observation title', subtitle=None, facts='["fact '+SECRET+'"]', narrative='invented narrative',
             concepts='["how-it-works"]', files_read='["src/invented.py"]', files_modified='[]', prompt_number=1,
             discovery_tokens=10, created_at=AT, created_at_epoch=EPOCH, rank=-1.5),
        dict(id=12, memory_session_id='memory-aaa', project='fixture-project', type='bugfix', title='second invented',
             narrative='done and passing', created_at=AT, created_at_epoch=EPOCH)],
    summaries=[dict(id=21, memory_session_id='memory-aaa', project='fixture-project', request='invented request',
        investigated='x', learned='y', completed='all tests pass '+SECRET, next_steps=None, files_read=None, files_edited=None,
        notes=None, prompt_number=1, discovery_tokens=5, created_at=AT, created_at_epoch=EPOCH)],
    prompts=[dict(id=31, content_session_id='content-aaa', memory_session_id='memory-aaa', prompt_number=1,
        prompt_text='invented prompt '+SECRET, created_at=AT, created_at_epoch=EPOCH)])


class ClaudeMemSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'exports').mkdir()
        self.write(EXPORT)

    def write(self, value, raw=None, **selection):
        (self.root/'exports/claude-mem.json').write_text(raw if raw is not None else json.dumps(value))
        self.selection = dict(schema_version=1, claude_mem_version='15.9.0', instance='fixture-instance',
            export='exports/claude-mem.json', **selection)
        (self.root/'selection.json').write_text(json.dumps(self.selection))
        self.source = SourceSpec('local_file', 'claude-mem-export', 'v1', digest(str(self.root.resolve())),
            digest('synthetic-project'), digest(self.selection), identity_provenance='source_declared_id', enabled=True)
        self.manifest = self.root/'source.json'
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='selection.json')))
        self.connector = ClaudeMemExport(self.manifest)

    def select(self, **changes):
        """Rewrite the selection and re-derive its scope, so a read gets past the scope check."""
        self.selection.update(changes)
        (self.root/'selection.json').write_text(json.dumps(self.selection))
        self.source = SourceSpec(**dict(self.source.to_dict(), scope_sha256=digest(self.selection)))
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='selection.json')))

    def edited(self, kind, index=0, **fields):
        value = copy.deepcopy(EXPORT)
        value[kind][index].update(fields)
        return value

    def read(self, value=None, **selection):
        if value is not None or selection: self.write(EXPORT if value is None else value, **selection)
        return self.connector.read(self.source)

    def claims(self, kind):
        return [c for c in self.connector.claims if c.claim_type == kind]

    def invoke(self, operation):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = main(['sources', operation, '--manifest', str(self.manifest), '--project-sha256', self.source.project_sha256,
                '--scope-sha256', self.source.scope_sha256, '--cache', str(self.root/'private/cache.json')])
        for text in ('SYNTHETIC_SECRET', 'memory-aaa', 'content-aaa', 'fixture-project', 'fixture-query', 'invented'):
            self.assertNotIn(text, output.getvalue()+error.getvalue())
        return code, output.getvalue()

    def test_each_record_type_becomes_its_own_claim_and_record(self):
        snapshot = self.read()
        summary = self.connector.summary
        self.assertEqual(summary['claims'], dict(observation=2, generated_summary=1, user_prompt=1, memory_session=1, search_hit=4))
        self.assertEqual(sorted({r.record_type for r in snapshot.records}), ['claude_mem_export', 'claude_mem_generated_summary',
            'claude_mem_memory_session', 'claude_mem_observation', 'claude_mem_search_hit', 'claude_mem_user_prompt'])
        self.assertEqual(len(snapshot.records), 10)
        self.assertEqual([c.detail for c in self.claims('observation')], ['discovery', 'bugfix'])
        session, = self.claims('memory_session')
        prompt, = self.claims('user_prompt')
        self.assertEqual(session.content_session_sha256, prompt.content_session_sha256)
        self.assertEqual({c.memory_session_sha256 for c in self.connector.claims if c.claim_type != 'search_hit'},
                         {session.memory_session_sha256})
        self.assertEqual((session.detail, session.platform_source, session.original_at), ('completed', 'claude', '2031-04-05T06:07:08+00:00'))
        # The clean fixture is the baseline every warning test is compared against.
        self.assertEqual(summary['warnings'], ['export_version_unstated', 'hits_not_evidence_of_use', 'query_selected_projection',
                                               'row_project_missing', 'summaries_not_independent'])
        for record in snapshot.records:
            self.assertEqual((record.plane, record.causal_status, record.independence, record.capture_completeness),
                             ('contextual_assertion', 'unknown', 'unknown', 'partial'))

    def test_summaries_are_derived_claims_never_observations_or_independent_cases(self):
        snapshot = self.read()
        summary, = self.claims('generated_summary')
        observations = self.claims('observation')
        self.assertEqual((summary.derivation, summary.synthesizes_multiple_origins), ('session_synthesis', True))
        self.assertTrue(all((o.derivation, o.synthesizes_multiple_origins) == ('observer_generated', False) for o in observations))
        self.assertNotIn(summary.native_id_sha256, {o.native_id_sha256 for o in observations})
        kinds = {r.native_id_sha256: (r.record_type, r.provenance) for r in snapshot.records}
        self.assertEqual(kinds[summary.native_id_sha256], ('claude_mem_generated_summary', 'model_hypothesis'))
        for claim in self.connector.claims:
            self.assertEqual((claim.independent_case, claim.evidence_of_use, claim.task_outcome_verified, claim.native_session_verified),
                             (False, False, False, False))
        with self.assertRaises(ValueError):
            MemoryClaim(**dict(summary.to_dict(), claim_type='observation'))
        with self.assertRaises(ValueError):
            MemoryClaim(**dict(summary.to_dict(), synthesizes_multiple_origins=False))
        with self.assertRaises(ValueError):
            MemoryClaim(**dict(summary.to_dict(), task_outcome_verified=True))
        self.assertTrue({'summaries_not_independent', 'hits_not_evidence_of_use'} <= set(self.connector.summary['warnings']))

    def test_search_hits_are_separate_claims_that_point_at_their_target(self):
        self.read()
        hits = self.claims('search_hit')
        targets = {c.native_id_sha256: c.claim_type for c in self.connector.claims if c.claim_type != 'search_hit'}
        self.assertEqual(sorted(h.target_claim_type for h in hits), ['generated_summary', 'observation', 'observation', 'user_prompt'])
        for hit in hits:
            self.assertEqual(targets[hit.target_native_id_sha256], hit.target_claim_type)
            self.assertEqual((hit.derivation, hit.body_sha256, hit.timestamp_status, hit.evidence_of_use),
                             ('query_projection', None, 'not_applicable', False))
        self.assertEqual([h.detail for h in hits if h.target_claim_type == 'observation'], ['score_reported', None])
        first = {h.native_id_sha256 for h in hits}
        self.read(dict(EXPORT, query='another-query'))
        self.assertFalse(first & {h.native_id_sha256 for h in self.claims('search_hit')})

    def test_profile_is_export_blocked_and_pinned_to_one_upstream_release(self):
        self.read()
        summary = self.connector.summary
        self.assertEqual((summary['profile'], summary['claude_mem_version'], summary['upstream_commit']),
                         ('claude-mem-export-v1', '15.9.0', 'd0968e5d136abc34dcfe1b78acf3726fa84978d8'))
        self.assertEqual((summary['version_attestation'], summary['compatibility'], summary['upstream_shape']),
                         ('caller_declared', 'unverified', 'source_inferred'))
        for key in ('task_outcomes_verified', 'evidence_of_use', 'publication_allowed', 'network_used', 'worker_contacted',
                    'database_opened', 'bodies_stored'):
            self.assertIs(summary[key], False)
        self.assertFalse(public_export_allowed([summary]))
        self.assertFalse(public_export_allowed([dict(summary, publication_allowed=True,
            provenance=dict(source_kind='synthetic', approved_publication=True, source_ref='x', derived_from_private=False))]))

    def test_unknown_or_mismatched_versions_are_refused(self):
        for version in ('15.8.3', '15.9', '', None, 15.9):
            with self.subTest(version=version):
                self.write(EXPORT); self.selection['claude_mem_version'] = version
                (self.root/'selection.json').write_text(json.dumps(self.selection))
                self.source = SourceSpec(**dict(self.source.to_dict(), scope_sha256=digest(self.selection)))
                self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='selection.json')))
                with self.assertRaisesRegex(ValueError, 'unsupported Claude-Mem release'): self.connector.read(self.source)
        for key in ('version', 'claudeMemVersion', 'schemaVersion'):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, 'version mismatch'): self.read(dict(EXPORT, **{key: '16.0.0'}))
        self.read(dict(EXPORT, claudeMemVersion='15.9.0'))
        self.assertEqual(self.connector.summary['version_attestation'], 'export_declared')
        self.assertNotIn('export_version_unstated', self.connector.summary['warnings'])
        with self.assertRaises(ValueError):
            self.connector.read(SourceSpec(**dict(self.source.to_dict(), version='v2')))

    def test_duplicate_ids_are_kept_as_flagged_occurrences(self):
        value = copy.deepcopy(EXPORT)
        value['observations'].append(dict(value['observations'][0], title='edited copy'))
        snapshot = self.read(value)
        observations = self.claims('observation')
        self.assertEqual([o.occurrence for o in observations], [0, 0, 1])
        self.assertEqual(len({o.native_id_sha256 for o in observations}), 3)
        self.assertEqual(len({r.native_id_sha256 for r in snapshot.records}), len(snapshot.records))
        self.assertIn('duplicate_native_id', self.connector.summary['warnings'])
        self.assertIn('declared_total_mismatch', self.connector.summary['warnings'])

    def test_unknown_fields_missing_timestamps_and_rejected_rows_are_explicit(self):
        value = copy.deepcopy(EXPORT)
        value['observations'][0]['invented_column'] = SECRET
        value['observations'][1].pop('created_at'); value['observations'][1].pop('created_at_epoch')
        value['summaries'][0]['created_at'] = '2031-04-05T06:07:08'
        value['prompts'][0]['created_at_epoch'] = EPOCH + 60000
        value['prompts'].append(dict(content_session_id='content-aaa', prompt_text='no id'))
        value['sessions'].append(dict(id=8, content_session_id='', project='fixture-project'))
        value['extraEnvelope'] = 1
        self.read(value)
        summary = self.connector.summary
        self.assertEqual(summary['rows_with_unknown_fields'], dict(observations=1, sessions=0, summaries=0, prompts=0))
        self.assertEqual(summary['rows_rejected'], dict(observations=0, sessions=1, summaries=0, prompts=1))
        self.assertEqual(summary['timestamps'], dict(native=2, missing=1, invalid=1, conflicting=1))
        self.assertTrue({'unknown_row_fields', 'unknown_envelope_fields', 'missing_timestamp', 'invalid_timestamp',
            'conflicting_timestamp', 'row_without_native_identity', 'export_version_unstated'} <= set(summary['warnings']))
        self.assertEqual([c.original_at for c in self.claims('observation')], ['2031-04-05T06:07:08+00:00', None])
        self.assertNotIn(SECRET, json.dumps(summary))

    def test_pinned_migration_columns_are_known_and_any_export_change_is_a_new_capture(self):
        from agent_harness_lab.sources import claude_mem
        columns = dict(content_hash='h', occurrence_count=1, title_norm_key='k', generated_by_model='m', relevance_count=0,
            reinforcement_dates='[]', last_reinforced=None, merged_into_project=None, metadata=None, synced_at=None,
            origin_device_id=None, origin_local_id=None, sync_rev='1')
        value = self.edited('observations', **columns)
        value['summaries'][0].update({k: columns[k] for k in ('merged_into_project', *claude_mem.SYNC)})
        value['prompts'][0].update({k: columns[k] for k in claude_mem.SYNC})
        self.read(value)
        self.assertNotIn('unknown_row_fields', self.connector.summary['warnings'])
        first = self.read(value)
        self.assertEqual(self.read(value).sha256, first.sha256)
        changed = copy.deepcopy(value); changed['observations'][0]['relevance_count'] = 5
        changed['sessions'][0]['completed_at'] = AT
        # Only unmodelled fields changed: the claims are identical, yet the checkpoint must move.
        self.assertNotEqual(self.read(changed).sha256, first.sha256)

    def test_unrecognized_status_prompt_number_and_overflowing_offsets_warn(self):
        value = self.edited('sessions', status='paused')
        value['observations'][0].update(prompt_number='one', created_at='0001-01-01T00:00:00+14:00', created_at_epoch=None)
        self.read(value)
        warnings = self.connector.summary['warnings']
        for warning in ('unrecognized_session_status', 'invalid_prompt_number', 'invalid_timestamp'):
            self.assertIn(warning, warnings)

    def test_scope_platform_and_orphan_warnings(self):
        value = copy.deepcopy(EXPORT)
        value.pop('project')
        value['observations'][0].update(memory_session_id='memory-elsewhere', platform_source=SECRET, type='custom-'+SECRET)
        self.read(value)
        summary = self.connector.summary
        self.assertEqual(summary['project_scope'], 'all_projects')
        self.assertTrue({'export_not_project_scoped', 'memory_session_not_exported', 'unrecognized_platform_source',
            'unrecognized_observation_type'} <= set(summary['warnings']))
        self.assertEqual((self.claims('observation')[0].platform_source, self.claims('observation')[0].detail), ('other', 'unrecognized'))
        self.read(self.edited('observations', project='other-project'))
        self.assertIn('row_outside_export_project', self.connector.summary['warnings'])
        self.read(self.edited('summaries', project='other-project', merged_into_project='another-project'))
        self.assertIn('row_outside_export_project', self.connector.summary['warnings'])

    def test_rows_in_scope_the_way_upstream_matches_projects_do_not_warn(self):
        # Upstream matches project or merged_into_project COLLATE NOCASE against a trimmed key, and fetches sessions
        # as parents by session ID rather than by project.
        merged = self.edited('observations', project='worktree-x', merged_into_project='fixture-project')
        merged['summaries'][0].update(project='worktree-y', merged_into_project='Fixture-Project')
        cases = dict(merged=merged, cased=self.edited('observations', project='Fixture-Project'),
                     padded=dict(EXPORT, project=' fixture-project '), session=self.edited('sessions', project='other-project'))
        for name, value in cases.items():
            with self.subTest(name):
                self.read(value)
                self.assertNotIn('row_outside_export_project', self.connector.summary['warnings'])

    def test_malformed_exports_are_rejected(self):
        bodies = ('', 'not json', '[]', '{"observations": []}', json.dumps(dict(EXPORT, sessions={})),
            json.dumps(dict(EXPORT, prompts=['row'])), '{"observations": [], "observations": []}',
            json.dumps(EXPORT).replace('-1.5', 'NaN'), '﻿'+json.dumps(EXPORT))
        for raw in bodies:
            with self.subTest(raw=raw[:40]):
                self.write(None, raw=raw)
                with self.assertRaises(ValueError): self.connector.read(self.source)
        (self.root/'exports/claude-mem.json').write_bytes(b'{"observations": "\xff"}')
        with self.assertRaises(ValueError): self.connector.read(self.source)

    def test_bounds_abort_before_and_during_decoding(self):
        big = dict(EXPORT, observations=[dict(EXPORT['observations'][1], id=i+1) for i in range(30)])
        cases = ((json.dumps(EXPORT), dict(max_export_bytes=100)), (json.dumps(big), dict(max_rows=10)),
                 (json.dumps(self.edited('sessions', **{f'x{i}': i for i in range(20)})), dict(max_fields=16)),
                 ('{"x": '+'['*40+']'*40+'}', None), (json.dumps(EXPORT), dict(max_json_containers=5)))
        for raw, bound in cases:
            with self.subTest(bound=bound):
                self.write(None, raw=raw, **({'limits': bound} if bound else {}))
                with self.assertRaises(ValueError): self.connector.read(self.source)
        for raw, bound in cases[3:]:
            self.write(None, raw=raw, **({'limits': bound} if bound else {}))
            with patch.object(claude_mem, 'parse', side_effect=AssertionError('decoded')):
                with self.assertRaises(HostileReport): self.connector.read(self.source)
        for bound in (dict(max_rows=0), dict(max_rows=10**9), dict(unknown=1), dict(max_fields=True), []):
            with self.subTest(bound=bound):
                with self.assertRaises(ValueError): self.read(EXPORT, limits=bound)

    def test_unterminated_escaped_strings_are_screened_in_linear_time(self):
        raw = b'{"query":"'+b'\\"'*(512*1024)
        started = time.perf_counter()
        with self.assertRaisesRegex(HostileReport, 'string is unterminated'): screen_json(raw, LIMITS, 'Claude-Mem export')
        self.assertLess(time.perf_counter()-started, 2)
        self.write(None, raw=raw.decode())
        with patch.object(claude_mem, 'parse', side_effect=AssertionError('decoded')):
            with self.assertRaisesRegex(HostileReport, 'unterminated'): self.connector.read(self.source)
        # Escaped quotes and brackets inside a terminated string are still skipped, not counted.
        screen_json(b'{"a": "x\\"[[[{{{\\\\", "b": [1, {"c": "]"}]}', dict(LIMITS, max_json_depth=3), 'Claude-Mem export')

    def test_scalar_values_are_bounded_before_decoding(self):
        def leaves(value):
            children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else None
            return 1 if children is None else sum(leaves(child) for child in children)
        total = leaves(EXPORT)
        # The screen counts exactly the decoded scalars, so the export's own count is the tightest admitted bound.
        for bound, outcome in ((total-1, HostileReport), (total, AssertionError)):
            with self.subTest(bound=bound):
                self.write(EXPORT, limits=dict(max_json_values=bound))
                with patch.object(hardened, 'unique_object', side_effect=AssertionError('decoded')):
                    with self.assertRaises(outcome): self.connector.read(self.source)
        self.assertEqual(len(self.read(EXPORT, limits=dict(max_json_values=total)).records), 10)
        with self.assertRaisesRegex(ValueError, 'only lower'): self.read(EXPORT, limits=dict(max_json_values=10**9))

    def test_selection_paths_and_capture_changes_are_checked(self):
        (self.root/'exports/link.json').symlink_to(self.root/'exports/claude-mem.json')
        (self.root/'exports/original.json').write_text(json.dumps(EXPORT))
        os.link(self.root/'exports/original.json', self.root/'exports/hard.json')
        os.mkfifo(self.root/'exports/fifo.json')
        cases = (('../x.json', ValueError, 'unsafe relative path'), ('/etc/passwd', ValueError, 'unsafe relative path'),
                 ('exports/link.json', ValueError, 'symlink'), ('exports/hard.json', ValueError, 'single-link regular file'),
                 ('exports/fifo.json', ValueError, 'single-link regular file'), ('exports/missing.json', FileNotFoundError, ''))
        for path, error, message in cases:
            with self.subTest(path=path):
                # The scope is re-derived so the read reaches the path checks, not the scope check.
                self.write(EXPORT); self.select(export=path)
                with self.assertRaisesRegex(error, message): self.connector.read(self.source)
        self.write(EXPORT)
        with patch.object(claude_mem, 'regular_sha256', lambda path, limit: '0'*64):
            with self.assertRaisesRegex(ValueError, 'changed during capture'): self.connector.read(self.source)

    def test_cli_preview_import_reimport_and_edit_keep_hashes_only(self):
        code, preview = self.invoke('preview')
        self.assertEqual(code, 0)
        result = json.loads(preview)
        self.assertEqual((result['records'], result['claude_mem_summary']['profile']), (10, 'claude-mem-export-v1'))
        self.assertFalse((self.root/'private').exists())
        code, receipt = self.invoke('import')
        self.assertEqual((code, json.loads(receipt)['imported']), (0, 10))
        raw = (self.root/'private/cache.json').read_text()
        for text in (SECRET, 'memory-aaa', 'content-aaa', 'fixture-project', 'fixture-query', 'invented', 'exports/claude-mem.json'):
            self.assertNotIn(text, raw)
        code, receipt = self.invoke('import')
        self.assertEqual((code, json.loads(receipt)['skipped']), (0, 10))
        status = json.loads(self.invoke('status')[1])
        # An edited observation body is a new revision of the same native claim, not a new claim.
        (self.root/'exports/claude-mem.json').write_text(json.dumps(self.edited('observations', narrative='revised invented')))
        code, receipt = self.invoke('import')
        receipt = json.loads(receipt)
        self.assertEqual((code, receipt['imported'], receipt['skipped']), (0, 2, 8))
        self.assertNotEqual(json.loads(self.invoke('status')[1])['cursor_sha256'], status['cursor_sha256'])
        before = (self.root/'private/cache.json').read_bytes()
        (self.root/'exports/claude-mem.json').write_text('{"observations": [')
        self.assertEqual(self.invoke('import')[0], 2)
        self.assertEqual((self.root/'private/cache.json').read_bytes(), before)

    def test_mismatched_scope_is_blocked_before_the_export_is_read(self):
        opened = []
        actual = claude_mem.read_regular
        def read(path, limit): opened.append(path); return actual(path, limit)
        with patch.object(claude_mem, 'read_regular', side_effect=read):
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                code = main(['sources', 'preview', '--manifest', str(self.manifest), '--project-sha256', digest('other'),
                    '--scope-sha256', self.source.scope_sha256])
        self.assertEqual((code, opened), (2, []))


if __name__ == '__main__':
    unittest.main()
