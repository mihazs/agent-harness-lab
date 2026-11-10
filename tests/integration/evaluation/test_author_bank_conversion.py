"""Synthetic conversion contract; live kernel custody is tested separately."""
import json
from pathlib import Path

import pytest

from agent_harness_lab.evaluation import author_bank_conversion as conversion
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.bank_heldout import freeze
from agent_harness_lab.evaluation.task_bank import validate
from tests.integration.evaluation.test_bank_qualification_controls import make_bank
from tests.integration.evaluation.test_bank_qualification_final import heldout_manifest


def authored(root):
    path=make_bank(root/'source')
    task=json.loads(path.read_bytes())['tasks'][0]
    task.update(review_status='accepted',independence='reviewed',permission='approved')
    manifest=dict(tasks=[dict(id=task['task_id'],split='heldout',fixture=task['fixture'],artifacts=task['artifacts'])],
        grader_bundles={task['task_id']:dict(path=task['grader_bundle'])})
    (path.parent/'manifest.json').write_text(json.dumps(manifest))
    metadata={task['task_id']:{key:task[key] for key in conversion.METADATA}}
    return path.parent,task,metadata


def test_conversion_preserves_frozen_author_fixtures_and_graders(tmp_path):
    source,task,metadata=authored(tmp_path)
    target=tmp_path/'output';target.mkdir()
    path,pin=conversion.build(source,metadata,target)
    bank=json.loads(path.read_bytes());converted=bank['tasks'][0]
    assert validate(path)['bank_sha256']==pin
    assert digest(target/converted['fixture'])==digest(source/task['fixture'])
    assert digest(target/converted['grader_bundle'])==digest(source/task['grader_bundle'])
    assert converted['partition']=='certification'


def test_conversion_rejects_missing_declarations(tmp_path):
    source,_,_=authored(tmp_path)
    target=tmp_path/'output';target.mkdir()
    with pytest.raises(ValueError,match='metadata'):
        conversion.build(source,{},target)


def test_control_bytes_exposed_in_author_input_are_rejected(tmp_path):
    source,_,metadata=authored(tmp_path)
    target=tmp_path/'output';target.mkdir()
    bank,_=conversion.build(source,metadata,target)
    manifest=heldout_manifest(tmp_path,bank)
    heldout=tmp_path/'frozen'
    controls,_=freeze(manifest,bank,'synthetic-author',heldout)
    item=next(iter(controls['tasks'].values()))[0]
    raw=(heldout/item['path']/'result.json').read_bytes()
    conversion.check_unexposed(target,controls,heldout,{b'unrelated'})
    with pytest.raises(ValueError,match='present in an author input'):
        conversion.check_unexposed(target,controls,heldout,{b'prefix '+raw+b' suffix'})


def test_forged_or_retired_stage_never_issues_conversion(tmp_path):
    for stage in (None,{},object()):
        with pytest.raises(ValueError,match='actual live author stage'):
            conversion.convert(stage,None,None,{},Path(tmp_path/'controls'),tmp_path/'output',tmp_path/'review',tmp_path/'decl')
    from agent_harness_lab.evaluation.construction_access import _Stage
    with pytest.raises(ValueError,match='actual live author stage'):
        conversion.convert(_Stage(),None,None,{},Path(tmp_path/'controls'),tmp_path/'output',tmp_path/'review',tmp_path/'decl')
    with pytest.raises(ValueError,match='actual live author conversion handoff'):
        conversion.consume({},tmp_path/'bank.json','a'*64)
    assert not (tmp_path/'output').exists()


def test_permission_audit_is_required_before_author_dispatch(tmp_path):
    from agent_harness_lab.evaluation import construction_access
    from types import SimpleNamespace
    from unittest.mock import patch
    access=construction_access.WorkflowAccess.__new__(construction_access.WorkflowAccess)
    config=dict(metadata={},controls_manifest=tmp_path/'controls',output=tmp_path/'out',
                review_store=tmp_path/'review',declaration=tmp_path/'declaration')
    data={'deadline':0,'store':None,'backend':SimpleNamespace(permission_audit=None)}
    with patch.object(construction_access,'_scope',return_value=data):
        with pytest.raises(ValueError,match='live native scope and permission audit'):
            access._run('evaluator_author',tmp_path,tmp_path/'out',bank_conversion=config)


from tests.integration.sandbox import test_sandbox_kernel as kernel


