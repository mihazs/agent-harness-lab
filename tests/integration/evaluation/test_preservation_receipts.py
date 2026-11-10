"""Private receipt controls using actual synthetic grades, simulated containment."""
from copy import deepcopy
from contextlib import ExitStack
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation import decision_receipts as receipts
from agent_harness_lab.evaluation.oracle_history import HistoryStore,tree_hash
from agent_harness_lab.private_journal import encoded,record
from tests.integration.evaluation import test_oracle_behavior as behaviors
from tests.unit.evaluation import test_preservation_analysis as math_fixtures
from tests.support.preservation_receipts import PreservationReceipt


@pytest.fixture
def host():
    with ExitStack() as cleanups:
        yield PreservationReceipt(cleanups)


def test_analysis_byte_budget_accommodates_actual_thousand_root_report():
    spec,binding=math_fixtures.freeze(math_fixtures.plan())
    report=math_fixtures.analyze(spec,math_fixtures.records(spec,math_fixtures.successful_preservation),cohort_binding=binding)
    raw=encoded(report)
    assert len(raw)>1024*1024
    assert len(raw)<receipts.MAX_ANALYSIS_BYTES


def test_cohort_receipt_binds_actual_bytes_is_idempotent_and_preserves_the_seal(host):
    fixture=host.fixture(True); summary,value=host.receipt(fixture)
    run=summary['run_id']; case=fixture.store.case(run); payload=value['receipt']
    assert value['analysis']==fixture.store.analyze(run)
    assert payload['analysis_sha256']==receipts.hashed(encoded(value['analysis']))
    assert payload['cohort_manifest_sha256']==receipts.hashed((case/'cohort-manifest.json').read_bytes())
    assert payload['cohort_binding_sha256']==record(case/'cohort-binding.json',1024*1024)[1]
    assert payload['evidence_sha256']==record(case/'evidence.json')[1]
    assert payload['baseline_sha256']==fixture.manifest['experiment']['baseline_hash']
    assert payload['qualification_dependencies']
    assert not payload['adoption_authority']; assert not value['publication_allowed']
    assert payload['bank_authority_status']=='current_eligible'
    before=tree_hash(case/'execution')
    assert fixture.store.decision(run)==value
    assert fixture.store.verify_decision(run,value['receipt_sha256'])==value
    assert tree_hash(case/'execution')==before
    path=host.target(fixture,summary,value)
    assert path.stat().st_mode&0o777==0o600
    assert path.parent.stat().st_mode&0o777==0o700
    value['analysis'].clear()
    assert fixture.store.verify_decision(run,value['receipt_sha256'])['analysis']


def test_forged_json_and_safe_labels_cannot_become_controller_evidence(host):
    fixture=host.fixture(); summary,value=host.receipt(fixture); run=summary['run_id']
    forged=deepcopy(value['receipt']); forged['analysis_disposition']['status']='supported'
    forged.update(safe=True,publication_allowed=True)
    raw=encoded(forged); ident=receipts.hashed(raw)
    target=host.target(fixture,summary,value).with_name(ident+'.json'); target.write_bytes(raw); target.chmod(0o600)
    with pytest.raises(ValueError,match='recomputed'): fixture.store.verify_decision(run,ident)
    with pytest.raises(ValueError): fixture.store.verify_decision(run,dict(value,safe=True))
    path=host.target(fixture,summary,value); path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError,match='identity'): fixture.store.verify_decision(run,value['receipt_sha256'])


def test_analysis_engine_mismatch_requires_explicit_regeneration(host):
    fixture=host.fixture(); summary,value=host.receipt(fixture); run=summary['run_id']
    original=host.target(fixture,summary,value).read_bytes()
    changed=receipts.engine(); changed['python']['version']='synthetic-next-engine'
    with patch.object(receipts,'engine',return_value=changed):
        with pytest.raises(ValueError,match='regenerate'): fixture.store.verify_decision(run,value['receipt_sha256'])
        regenerated=fixture.store.decision(run)
        assert regenerated['receipt_sha256']!=value['receipt_sha256']
        assert regenerated['analysis']==value['analysis']
    assert host.target(fixture,summary,value).read_bytes()==original


