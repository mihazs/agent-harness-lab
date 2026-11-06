"""Synthetic Git harness bindings; only owned in-process captures can be compared."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import agent_harness_lab
from agent_harness_lab.evaluation import controller_receipts as owned
from agent_harness_lab.evaluation.integrity import digest as tree_digest, location
from agent_harness_lab.sources import harness_binding
from agent_harness_lab.sources.harness_binding import PROFILE as PROFILE_NAME
from agent_harness_lab.sources.controller import SourceSink
from agent_harness_lab.sources.git import GitExport
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.store import SourceStore


def git_available():
    if not Path('/usr/bin/git').is_file(): return False
    raw = subprocess.check_output(['/usr/bin/git', '--version'], env={'PATH': '/usr/bin:/bin'})
    match = re.match(rb'git version 2\.(\d+)\.', raw)
    return bool(match and int(match[1]) >= 43)


class SyntheticHarnessRepository:
    """A synthetic checkout with a committed `harness/` directory and an owned Git capture."""
    def __init__(self, root, name='repo'):
        self.root = Path(root); self.repo = self.root/name; self.repo.mkdir(parents=True)
        self.env = dict(PATH='/usr/bin:/bin', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
            GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='synthetic@example.invalid',
            GIT_COMMITTER_NAME='Synthetic', GIT_COMMITTER_EMAIL='synthetic@example.invalid',
            GIT_AUTHOR_DATE='2026-01-01T00:00:00Z', GIT_COMMITTER_DATE='2026-01-01T00:00:00Z')
        self.git('init', '-b', 'main')
        (self.repo/'harness').mkdir()
        (self.repo/'harness/AGENTS.md').write_text('SYNTHETIC_HARNESS_INSTRUCTION\n')
        (self.repo/'harness/settings.json').write_text('{"synthetic": true}\n')
        self.git('add', '--all'); self.git('commit', '-m', 'synthetic harness')
        self.declare(name, ['harness/AGENTS.md', 'harness/settings.json'])

    def git(self, *arguments):
        return subprocess.check_output(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
            '-c', 'commit.gpgsign=false', '-c', 'maintenance.auto=false', *arguments], cwd=self.repo, env=self.env,
            stderr=subprocess.DEVNULL)

    def declare(self, name, paths, project='synthetic-project'):
        self.selection = dict(schema_version=1, checkout=name, git_dir=name+'/.git', common_dir=name+'/.git',
            harness_paths=paths, max_commits=10)
        self.source = SourceSpec('local_git', 'git', 'v2', digest(str(self.root.resolve())), digest(project),
            digest(self.selection), identity_provenance='source_declared_id', enabled=True)
        (self.root/'selection.json').write_text(json.dumps(self.selection))
        self.manifest = self.root/'source.json'
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='selection.json')))

    def capture(self, reader=GitExport):
        collector = reader(self.manifest); snapshot = collector.read(self.source)
        return collector, snapshot


def producer_facts(attempt, harness):
    """Stand in for the real kernel producers' private executor/grader facts; the native kernel test runs them."""
    fact = lambda value: hashlib.sha256(repr(value).encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    attempt.data['kernels'] = [
        dict(complete=True, role='executor', ordinal=0, binary_sha256=fact('binary'), image_sha256=fact('image'),
            input_digests={} if harness is None else dict(harness=tree_digest(harness)), observed_at=now),
        dict(complete=True, role='grader', ordinal=1, binary_sha256=fact('binary'), image_sha256=fact('image'),
            input_digests=dict(oracle=fact('oracle'), fixture=fact('fixture')), observed_at=now)]
    attempt.data['grade'] = dict(executor_ordinal=0, grader_ordinal=1, oracle_sha256=fact('oracle'), artifacts_sha256=fact('artifacts'))
    if harness is not None: attempt.locations['0'] = location(harness)
    owned._PENDING[attempt] = attempt._state()


@unittest.skipUnless(git_available(), 'explicit system Git 2.43+ is required')
class HarnessBindingTests(unittest.TestCase):
    def setUp(self):
        expected = os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:
            self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()))
        self.temp = tempfile.TemporaryDirectory(prefix='ahl-harness-binding-'); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = SyntheticHarnessRepository(self.root/'source')
        self.harness = self.fixture.repo/'harness'

    def compare(self, collector, root=None, started_ns=None):
        """Controller-equivalent facts for one synthetic input tree; real issuance is tested natively."""
        root = self.harness if root is None else root
        captured = harness_binding._ISSUED[collector.harness_capture]
        return harness_binding._compare(captured, tree_digest(root), location(root),
            time.monotonic_ns() if started_ns is None else started_ns)

    def test_clean_capture_before_execution_matches_the_same_checkout_tree(self):
        collector, _ = self.fixture.capture()
        self.assertEqual(self.compare(collector), ('verified', None))

    def test_change_or_untracked_file_after_capture_is_a_mismatch_never_verified(self):
        collector, _ = self.fixture.capture()
        (self.harness/'AGENTS.md').write_text('SYNTHETIC_UNCOMMITTED_CHANGE\n')
        self.assertEqual(self.compare(collector), ('mismatch', 'input_tree_differs'))
        (self.harness/'AGENTS.md').write_text('SYNTHETIC_HARNESS_INSTRUCTION\n')
        self.assertEqual(self.compare(collector), ('verified', None))
        (self.harness/'extra.md').write_text('SYNTHETIC_UNTRACKED_ADDITION\n')
        self.assertEqual(self.compare(collector), ('mismatch', 'input_tree_differs'))
        (self.harness/'extra.md').unlink(); (self.harness/'AGENTS.md').chmod(0o755)
        self.assertEqual(self.compare(collector), ('mismatch', 'input_tree_differs'))

    def test_dirty_untracked_or_deleted_selection_at_capture_is_not_a_revision_snapshot(self):
        (self.harness/'AGENTS.md').write_text('SYNTHETIC_DIRTY_AT_CAPTURE\n')
        collector, _ = self.fixture.capture()
        self.assertEqual(self.compare(collector), ('unknown', 'snapshot_not_clean'))
        self.fixture.git('checkout', '--', 'harness/AGENTS.md')
        (self.harness/'new.md').write_text('SYNTHETIC_UNTRACKED_SELECTED\n')
        self.fixture.declare('repo', ['harness/AGENTS.md', 'harness/new.md', 'harness/settings.json'])
        collector, _ = self.fixture.capture()
        self.assertEqual(self.compare(collector), ('unknown', 'snapshot_not_clean'))
        (self.harness/'new.md').unlink(); (self.harness/'settings.json').unlink()
        self.fixture.declare('repo', ['harness/AGENTS.md', 'harness/settings.json'])
        collector, _ = self.fixture.capture()
        self.assertEqual(self.compare(collector), ('unknown', 'snapshot_not_clean'))

    def test_renamed_relocated_or_identical_foreign_checkout_is_unknown(self):
        collector, _ = self.fixture.capture()
        copy = self.root/'copy'; shutil.copytree(self.fixture.repo, copy, symlinks=True)
        self.assertEqual(tree_digest(copy/'harness'), tree_digest(self.harness))
        self.assertEqual(self.compare(collector, copy/'harness'), ('unknown', 'harness_location_not_in_captured_checkout'))
        other = SyntheticHarnessRepository(self.root/'other')
        self.assertEqual(tree_digest(other.repo/'harness'), tree_digest(self.harness))
        self.assertEqual(self.compare(collector, other.repo/'harness'), ('unknown', 'harness_location_not_in_captured_checkout'))
        renamed = self.fixture.repo.rename(self.root/'source/renamed')
        self.assertEqual(self.compare(collector, renamed/'harness'), ('unknown', 'harness_location_not_in_captured_checkout'))

    def test_capture_after_execution_began_and_unobserved_inputs_are_unknown(self):
        started = time.monotonic_ns()
        collector, _ = self.fixture.capture()
        self.assertEqual(self.compare(collector, started_ns=started), ('unknown', 'snapshot_not_captured_before_execution'))
        captured = harness_binding._ISSUED[collector.harness_capture]
        self.assertEqual(harness_binding._compare(captured, None, location(self.harness), time.monotonic_ns()),
            ('unknown', 'controller_harness_input_unobserved'))
        self.assertEqual(harness_binding._compare(captured, tree_digest(self.harness), None, time.monotonic_ns()),
            ('unknown', 'harness_location_not_in_captured_checkout'))

    def test_selection_outside_the_executed_root_is_not_the_executed_harness(self):
        (self.fixture.repo/'ROOT.md').write_text('SYNTHETIC_ROOT_INSTRUCTION\n')
        self.fixture.git('add', '--all'); self.fixture.git('commit', '-m', 'synthetic root')
        self.fixture.declare('repo', ['ROOT.md', 'harness/AGENTS.md', 'harness/settings.json'])
        collector, _ = self.fixture.capture()
        self.assertEqual(self.compare(collector), ('unknown', 'snapshot_exceeds_harness_root'))
        self.assertEqual(self.compare(collector, self.fixture.repo), ('unknown', 'snapshot_does_not_cover_input_tree'))

    def test_unselected_files_in_the_executed_directory_are_a_coverage_gap_not_a_difference(self):
        (self.harness/'unselected.md').write_text('SYNTHETIC_UNSELECTED_AT_CAPTURE\n')
        collector, _ = self.fixture.capture()
        self.assertEqual(self.compare(collector), ('unknown', 'snapshot_does_not_cover_input_tree'))
        (self.harness/'unselected.md').unlink()
        self.assertEqual(self.compare(collector), ('unknown', 'snapshot_does_not_cover_input_tree'))
        (self.harness/'nested').mkdir(); (self.harness/'nested/ignored.txt').write_text('SYNTHETIC_IGNORED\n')
        collector, _ = self.fixture.capture()
        self.assertEqual(self.compare(collector), ('unknown', 'snapshot_does_not_cover_input_tree'))
        (self.harness/'nested/ignored.txt').unlink(); (self.harness/'nested').rmdir()
        collector, _ = self.fixture.capture()
        self.assertEqual(self.compare(collector), ('verified', None))

    def test_direct_issuance_cannot_mint_a_capture_handle(self):
        from agent_harness_lab.sources.git import HarnessSnapshot
        self.assertFalse(hasattr(harness_binding, 'issue'))
        snapshot = HarnessSnapshot(digest('fabricated-head'), ())
        fabricated = {location(self.harness): ('harness', frozenset())}
        with self.assertRaises(ValueError):
            harness_binding._issue(object(), self.fixture.source, snapshot, fabricated, ())
        with self.assertRaises(ValueError):
            harness_binding._register_issuer()
        before = len(harness_binding._ISSUED)
        self.assertEqual(len(harness_binding._ISSUED), before)

    def test_attempt_keeps_harness_location_out_of_hashed_event_data(self):
        sink = SourceSink(self.root/'controller.json', self.fixture.source.project_sha256, digest('synthetic-scope'))
        with owned.session():
            attempt = sink.begin(['result.txt'], time.monotonic()+5)
            self.assertEqual(attempt.locations, {})
            state = attempt._state()
            attempt.locations['0'] = '0'*64
            self.assertNotEqual(attempt._state(), state)
            self.assertNotIn('locations', attempt.data)
            self.assertNotIn('harness_location_sha256', json.dumps(attempt.data))

    def test_imported_journal_forged_handles_and_reader_subclasses_cannot_bind(self):
        collector, snapshot = self.fixture.capture()
        store = SourceStore(self.root/'journal.json', project_sha256=self.fixture.source.project_sha256,
            scope_sha256=self.fixture.source.scope_sha256)
        registry = Registry(); registry.register(Capability('local_git', 'git', 'v2', ('development_artifact',)), GitExport(self.fixture.manifest))
        registry.ingest(self.fixture.source, store, project_sha256=self.fixture.source.project_sha256,
            scope_sha256=self.fixture.source.scope_sha256)
        class Subclass(GitExport): pass
        subclass, _ = self.fixture.capture(Subclass)
        self.assertIsNone(subclass.harness_capture)
        sink = SourceSink(self.root/'controller.json', self.fixture.source.project_sha256, digest('synthetic-scope'))
        with owned.session():
            attempt = sink.begin(['result.txt'], time.monotonic()+5)
            claims = (collector.harness, collector.summary['harness'], store.capture(self.fixture.source), store.graph(self.fixture.source),
                snapshot, harness_binding.HarnessCapture(), {'status': 'verified'}, None, subclass.harness_capture)
            for claim in claims:
                with self.subTest(claim=type(claim).__name__):
                    receipt = harness_binding.bind(attempt, claim)
                    self.assertEqual(receipt['status'], 'unknown')
                    self.assertEqual(receipt['reason'], 'owned_harness_capture_required')
            for attempt_claim in ({'status': 'verified', 'kernels': []}, attempt, None):
                receipt = harness_binding.bind(attempt_claim, collector.harness_capture)
                self.assertEqual((receipt['status'], receipt['reason']), ('unknown', 'owned_controller_capture_required'))
                for key in ('controller_harness_input_sha256', 'harness_snapshot_sha256', 'head_sha256', 'git_source_sha256'):
                    self.assertIsNone(receipt[key])
                self.assertFalse(receipt['authority_granted']); self.assertFalse(receipt['publication_allowed'])
                self.assertEqual(receipt['effective_loading'], 'unverified')

    def test_forked_process_inherits_no_capture_handle(self):
        collector, _ = self.fixture.capture()
        captured = harness_binding._ISSUED[collector.harness_capture]
        with patch.object(harness_binding.os, 'getpid', return_value=captured['pid']+1), owned.session():
            sink = SourceSink(self.root/'controller.json', self.fixture.source.project_sha256, digest('synthetic-scope'))
            attempt = sink.begin(['result.txt'], time.monotonic()+5)
            receipt = harness_binding.bind(attempt, collector.harness_capture)
            self.assertEqual((receipt['status'], receipt['reason']), ('unknown', 'owned_harness_capture_required'))

    def test_binding_receipts_and_private_profile_are_never_public(self):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        if __package__:
            from . import test_benchmark_contracts as fixtures
        else:
            import test_benchmark_contracts as fixtures
        receipt = {**harness_binding._receipt('verified', None), 'publication_allowed': True}
        self.assertFalse(public_export_allowed([{**fixtures.record(), **receipt}]))
        self.assertFalse(public_export_allowed([{**fixtures.record(), 'harness_snapshot_binding': {'publication_allowed': True}}]))

    def sink_finish(self, harness_capture, harness=None, mutate=None, inputs_unchanged=True, scope='synthetic-scope'):
        sink = SourceSink(self.root/(scope+'.json'), self.fixture.source.project_sha256, digest(scope), harness_capture=harness_capture)
        with owned.session():
            attempt = sink.begin(['result.txt'], time.monotonic()+5)
            if mutate is not None: mutate()
            producer_facts(attempt, self.harness if harness is None else harness)
            result = sink.finish(attempt, inputs_unchanged)
            direct = harness_binding.bind(attempt, harness_capture) if result['status'] == 'observed' else None
        return sink, result, direct

    def test_live_sink_reports_the_binding_with_the_verified_session_and_attempt(self):
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        collector, _ = self.fixture.capture()
        sink, result, direct = self.sink_finish(collector.harness_capture)
        self.assertEqual(result['status'], 'observed', result)
        live = result['live_verification']; binding = live['harness_snapshot_binding']
        self.assertEqual((binding['status'], binding['reason']), ('verified', None))
        self.assertEqual(binding, direct)
        fields = {field['name']: field for field in live['verified_execution']['fields']}
        self.assertEqual(fields['attempt']['status'], 'verified')
        self.assertEqual(binding['controller_attempt_sha256'], fields['attempt']['value_sha256'])
        self.assertEqual(binding['controller_harness_input_sha256'], live['controller_harness_input']['sha256'])
        self.assertEqual(binding['harness_snapshot_sha256'], collector.harness.sha256)
        self.assertEqual(binding['head_sha256'], collector.harness.head_sha256)
        self.assertEqual(binding['effective_loading'], 'unverified')
        self.assertFalse(binding['authority_granted']); self.assertFalse(binding['publication_allowed'])
        self.assertNotIn('effective_harness', fields)
        self.assertTrue(all(field.status == 'declared' for item in sink.store.capture(sink.source).identities for field in item.fields))
        journal = sink.store.path.read_text()
        for private in ('harness_snapshot_binding', PROFILE_NAME, collector.harness.sha256, str(self.root)):
            self.assertNotIn(private, journal)
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertFalse(public_export_allowed([{**result, 'publication_allowed': True}]))

    def test_live_sink_without_a_capture_adds_no_binding_and_changes_keep_mismatch_or_unknown(self):
        _, plain, _ = self.sink_finish(None, scope='plain')
        self.assertEqual(plain['status'], 'observed', plain)
        self.assertNotIn('harness_snapshot_binding', plain['live_verification'])
        collector, _ = self.fixture.capture()
        changed = lambda: (self.harness/'AGENTS.md').write_text('SYNTHETIC_UNCOMMITTED_CHANGE\n')
        _, result, _ = self.sink_finish(collector.harness_capture, mutate=changed, scope='changed')
        binding = result['live_verification']['harness_snapshot_binding']
        self.assertEqual((binding['status'], binding['reason']), ('mismatch', 'input_tree_differs'))
        self.fixture.git('checkout', '--', 'harness/AGENTS.md')
        copy = self.root/'copy'; shutil.copytree(self.fixture.repo, copy, symlinks=True)
        _, result, _ = self.sink_finish(collector.harness_capture, harness=copy/'harness', scope='relocated')
        binding = result['live_verification']['harness_snapshot_binding']
        self.assertEqual((binding['status'], binding['reason']), ('unknown', 'harness_location_not_in_captured_checkout'))
        self.assertIsNone(binding['harness_snapshot_sha256'])

    def test_live_sink_reports_unowned_captures_unknown_and_incomplete_attempts_without_binding(self):
        collector, snapshot = self.fixture.capture()
        for index, claim in enumerate((collector.harness, snapshot, harness_binding.HarnessCapture(), {'status': 'verified'})):
            with self.subTest(claim=type(claim).__name__):
                _, result, _ = self.sink_finish(claim, scope='claim-%d' % index)
                self.assertEqual(result['status'], 'observed', result)
                binding = result['live_verification']['harness_snapshot_binding']
                self.assertEqual((binding['status'], binding['reason']), ('unknown', 'owned_harness_capture_required'))
                self.assertIsNone(binding['controller_attempt_sha256'])
        _, result, _ = self.sink_finish(collector.harness_capture, inputs_unchanged=False, scope='incomplete')
        self.assertEqual((result['status'], result['reason']), ('unknown', 'owned_source_capture_incomplete'))
        self.assertNotIn('live_verification', result); self.assertNotIn('harness_snapshot_binding', json.dumps(result))
        late_sink = SourceSink(self.root/'late.json', self.fixture.source.project_sha256, digest('late'), harness_capture=None)
        with owned.session():
            attempt = late_sink.begin(['result.txt'], time.monotonic()+5)
            late, _ = self.fixture.capture(); late_sink.harness_capture = late.harness_capture
            producer_facts(attempt, self.harness)
            binding = late_sink.finish(attempt, True)['live_verification']['harness_snapshot_binding']
        self.assertEqual((binding['status'], binding['reason']), ('unknown', 'snapshot_not_captured_before_execution'))

    def test_git_snapshot_and_summary_stay_unchanged_by_handle_issuance(self):
        first, one = self.fixture.capture(); second, two = self.fixture.capture()
        self.assertEqual(one.sha256, two.sha256)
        self.assertIsNot(first.harness_capture, second.harness_capture)
        self.assertNotIn('harness_capture', json.dumps(first.summary))
        for private in (str(self.root), 'SYNTHETIC_HARNESS', 'harness/AGENTS.md'):
            self.assertNotIn(private, json.dumps(first.summary))


if __name__ == '__main__': unittest.main()
