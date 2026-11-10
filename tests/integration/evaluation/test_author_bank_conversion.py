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