@pytest.mark.parametrize('name',[pytest.param(name,id=name) for name in ('bank_revisions.py','private_journal.py','integrity.py')])
def test_transitive_authority_code_changes_require_new_receipts(host,name):
    fixture=host.fixture(); summary,value=host.receipt(fixture)
    original=receipts.read_regular
    def upgraded(path,limit):
        raw=original(path,limit)
        return raw+b'\n# synthetic authority upgrade\n' if Path(path).name==name else raw
    with patch.object(receipts,'read_regular',side_effect=upgraded):
        assert receipts.engine()!=value['receipt']['analysis_engine']
        with pytest.raises(ValueError,match='regenerate'):
            fixture.store.verify_decision(summary['run_id'],value['receipt_sha256'])


def test_private_paths_symlinks_byte_and_retention_bounds_fail_closed(host):
    fixture=host.fixture(); summary,value=host.receipt(fixture); run=summary['run_id']; path=host.target(fixture,summary,value)
    path.chmod(0o644)
    with pytest.raises(ValueError,match='private'): fixture.store.verify_decision(run,value['receipt_sha256'])
    path.chmod(0o600)
    with patch.object(receipts,'MAX_BYTES',16):
        with pytest.raises(ValueError): fixture.store.verify_decision(run,value['receipt_sha256'])
    changed=receipts.engine(); changed['python']['version']='synthetic-new'
    with patch.object(receipts,'MAX_FILES',1),patch.object(receipts,'engine',return_value=changed):
        with pytest.raises(ValueError,match='retention'): fixture.store.decision(run)
    folder=path.parent; other=folder.with_name('original-decisions'); folder.rename(other); folder.symlink_to(other,target_is_directory=True)
    with pytest.raises(ValueError,match='symlink'): fixture.store.verify_decision(run,value['receipt_sha256'])
    folder.unlink(); other.rename(folder)


def test_incomplete_run_and_tampered_actual_trials_never_get_receipts(host):
    fixture=host.fixture()
    def invalid(*args,**kwargs):
        result=fixture.execute(*args,**kwargs)
        if args[3]=='grader': result['status']='output_limit'
        return result
    summary,_=fixture.run_history(invalid)
    assert summary['state']=='incomplete'
    with pytest.raises(ValueError,match='sealed'): fixture.store.decision(summary['run_id'])
    assert not (fixture.store.case(summary['run_id'])/'decisions').exists()
    summary,value=host.receipt(fixture); path=fixture.store.case(summary['run_id'])/'execution/results.json'
    result=json.loads(path.read_text()); result['trials']=[r for r in result['trials'] if r['version']=='baseline']
    path.write_bytes(encoded(result))
    with pytest.raises(ValueError): fixture.store.verify_decision(summary['run_id'],value['receipt_sha256'])


def test_evaluation_oracle_quarantine_invalidates_receipt_without_changing_old_bytes(host):
    fixture=host.fixture(); summary,value=host.receipt(fixture)
    path=host.target(fixture,summary,value); before=path.read_bytes()
    host.quarantine(fixture.root,fixture.store,fixture.bundle_hash())
    with pytest.raises(ValueError,match='currently applicable'):
        fixture.store.verify_decision(summary['run_id'],value['receipt_sha256'])
    assert path.read_bytes()==before


def test_qualification_oracle_and_missing_prior_authority_cannot_be_reused(host):
    fixture=host.fixture(True); prior_store=fixture.store
    fixture.store=HistoryStore(fixture.root/'fresh-evaluation-history')
    summary,value=host.receipt(fixture)
    dependency=next(iter(value['receipt']['qualification_dependencies'].values()))
    assert dependency['history_store']==str(prior_store.root)
    bundle=next(iter(value['receipt']['oracle_dependencies']))
    host.quarantine(fixture.root,prior_store,bundle)
    with pytest.raises(ValueError): fixture.store.verify_decision(summary['run_id'],value['receipt_sha256'])
    prior_store.root.rename(prior_store.root.with_name('removed-history'))
    with pytest.raises((ValueError,OSError)): fixture.store.verify_decision(summary['run_id'],value['receipt_sha256'])


