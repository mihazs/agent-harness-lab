"""Public synthetic fresh qualification; actual grades, simulated sandbox attestations."""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import evaluator
from agent_harness_lab.private_journal import encoded, record
from agent_harness_lab.evaluation import cohort_requalification as core, cohorts
from agent_harness_lab.evaluation.bank_tools import export_manifest
from agent_harness_lab.evaluation.oracle import identity
from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from tests.integration.evaluation import test_bank_corrections as applications
from tests.integration.evaluation import test_offline_evaluation as offline


class RequalificationFixture(unittest.TestCase):
    """Reusable real controller fixture; the initial bank already has structural v2."""
    def setUp(self):
        self.application = applications.BankCorrectionTests(); self.application.setUp()
        self.addCleanup(self.application.doCleanups)
        f = self.application
        self.root, self.history = f.root, f.history
        self.applied = f.apply(); self.bank = f.bank(self.applied)
        self.runner = evaluator()
        self.before, self.baseline, self.candidate = [self.root / name for name in ('original-D', 'original-B', 'original-C')]
        for path, count, suffix in ((self.before, 0, ''), (self.baseline, 3, ''), (self.candidate, 3, '\n# candidate\n')):
            path.mkdir()
            (path / 'solve.py').write_text("from pathlib import Path\nPath('result.json').write_text(" +
                repr(json.dumps(dict(count=count, label='ok'))) + ')' + suffix + '\n')
        self.export = self.root / 'old-qualification'
        source = export_manifest(self.bank, self.export)
        for task in source['tasks']:
            task['offline_executor'] = ['/usr/bin/python3', '-I', '/inputs/harness/solve.py']
        tasks = tuple((v['id'], v['root'], v['family']) for v in source['tasks'])
        families = sorted({t[2] for t in tasks})
        spec = ExperimentSpec('synthetic-old-qualification', tasks, 1, 17, 2*len(tasks), 2, .2, .3, .1,
            tuple((f, 1/len(families)) for f in families), self.runner.digest(self.before), self.runner.digest(self.baseline),
            identity(self.export, source['tasks'], source['grader_bundles']), 'e'*64)
        source.update(experiment=spec.to_dict(), max_trials=spec.max_trials, budget_seconds=60, timeout_seconds=3)
        (self.export / 'manifest.json').write_bytes(encoded(source))
        with self.workers():
            run = self.history.run(self.export / 'manifest.json', self.before, self.baseline, **self.profile())
        self.assertEqual(run['state'], 'sealed', run)
        prior_q = self.history.historical_plan(run['run_id'])
        inputs = record(self.history.case(run['run_id']) / 'inputs.json')[0]
        bank = record(self.bank)[0]
        members = []
        for task in bank['tasks']:
            bound = inputs['bank_tasks'][task['task_id']]
            members.append(dict(task_id=task['task_id'], root_id=task['root_id'], family=task['family'],
                bank_sha256=inputs['bank_authority']['bank_sha256'], bank_revision_sha256=inputs['bank_authority']['revision_sha256'],
                task_revision_sha256=bound['task_revision_sha256'], fixture_sha256=task['fixture_digest'],
                materialized_sha256=bound['materialized_sha256'], execution_fixture_sha256=bound['execution_fixture_sha256'],
                bundle_sha256=task['grader_digest'], grader_version=task['grader_version']))
        qualification = dict(history_store=str(self.history.root), arm='candidate', rule=dict(min_trials=1, min_successes=1),
            review=dict(identity='synthetic-old-reviewer', source='operator_attestation', basis_kind='independent_artifact_review',
                        evidence_refs=[dict(ref='synthetic:old-review', sha256='a'*64)]),
            **{k: prior_q[k] for k in ('run_id', 'event_sha256', 'evidence_sha256', 'plan_sha256')})
        old_manifest = dict(schema_version=1, profile=cohorts.PROFILE, author='synthetic-old-author', cohorts=[])
        for name, role in (('passed', 'historical_pass'), ('repaired', 'previously_repaired')):
            old_manifest['cohorts'].append(dict(cohort_id=name, role=role, required=True, margin=.15,
                exposure='development_regression', severity_policy='verified_critical_veto',
                qualification=deepcopy(qualification), members=deepcopy(members)))
        old_path = self.export / 'cohort-manifest.json'; old_path.write_bytes(encoded(old_manifest))
        pin = record(old_path, core.MAX_BYTES)[1]
        source['cohort_manifest'] = dict(path='cohort-manifest.json', sha256=pin)
        source['experiment'] = replace(spec, experiment_id='synthetic-old-comparison', baseline_hash=spec.candidate_hash,
            candidate_hash=self.runner.digest(self.candidate), cohort_manifest_sha256=pin).to_dict()
        (self.export / 'manifest.json').write_bytes(encoded(source))
        with self.workers():
            run = self.history.run(self.export / 'manifest.json', self.baseline, self.candidate, **self.profile())
        self.assertEqual(run['state'], 'sealed', run)
        self.prior = self.history.historical_plan(run['run_id'])
        self.protocol_path = self.root / 'qualification-protocol.json'
        self.protocol = dict(schema_version=2, algorithm=core.ALGORITHM, prior_run_id=self.prior['run_id'],
            prior_plan_sha256=self.prior['plan_sha256'], cohort_manifest_sha256=pin, author='synthetic-protocol-author',
            image_sha256='e'*64, max_total_trials=6*len(tasks),
            before_harnesses=[dict(history_store=str(self.history.root), run_id=prior_q['run_id'], path=str(self.before))])
        self.write_protocol()

    def write_protocol(self):
        self.protocol_path.write_bytes(encoded(self.protocol))
        self.protocol_sha256 = record(self.protocol_path, core.MAX_BYTES)[1]
        self.review = dict(identity='synthetic-protocol-reviewer', source='operator_attestation',
            basis_kind='independent_artifact_review', evidence_refs=[dict(ref='synthetic:predeclared-protocol', sha256=self.protocol_sha256)])

    def profile(self):
        return dict(cgroup_root=self.root/'delegation', image_root=self.root/'image', image_sha256='e'*64,
                    disk_bytes=64*1024*1024, budget_seconds=60)

    def execute(self, *args, **kwargs):
        result = offline.OfflineFixture.emulate(self, *args)
        result['attestation'] = dict(backend='bubblewrap-offline-v1', role=args[3], network='none',
            aggregate_disk_bound_verified=True, cgroup_v2={'empty_after_cleanup':True},
            runtime_image=dict(sha256='e'*64, verified_before=True, verified_after=True))
        return result

    def workers(self, execute=None):
        stack = ExitStack()
        stack.enter_context(patch.object(SandboxBackend, 'capability', return_value={'status':'available'}))
        self.worker = stack.enter_context(patch.object(SandboxBackend, 'run', side_effect=execute or self.execute))
        return stack

    def qualify(self, bank_path=None, **changes):
        profile = self.profile(); profile.pop('budget_seconds')
        kwargs = dict(review=self.review, parent_deadline=time.monotonic()+300, **profile)
        kwargs.update(changes)
        with self.workers():
            result = core.qualify(self.protocol_path, self.protocol_sha256, self.prior, bank_path or self.bank,
                self.baseline, self.candidate, self.root/'fresh-qualification', self.history, **kwargs)
        return result


