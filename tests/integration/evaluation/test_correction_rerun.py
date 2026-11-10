"""Fresh synthetic subprocess executions; no model, native-authentication or kernel proof."""
from contextlib import ExitStack
from dataclasses import asdict,replace
import hashlib
import json
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import evaluator,main
from agent_harness_lab.evaluation.oracle import identity
from agent_harness_lab.evaluation.plan import ExperimentSpec,schedule
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from agent_harness_lab.evaluation.bank_tools import export_manifest
from agent_harness_lab.evaluation.grader_correction import CorrectionStore
from agent_harness_lab.evaluation.integrity import digest
from tests.integration.evaluation import test_bank_corrections as applications
from tests.integration.evaluation import test_offline_evaluation as offline


class CorrectionRerunHarness:
    """An applied synthetic correction with a sealed prior comparison, built by setUp and released by doCleanups."""
    def prior_selector(self): return sys.executable

    def setUp(self):
        self.cleanups = ExitStack()
        self.application = applications.BankCorrectionTests(); self.application.setUp()
        self.cleanups.callback(self.application.doCleanups)
        f = self.application
        self.root,self.store,self.history,self.case_id = f.root,f.store,f.history,f.case_id
        self.runner = evaluator(); self.export = self.root/'prior-export'; self.export.mkdir()
        shutil.copytree(self.root/'fixture',self.export/'fixture'); shutil.copytree(self.root/'oldoracle',self.export/'oracle')
        self.baseline,self.candidate = self.root/'rerun-baseline',self.root/'rerun-candidate'
        for folder,value in [(self.baseline,'{"count":3,"label":"ok"}'),(self.candidate,'{ "label": "ok", "count":3 }')]:
            folder.mkdir(); (folder/'solve.py').write_text('from pathlib import Path\nPath("result.json").write_text('+repr(value)+')\n')
        task = f.fixture.task
        selected = dict(id=task['task_id'],root=task['root_id'],family=task['family'],split='development',
            prompt=json.loads((self.export/'fixture/task.json').read_text())['prompt'],fixture='fixture',artifacts=task['artifacts'],
            grader=[self.prior_selector(),str(self.export/'oracle/grade.py.txt'),'{workspace}',str(self.export/'oracle/expected.json')],
            offline_executor=['/usr/bin/python3','-I','/inputs/harness/solve.py'])
        self.manifest = dict(schema_version=1,tasks=[selected],grader_bundles={task['task_id']:dict(path='oracle',digest=task['grader_digest'])},
            max_trials=6,budget_seconds=45,timeout_seconds=3)
        self.old = ExperimentSpec('synthetic-corrected-pair',((task['task_id'],task['root_id'],task['family']),),3,17,6,2,.2,.3,.01,
            ((task['family'],1),),self.runner.digest(self.baseline),self.runner.digest(self.candidate),
            identity(self.export,[selected],self.manifest['grader_bundles']),'e'*64)
        self.manifest['experiment'] = asdict(self.old)
        (self.export/'manifest.json').write_text(json.dumps(self.manifest))
        with self.workers(),patch.object(sys,'executable',selected['grader'][0]):
            self.prior = self.history.run(self.export/'manifest.json',self.baseline,self.candidate,**self.profile())
        assert self.prior['state'] == 'sealed'
        self.applied = f.apply()

    def doCleanups(self):
        self.cleanups.close()

    def profile(self):
        return dict(cgroup_root=self.root/'delegation',image_root=self.root/'image',image_sha256='e'*64,disk_bytes=64*1024*1024,budget_seconds=60)

    def execute(self,*args,**kwargs):
        result = offline.OfflineFixture().emulate(*args)
        result['attestation'] = dict(backend='bubblewrap-offline-v1',role=args[3],network='none',aggregate_disk_bound_verified=True,
            cgroup_v2={'empty_after_cleanup':True},runtime_image=dict(sha256='e'*64,verified_before=True,verified_after=True))
        return result

    def workers(self,execute=None):
        from contextlib import ExitStack
        stack = ExitStack(); stack.enter_context(patch.object(SandboxBackend,'capability',return_value={'status':'available'}))
        self.worker = stack.enter_context(patch.object(SandboxBackend,'run',side_effect=execute or self.execute))
        return stack

    def request(self,**changes):
        current = self.store.show(self.case_id); prior = self.history.historical_plan(self.prior['run_id'])
        value = dict(schema_version=1,case_id=self.case_id,expected_event_sha256=current['event_sha256'],
            evidence_sha256=current['evidence_sha256'],bank_sha256=self.applied['bank_manifest_sha256'],
            revision_sha256=self.applied['revision_sha256'],history_store=str(self.history.root),
            prior={k:prior[k] for k in ('run_id','event_sha256','inputs_sha256','plan_sha256','evidence_sha256')},
            operator=dict(identity='synthetic-rerun-operator',source='operator_attestation',provenance='synthetic operator',
                basis='Execute a fresh full paired schedule after reviewed correction'),reason='Synthetic corrected comparison')
        value.update(changes); path = self.root/'rerun-request.json'; path.write_text(json.dumps(value)); return path

    def rerun(self,execute=None,**profile):
        request = self.request()
        with self.workers(execute):
            result = self.store.rerun(self.case_id,request,self.baseline,self.candidate,**(self.profile()|profile))
            return result,self.worker.call_count