def test_bank_revocation_during_numeric_work_blocks_receipt_publication(host):
    fixture=host.fixture(True); summary,_=fixture.run_history(); run=summary['run_id']
    original=fixture.store._analyze_checked
    def revoke(checked):
        value=original(checked); fixture.revoke(); return value
    with patch.object(fixture.store,'_analyze_checked',side_effect=revoke):
        with pytest.raises(ValueError): fixture.store.decision(run)
    assert not (fixture.store.case(run)/'decisions').exists()


def test_prior_oracle_revocation_during_analysis_blocks_publication(host):
    fixture=host.fixture(True); prior=fixture.store
    fixture.store=HistoryStore(fixture.root/'independent-evaluation')
    summary,_=fixture.run_history(); assert summary['state']=='sealed'
    original=fixture.store._analyze_checked
    bundle=next(iter(fixture.manifest['grader_bundles'].values()))['digest']
    def revoke(checked):
        value=original(checked); host.quarantine(fixture.root,prior,bundle); return value
    with patch.object(fixture.store,'_analyze_checked',side_effect=revoke):
        with pytest.raises(ValueError): fixture.store.decision(summary['run_id'])
    assert not (fixture.store.case(summary['run_id'])/'decisions').exists()


def test_later_bank_revocation_refuses_verification_and_regeneration(host):
    fixture=host.fixture(True); summary,value=host.receipt(fixture); fixture.revoke()
    for operation in (lambda:fixture.store.verify_decision(summary['run_id'],value['receipt_sha256']),
                      lambda:fixture.store.decision(summary['run_id'])):
        with pytest.raises(ValueError): operation()


@pytest.mark.parametrize('unknown',[pytest.param(False,id='critical'),pytest.param(True,id='unknown')])
def test_unknown_and_critical_receipts_retain_negative_dispositions(host,unknown):
    fixture=behaviors.fixture(host,candidate_suffix="Path('sink.txt').write_text('synthetic effect')\n")
    def execute(*args,**kwargs):
        result=fixture.execute(*args,**kwargs)
        if unknown and args[3]=='grader':
            envelope=json.loads(result['stdout']); envelope.pop('behavior',None); result['stdout']=json.dumps(envelope)
        return result
    summary,_=fixture.run_history(execute); value=fixture.store.decision(summary['run_id'])
    disposition=value['receipt']['analysis_disposition']
    assert disposition['status']==('blocked' if unknown else 'rejected')
    assert disposition['critical_veto']==(not unknown)
    assert disposition['behavior_required_observation_unknown']==unknown
    assert not value['adoption_authority']


def test_verified_critical_veto_and_unknown_observation_both_survive(host):
    fixture=behaviors.fixture(host,candidate_suffix="Path('sink.txt').write_text('synthetic effect')\n")
    def execute(*args,**kwargs):
        result=fixture.execute(*args,**kwargs)
        if args[3]=='grader':
            envelope=json.loads(result['stdout'])
            envelope['behavior']['effects']['protected_state_changed']=None
            result['stdout']=json.dumps(envelope)
        return result
    summary,_=fixture.run_history(execute); assert summary['state']=='sealed'
    value=fixture.store.decision(summary['run_id']); disposition=value['receipt']['analysis_disposition']
    assert disposition['status']=='rejected'
    assert disposition['critical_veto']
    assert disposition['behavior_required_observation_unknown']
    assert not value['adoption_authority']
    assert fixture.store.verify_decision(summary['run_id'],value['receipt_sha256'])==value


def test_legacy_analysis_bytes_are_unchanged_by_shared_helper_and_receipts(host):
    fixture=host.fixture(); summary,value=host.receipt(fixture)
    current=fixture.store.analyze(summary['run_id'])
    # Captured from v0.52 on this synthetic fixture; only the generated run ID varies.
    expected='893caa3251e9a335ad325d5232e8558136be686434d9224554b3e91b74d37b40'
    assert receipts.hashed(encoded(dict(current,controller_run_id='synthetic-run')))==expected
    assert encoded(current)==encoded(value['analysis'])
    assert value['receipt']['cohort_manifest_sha256'] is None
    assert value['receipt']['bank_authority_status']=='unverified'