class CohortRequalificationTests(unittest.TestCase):
    def setUp(self):
        self.f = RequalificationFixture(); self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def test_actual_fresh_phases_freeze_same_members_roles_rules_and_private_attribution(self):
        f = self.f; started = []
        binding = f.qualify(on_started=lambda phase, run: started.append((phase, run)))
        self.assertEqual(len(binding['phases']), 2)
        self.assertEqual(f.worker.call_count, 8)
        self.assertEqual(len(started), 2)
        self.assertTrue(all(p['run_id'] != f.prior['run_id'] for p in binding['phases']))
        for phase in binding['phases']:
            actual = f.history.historical_plan(phase['run_id'])
            self.assertEqual(actual['plan']['candidate_hash'], f.prior['plan']['baseline_hash'])
            if phase['role'] == 'historical_pass':
                self.assertEqual(actual['plan']['baseline_hash'], actual['plan']['candidate_hash'])
        self.assertFalse(binding['publication_allowed'])
        self.assertNotIn('supported', binding)
        self.assertEqual(core.verify_completion(binding, f.prior, f.bank, f.history), binding)
        self.assertEqual(core.verify_completion(binding, f.prior, f.bank, f.history, current=True), binding)

    def test_protocol_raw_parser_types_and_exact_identity_reject_before_dispatch(self):
        f = self.f
        for raw in (b'{"schema_version":2,"schema_version":2}', b'{"schema_version":NaN}', b'[]'):
            f.protocol_path.write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                core.load_protocol(f.protocol_path, hashlib.sha256(raw).hexdigest(), f.prior,
                                   f.baseline, f.candidate, 'e'*64)
        for change in (dict(schema_version=True), dict(max_total_trials=True), dict(max_total_trials=6.0),
                       dict(prior_plan_sha256='f'*64), dict(image_sha256='f'*64), dict(unexpected='field')):
            original = deepcopy(f.protocol); f.protocol.update(change); f.write_protocol()
            with self.subTest(change=change), patch.object(core.HistoryStore, 'run') as run:
                with self.assertRaises(ValueError): f.qualify()
                run.assert_not_called()
            self.assertFalse((f.root/'fresh-qualification').exists())
            f.protocol = original
        f.write_protocol()
        with self.assertRaisesRegex(ValueError, 'digest'):
            core.load_protocol(f.protocol_path, 'f'*64, f.prior, f.baseline, f.candidate, 'e'*64)

    def test_missing_duplicate_D_ceiling_wrong_runtime_and_review_fail_before_dispatch(self):
        f = self.f
        for changes in (dict(before_harnesses=[]),
                        dict(before_harnesses=f.protocol['before_harnesses']*2), dict(max_total_trials=5)):
            saved = deepcopy(f.protocol); f.protocol.update(changes); f.write_protocol()
            with self.subTest(changes=changes), patch.object(core.HistoryStore, 'run') as run:
                with self.assertRaises(ValueError): f.qualify()
                run.assert_not_called()
            self.assertFalse((f.root/'fresh-qualification').exists()); f.protocol = saved
        f.write_protocol()
        with patch.object(core.HistoryStore, 'run') as run:
            for kwargs in (dict(image_sha256='f'*64), dict(review=dict(f.review, identity=f.protocol['author'])),
                           dict(review=dict(f.review, evidence_refs=[]))):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): f.qualify(**kwargs)
            run.assert_not_called()
        (f.before/'solve.py').write_text('# missing original before bytes\n')
        with patch.object(core.HistoryStore, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'before-harness'): f.qualify()
            run.assert_not_called()

    def test_actual_repaired_phase_without_new_failure_to_pass_cannot_freeze(self):
        f = self.f
        def all_pass(*args, **kwargs):
            result = f.execute(*args, **kwargs)
            if args[3] == 'executor':
                (args[1]/'result.json').write_text('{"count":3,"label":"ok"}')
            return result
        profile = f.profile(); profile.pop('budget_seconds')
        with f.workers(all_pass), self.assertRaisesRegex(ValueError, 'failure-to-pass'):
            core.qualify(f.protocol_path, f.protocol_sha256, f.prior, f.bank, f.baseline, f.candidate,
                f.root/'fresh-qualification', f.history, review=f.review,
                parent_deadline=time.monotonic()+300, **profile)
        self.assertEqual(f.worker.call_count, 8)
        self.assertEqual(f.history.historical_plan(f.prior['run_id'])['state'], 'sealed')
        self.assertFalse((f.root/'fresh-qualification/cohort-binding.json').exists())

    def test_historical_completion_survives_external_deletion_and_later_revocation(self):
        f = self.f; binding = f.qualify()
        f.protocol_path.unlink(); (f.baseline/'solve.py').unlink(); (f.before/'solve.py').unlink()
        self.assertEqual(core.verify_completion(binding, f.prior, f.bank, f.history), binding)
        with self.assertRaises((ValueError, OSError)):
            core.verify_completion(binding, f.prior, f.bank, f.history, current=True)
        bundle = binding['cohort_binding']['manifest']['cohorts'][0]['members'][0]['bundle_sha256']
        active = f.history.oracle(bundle)
        request = dict(schema_version=1, bundle_sha256=bundle, expected_event_sha256=active['event_sha256'],
            operator=dict(identity='synthetic-quarantine-reviewer', source='operator_attestation',
                          provenance='synthetic evidence', basis='Revoke synthetic oracle'),
            evidence_refs=[dict(ref='synthetic:revocation', sha256='b'*64)], reason='Synthetic later revocation')
        path = f.root/'quarantine.json'; path.write_bytes(encoded(request)); f.history.quarantine(path)
        self.assertEqual(core.verify_completion(binding, f.prior, f.bank, f.history), binding)

    def test_binding_mutants_cannot_replace_actual_sealed_evidence(self):
        f = self.f; binding = f.qualify()
        mutations = [lambda b:b['phases'][0].update(run_id=f.prior['run_id']),
            lambda b:b['phases'][0].update(event_sha256='f'*64),
            lambda b:b['phases'][0].update(manifest_sha256='f'*64),
            lambda b:b['cohort_binding']['qualification_bindings']['passed']['counts'][next(iter(
                b['cohort_binding']['qualification_bindings']['passed']['counts']))].update(successes=0),
            lambda b:b['snapshots']['candidate'].update(mode_sha256='f'*64),
            lambda b:b.update(publication_allowed=True)]
        for mutate in mutations:
            changed = deepcopy(binding); mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                core.verify_completion(changed, f.prior, f.bank, f.history)
        phase = binding['phases'][0]; Path(phase['started_path']).write_bytes(encoded(dict(run_id=f.prior['run_id'])))
        with self.assertRaisesRegex(ValueError, 'custody'):
            core.verify_completion(binding, f.prior, f.bank, f.history)

    def test_interrupt_preserves_started_real_run_without_redispatch_or_success(self):
        f = self.f; started = []
        def interrupt(phase, run):
            started.append((phase, run)); raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt): f.qualify(on_started=interrupt)
        self.assertEqual(f.worker.call_count, 0)
        self.assertEqual(len(started), 1)
        phase, run = started[0]
        self.assertEqual(record(f.root/'fresh-qualification'/phase/'started-run.json')[0], dict(run_id=run))
        self.assertEqual(f.history.historical_plan(run)['state'], 'incomplete')
        with self.assertRaisesRegex(ValueError, 'must be new'): f.qualify()

    def test_one_deadline_includes_preflight_and_final_revalidation(self):
        f = self.f
        with patch.object(core.HistoryStore, 'run') as run:
            with self.assertRaises(TimeoutError): f.qualify(parent_deadline=time.monotonic()-1)
            run.assert_not_called()
        binding = f.qualify()
        clock = [time.monotonic()]; real = core.verify_completion
        def late(*args, **kwargs):
            result = real(*args, **kwargs); clock[0] += 301; return result
        with patch.object(core, 'verify_completion', side_effect=late), \
                patch.object(core.time, 'monotonic', side_effect=lambda:clock[0]), self.assertRaises(TimeoutError):
            core.revalidate(binding, f.prior, f.baseline, f.candidate, f.bank, f.history, parent_deadline=clock[0]+300)

    def test_late_original_edit_during_live_authority_recheck_rejects_completion(self):
        f = self.f; binding = f.qualify(); original = cohorts.revalidate
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            (f.baseline/'solve.py').write_text('# changed during final check\n')
            return result
        with patch.object(cohorts, 'revalidate', side_effect=changed), self.assertRaisesRegex(ValueError, 'original harness'):
            core.revalidate(binding, f.prior, f.baseline, f.candidate, f.bank, f.history,
                            parent_deadline=time.monotonic()+300)
        self.assertEqual(core.verify_completion(binding, f.prior, f.bank, f.history), binding)

    def test_whole_membership_comparison_rejects_tail_loss_and_order_changes(self):
        original = record(self.f.history.case(self.f.prior['run_id'])/'cohort-binding.json', core.MAX_BYTES)[0]
        member = original['manifest']['cohorts'][0]['members'][0]
        second = dict(member, task_id='synthetic-second-member')
        for updated in ([member], [second, member], [member, second, second]):
            with self.subTest(updated=updated), self.assertRaisesRegex(ValueError, 'membership'):
                core._members([member, second], updated)
        core._members([member, second], [dict(member, bundle_sha256='f'*64), second])

    def test_snapshot_uses_bounded_captured_bytes_without_following_late_source_symlink(self):
        f = self.f; source = f.root/'source-race'; source.mkdir()
        (source/'allowed.txt').write_text('original synthetic bytes')
        outside = f.root/'synthetic-outside.txt'; outside.write_text('distinct synthetic sentinel')
        expected = core.runtime_hash(core.scan(source)[1]); actual_scan = core.scan
        def changed(path):
            result = actual_scan(path)
            if Path(path) == source:
                (source/'allowed.txt').unlink(); (source/'allowed.txt').symlink_to(outside)
            return result
        with patch.object(core, 'scan', side_effect=changed):
            binding = core._copy(source, f.root/'bounded-copy', expected)
        self.assertEqual((Path(binding['path'])/'allowed.txt').read_text(), 'original synthetic bytes')
        self.assertEqual(outside.read_text(), 'distinct synthetic sentinel')

    def test_destination_under_original_before_harness_rejects_without_source_writes(self):
        f = self.f; profile = f.profile(); profile.pop('budget_seconds')
        destination = f.before/'unapproved-custody'
        with f.workers(), self.assertRaisesRegex(ValueError, 'overlaps'):
            core.qualify(f.protocol_path, f.protocol_sha256, f.prior, f.bank, f.baseline, f.candidate,
                destination, f.history, review=f.review, parent_deadline=time.monotonic()+300, **profile)
        self.assertFalse(destination.exists())
        self.assertEqual(f.worker.call_count, 0)
