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


def test_final_author_exception_never_exempts_development_or_unresolved_work():
    from agent_harness_lab.evaluation.certification_access import _managed_available
    asset='a'*64;final='b'*64;development='c'*64
    state={'constructions':{final:dict(state='complete',role='final_bank_author',children=[asset])}}
    with pytest.raises(ValueError,match='development assets'):_managed_available(state,[asset])
    _managed_available(state,[asset],final_author=final)
    state['constructions'][development]=dict(state='complete',role='evaluator_author',children=[asset])
    with pytest.raises(ValueError,match='development assets'):_managed_available(state,[asset],final_author=final)
    state['constructions'][development]=dict(state='pending',role='evaluator_author',children=[])
    with pytest.raises(ValueError,match='pending'):_managed_available(state,[asset],final_author=final)


def test_final_author_scope_refuses_later_dispatch(tmp_path):
    from agent_harness_lab.evaluation import construction_access
    from unittest.mock import patch
    access=construction_access.WorkflowAccess.__new__(construction_access.WorkflowAccess)
    data={'deadline':0,'store':None,'final_author_started':True}
    with patch.object(construction_access,'_scope',return_value=data):
        with pytest.raises(ValueError,match='cannot dispatch further roles'):
            access._run('proposer',tmp_path,tmp_path/'out')


from tests.integration.sandbox import test_sandbox_kernel as kernel


