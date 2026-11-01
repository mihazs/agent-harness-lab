"""Synthetic metadata reviews never authenticate native lineage or task outcomes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
if __package__:
    from . import test_source_store as fixtures
    from .test_regression_replay import make_replay
else:
    import test_source_store as fixtures
    from test_regression_replay import make_replay


class LineageSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SourceStoreTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.store.ingest(self.fixture.source, [self.fixture.record])

    def test_snapshot_returns_the_same_bytes_validated_even_when_path_changes(self):
        f = self.fixture; original = f.store.path.read_bytes(); validate = f.store._validate
        def mutate(value):
            f.store.path.write_text('{broken')
            return validate(value)
        with patch.object(f.store, '_validate', side_effect=mutate): state, raw = f.store.read_snapshot()
        self.assertEqual(raw, original); self.assertEqual(state['records'][0]['record'], f.record.to_dict())
        self.assertEqual(f.store.path.read_text(), '{broken')

    def test_snapshot_requires_an_existing_complete_selected_cache(self):
        f = self.fixture; original = f.store.path.read_bytes()
        for raw in (b'{partial', original.replace(b'"schema_version": 4', b'"schema_version": NaN'), b'{"schema_version":4,"schema_version":4}'):
            f.store.path.write_bytes(raw)
            with self.assertRaises(ValueError): f.store.read_snapshot()
        f.store.path.unlink()
        with self.assertRaises(FileNotFoundError): f.store.read_snapshot()
        self.assertFalse(f.store.path.exists())

    def test_snapshot_preserves_exact_scope_validation(self):
        from agent_harness_lab.sources.schema import digest
        from agent_harness_lab.sources.store import SourceStore
        f = self.fixture
        foreign = SourceStore(f.store.path, project_sha256=digest('other'), scope_sha256=f.source.scope_sha256)
        with self.assertRaises(ValueError): foreign.read_snapshot()


@unittest.skipUnless(os.environ.get('AHL_LINEAGE_BASELINE_ROOT'), 'requires explicit frozen baseline; use task test:sources:lineage:compat')
class JournalCompatibilityTests(unittest.TestCase):
    def test_journal_bytes_and_domain_transitions_match_frozen_baseline(self):
        baseline = Path(os.environ['AHL_LINEAGE_BASELINE_ROOT']).resolve()
        import agent_harness_lab
        candidate = Path(agent_harness_lab.__file__).resolve().parent.parent
        script = '''
import hashlib, importlib, json, sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from agent_harness_lab.evaluation.regression_review import ReviewStore
module = importlib.import_module('agent_harness_lab.evaluation.regression_review')
if not hasattr(module, 'datetime'): module = importlib.import_module('agent_harness_lab.private_journal')
class Clock:
    @staticmethod
    def now(tz): return datetime(2000, 1, 1, tzinfo=timezone.utc)
store = ReviewStore(sys.argv[1])
with patch.object(module, 'datetime', Clock):
    result = store.propose(*sys.argv[2:5], 'synthetic-author')
    case = store.case(result['case_id'])
    with store.locked():
        events = store.history(case)
        for index in range(3):
            store.append(case, events, 'attempt_started', 'reported', {'attempt':'attempts/synthetic-'+str(index)})
            store.append(case, events, 'attempt_finished', 'reported', {'status':'blocked'})
        store.append(case, events, 'quarantined', 'quarantined', {'reason':'synthetic_operator_intervention'})
    result = store.show(result['case_id'])
files = {str(p.relative_to(store.root)):hashlib.sha256(p.read_bytes()).hexdigest()
         for p in store.root.rglob('*') if p.is_file() and p.name != '.lock'}
print(json.dumps({'files':files, 'summary':result}, sort_keys=True))
'''
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); inputs = root/'inputs'; inputs.mkdir()
            proposal, draft, spec = make_replay(inputs)
            outputs = []
            for label, version in (('baseline', baseline), ('candidate', candidate)):
                run = subprocess.run([sys.executable,'-c',script,str(root/label),str(proposal),str(draft),str(spec)],
                    cwd=version, env={**os.environ,'PYTHONPATH':''}, capture_output=True,text=True,timeout=10)
                self.assertEqual(run.returncode, 0, run.stderr); outputs.append(json.loads(run.stdout))
            self.assertEqual(outputs[0], outputs[1])


class LineageReviewTests(unittest.TestCase):
    def setUp(self):
        from dataclasses import replace
        from agent_harness_lab.sources.execution import RecordRef
        from agent_harness_lab.sources.lineage import LineageStore
        from agent_harness_lab.sources.schema import digest
        self.fixture = fixtures.SourceStoreTests(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.records = [replace(f.record, native_id_sha256=digest(name), revision_sha256=digest('revision-'+name)) for name in ('a','b','c')]
        f.store.ingest(f.source, self.records)
        self.refs = [RecordRef(r.source_sha256, r.native_id_sha256, r.revision_sha256) for r in self.records]
        self.store = LineageStore(f.root/'lineage', project_sha256=f.source.project_sha256, scope_sha256=f.source.scope_sha256)

    def propose(self, left=0, right=1, relation='same_observation'):
        from agent_harness_lab.sources.lineage import LineageClaim
        claim = LineageClaim(self.refs[left], self.refs[right], relation, 'synthetic-author', (self.refs[left],self.refs[right]))
        path = self.fixture.root/'claim.json'; path.write_text(json.dumps(claim.to_dict()))
        return self.store.propose(self.fixture.store.path, path)

    def review_file(self, proposed, decision='reviewed', **changes):
        from dataclasses import replace
        from agent_harness_lab.sources.lineage import LineageReview
        current = self.store.show(proposed['case_id'])
        review = LineageReview(proposed['case_id'], current['event_sha256'], current['cache_sha256'], decision,
            'synthetic-reviewer','synthetic operator provenance','independent_metadata_review',
            'Explicit review of the frozen synthetic metadata.',(self.refs[0],self.refs[1]),'Synthetic decision.')
        review = replace(review, **changes)
        path = self.fixture.root/'review.json'; path.write_text(json.dumps(review.to_dict()))
        return path

    def review(self, proposed, decision='reviewed', **changes):
        return self.store.review(proposed['case_id'], self.fixture.store.path, self.review_file(proposed,decision,**changes))

    def test_reviewed_edges_remain_scoped_operator_assertions_without_independent_roots(self):
        from unittest.mock import patch
        import socket
        with patch.object(socket,'socket',side_effect=AssertionError('no network')):
            proposed = self.propose(); result = self.review(proposed); queried = self.store.query(self.fixture.store.path)
        self.assertEqual(result['state'],'reviewed'); self.assertEqual(result['review_authentication'],'operator_attested')
        self.assertEqual(len(queried['links']),1); self.assertEqual(len(queried['components']),1)
        self.assertEqual(len(queried['components'][0]['members']),2)
        self.assertIsNone(queried['independent_roots']); self.assertFalse(queried['task_outcomes_verified'])
        self.assertFalse(queried['execution_authorized']); self.assertFalse(queried['publication_allowed'])
        self.assertNotIn(str(self.fixture.root),json.dumps(queried)); self.assertNotIn('synthetic-reviewer',json.dumps(queried))

    def test_cache_change_stales_use_without_rewriting_the_original_decision_or_snapshot(self):
        from dataclasses import replace
        from agent_harness_lab.sources.schema import digest
        proposed = self.propose(); self.review(proposed)
        case = self.store.case(proposed['case_id'])
        original = {p.name:p.read_bytes() for p in (case/'events').iterdir()}
        snapshot = self.store.root/'snapshots'/(proposed['cache_sha256']+'.json'); captured = snapshot.read_bytes()
        f = self.fixture
        f.store.ingest(f.source,[replace(f.record,native_id_sha256=digest('another'),revision_sha256=digest('another-revision'))])
        result = self.store.query(f.store.path)
        self.assertEqual(result['links'],[]); self.assertEqual(result['stale_cases'],[proposed['case_id']])
        self.assertEqual(self.store.show(proposed['case_id'])['state'],'reviewed'); self.assertEqual(snapshot.read_bytes(),captured)
        self.assertEqual(original,{p.name:p.read_bytes() for p in (case/'events').iterdir()})

    def test_stale_head_cache_self_review_and_session_only_approval_fail_closed(self):
        proposed = self.propose()
        for changes in (dict(expected_event_sha256='a'*64),dict(cache_sha256='b'*64),dict(reviewer='SYNTHETIC-AUTHOR'),dict(basis_kind='fresh_session')):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError): self.review(proposed,**changes)
        self.assertEqual(self.store.show(proposed['case_id'])['state'],'proposed')

    def test_superseded_endpoint_is_not_a_current_relation(self):
        from dataclasses import replace
        from agent_harness_lab.sources.schema import digest
        f = self.fixture; before = self.records[0]
        after = replace(before,revision_sha256=digest('new-revision'))
        f.store.ingest(f.source,[after],supersessions=[(before.native_id_sha256,before.revision_sha256,after.revision_sha256)])
        with self.assertRaises(ValueError): self.propose()
        self.assertFalse(self.store.root.exists())

    def test_removed_endpoint_is_not_a_current_relation(self):
        from dataclasses import replace
        from agent_harness_lab.sources.schema import digest
        f = self.fixture; before = self.records[0]
        removed = replace(before,revision_sha256=digest('removed'),state='tombstone')
        f.store.ingest(f.source,[removed],supersessions=[(before.native_id_sha256,before.revision_sha256,removed.revision_sha256)])
        self.refs[0] = type(self.refs[0])(removed.source_sha256,removed.native_id_sha256,removed.revision_sha256)
        with self.assertRaises(ValueError): self.propose()

    def test_competing_current_native_revisions_do_not_gain_freshness_from_timestamps(self):
        from dataclasses import replace
        from agent_harness_lab.sources.schema import digest
        f = self.fixture; f.store.ingest(f.source,[replace(self.records[0],revision_sha256=digest('competing'))])
        with self.assertRaises(ValueError): self.propose()

    def test_foreign_or_missing_endpoint_cannot_be_linked_by_equal_names(self):
        from agent_harness_lab.sources.execution import RecordRef
        known = self.refs[0]; self.refs[0] = RecordRef('f'*64,known.native_id_sha256,known.revision_sha256)
        with self.assertRaises(ValueError): self.propose()
        self.assertFalse(self.store.root.exists())

    def test_cache_scope_and_journal_format_cannot_be_crossed(self):
        from agent_harness_lab.evaluation.regression_review import ReviewStore
        from agent_harness_lab.sources.lineage import LineageStore
        from agent_harness_lab.sources.schema import digest
        proposed = self.propose()
        other = LineageStore(self.store.root,project_sha256=digest('other-project'),scope_sha256=self.fixture.source.scope_sha256)
        with self.assertRaises(ValueError): other.show(proposed['case_id'])
        with self.assertRaises(ValueError): ReviewStore(self.store.root).show(proposed['case_id'])

    def test_transitive_correlation_conflicts_with_independence_without_splitting_components(self):
        first = self.propose(0,1,'derived_from'); self.review(first)
        second = self.propose(1,2,'same_observation'); self.review(second)
        independent = self.propose(0,2,'independent_case'); self.review(independent)
        result = self.store.query(self.fixture.store.path)
        self.assertEqual(len(result['components']),1); self.assertEqual(len(result['components'][0]['members']),3)
        self.assertEqual(result['links'][0]['authentication'],'operator_attested')
        self.assertEqual(result['conflicts'][0]['case_id'],independent['case_id'])
        self.assertIsNone(result['independent_roots'])
        old_revision = result['grouping_revision_sha256']; self.review(first,decision='quarantined')
        result = self.store.query(self.fixture.store.path)
        self.assertEqual(result['conflicts'],[]); self.assertNotEqual(result['grouping_revision_sha256'],old_revision)
        self.assertEqual(self.store.show(first['case_id'])['state'],'quarantined')

    def test_independence_assertions_are_neither_merges_nor_transitive(self):
        self.review(self.propose(0,1,'independent_case')); self.review(self.propose(1,2,'independent_case'))
        result = self.store.query(self.fixture.store.path)
        self.assertEqual(result['components'],[]); self.assertEqual(result['links'],[])
        self.assertEqual(len(result['independence_assertions']),2); self.assertIsNone(result['independent_roots'])

    def test_duplicate_intake_has_one_immutable_winner_under_contention(self):
        from concurrent.futures import ThreadPoolExecutor
        from agent_harness_lab.sources.lineage import LineageClaim
        claim = LineageClaim(self.refs[0],self.refs[1],'same_observation','synthetic-author',(self.refs[0],))
        path = self.fixture.root/'concurrent.json'; path.write_text(json.dumps(claim.to_dict()))
        def propose(_):
            try: return self.store.propose(self.fixture.store.path,path)
            except (ValueError,BlockingIOError): return None
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(propose,range(2)))
        self.assertEqual(sum(value is not None for value in results),1); self.assertEqual(len(self.store.cases()),1)

    def test_source_snapshot_and_case_tampering_do_not_silently_change_grouping(self):
        proposed = self.propose(); self.review(proposed)
        snapshot = self.store.root/'snapshots'/(proposed['cache_sha256']+'.json')
        snapshot.write_bytes(snapshot.read_bytes()+b' ')
        with self.assertRaises(ValueError): self.store.query(self.fixture.store.path)
        with self.assertRaises(ValueError): self.store.show(proposed['case_id'])

    def test_quarantine_can_be_recorded_after_source_disappears(self):
        proposed = self.propose(); self.review(proposed); self.fixture.store.path.unlink()
        self.assertEqual(self.review(proposed,decision='quarantined')['state'],'quarantined')
        self.assertEqual(self.review(proposed,decision='retired')['state'],'retired')
        with self.assertRaises(ValueError): self.review(proposed,decision='quarantined')

    def test_review_capacity_and_snapshot_bounds_precede_any_case_commit(self):
        with patch('agent_harness_lab.sources.lineage.MAX_SNAPSHOT_BYTES',0):
            with self.assertRaises(ValueError): self.propose()
        self.assertEqual(self.store.cases(),[])
        self.assertEqual(self.fixture.store.path.read_bytes(),self.fixture.store.read_snapshot()[1])

    def test_persisted_operator_record_must_bind_the_event_it_follows(self):
        proposed = self.propose(); self.review(proposed)
        path = self.store.case(proposed['case_id'])/'events/0001.json'
        value = json.loads(path.read_text()); value['payload']['review']['expected_event_sha256']='f'*64
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError): self.store.query(self.fixture.store.path)

    def test_directed_derivation_cycles_are_visible_as_conflicts(self):
        self.review(self.propose(0,1,'derived_from')); self.review(self.propose(1,0,'derived_from'))
        result = self.store.query(self.fixture.store.path)
        self.assertTrue(any(row['reason']=='derivation_cycle' for row in result['conflicts']))

    def test_same_observation_aliases_cannot_hide_a_derivation_cycle(self):
        self.review(self.propose(1,2,'same_observation'))
        self.review(self.propose(0,1,'derived_from'))
        control = self.store.query(self.fixture.store.path)
        self.assertEqual(control['conflicts'],[])
        self.review(self.propose(2,0,'derived_from'))
        result = self.store.query(self.fixture.store.path)
        cycles = [row for row in result['conflicts'] if row['reason']=='derivation_cycle']
        self.assertEqual(len(cycles),2)

    def test_explicit_cross_source_review_does_not_turn_equal_native_ids_into_native_proof(self):
        from dataclasses import replace
        from agent_harness_lab.sources.execution import RecordRef
        from agent_harness_lab.sources.schema import digest
        f = self.fixture
        other = replace(f.source,root_sha256=digest('other-synthetic-repository'))
        record = replace(self.records[0],source_sha256=other.source_key)
        f.store.ingest(other,[record]); self.refs[1] = RecordRef(record.source_sha256,record.native_id_sha256,record.revision_sha256)
        proposed = self.propose()
        self.assertEqual(self.store.query(f.store.path)['links'],[])
        self.review(proposed); result = self.store.query(f.store.path)
        self.assertEqual(result['links'][0]['evidence_class'],'reviewed_link')
        self.assertEqual(result['native_freshness'],'unverified'); self.assertIsNone(result['independent_roots'])
        self.assertNotEqual(result['links'][0]['left']['source_sha256'],result['links'][0]['right']['source_sha256'])

    def test_cache_change_during_capture_never_commits_a_claim_or_adopts_orphaned_snapshot(self):
        from agent_harness_lab.sources.store import SourceStore
        f = self.fixture; validate = SourceStore._validate; changed = [False]
        def mutate(store,value):
            state = validate(store,value)
            if not changed[0]: f.store.path.write_text('{partial'); changed[0] = True
            return state
        with patch.object(SourceStore,'_validate',mutate):
            with self.assertRaises(ValueError): self.propose()
        self.assertEqual(self.store.cases(),[])
        self.assertFalse((self.store.root/'cases').exists())

    def test_snapshot_directory_link_cannot_write_outside_private_store(self):
        proposed = self.propose(); folder = self.store.root/'snapshots'
        moved = self.fixture.root/'outside'; folder.rename(moved); folder.symlink_to(moved)
        original = {p.name:p.read_bytes() for p in moved.iterdir()}
        with self.assertRaises(ValueError): self.propose(1,2,'derived_from')
        self.assertEqual(original,{p.name:p.read_bytes() for p in moved.iterdir()})
        self.assertEqual(len(self.store.cases()),1)

    def test_arbitrary_evidence_reference_and_strong_authentication_labels_are_rejected(self):
        from agent_harness_lab.sources.execution import RecordRef
        proposed = self.propose()
        with self.assertRaises(ValueError): self.review(proposed,evidence=(RecordRef('a'*64,'b'*64,'c'*64),))
        with self.assertRaises(ValueError): self.review(proposed,source='verified_native_id')
        self.assertEqual(self.store.show(proposed['case_id'])['state'],'proposed')

    def cli(self, action):
        from agent_harness_lab.cli import main
        f = self.fixture
        prefix = ['sources','lineage','--store',str(self.store.root),'--project-sha256',f.source.project_sha256,
            '--scope-sha256',f.source.scope_sha256]
        with patch('builtins.print') as printed:
            code = main(prefix+action)
        return code,json.loads(printed.call_args.args[0])

    def test_cli_review_query_and_stale_exit_codes_preserve_private_prose(self):
        proposed = self.propose(); path = self.review_file(proposed)
        value = json.loads(path.read_text()); value['basis']='SYNTHETIC_PRIVATE_REVIEW_PROSE'; path.write_text(json.dumps(value))
        code, result = self.cli(['review','--case',proposed['case_id'],'--cache',str(self.fixture.store.path),'--record',str(path)])
        self.assertEqual(code,0); self.assertEqual(result['state'],'reviewed')
        code, result = self.cli(['query','--cache',str(self.fixture.store.path)])
        self.assertEqual(code,0); self.assertNotIn('SYNTHETIC_PRIVATE',json.dumps(result)); self.assertNotIn(str(self.fixture.root),json.dumps(result))
        self.fixture.store.path.write_bytes(self.fixture.store.path.read_bytes()+b' ')
        code, result = self.cli(['query','--cache',str(self.fixture.store.path)])
        self.assertEqual(code,3); self.assertEqual(result['stale_cases'],[proposed['case_id']])

    def test_cli_busy_and_help_do_not_initialize_or_authorize_execution(self):
        proposed = self.propose()
        with self.store.locked():
            code, result = self.cli(['show','--case',proposed['case_id']])
        self.assertEqual(code,3); self.assertEqual(result['status'],'busy'); self.assertFalse(result['execution_authorized'])
        import io
        from contextlib import redirect_stdout
        from agent_harness_lab.cli import main
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as exit:
            main(['sources','lineage','--help'])
        self.assertEqual(exit.exception.code,0); self.assertIn('--scope-sha256',out.getvalue())

    def test_negative_review_still_requires_the_explicit_case_cache_locator(self):
        proposed = self.propose(); path = self.review_file(proposed,decision='quarantined')
        with self.assertRaises(ValueError):
            self.store.review(proposed['case_id'],self.fixture.root/'unrelated-cache.json',path)

    def test_cli_propose_records_only_explicit_known_endpoints(self):
        from agent_harness_lab.sources.lineage import LineageClaim
        claim = LineageClaim(self.refs[0],self.refs[1],'same_observation','synthetic-author',(self.refs[0],))
        path = self.fixture.root/'explicit-claim.json'; path.write_text(json.dumps(claim.to_dict()))
        code, result = self.cli(['propose','--cache',str(self.fixture.store.path),'--claim',str(path)])
        self.assertEqual(code,0); self.assertEqual(result['state'],'proposed')
        self.assertFalse(result['execution_authorized']); self.assertIsNone(result['independent_roots'])

    @unittest.skipUnless(Path('/proc/self/fd').is_dir(), 'requires Linux descriptor fault injection')
    def test_snapshot_fsync_failure_cannot_commit_a_lineage_claim(self):
        sync = os.fsync
        def fail(descriptor):
            if 'snapshots' in Path(os.readlink('/proc/self/fd/'+str(descriptor))).parts:
                raise OSError('SYNTHETIC_FSYNC_FAILURE')
            return sync(descriptor)
        with patch('os.fsync',side_effect=fail):
            with self.assertRaises(OSError): self.propose()
        self.assertEqual(self.store.cases(),[])

    def test_concurrent_positive_reviews_cannot_overwrite_the_first_decision(self):
        from concurrent.futures import ThreadPoolExecutor
        proposed = self.propose(); path = self.review_file(proposed)
        def review(_):
            try: return self.store.review(proposed['case_id'],self.fixture.store.path,path)
            except (ValueError,BlockingIOError): return None
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(review,range(2)))
        self.assertEqual(sum(value is not None for value in results),1)
        self.assertEqual(len(self.store.history(self.store.case(proposed['case_id']))),2)
