"""Synthetic immutable application, with no paid inference or historical proof."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation.bank_corrections import PROFILE
from agent_harness_lab.evaluation.bank_revisions import controls_for_manifest,task_identity
from agent_harness_lab.evaluation.bank_tools import export_manifest
from agent_harness_lab.evaluation.grader_correction import CorrectionStore
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.oracle_history import HistoryStore,bank_authority
from agent_harness_lab.evaluation.task_bank import validate
from tests.integration.evaluation import test_regression_admission as admissions


@pytest.fixture
def correction():
    from tests.support.bank_correction import bank_correction

    with bank_correction() as value:
        yield value


@pytest.fixture
def admission():
    case = admissions.RegressionAdmissionTests(); case.setUp()
    yield case
    case.doCleanups()


def test_legacy_parent_application_preserves_metadata_and_invalidates_old_first(correction):
    original = correction.parent.read_bytes(); before = json.loads(original)['tasks'][0]
    result = correction.apply(); bank = correction.bank(result); after = json.loads(bank.read_text())['tasks'][0]
    assert correction.parent.read_bytes() == original
    expected = dict(before,grader_digest=correction.fixture.spec_value['replacement']['sha256'],grader_version='v2')
    assert task_identity(after) == task_identity(expected)
    assert json.loads(bank.read_text())['controller_profile'] == PROFILE
    assert validate(bank)['admission']['current_eligible']
    assert not validate(bank)['certification_eligible']
    assert correction.history.oracle(before['grader_digest'])['state'] == 'quarantined'
    assert correction.store.show(correction.case_id)['state'] == 'applied'
    assert correction.store.show(correction.case_id)['proposed_bank_diff']['applied']
    assert result['rerun_status'] == 'pending_rerun'
    for key in ('task_failure_verified','automatic_promotion','publication_allowed'): assert not result[key]


def test_export_uses_all_external_reviewed_raw_controls_without_mutating_oracle(correction):
    result = correction.apply(); bank = correction.bank(result); task = json.loads(bank.read_text())['tasks'][0]
    oracle = bank.parent/task['grader_bundle']; before = digest(oracle)
    controls = controls_for_manifest(bank,task['task_id']); assert len(controls) == 5
    manifest = export_manifest(bank,correction.root/'corrected-export')
    assert digest(oracle) == before
    assert len(list((correction.root/'corrected-export/controls'/task['task_id']).iterdir())) == 5
    assert {c['state'] for c in manifest['grader_self_tests']} == {'broken','partial','complete'}
    assert manifest['bank_authority']['revision_sha256'] == result['revision_sha256']
    assert set(bank_authority(manifest,export_root=correction.root/'corrected-export')) == {task['task_id']}


@pytest.mark.parametrize('key',['expected_event_sha256','parent_bank_sha256','evidence_sha256'])
def test_stale_case_parent_and_evidence_refuse_before_old_quarantine(correction,key):
    with pytest.raises(ValueError): correction.apply(**{key:'f'*64})
    if correction.history.root.exists(): assert not (correction.history.root/'cases').exists()
    assert not (correction.store.root/'revisions').exists()


@pytest.mark.parametrize('changes',[
    pytest.param(dict(expected_oracle_event_sha256='f'*64),id='stale-old-oracle-event'),
    pytest.param(dict(extra='unknown'),id='unknown-field'),
    pytest.param(dict(operator=dict(identity='synthetic-curator',source='verified',provenance='synthetic',basis='synthetic')),
                 id='unattested-operator')])
def test_strict_request_provenance_unknown_fields_and_old_oracle_cas_refuse(correction,changes):
    with pytest.raises(ValueError): correction.apply(**changes)
    if correction.history.root.exists(): assert not (correction.history.root/'cases').exists()


def test_negative_durability_failure_cannot_publish_a_revision(correction):
    from agent_harness_lab.evaluation import bank_corrections
    sync = bank_corrections.sync_tree
    def fail_negative(path):
        if Path(path).parent.name=='cases': raise OSError('synthetic negative durability failure')
        return sync(path)
    with patch.object(bank_corrections,'sync_tree',side_effect=fail_negative):
        with pytest.raises(OSError): correction.apply()
    assert correction.history.oracle(correction.fixture.task['grader_digest'])['state'] == 'quarantined'
    assert not list((correction.store.root/'revisions').iterdir())


def test_failure_after_quarantine_never_restores_old_oracle(correction):
    with patch('agent_harness_lab.evaluation.bank_corrections._copy_assets',side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt): correction.apply()
    assert correction.history.oracle(correction.fixture.task['grader_digest'])['state'] == 'quarantined'
    assert correction.store.show(correction.case_id)['state'] == 'reviewed'


def test_orphan_without_applied_marker_is_unusable_and_old_remains_revoked(correction):
    original = correction.store.append
    def interrupt(case,events,kind,state,payload):
        if kind=='applied': raise KeyboardInterrupt
        return original(case,events,kind,state,payload)
    with patch.object(correction.store,'append',side_effect=interrupt):
        with pytest.raises(KeyboardInterrupt): correction.apply()
    revision = next((correction.store.root/'revisions').iterdir())
    with pytest.raises(ValueError): validate(revision/'bank.json')
    with pytest.raises(ValueError): export_manifest(revision/'bank.json',correction.root/'orphan-export')
    assert not (correction.root/'orphan-export').exists()
    assert correction.history.oracle(correction.fixture.task['grader_digest'])['state'] == 'quarantined'


def test_exact_duplicate_request_is_idempotent_but_new_request_refuses(correction):
    request = correction.request(); first = correction.store.apply(correction.case_id,correction.parent,correction.history,request)
    assert correction.store.apply(correction.case_id,correction.parent,correction.history,request) == first
    with pytest.raises(ValueError): correction.apply(reason='A different application request')


def test_final_application_pointer_cannot_alias_another_reviewed_case(correction):
    first = correction.apply(); original_case = correction.case_id
    correction.fixture.spec_value['reason'] = 'A separately recorded synthetic correction review'
    correction.fixture.write_spec(); correction.fixture.audited(); correction.store.review(correction.fixture.case_id,correction.fixture.review_file())
    correction.case_id = correction.fixture.case_id; second = correction.apply()
    final = sorted((correction.store.case(original_case)/'events').glob('*.json'))[-1]
    event = json.loads(final.read_text()); event['payload']['revision_sha256'] = second['revision_sha256']
    final.write_text(json.dumps(event))
    assert first['revision_sha256'] != second['revision_sha256']
    with pytest.raises(ValueError): correction.store.show(original_case)


def test_concurrent_application_has_one_committed_marker(correction):
    request = correction.request()
    def attempt():
        try: return correction.store.apply(correction.case_id,correction.parent,correction.history,request)['revision_sha256']
        except (ValueError,BlockingIOError): return None
    with ThreadPoolExecutor(max_workers=2) as executor: results = list(executor.map(lambda _:attempt(),range(2)))
    assert any(results)
    events = correction.store.history(correction.store.case(correction.case_id))
    assert sum(e['kind']=='applied' for e,_ in events) == 1


def test_changed_evidence_or_parent_does_not_apply(correction):
    request = correction.request()
    correction.parent.write_text(correction.parent.read_text()+' ')
    with pytest.raises(ValueError): correction.store.apply(correction.case_id,correction.parent,correction.history,request)
    correction.parent.write_text(correction.parent.read_text().rstrip())
    report = next(correction.store.root.rglob('review.json')); report.write_text(report.read_text()+' ')
    with pytest.raises(ValueError): correction.store.apply(correction.case_id,correction.parent,correction.history,request)


def test_negative_event_mutation_or_correction_revocation_blocks_child(correction):
    result = correction.apply(); bank = correction.bank(result)
    manifest = export_manifest(bank,correction.root/'revocation-export')
    path = next((correction.history.root/'cases').rglob('0001.json'))
    raw = path.read_bytes(); value = json.loads(raw); value['payload']['record']['reason'] = 'changed'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError): validate(bank)
    path.write_bytes(raw)
    correction.store.review(correction.case_id,correction.fixture.review_file('quarantined'))
    assert not validate(bank)['admission']['current_eligible']
    with pytest.raises(ValueError): bank_authority(manifest,export_root=correction.root/'revocation-export')


def test_genuine_admitted_parent_preserves_live_original_acceptance_authority(correction,admission):
    f = admission
    admitted = f.bank(f.accept()); task = next(t for t in json.loads(admitted.read_text())['tasks'] if t['task_id'].startswith('regression-'))
    # The declared correction permits extra valid nested fields; the old
    # accepted grader compares nested dictionary keys for exact equality.
    bundle = correction.root/'admitted-replacement'; shutil.copytree(admitted.parent/task['grader_bundle'],bundle)
    script = bundle/'grade.py.txt'; source = script.read_text().replace('actual.keys()==expected.keys()','expected.keys()<=actual.keys()')
    source = source.replace("'v2'","'v3'").replace('"v2"','"v3"')
    script.write_text(source)
    spec = dict(correction.fixture.spec_value)
    spec['parent'] = dict(bank=str(admitted.relative_to(f.fixture.root)),sha256=hashlib.sha256(admitted.read_bytes()).hexdigest(),task_id=task['task_id'])
    # Declarations must stay relative: copy replacement and reviewed controls beside the parent locator.
    target = f.fixture.root/'correction-new'; shutil.copytree(bundle,target)
    spec['replacement'] = dict(bundle='correction-new',sha256=digest(target),grader_version='v3')
    oldcontrols = controls_for_manifest(admitted,task['task_id']); spec['controls'] = []
    for i,c in enumerate(oldcontrols):
        local = f.fixture.root/('correction-control-'+str(i)); shutil.copytree(c['fixture'],local)
        if c['state']=='alternative_valid':
            value = json.loads((local/'result.json').read_text()); value['settings']['synthetic_extra'] = True
            (local/'result.json').write_text(json.dumps(value))
        spec['controls'].append(dict(state=c['state'],fixture=local.name,digest=digest(local)))
    path = f.fixture.root/'correction-spec.json'; path.write_text(json.dumps(spec))
    store = CorrectionStore(f.fixture.root/'correction-store'); proposed = store.propose(path,'synthetic-correction-author')
    audited = store.audit(proposed['case_id'],f.fixture.backend)
    assert audited['old_audit_status'] == 'controls_failed'
    assert audited['replacement_audit_status'] == 'grader_controls_passed'
    review = json.loads(correction.fixture.review_file().read_text())
    review.update(case_id=proposed['case_id'],expected_event_sha256=audited['event_sha256'],evidence_sha256=audited['evidence_sha256'])
    operator = f.fixture.root/'correction-review.json'; operator.write_text(json.dumps(review)); store.review(proposed['case_id'],operator)
    correction.store,correction.case_id,correction.parent = store,proposed['case_id'],admitted
    correction.history = HistoryStore(f.fixture.root/'application-history')
    result = correction.apply(); child = correction.bank(result)
    assert validate(child)['admission']['current_eligible']
    f.store.review(f.case_id,f.fixture.review_file('quarantined'))
    assert not validate(child)['admission']['current_eligible']
    shutil.rmtree(admitted.parent)
    assert not validate(child)['admission']['current_eligible']


def test_corrected_descendant_needs_live_case_markers_not_ancestor_revision_directories(correction):
    first = correction.apply(); parent = correction.bank(first); old_store = correction.store
    old_history = correction.history
    bundle = correction.root/'replacement-v3'; shutil.copytree(correction.root/'neworacle',bundle)
    script = bundle/'grade.py.txt'; source = script.read_text().replace("'v2'","'v3'")
    source = source.replace('json.loads(actual)==json.loads(expected)',
        'all(json.loads(actual).get(k)==v for k,v in json.loads(expected).items())')
    script.write_text(source)
    declaration = json.loads(json.dumps(correction.fixture.spec_value))
    declaration['parent'].update(bank=str(parent.relative_to(correction.root)),sha256=hashlib.sha256(parent.read_bytes()).hexdigest())
    declaration['replacement'] = dict(bundle=bundle.name,sha256=digest(bundle),grader_version='v3')
    for c in declaration['controls']:
        if c['state']=='alternative_valid':
            folder = correction.root/c['fixture']; value = json.loads((folder/'result.json').read_text()); value['extra'] = True
            (folder/'result.json').write_text(json.dumps(value)); c['digest'] = digest(folder)
    path = correction.root/'second-correction.json'; path.write_text(json.dumps(declaration))
    correction.store = CorrectionStore(correction.root/'second-correction-store')
    proposed = correction.store.propose(path,'second-synthetic-author'); correction.case_id = proposed['case_id']
    audited = correction.store.audit(correction.case_id,correction.fixture.backend); assert audited['state'] == 'audited'
    review = json.loads(correction.fixture.review_file().read_text())
    review.update(case_id=correction.case_id,expected_event_sha256=audited['event_sha256'],evidence_sha256=audited['evidence_sha256'])
    decision = correction.root/'second-review.json'; decision.write_text(json.dumps(review)); correction.store.review(correction.case_id,decision)
    correction.parent = parent; correction.history = HistoryStore(correction.root/'second-selected-history')
    child = correction.bank(correction.apply())
    negative = correction.root/'first-negative-review.json'; negative.write_bytes(correction.fixture.review_file('quarantined').read_bytes())
    shutil.rmtree(parent.parent)
    assert validate(child)['admission']['current_eligible']
    missing = correction.root/'missing-first-history'; old_history.root.rename(missing)
    with pytest.raises((ValueError,OSError)): validate(child)
    missing.rename(old_history.root)
    old_store.review(correction.fixture.case_id,negative)
    assert not validate(child)['admission']['current_eligible']


def test_unchanged_protected_task_is_preserved_but_selected_protected_target_refuses(correction):
    import agent_harness_lab
    source = Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1'
    protected = next(t for t in json.loads((source/'bank.json').read_text())['tasks'] if t['task_id']=='resumption')
    shutil.copytree(source/protected['fixture'],correction.root/'protected-fixture')
    shutil.copytree(source/protected['grader_bundle'],correction.root/'protected-grader')
    protected.update(fixture='protected-fixture',grader_bundle='protected-grader',partition='certification')
    parent = json.loads(correction.parent.read_text()); parent['tasks'].append(protected)
    correction.parent.write_text(json.dumps(parent))
    correction.fixture.spec_value['parent']['sha256'] = hashlib.sha256(correction.parent.read_bytes()).hexdigest()
    correction.fixture.write_spec(); correction.fixture.audited(); correction.store.review(correction.fixture.case_id,correction.fixture.review_file())
    correction.case_id = correction.fixture.case_id
    child = correction.bank(correction.apply()); copied = next(t for t in json.loads(child.read_text())['tasks'] if t['task_id']=='resumption')
    assert task_identity(copied) == task_identity(protected)
    assert not validate(child)['certification_eligible']
    spec = dict(correction.fixture.spec_value); spec['parent'] = dict(spec['parent'],task_id='resumption')
    path = correction.root/'protected-target.json'; path.write_text(json.dumps(spec))
    with pytest.raises(ValueError): correction.store.propose(path,'synthetic-protected-author')
