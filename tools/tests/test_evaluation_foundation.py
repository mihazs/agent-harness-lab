from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
from agent_harness_lab.evaluation.statistics import TrialRecord, analyze
from agent_harness_lab.evaluation.task_bank import CertificationLedger, TaskSpec, validate
from agent_harness_lab.evaluation.integrity import artifacts, digest
from agent_harness_lab.evaluation.sandbox import SandboxBackend, Limits
from agent_harness_lab.promotion.policy import decide


def plan(n=4, repeats=2, profile='certification'):
    return ExperimentSpec('synthetic', tuple((f't{i}', f'r{i}', 'family') for i in range(n)),
                          repeats, 13, 2*n*repeats, 3, .05, .1, .05, (('family', 1),),
                          'base', 'candidate', 'grader', 'runtime', profile, 'certification')


def records(spec, outcomes=None):
    outcomes = outcomes or (lambda trial: trial['version'] == 'candidate')
    return [TrialRecord(t['trial_id'], outcomes(t), 'graded', 'executable_grader',
                        'base', 'candidate', 'grader', 'runtime') for t in schedule(spec)]


class ExperimentTests(unittest.TestCase):
    def test_scheduler_replay_counterbalance_and_budget(self):
        spec = plan()
        first = schedule(spec)
        self.assertEqual(first, schedule(spec))
        self.assertEqual(len(first), 16)
        for task in {t['task'] for t in first}:
            self.assertEqual([t['version'] for t in first if t['task'] == task][::2].count('baseline'), 1)
        with self.assertRaises(ValueError): schedule(replace(spec, max_trials=15))
        self.assertNotEqual(spec.digest, replace(spec, effect_margin=.06).digest)

    def test_empty_duplicate_missing_unknown_and_provenance_fail_closed(self):
        spec, data = plan(), records(plan())
        for bad in ([], data[:-1], data[:-1]+[data[0]], [replace(data[0], outcome=None)]+data[1:]):
            self.assertEqual(analyze(spec, bad)['status'], 'incomplete')
        self.assertEqual(analyze(spec, [replace(data[0], runtime_hash='different')]+data[1:])['status'], 'blocked')
        self.assertEqual(analyze(spec, [replace(data[0], critical_regression=True)]+data[1:])['status'], 'rejected')

    def test_repeated_related_roots_are_not_independent(self):
        spec = replace(plan(), tasks=tuple((f't{i}', 'shared', 'family') for i in range(4)))
        result = analyze(spec, records(spec))
        self.assertEqual(result['status'], 'inconclusive')
        self.assertEqual(result['roots'], 1)
        # A lucky pair cannot pass even with repeated copies of the same root.
        self.assertFalse(decide(spec, result).automatic)

    def test_benefit_harm_and_cooperative_gate(self):
        spec = plan(n=1000, repeats=1)
        benefit = analyze(spec, records(spec))
        self.assertEqual(benefit['status'], 'supported')
        self.assertEqual(decide(spec, benefit).status, 'blocked')
        decision = decide(spec, benefit, isolation_verified=True, effective_runtime_verified=True,
                          graders_audited=True, lineage_reviewed=True, certification_reserved=True)
        self.assertEqual(decision.status, 'recommended')
        self.assertFalse(decision.automatic)
        harm = analyze(spec, records(spec, lambda t: t['version'] == 'baseline'))
        self.assertLess(harm['primary']['high'], 0)
        self.assertNotEqual(harm['status'], 'supported')
        smoke = replace(spec, profile='descriptive_smoke')
        self.assertEqual(decide(smoke, analyze(smoke, records(smoke))).status, 'inconclusive')

    def test_seeded_null_simulation_false_promotions(self):
        # Predeclared check: 100 null simulations, seed 19; at most 10 false
        # promotions; also require the Wilson 95% upper bound <= .05.
        # This is an engineering falsification check, not calibration
        # over arbitrary task dependence. Wilson upper bound is reported in docs.
        rng, promoted = random.Random(19), 0
        spec = plan(n=40, repeats=2)
        for _ in range(100):
            # Correlated repeats within each independent root: one draw per root/version.
            outcomes = {(root, v): rng.random() < .5 for _, root, _ in spec.tasks for v in ('baseline','candidate')}
            data = records(spec, lambda t: outcomes[t['root'], t['version']])
            promoted += analyze(spec, data)['status'] == 'supported'
        import math
        p, n, z = promoted / 100, 100, 1.96
        upper = (p + z*z/(2*n) + z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / (1+z*z/n)
        self.assertLessEqual(upper, .05)  # Predeclared acceptable false-promotion rate.


class TaskBankTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tasks = []
        for i, partition in enumerate(('development','certification')):
            fixture = self.root/f'fixture{i}'; fixture.mkdir(); (fixture/'value').write_text(str(i))
            grader = self.root/f'grader{i}'; grader.mkdir(); (grader/'grade.py').write_text('pass\n')
            self.tasks.append(dict(task_id=f't{i}', root_id=f'r{i}', family=f'f{i}', partition=partition,
                project_group=f'p{i}', time_group=f'time{i}', fixture=fixture.name, fixture_digest=digest(fixture),
                grader_bundle=grader.name, grader_digest=digest(grader), grader_version='v1',
                source_kind='synthetic', source_ref='independently synthetic unit fixture', permission='approved',
                reviewer='test-contract', review_status='accepted', independence='reviewed',
                safety=['offline disposable fixture'], artifacts=['answer.json']))
        self.path = self.root/'bank.json'

    def write(self):
        self.path.write_text(json.dumps(dict(schema_version=1, tasks=self.tasks,
            separate_by=['root_id','family','project_group','time_group'])))

    def test_reviewed_synthetic_bank_and_mutated_oracle(self):
        self.write(); self.assertTrue(validate(self.path)['certification_eligible'])
        (self.root/'grader1/grade.py').write_text('different\n')
        with self.assertRaisesRegex(ValueError, 'integrity'): validate(self.path)

    def test_leakage_and_unknown_independence(self):
        for dimension in ('root_id', 'family', 'project_group', 'time_group'):
            original=self.tasks[1][dimension]; self.tasks[1][dimension]=self.tasks[0][dimension]
            self.write()
            with self.assertRaisesRegex(ValueError, 'leakage'): validate(self.path)
            self.tasks[1][dimension]=original
        self.tasks[1]['independence']='unverified'; self.write()
        self.assertFalse(validate(self.path)['certification_eligible'])

    def test_certification_access_is_consumed_before_feedback(self):
        ledger = CertificationLedger(self.root/'private/final.json')
        ledger.reserve('bank', 'candidate', 'controller')
        with self.assertRaises(ValueError): ledger.access('candidate', 'proposer')
        with self.assertRaises(ValueError): ledger.access('other', 'evaluator')
        self.assertEqual(ledger.access('candidate', 'evaluator'), 'bank')
        with self.assertRaises(ValueError): ledger.access('candidate', 'evaluator')
        with self.assertRaises(FileExistsError): ledger.reserve('bank', 'other', 'controller')

    def test_artifact_extraction_rejects_escape_hardlink_symlink_and_limits(self):
        fixture=self.root/'fixture0'; (fixture/'answer').write_text('ok')
        self.assertEqual(artifacts(fixture,['answer']),{'answer':b'ok'})
        with self.assertRaises(ValueError): artifacts(fixture,['../bank.json'])
        with self.assertRaises(ValueError): artifacts(fixture,['answer'],max_bytes=1)
        (fixture/'link').symlink_to(fixture/'answer')
        with self.assertRaises(ValueError): artifacts(fixture,['link'])
        (fixture/'link').unlink(); os.link(fixture/'answer',fixture/'hard')
        with self.assertRaises(ValueError): artifacts(fixture,['hard'])


class SandboxTests(unittest.TestCase):
    def test_role_policy_and_missing_capability_never_fall_back(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'work').mkdir(); (root/'oracle').mkdir()
            backend=SandboxBackend('synthetic-nonexistent')
            self.assertEqual(backend.capability()['status'],'unsupported')
            with self.assertRaises(ValueError): backend.run(['/usr/bin/true'],root/'work',{},'grader')
            backend.binary='/usr/bin/true'
            with self.assertRaises(ValueError): backend.command(['/usr/bin/true'],root/'work',{'oracle':root/'oracle'},'executor')
            command=backend.command(['/usr/bin/true'],root/'work',{},'grader')
            self.assertIn('--unshare-net',command); self.assertIn('--clearenv',command)
            self.assertNotIn('--share-net',command)
        with self.assertRaises(ValueError): Limits(seconds=0).validate()

    def test_supported_host_isolation_canaries_and_output_limit(self):
        backend=SandboxBackend()
        if backend.capability()['status'] != 'available':
            self.skipTest('offline namespace backend unavailable; containment not established on this host')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'work').mkdir(); (root/'secret').write_text('CANARY')
            script="import os,pathlib,socket; assert 'SECRET' not in os.environ; assert not pathlib.Path('"+str(root/'secret')+"').exists(); s=socket.socket(); s.settimeout(.2);\ntry: s.connect(('1.1.1.1',443)); raise AssertionError('network escape')\nexcept OSError: pass\n"
            with patch.dict(os.environ, SECRET='CANARY'):
                run=backend.run(['/usr/bin/python3','-c',script],root/'work',{},'grader')
            self.assertEqual(run['exit_code'],0)
            flood=backend.run(['/usr/bin/python3','-c',"print('x'*100000)"],root/'work',{},'grader',Limits(output_bytes=1024))
            self.assertEqual(flood['status'],'output_limit')
            self.assertLessEqual(len(flood['stdout']),1024)

class CoverageTests(unittest.TestCase):
    def test_vacuous_legacy_result_cannot_become_no_change(self):
        from agent_harness_lab.promotion.policy import legacy_coverage
        manifest={'tasks':[{'id':'t'}]}
        self.assertFalse(legacy_coverage(manifest,[]))
        self.assertFalse(legacy_coverage(manifest,[{'version':'candidate','task':'t'}]*2))
        self.assertTrue(legacy_coverage(manifest,[{'version':v,'task':'t'} for v in ('baseline','candidate')]))

    def test_certification_concurrent_access_is_single_use(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as folder:
            ledger=CertificationLedger(Path(folder)/'final.json')
            ledger.reserve('bank','candidate','controller')
            def access():
                try: return ledger.access('candidate','evaluator')
                except ValueError: return 'denied'
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _: access(),range(2)))
            self.assertEqual(sorted(results),['bank','denied'])
