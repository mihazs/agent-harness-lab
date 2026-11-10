"""Synthetic oracle reuse checks against the real access ledger."""
import json
import subprocess
import sys

import pytest

from agent_harness_lab.evaluation import certification_access as ledger
from agent_harness_lab.evaluation.reference_grader import PROGRAM, validate


def assets(root, name, reference, control):
    fixture=root/(name+'-fixture');fixture.mkdir();(fixture/'task').write_text(name)
    oracle=root/(name+'-oracle');oracle.mkdir()
    (oracle/'grade.py.txt').write_text(PROGRAM)
    (oracle/'expected.json').write_text(reference)
    (oracle/'controls.json').write_text(control)
    tasks=[dict(id=name,prompt=name,fixture=fixture.name)]; bundles={name:dict(path=oracle.name)}
    return ledger.exposure(root,tasks,bundles)[0],ledger.oracle_references(root,tasks,bundles)


def test_changed_controls_do_not_hide_reused_reference_in_real_ledger(tmp_path):
    prior,prior_refs=assets(tmp_path,'prior','{"a":1,"b":2}','{"prior":true}')
    _,reused=assets(tmp_path,'reused','{ "b": 2, "a": 1 }','{"new":true}')
    _,distinct=assets(tmp_path,'distinct','{"a":3}','{"other":true}')
    path=tmp_path/'ledger.json'; pin='a'*64
    ident=ledger.begin_construction(path,'evaluator_author',pin)
    ledger.finish_construction(path,ident,pin,prior,'b'*64,references=prior_refs)
    with ledger._locked(path) as (_,state):
        with pytest.raises(ValueError,match='previously exposed'):
            ledger.reference_guard(state,reused,None)
        ledger.reference_guard(state,distinct,None)


def test_uncovered_legacy_history_is_never_promoted(tmp_path):
    path=tmp_path/'ledger.json'; pin='a'*64
    path.write_text(json.dumps(dict(schema_version=2,profile=ledger.PROFILE,
        reservations={},observed_assets={pin:'b'*64})))
    path.chmod(0o600)
    with ledger._locked(path) as (_,state):
        assert state['reference_coverage'] is False


def test_trusted_program_cannot_import_shadow_modules(tmp_path):
    (tmp_path/'grade.py.txt').write_text(PROGRAM)
    (tmp_path/'expected.json').write_text('{"answer":1}')
    (tmp_path/'result.json').write_text('{"answer":1}')
    (tmp_path/'json.py').write_text('raise RuntimeError("shadow imported")')
    (tmp_path/'pathlib.py').write_text('raise RuntimeError("shadow imported")')
    validate(tmp_path)
    run=subprocess.run([sys.executable,'-I','-S',str(tmp_path/'grade.py.txt'),str(tmp_path),
        str(tmp_path/'expected.json')],capture_output=True,text=True,timeout=5)
    assert run.returncode == 0 and json.loads(run.stdout)['outcome'] is True
    (tmp_path/'grade.py.txt').write_text(PROGRAM+'\n')
    with pytest.raises(ValueError,match='unsupported reference grader'):validate(tmp_path)


def test_strict_grader_command_preserves_owned_receipt_matching():
    from agent_harness_lab.evaluation import controller_receipts as receipts
    run={'status':'completed'}
    command=receipts.GRADER_ARGV[:2]+['-S']+receipts.GRADER_ARGV[2:]
    event=dict(role='grader',requested_argv_sha256=receipts._hash(command),result_sha256=receipts._hash(run),
        input_digests=dict(oracle='oracle',fixture='fixture'))
    assert receipts._matches_grade(run,event,'oracle','fixture',True)
    event['requested_argv_sha256']=receipts._hash(command+['unexpected'])
    assert not receipts._matches_grade(run,event,'oracle','fixture',True)