@pytest.mark.skipif(not kernel.ENABLED,reason='explicit delegated native scope required')
def test_native_author_conversion_and_signed_review(tmp_path):
    import subprocess
    import time
    from agent_harness_lab.evaluation import bank_qualification as bankq, construction_access, responses, review_authority
    from agent_harness_lab.evaluation.instruction_context import PROFILE as INSTRUCTION_PROFILE
    from agent_harness_lab.evaluation.permission_audit import PROFILE as PERMISSION_PROFILE
    from agent_harness_lab.evaluation.regression_review import ReviewStore
    from agent_harness_lab.evaluation.sandbox import SandboxBackend
    from agent_harness_lab.promotion import prerequisites
    from tests.integration.sandbox import test_sandbox_construction as roles, test_sandbox_inference as wire

    fixture=roles.ControlledWorkflowTests();fixture.setUp()
    try:
        root=fixture.root;args=fixture.args
        with kernel.kernel_subtree() as cgroup:
            runtime=root/'native-runtime';image=kernel.pinned_runtime(runtime)['sha256']
            args.evaluation_store=root/'native-history';args.cgroup_root=cgroup;args.image_root=runtime
            args.image_sha256=image;args.disk_bytes=64*1024*1024;args.permission_audit=PERMISSION_PROFILE
            fixture.settings['instruction_protocol']=INSTRUCTION_PROFILE;fixture.write_settings()
            baseline=root/'native-baseline';baseline.mkdir();(baseline/'AGENTS.md').write_text('Synthetic baseline.')
            context=root/'native-context';context.mkdir();(context/'summary.json').write_text('{}')
            output=root/'native-workflow';output.mkdir(mode=0o700)
            deadline=time.monotonic()+180
            backend=SandboxBackend(cgroup_root=cgroup,image_root=runtime,image_sha256=image,
                disk_bytes=64*1024*1024,permission_audit=PERMISSION_PROFILE)
            script=roles.AUTHOR+'''\nfrom pathlib import Path\nimport json\nf=Path('heldout/task.json');v=json.loads(f.read_text());v['prompt']='Produce the synthetic result';f.write_text(json.dumps(v))\nbundle=Path('oracle')\n(bundle/'grade.py.txt').write_text("""from pathlib import Path\nimport json,sys\nf=Path(sys.argv[1])/'result.json'\na=json.loads(f.read_text()) if f.exists() else {}\noutcome=a.get('complete') is True and a.get('scenario')==1\nprint(json.dumps(dict(schema_version=1,valid=True,outcome=outcome,grader_id='synthetic',grader_version='v1')))\nsys.exit(0 if outcome else 1)\n""")\ncontrols=[]\nfor state in ('broken','partial','complete','alternative_valid','superficial'):\n d=bundle/state;d.mkdir();(d/'result.json').write_text(json.dumps(dict(complete=state in ('complete','alternative_valid'),scenario=1)))\n controls.append(dict(state=state,fixture=state))\n(bundle/'controls.json').write_text(json.dumps(controls))\n'''
            calls=[]
            def transport(_key,body,_size,_deadline):
                calls.append(json.loads(body));ident=str(len(calls))
                if len(calls)==1:
                    return wire.reply([wire.call(ident,arguments=json.dumps(dict(argv=['/usr/bin/python3','-c',script,'1','complete'])))])
                return wire.reply([wire.message('final-'+ident)])
            client=responses.Client(fixture.settings,None,deadline,transport=transport)
            private=root/'private-controls';private.mkdir(mode=0o700)
            hidden=private/'control';hidden.mkdir();(hidden/'result.json').write_text('{"complete":true,"scenario":987}')
            manifest=private/'controls.json'
            manifest.write_text(json.dumps(dict(schema_version=1,curator='synthetic-curator',
                evidence_refs=[dict(ref='synthetic:curation',sha256='b'*64)],
                tasks={'heldout':[dict(path='control',sha256=digest(hidden))]})))
            declaration=root/'bank-declaration.json'
            declaration.write_text(json.dumps(dict(schema_version=1,author='synthetic-author',history_store=str(args.evaluation_store),
                protocol=bankq.PROTOCOL,judgments={'heldout':dict(source_permission='approved',lineage='reviewed',
                split_separation='reviewed',evidence_refs=[dict(ref='synthetic:lineage',sha256='a'*64)])},
                external_exposure='unknown',pretraining_exposure='unknown')))
            meta={'heldout':dict(root_id='root',family='family',project_group='project',time_group='time',
                grader_version='v1',source_kind='synthetic',source_ref='synthetic:task',permission='approved',
                reviewer='synthetic-reviewer',review_status='accepted',independence='reviewed',safety=['synthetic'])}
            scope=prerequisites._begin(baseline,image,deadline)
            try:
                access=construction_access.WorkflowAccess(args,output,baseline,client,backend,deadline)
                try:
                    _,_,converted=access.author(context,output/'author',bank_conversion=dict(metadata=meta,
                        controls_manifest=manifest,output=root/'converted',review_store=root/'review',declaration=declaration))
                finally:access.retire()
            finally:scope.end()
            store=ReviewStore(root/'review');case=converted['case']['case_id']
            bankq.audit(store,case,backend,budget_seconds=120)
            operator=root/'operator';operator.mkdir(mode=0o700);key=operator/'reviewer'
            subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True,timeout=10)
            policy=operator/'policy.json';policy.write_text(json.dumps(dict(schema_version=1,profile=review_authority.POLICY,
                purpose=review_authority.BANK_PURPOSE,reviewers={'synthetic-reviewer':' '.join(key.with_suffix('.pub').read_text().split()[:2])})))
            policy.chmod(0o600);bankq.install_policy(store,policy)
            prepared=bankq.prepare(store,case,'synthetic-reviewer');request=Path(prepared['request_path'])
            subprocess.run(['/usr/bin/ssh-keygen','-Y','sign','-f',str(key),'-n','ahl.final-bank-review.v1',str(request)],check=True,timeout=10)
            result=bankq.qualify(store,case,request,Path(str(request)+'.sig'),budget_seconds=60)
            assert result['qualification']=='qualified_under_protocol'
            assert result['external_exposure']=='unknown'
    finally:fixture.doCleanups()