class TestCorrectionRerun(CorrectionRerunHarness):
    @pytest.fixture(autouse=True)
    def harness(self):
        try:
            self.setUp()
            yield
        finally:
            self.doCleanups()

    def test_actual_entire_both_arm_schedule_preserves_plan_with_new_oracle_and_trial_ids(self):
        result,calls = self.rerun(); assert result['rerun_status'] == 'completed', result
        assert calls == 12
        new = self.history.historical_plan(result['new_run_id']); spec = ExperimentSpec.parse(new['plan'])
        expected = asdict(self.old); expected['grader_hash'] = spec.grader_hash
        assert asdict(spec) == expected
        assert spec.grader_hash != self.old.grader_hash
        assert {t['trial_id'] for t in schedule(self.old)}.isdisjoint(t['trial_id'] for t in schedule(spec))
        assert new['fixtures'] == self.history.historical_plan(self.prior['run_id'])['fixtures']
        assert result['harness_mode_evidence'] == 'unknown'
        assert result['fixture_mode_evidence'] == 'unknown'
        assert result['historical_state_reproduction'] == 'unverified'
        assert self.history.show(self.prior['run_id'])['oracle_validity'] == 'invalidated'

    def test_relocated_old_host_python_selector_normalizes_inside_same_pinned_image(self):
        relocated = CorrectionRerunHarness()
        old = '/retired/synthetic-env/bin/python3.11'
        try:
            with patch.object(relocated,'prior_selector',return_value=old): relocated.setUp()
        finally: self.cleanups.callback(relocated.doCleanups)
        assert sys.executable != old
        result,calls = relocated.rerun(); assert result['rerun_status'] == 'completed', result; assert calls == 12
        observed = relocated.history.historical_plan(result['new_run_id'])
        assert observed['manifest']['tasks'][0]['grader'][0] == '/usr/bin/python3'
        assert observed['manifest']['tasks'][0]['offline_executor'] == relocated.manifest['tasks'][0]['offline_executor']
        assert observed['plan']['runtime_hash'] == relocated.old.runtime_hash
        with patch.object(sys,'executable',old):
            assert relocated.store.show(relocated.case_id)['rerun_status'] == 'completed'

    def test_completed_receipt_retargeting_cannot_import_an_earlier_comparison(self):
        result,_ = self.rerun(); case = self.store.case(self.case_id)
        final = sorted((case/'events').glob('*.json'))[-1]; event = json.loads(final.read_text())
        path = case/event['payload']['attempt']/'receipt.json'; receipt = json.loads(path.read_text())
        receipt['new_run_id'] = self.prior['run_id']; raw = json.dumps(receipt).encode(); path.write_bytes(raw)
        event['payload']['receipt_sha256'] = hashlib.sha256(raw).hexdigest(); final.write_text(json.dumps(event))
        with pytest.raises(ValueError): self.store.show(self.case_id)

    @pytest.mark.parametrize('forgery',['retargeted-run','pinned-fixture-mode','fabricated-prior-state','changed-event'])
    def test_failure_receipt_cannot_retarget_run_or_upgrade_unrecorded_prior_modes(self,forgery):
        def fail(*args,**kwargs):
            value = self.execute(*args,**kwargs)
            if args[3]=='executor': value.update(status='timeout',exit_code=None)
            return value
        self.rerun(execute=fail); case = self.store.case(self.case_id)
        final = sorted((case/'events').glob('*.json'))[-1]; event = json.loads(final.read_text())
        path = case/event['payload']['attempt']/'receipt.json'; original = json.loads(path.read_text())
        change = {'retargeted-run':lambda r:r.update(new_run_id=self.prior['run_id']),
                  'pinned-fixture-mode':lambda r:r.update(fixture_mode_evidence='pinned'),
                  'fabricated-prior-state':lambda r:r.update(prior_state='sealed-fabricated'),
                  'changed-event':lambda r:r.update(new_event_sha256='bad')}[forgery]
        value = json.loads(json.dumps(original)); change(value); raw = json.dumps(value).encode(); path.write_bytes(raw)
        event['payload']['receipt_sha256'] = hashlib.sha256(raw).hexdigest(); final.write_text(json.dumps(event))
        with pytest.raises(ValueError): self.store.show(self.case_id)

    def test_stale_forged_prior_state_or_changed_harness_runtime_fail_before_dispatch(self):
        original = self.request(); value = json.loads(original.read_text())
        for change in (lambda r:r.update(expected_event_sha256='f'*64),lambda r:r['prior'].update(inputs_sha256='f'*64),
                       lambda r:r['prior'].update(evidence_sha256='f'*64)):
            record = json.loads(json.dumps(value)); change(record); original.write_text(json.dumps(record))
            with self.workers():
                with pytest.raises(ValueError): self.store.rerun(self.case_id,original,self.baseline,self.candidate,**self.profile())
                self.worker.assert_not_called()
        (self.baseline/'solve.py').write_text('raise RuntimeError("changed")\n')
        with self.workers():
            with pytest.raises(ValueError): self.store.rerun(self.case_id,self.request(),self.baseline,self.candidate,**self.profile())
            self.worker.assert_not_called()
        with pytest.raises(ValueError): self.rerun(image_sha256='a'*64)

    def test_incomplete_and_faked_success_cannot_qualify_and_retries_execute_fresh(self):
        def fail(*args,**kwargs):
            result = self.execute(*args,**kwargs)
            if args[3]=='executor': result.update(status='timeout',exit_code=None)
            return result
        result,_ = self.rerun(execute=fail)
        assert result['rerun_status'] == 'incomplete'
        assert result['new_run_id'] is not None
        with patch.object(type(self.history),'run',return_value=self.prior):
            result,_ = self.rerun()
        assert result['rerun_status'] == 'incomplete'
        completed,calls = self.rerun(); assert calls == 12
        again,calls = self.rerun(); assert calls == 12
        assert completed['new_run_id'] != again['new_run_id']

    def test_interruption_records_generated_run_id_and_old_quarantine_persists(self):
        with pytest.raises(KeyboardInterrupt):
            self.rerun(execute=lambda *a,**kw:(_ for _ in ()).throw(KeyboardInterrupt()))
        current = self.store.show(self.case_id)
        assert current['rerun_status'] == 'interrupted'
        assert current['new_run_id'] is not None
        assert self.history.show(self.prior['run_id'])['oracle_validity'] == 'invalidated'
        assert self.rerun()[0]['rerun_status'] == 'completed'

    def test_start_callback_failure_latency_and_non_callable_never_dispatch(self):
        for callback in (False,lambda run:(_ for _ in ()).throw(RuntimeError('synthetic observer failure'))):
            with self.workers():
                if callback is False:
                    with pytest.raises(ValueError): self.history.run(self.export/'manifest.json',self.baseline,self.candidate,**self.profile(),on_started=callback)
                else:
                    # A separate unquarantined identity is required to reach observation.
                    from agent_harness_lab.evaluation.oracle_history import HistoryStore
                    other = HistoryStore(self.root/'observer-history')
                    result = other.run(self.export/'manifest.json',self.baseline,self.candidate,**self.profile(),on_started=callback)
                    assert result['state'] == 'incomplete'
                self.worker.assert_not_called()
        from agent_harness_lab.evaluation import oracle_history
        clock = [0]; observed = []
        def late(run): observed.append(run); clock[0]=2
        with self.workers(), patch.object(oracle_history.time,'monotonic',side_effect=lambda:clock[0]):
            other = oracle_history.HistoryStore(self.root/'late-observer-history')
            result = other.run(self.export/'manifest.json',self.baseline,self.candidate,**(self.profile()|dict(budget_seconds=1)),parent_deadline=.5,on_started=late)
            assert result['state'] == 'incomplete'; self.worker.assert_not_called()
        assert observed == [result['run_id']]

    def test_one_arm_results_do_not_qualify_as_completed_comparison(self):
        runner = evaluator(); evaluate = runner.evaluate
        def one_arm(args):
            code = evaluate(args); path = args.output/'results.json'; value = json.loads(path.read_text())
            value['trials'] = [t for t in value['trials'] if t['version']=='candidate']
            path.write_text(json.dumps(value)); return code
        with patch('agent_harness_lab.cli.evaluator',return_value=runner),patch.object(runner,'evaluate',side_effect=one_arm):
            result,_ = self.rerun()
        assert result['rerun_status'] == 'incomplete'
        assert result['new_run_id'] is not None

    def test_v2_incomplete_prior_uses_only_pinned_inputs_not_unsealed_results(self):
        parent = self.application.bank(self.applied); export = self.root/'controlled-prior'
        source = export_manifest(parent,export)
        for t in source['tasks']: t['offline_executor']=['/usr/bin/python3','-I','/inputs/harness/solve.py']
        old = replace(self.old,grader_hash=identity(export,source['tasks'],source['grader_bundles']))
        source['experiment'] = asdict(old); (export/'manifest.json').write_text(json.dumps(source))
        def broken_grading(*args,**kwargs):
            result = self.execute(*args,**kwargs)
            if args[3]=='grader': result.update(stdout='{}',status='failed',exit_code=1)
            return result
        with self.workers(broken_grading):
            previous = self.history.run(export/'manifest.json',self.baseline,self.candidate,**self.profile())
        assert previous['state'] == 'incomplete'
        (self.history.case(previous['run_id'])/'execution/results.json').write_text('[]')
        fixture = self.application.fixture
        bundle = self.root/'rerun-v3'; shutil.copytree(self.root/'neworacle',bundle)
        script = bundle/'grade.py.txt'; script.write_text(script.read_text().replace("'v2'","'v3'").replace(
            'json.loads(actual)==json.loads(expected)','all(json.loads(actual).get(k)==v for k,v in json.loads(expected).items())'))
        declaration = json.loads(json.dumps(fixture.spec_value))
        declaration['parent'].update(bank=str(parent.relative_to(self.root)),sha256=hashlib.sha256(parent.read_bytes()).hexdigest())
        declaration['replacement'] = dict(bundle=bundle.name,sha256=digest(bundle),grader_version='v3')
        for c in declaration['controls']:
            if c['state']=='alternative_valid':
                folder = self.root/c['fixture']; value = json.loads((folder/'result.json').read_text()); value['extra']=True
                (folder/'result.json').write_text(json.dumps(value)); c['digest']=digest(folder)
        path = self.root/'v3-spec.json'; path.write_text(json.dumps(declaration))
        second = CorrectionStore(self.root/'rerun-v3-store'); proposed = second.propose(path,'second-synthetic-author')
        audited = second.audit(proposed['case_id'],fixture.backend); assert audited['state'] == 'audited'
        review = json.loads(fixture.review_file().read_text()); review.update(case_id=proposed['case_id'],
            expected_event_sha256=audited['event_sha256'],evidence_sha256=audited['evidence_sha256'])
        record = self.root/'v3-review.json'; record.write_text(json.dumps(review)); second.review(proposed['case_id'],record)
        self.application.store,self.application.case_id,self.application.parent = second,proposed['case_id'],parent
        self.applied = self.application.apply(); self.store,self.case_id = second,proposed['case_id']
        self.prior,self.old = previous,old
        result,calls = self.rerun(); assert result['rerun_status'] == 'completed', result; assert calls == 12
        assert result['fixture_mode_evidence'] == 'pinned'
        first = self.application.fixture
        first.store.review(first.case_id,first.review_file('quarantined'))
        observed = self.store.show(self.case_id)
        assert observed['rerun_status'] == 'completed'; assert not observed['current_eligible']
        with pytest.raises(ValueError): self.history.analyze(result['new_run_id'])
        assert self.history.historical_plan(result['new_run_id'])['current_applicability'] == 'not_evaluated'

    def test_preparation_and_receipt_sealing_share_original_deadline(self):
        from agent_harness_lab.evaluation import correction_rerun
        clock = [0]; export = correction_rerun.export_manifest
        def slow_export(*args,**kwargs):
            value = export(*args,**kwargs); clock[0]=2; return value
        with patch.object(correction_rerun.time,'monotonic',side_effect=lambda:clock[0]), \
                patch.object(correction_rerun,'export_manifest',side_effect=slow_export):
            result,calls = self.rerun(budget_seconds=1)
        assert result['rerun_status'] == 'budget_exhausted'; assert calls == 0
        clock[0] = 0; sync = correction_rerun.sync_directory
        def slow_receipt(path):
            sync(path)
            if (Path(path)/'receipt.json').exists(): clock[0]=2
        with patch.object(correction_rerun.time,'monotonic',side_effect=lambda:clock[0]), \
                patch.object(correction_rerun,'sync_directory',side_effect=slow_receipt):
            result,calls = self.rerun(budget_seconds=1)
        assert result['rerun_status'] == 'budget_exhausted'; assert calls == 12

    def test_operator_apply_rerun_and_historical_plan_cli_use_actual_controller(self):
        prefix = ['grader-correction','--store',str(self.store.root)]
        with patch('builtins.print'):
            assert (main(prefix+['apply','--case',self.case_id,'--parent',str(self.application.parent),
                '--history-store',str(self.history.root),'--record',str(self.root/'application.json')])) == 0
        with patch('builtins.print') as output:
            assert main(['experiment-history','--store',str(self.history.root),'historical-plan','--run',self.prior['run_id']]) == 0
            assert json.loads(output.call_args.args[0])['current_applicability'] == 'not_evaluated'
        request = self.request()
        with self.workers(),patch('builtins.print') as output:
            code = main(prefix+['rerun','--case',self.case_id,'--record',str(request),'--baseline',str(self.baseline),
                '--candidate',str(self.candidate),'--cgroup-root',str(self.root/'delegation'),'--image-root',str(self.root/'image'),
                '--image-sha256','e'*64,'--disk-bytes','67108864','--budget-seconds','60'])
            assert code == 0; assert self.worker.call_count == 12
            assert json.loads(output.call_args.args[0])['rerun_status'] == 'completed'

    def test_missing_harness_or_incomplete_legacy_prior_refuses_without_success_upgrade(self):
        from agent_harness_lab.evaluation.oracle_history import HistoryStore
        other = HistoryStore(self.root/'legacy-incomplete-history')
        def broken(*args,**kwargs):
            value = self.execute(*args,**kwargs)
            if args[3]=='grader': value.update(stdout='{}')
            return value
        with self.workers(broken):
            result = other.run(self.export/'manifest.json',self.baseline,self.candidate,**self.profile())
        assert result['state'] == 'incomplete'
        with pytest.raises(ValueError, match='legacy'): other.historical_plan(result['run_id'])
        shutil.rmtree(self.baseline)
        with pytest.raises((ValueError,OSError)): self.rerun()