def native_qualified_bank(fixture,cgroup):
    import subprocess
    import time
    from agent_harness_lab.evaluation import bank_qualification as bankq, construction_access, responses, review_authority
    from agent_harness_lab.evaluation.instruction_context import PROFILE as INSTRUCTION_PROFILE
    from agent_harness_lab.evaluation.permission_audit import PROFILE as PERMISSION_PROFILE
    from agent_harness_lab.evaluation.regression_review import ReviewStore
    from agent_harness_lab.evaluation.sandbox import SandboxBackend
    from agent_harness_lab.evaluation.reference_grader import PROGRAM
    from agent_harness_lab.promotion import prerequisites
    from tests.integration.sandbox import test_sandbox_inference as wire
    from tests.integration.evaluation.test_bank_qualification_final import invoke

    root=fixture.root;args=fixture.args
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
    script=f'''from pathlib import Path
import json
bundle=Path('oracle');bundle.mkdir()
(bundle/'grade.py.txt').write_text({PROGRAM!r})
(bundle/'expected.json').write_text(json.dumps(dict(complete=True,scenario=1)))
tasks=[];tests=[];controls=[]
for name in ('heldout-a','heldout-b','heldout-c'):
 fixture=Path(name);fixture.mkdir()
 (fixture/'task.json').write_text(json.dumps(dict(prompt='Produce '+name,scenario=1)))
 tasks.append(dict(id=name,split='heldout',fixture=name,prompt='Produce '+name,artifacts=['result.json']))
 for state in ('broken','partial','complete'):
  test=Path(name+'-'+state);test.mkdir()
  (test/'result.json').write_text(json.dumps(dict(complete=state=='complete',scenario=1)))
  tests.append(dict(task=name,state=state,fixture=str(test)))
for state in ('broken','partial','complete','alternative_valid','superficial'):
 folder=bundle/state;folder.mkdir()
 (folder/'result.json').write_text(json.dumps(dict(complete=state in ('complete','alternative_valid'),scenario=1)))
 controls.append(dict(state=state,fixture=state))
(bundle/'controls.json').write_text(json.dumps(controls))
Path('manifest.json').write_text(json.dumps(dict(skills=[],harness_paths=[],tasks=tasks,grader_self_tests=tests,
 grader_bundles={{task['id']:dict(path='oracle') for task in tasks}})))
'''
    calls=[]
    def transport(_key,body,_size,_deadline):
        calls.append(json.loads(body));ident=str(len(calls))
        if len(calls)==1:
            return wire.reply([wire.call(ident,arguments=json.dumps(dict(argv=['/usr/bin/python3','-c',script,'1','complete'])))])
        return wire.reply([wire.message('final-'+ident)])
    client=responses.Client(fixture.settings,None,deadline,transport=transport)
    private=root/'private-controls';private.mkdir(mode=0o700)
    controls={}
    for index,name in enumerate(('heldout-a','heldout-b','heldout-c')):
        hidden=private/name;hidden.mkdir()
        (hidden/'result.json').write_text(json.dumps(dict(complete=True,scenario=987+index)))
        controls[name]=[dict(path=name,sha256=digest(hidden))]
    manifest=private/'controls.json'
    manifest.write_text(json.dumps(dict(schema_version=1,curator='synthetic-curator',
        evidence_refs=[dict(ref='synthetic:curation',sha256='b'*64)],tasks=controls)))
    declaration=root/'bank-declaration.json'
    declaration.write_text(json.dumps(dict(schema_version=1,author='synthetic-author',history_store=str(args.evaluation_store),
        protocol=bankq.PROTOCOL,judgments={name:dict(source_permission='approved',lineage='reviewed',
        split_separation='reviewed',evidence_refs=[dict(ref='synthetic:lineage',sha256='a'*64)]) for name in controls},
        external_exposure='unknown',pretraining_exposure='unknown')))
    meta={name:dict(root_id=name,family='family',project_group='project',time_group='time',
        grader_version='v1',source_kind='synthetic',source_ref='synthetic:task',permission='approved',
        reviewer='synthetic-reviewer',review_status='accepted',independence='reviewed',safety=['synthetic']) for name in controls}
    scope=prerequisites._begin(baseline,image,deadline)
    try:
        access=construction_access.WorkflowAccess(args,output,baseline,client,backend,deadline)
        try:
            _,_,converted=access.author(context,output/'author',bank_conversion=dict(metadata=meta,
                controls_manifest=manifest,output=root/'converted',review_store=root/'review',declaration=declaration))
        finally:access.retire()
    finally:scope.end()
    store=ReviewStore(root/'review');case=converted['case']['case_id']
    code,audited=invoke(store.root,'audit','--case',case,'--cgroup-root',cgroup,
        '--image-root',runtime,'--image-sha256',image,'--disk-bytes',64*1024*1024,'--budget-seconds',120)
    assert code==0 and audited['state']=='bank_audited'
    operator=root/'operator';operator.mkdir(mode=0o700);key=operator/'reviewer'
    subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True,timeout=10)
    policy=operator/'policy.json';policy.write_text(json.dumps(dict(schema_version=1,profile=review_authority.POLICY,
        purpose=review_authority.BANK_PURPOSE,reviewers={'synthetic-reviewer':' '.join(key.with_suffix('.pub').read_text().split()[:2])})))
    policy.chmod(0o600);assert invoke(store.root,'trust-reviewers','--policy',policy)[0]==0
    code,prepared=invoke(store.root,'prepare-review','--case',case,'--reviewer','synthetic-reviewer')
    assert code==0;request=Path(prepared['request_path'])
    subprocess.run(['/usr/bin/ssh-keygen','-Y','sign','-f',str(key),'-n','ahl.final-bank-review.v1',str(request)],check=True,timeout=10)
    code,result=invoke(store.root,'qualify','--case',case,'--request',request,'--signature',str(request)+'.sig','--budget-seconds',60)
    assert code==0 and result['qualification']=='qualified_under_protocol'
    assert result['qualification']=='qualified_under_protocol'
    assert result['external_exposure']=='unknown'
    from agent_harness_lab.evaluation.oracle_history import HistoryStore
    actual=store.root/'revisions'/result['revision_sha256']/'bank.json'
    return actual,result,policy,runtime,image,HistoryStore(args.evaluation_store)


@pytest.mark.skipif(not kernel.ENABLED,reason='explicit delegated native scope required')
def test_native_author_conversion_and_signed_review(tmp_path):
    from tests.integration.sandbox import test_sandbox_construction as roles
    fixture=roles.ControlledWorkflowTests();fixture.setUp()
    try:
        with kernel.kernel_subtree() as cgroup:
            _,result,_,_,_,_=native_qualified_bank(fixture,cgroup)
            assert result['qualification']=='qualified_under_protocol'
            assert result['external_exposure']=='unknown'
    finally:fixture.doCleanups()
