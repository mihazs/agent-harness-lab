"""Conditional bank review through public commands; native execution is opt-in."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, replace
import io
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import evaluator, main
from agent_harness_lab.monitoring import state as monitoring_state
from agent_harness_lab.evaluation import bank_qualification as bankq, review_authority
from agent_harness_lab.evaluation.bank_tools import export_manifest
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.evaluation.oracle import identity
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.evaluation.regression_review import ReviewStore
from agent_harness_lab.evaluation.task_bank import validate
from agent_harness_lab.evaluation.integrity import digest, tree
if __package__:
    from .test_bank_qualification_controls import make_bank
    from .test_offline_evaluation import SOLVER
    from . import test_sandbox_kernel as kernel
else:
    from test_bank_qualification_controls import make_bank
    from test_offline_evaluation import SOLVER
    import test_sandbox_kernel as kernel


def proposal(root):
    bank=make_bank(root/'source')
    value=json.loads(bank.read_bytes())
    for task in value['tasks']:task.update(review_status='accepted',independence='reviewed',permission='approved')
    bank.write_text(json.dumps(value))
    declaration=dict(schema_version=1,author='synthetic-author',history_store=str(root/'history'),protocol=bankq.PROTOCOL,
        judgments={task['task_id']:dict(source_permission='approved',lineage='reviewed',split_separation='reviewed',
            evidence_refs=[dict(ref='synthetic:fixture',sha256='a'*64)]) for task in value['tasks']},
        external_exposure='unknown',pretraining_exposure='unknown')
    path=root/'declaration.json';path.write_text(json.dumps(declaration))
    return bank,path


def invoke(store,*args):
    output=io.StringIO()
    with redirect_stdout(output),redirect_stderr(io.StringIO()):
        try:code=main(['bank-review','--store',str(store),*map(str,args)])
        except SystemExit as error:code=error.code
    return code,json.loads(output.getvalue()) if output.getvalue() else None


class BankQualificationBoundaryTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.root=Path(folder.name);self.bank,self.declaration=proposal(self.root)
        self.store=self.root/'review'

    def test_labels_and_unsigned_intake_never_qualify(self):
        checked=validate(self.bank)
        self.assertTrue(checked['review_declarations_complete'])
        self.assertFalse(checked['certification_eligible'])
        code,result=invoke(self.store,'intake','--bank',self.bank,'--declaration',self.declaration)
        self.assertEqual(code,0);self.assertEqual(result['state'],'bank_intake')
        code,_=invoke(self.store,'prepare-review','--case',result['case_id'],'--reviewer','synthetic-reviewer')
        self.assertEqual(code,2)
        self.assertEqual(bankq.summary(ReviewStore(self.store),result['case_id'])['state'],'bank_intake')

    def test_unknown_required_judgment_and_false_global_secrecy_are_refused(self):
        original=json.loads(self.declaration.read_bytes())
        for field in ('source_permission','lineage','split_separation','external_exposure','pretraining_exposure'):
            with self.subTest(field=field):
                value=json.loads(json.dumps(original))
                if field in ('external_exposure','pretraining_exposure'):value[field]='untouched'
                else:next(iter(value['judgments'].values()))[field]='unknown'
                self.declaration.write_text(json.dumps(value))
                code,_=invoke(self.store,'intake','--bank',self.bank,'--declaration',self.declaration)
                self.assertEqual(code,2)
        self.assertFalse(self.store.exists())

    def test_development_revision_and_path_alias_do_not_become_final_intake(self):
        value=json.loads(self.bank.read_bytes());value['controller_profile']='regression-bank-v1'
        self.bank.write_text(json.dumps(value))
        self.assertEqual(invoke(self.store,'intake','--bank',self.bank,'--declaration',self.declaration)[0],2)
        value.pop('controller_profile');self.bank.write_text(json.dumps(value))
        alias=self.root/'alias';alias.symlink_to(self.bank.parent,target_is_directory=True)
        self.assertEqual(invoke(self.store,'intake','--bank',alias/'bank.json','--declaration',self.declaration)[0],2)

    def test_intake_retains_aggregate_copy_budget_across_tasks(self):
        bank=json.loads(self.bank.read_bytes());first=bank['tasks'][0]
        fixture=self.bank.parent/'second-fixture'
        shutil.copytree(self.bank.parent/first['fixture'],fixture)
        task_file=fixture/'task.json';value=json.loads(task_file.read_bytes())
        value['prompt']+=' Independently synthetic second task.';task_file.write_text(json.dumps(value))
        second=dict(first,task_id='second-task',root_id='second-root',fixture=fixture.name,fixture_digest=digest(fixture))
        bank['tasks'].append(second);self.bank.write_text(json.dumps(bank))
        declaration=json.loads(self.declaration.read_bytes())
        declaration['judgments']['second-task']=dict(declaration['judgments'][first['task_id']])
        self.declaration.write_text(json.dumps(declaration))
        one=sum((self.bank.parent/first[field]/name).stat().st_size
            for field in ('fixture','grader_bundle') for name in tree(self.bank.parent/first[field]))
        with patch('agent_harness_lab.evaluation.bank_revisions.MAX_REVISION_BYTES',one+1):
            code,_=invoke(self.store,'intake','--bank',self.bank,'--declaration',self.declaration)
        self.assertEqual(code,2)
        self.assertEqual(ReviewStore(self.store).case_ids(256),[])

    def test_private_qualification_cannot_be_relabelled_public(self):
        public=dict(provenance=dict(source_kind='synthetic',approved_publication=True,source_ref='synthetic:bank',derived_from_private=False))
        for marker in (dict(profile=bankq.PROFILE),dict(bank_qualification={})):
            for wrapper in (None,'receipt','analysis','context','binding'):
                value=dict(public);value.update(marker if wrapper is None else {wrapper:marker})
                self.assertFalse(public_export_allowed([value]))


@unittest.skipUnless(kernel.ENABLED,'explicit delegated kernel profile required')
class QualifiedBankKernelTests(kernel.KernelFixture):
    def qualified_comparison(self):
        bank,declaration=proposal(self.root)
        store=self.root/'review';runtime=self.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
        operator=self.root/'operator';operator.mkdir(mode=0o700)
        key=operator/'reviewer'
        subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-C','synthetic','-f',str(key)],
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5,check=True)
        public=' '.join(key.with_suffix('.pub').read_text().split()[:2])
        policy=operator/'policy.json'
        policy.write_text(json.dumps(dict(schema_version=1,profile=review_authority.POLICY,
            purpose=review_authority.BANK_PURPOSE,reviewers={'synthetic-reviewer':public})))
        policy.chmod(0o600)
        self.assertEqual(invoke(store,'trust-reviewers','--policy',policy)[0],0)
        code,intaken=invoke(store,'intake','--bank',bank,'--declaration',declaration)
        self.assertEqual(code,0);case_id=intaken['case_id']
        code,audited=invoke(store,'audit','--case',case_id,'--cgroup-root',self.cgroup_root,
            '--image-root',runtime,'--image-sha256',pin,'--disk-bytes',64*1024*1024,'--budget-seconds',60)
        self.assertEqual(code,0);self.assertEqual(audited['state'],'bank_audited')
        self.assertEqual(invoke(store,'audit','--case',case_id,'--cgroup-root',self.cgroup_root,
            '--image-root',runtime,'--image-sha256',pin,'--disk-bytes',64*1024*1024)[0],2)
        code,prepared=invoke(store,'prepare-review','--case',case_id,'--reviewer','synthetic-reviewer')
        self.assertEqual(code,0)
        request=Path(prepared['request_path'])
        subprocess.run(['/usr/bin/ssh-keygen','-Y','sign','-f',str(key),'-n','ahl.final-bank-review.v1',str(request)],
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5,check=True)
        code,qualified=invoke(store,'qualify','--case',case_id,'--request',request,'--signature',str(request)+'.sig')
        self.assertEqual(code,0);self.assertEqual(qualified['qualification'],'qualified_under_protocol')
        self.assertEqual(qualified['external_exposure'],'unknown')
        self.assertEqual(qualified['pretraining_exposure'],'unknown')
        actual=store/'revisions'/qualified['revision_sha256']/'bank.json'
        checked=validate(actual)
        self.assertFalse(checked['certification_eligible'])
        self.assertEqual(checked['admission']['qualification'],'qualified_under_protocol')
        exported=self.root/'export';manifest=export_manifest(actual,exported,max_trials=2)
        runner=evaluator()
        for version in ('baseline','candidate'):
            harness=self.root/version;harness.mkdir();(harness/'solve.py').write_text(SOLVER)
        for task in manifest['tasks']:task['offline_executor']=['/usr/bin/python3','-I','/inputs/harness/solve.py']
        tasks=manifest['tasks'];family=tasks[0]['family']
        spec=ExperimentSpec('synthetic-qualified-bank',tuple((t['id'],t['root'],t['family']) for t in tasks),
            1,13,2,2,0.,0.,.05,((family,1.),),runner.digest(self.root/'baseline'),runner.digest(self.root/'candidate'),
            identity(exported,tasks,manifest['grader_bundles']),pin)
        manifest['experiment']=asdict(spec)
        path=exported/'manifest.json';path.write_text(json.dumps(manifest))
        history=HistoryStore(self.root/'history')
        return actual,qualified,policy,runtime,pin,manifest,spec,path,history

    def test_reservation_expiry_after_real_exposure_check_does_not_publish(self):
        *_,path,history=self.qualified_comparison()
        ledger=history.access_ledger().path
        before=ledger.read_bytes() if ledger.exists() else None
        clock=[time.monotonic()];deadline=clock[0]+30
        exposure=bankq.exposure_check;expired=[]
        def finish_then_expire(*args,**kwargs):
            result=exposure(*args,**kwargs)
            clock[0]=deadline+1;expired.append(True)
            return result
        timed_out=False
        with patch('time.monotonic',side_effect=lambda:clock[0]),patch.object(bankq,'exposure_check',side_effect=finish_then_expire):
            try:history.reserve_access(path,self.root/'baseline',self.root/'candidate',budget_seconds=30)
            except TimeoutError:timed_out=True
        after=ledger.read_bytes() if ledger.exists() else None
        self.assertTrue(expired,'test must expire only after the actual exposure check')
        self.assertEqual((timed_out,after==before,history.case_ids(256)),(True,True,[]))

    def test_consumption_expiry_after_checkpoint_fsync_preserves_reservation(self):
        *_,runtime,pin,manifest,spec,path,history=self.qualified_comparison()
        reservation=history.reserve_access(path,self.root/'baseline',self.root/'candidate')
        ledger=history.access_ledger().path;before=ledger.read_bytes()
        clock=[time.monotonic()];deadline=clock[0]+30
        fsync=monitoring_state.os.fsync;expired=[]
        def fsync_then_expire(descriptor):
            result=fsync(descriptor)
            target=Path(os.readlink('/proc/self/fd/'+str(descriptor)))
            if target.parent==ledger.parent and target.name.startswith('.'+ledger.name):
                clock[0]=deadline+1;expired.append(True)
            return result
        timed_out=False
        with patch('time.monotonic',side_effect=lambda:clock[0]),patch.object(monitoring_state.os,'fsync',side_effect=fsync_then_expire):
            try:
                history.run(path,self.root/'baseline',self.root/'candidate',cgroup_root=self.cgroup_root,
                    image_root=runtime,image_sha256=pin,disk_bytes=64*1024*1024,budget_seconds=30,
                    access_reservation=reservation['reservation_id'])
            except TimeoutError:timed_out=True
        self.assertTrue(expired,'test must expire only after actual checkpoint temporary-file fsync')
        after=ledger.read_bytes()
        status=history.access_ledger().managed_status(reservation['reservation_id'])
        self.assertEqual((timed_out,after==before,status['state'],history.case_ids(256)),(True,True,'reserved',[]))
        self.assertEqual(list(ledger.parent.glob('.'+ledger.name+'*')),[])

    def test_signed_native_bank_qualification_is_consumed_by_protected_diagnostic(self):
        actual,qualified,policy,runtime,pin,manifest,spec,path,history=self.qualified_comparison()
        reservation=history.reserve_access(path,self.root/'baseline',self.root/'candidate')
        ident=reservation['reservation_id']
        with self.assertRaises(ValueError):
            other=HistoryStore(self.root/'other-history');other.reserve_access(path,self.root/'baseline',self.root/'candidate')
        result=history.run(path,self.root/'baseline',self.root/'candidate',cgroup_root=self.cgroup_root,
            image_root=runtime,image_sha256=pin,disk_bytes=64*1024*1024,budget_seconds=60,access_reservation=ident)
        self.assertEqual(result['state'],'sealed')
        self.assertFalse(result['automatic_promotion'])
        case=history.case(result['run_id'])
        receipt=json.loads((case/'protected-access.json').read_bytes())
        self.assertEqual(receipt['state'],'retired')
        self.assertEqual(receipt['binding']['bank_qualification']['metadata']['qualification_event_sha256'],qualified['qualification_event_sha256'])
        self.assertEqual(history.access_ledger().managed_status(ident)['state'],'retired')
        with self.assertRaises(ValueError):history.reserve_access(path,self.root/'baseline',self.root/'candidate')
        manifest['experiment']=asdict(replace(spec,profile='certification',partition='certification'))
        path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):history.reserve_access(path,self.root/'baseline',self.root/'candidate')
        policy.unlink()
        self.assertFalse(validate(actual)['admission']['current_eligible'])
