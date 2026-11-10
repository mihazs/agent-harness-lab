"""Synthetic private admission; operator labels never prove native provenance."""
import hashlib
import json
from pathlib import Path
import shutil
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

import agent_harness_lab
from agent_harness_lab.evaluation.task_bank import validate
from tests.integration.evaluation import test_regression_review as fixtures


class RegressionAdmission:
    """Hosts the reviewed-case admission fixture that other modules build on; it defines no tests of its own."""

    def __init__(self, add_cleanup):
        self.fixture = fixtures.ReviewFixture(add_cleanup)
        f = self.fixture
        f.drafted(); f.store.review(f.case_id,f.review_file())
        self.store, self.case_id = f.store, f.case_id
        source = Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1'
        parent = f.root/'parent'; shutil.copytree(source,parent)
        value = json.loads((parent/'bank.json').read_text())
        value['tasks'] = [t for t in value['tasks'] if t['task_id']=='resumption']
        self.parent = parent/'bank.json'; self.parent.write_text(json.dumps(value))
        self.initial_parent = self.parent.read_bytes()

    def request(self, **changes):
        current = self.store.show(self.case_id)
        value = dict(schema_version=1,case_id=self.case_id,expected_event_sha256=current['event_sha256'],
            evidence_sha256=current['evidence_sha256'],parent_bank_sha256=hashlib.sha256(self.parent.read_bytes()).hexdigest(),
            parent_revision_sha256=None,operator=dict(identity='synthetic-curator',source='operator_attestation',
                provenance='synthetic local review',basis='Freeze the reviewed synthetic failure.'),
            purpose='development_regression',provenance_binding=None,reason='Synthetic regression admission.')
        value.update(changes)
        path = self.fixture.root/'admission.json'; path.write_text(json.dumps(value)); return path

    def accept(self, **changes):
        return self.store.accept(self.case_id,self.parent,self.request(**changes))

    def bank(self, result):
        return self.store.root/'revisions'/result['revision_sha256']/'bank.json'

    def second_reviewed(self, *, root_id=None):
        from agent_harness_lab.evaluation.integrity import digest
        make_replay = fixtures.fixtures.make_replay
        folder = self.fixture.root/'second-inputs'; folder.mkdir()
        source, draft, spec = make_replay(folder)
        state = json.loads((folder/'fixture/task.json').read_text()); state['second_variant'] = True
        (folder/'fixture/task.json').write_text(json.dumps(state))
        proposal = json.loads(source.read_text()); descriptor = json.loads(draft.read_text())
        if root_id is not None:
            proposal['root_id'] = root_id; descriptor['task']['root_id'] = root_id
        source.write_text(json.dumps(proposal))
        descriptor['proposal_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
        descriptor['task']['fixture_digest'] = digest(folder/'fixture'); draft.write_text(json.dumps(descriptor))
        replay = json.loads(spec.read_text())
        replay.update(proposal_sha256=descriptor['proposal_sha256'],draft_sha256=hashlib.sha256(draft.read_bytes()).hexdigest())
        spec.write_text(json.dumps(replay))
        proposed = self.store.propose(source,draft,spec,'second-author'); case_id = proposed['case_id']
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',return_value=self.fixture.backend):
            assert self.store.reproduce(case_id,self.fixture.backend)['state'] == 'reproduced'
        current = self.store.draft(case_id)
        review = json.loads(self.fixture.review_file().read_text())
        review.update(case_id=case_id,expected_event_sha256=current['event_sha256'],evidence_sha256=current['evidence_sha256'])
        record = folder/'review.json'; record.write_text(json.dumps(review)); self.store.review(case_id,record)
        return case_id


@pytest.fixture
def admission():
    with ExitStack() as cleanups:
        yield RegressionAdmission(cleanups.callback)


def test_reviewed_case_enters_a_new_private_immutable_development_bank(admission):
    result = admission.accept(); bank = admission.bank(result); checked = validate(bank)
    assert result['current_eligible']; assert not checked['certification_eligible']
    assert checked['privacy'] == 'private'; assert checked['tasks'] == 2
    assert admission.parent.read_bytes() == admission.initial_parent
    tasks = json.loads(bank.read_text())['tasks']; added = next(t for t in tasks if t['task_id'].startswith('regression-'))
    assert added['partition'] == 'development'; assert added['independence'] == 'unverified'
    assert added['review_status'] == 'accepted'; assert not result['publication_allowed']
    assert admission.store.show(admission.case_id)['state'] == 'accepted'
    assert admission.store.show(admission.case_id)['bank_admission']
    assert bank.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('field',[pytest.param(field,id=field)
                                   for field in ('expected_event_sha256','parent_bank_sha256','evidence_sha256')])
def test_stale_case_parent_or_evidence_reject_before_any_revision(admission,field):
    with pytest.raises(ValueError): admission.accept(**{field:'f'*64})
    assert not (admission.store.root/'revisions').exists()
    assert admission.store.show(admission.case_id)['state'] == 'reviewed'


def test_protected_exposure_and_fake_native_binding_cannot_be_requested(admission):
    for change in (dict(purpose='certification'),dict(provenance_binding={'verified':True})):
        with pytest.raises(ValueError): admission.accept(**change)
    assert not (admission.store.root/'revisions').exists()


def test_changed_evidence_cannot_be_accepted(admission):
    (admission.fixture.attempt()/'controls-source/4/original.txt').write_text('changed\n')
    with pytest.raises(ValueError): admission.accept()
    assert admission.store.show(admission.case_id)['state'] == 'quarantined'


def test_prepared_orphan_cannot_be_validated_or_exported_as_accepted(admission):
    from agent_harness_lab.evaluation.bank_tools import export_manifest
    append = admission.store.append
    def interrupt(case, events, kind, state, payload):
        if kind=='accepted': raise KeyboardInterrupt()
        return append(case,events,kind,state,payload)
    with patch.object(admission.store,'append',side_effect=interrupt):
        with pytest.raises(KeyboardInterrupt): admission.accept()
    revisions = list((admission.store.root/'revisions').iterdir()); assert len(revisions) == 1
    with pytest.raises(ValueError): validate(revisions[0]/'bank.json')
    output = admission.fixture.root/'orphan-export'
    with pytest.raises(ValueError): export_manifest(revisions[0]/'bank.json',output)
    assert not output.exists(); assert admission.store.show(admission.case_id)['state'] == 'reviewed'


def test_quarantine_preserves_revision_but_blocks_new_export(admission):
    from agent_harness_lab.evaluation.bank_tools import export_manifest
    result = admission.accept(); bank = admission.bank(result); frozen = bank.read_bytes()
    admission.store.review(admission.case_id,admission.fixture.review_file('quarantined'))
    checked = validate(bank); assert not checked['admission']['current_eligible']
    output = admission.fixture.root/'quarantined-export'
    with pytest.raises(ValueError): export_manifest(bank,output)
    assert not output.exists(); assert bank.read_bytes() == frozen


def test_export_uses_all_frozen_controls_without_mutating_the_oracle(admission):
    from agent_harness_lab.evaluation.bank_tools import export_manifest,audit_graders
    from agent_harness_lab.evaluation.integrity import digest
    result = admission.accept(); bank = admission.bank(result)
    value = json.loads(bank.read_text()); roots = {t['task_id']:digest(bank.parent/t['grader_bundle']) for t in value['tasks']}
    exported = export_manifest(bank,admission.fixture.root/'export')
    assert exported['bank_revision_sha256'] == result['revision_sha256']
    assert roots == {t['task_id']:digest(bank.parent/t['grader_bundle']) for t in value['tasks']}
    checked = audit_graders(bank,cooperative=True)
    assert checked['status'] == 'controls_passed'
    added = [row for row in checked['checks'] if row['task'].startswith('regression-')]
    assert {row['control'] for row in added} == {'broken','partial','complete','alternative_valid','superficial'}


def test_retry_of_the_same_committed_request_is_idempotent(admission):
    request = admission.request(); first = admission.store.accept(admission.case_id,admission.parent,request)
    second = admission.store.accept(admission.case_id,admission.parent,request)
    assert first['revision_sha256'] == second['revision_sha256']
    assert len(list((admission.store.root/'revisions').iterdir())) == 1


def test_normalized_fixture_duplicate_is_rejected_even_when_new_task_sorts_first(admission):
    from agent_harness_lab.evaluation.integrity import digest
    source = Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1'
    parent_root = admission.parent.parent
    task = next(t for t in json.loads((source/'bank.json').read_text())['tasks'] if t['task_id']=='regression')
    task['task_id']='zzz-existing'; folder=parent_root/task['fixture']
    state=json.loads((folder/'task.json').read_text()); (folder/'task.json').write_text(json.dumps(state,separators=(',',':')))
    task['fixture_digest']=digest(folder)
    admission.parent.write_text(json.dumps(dict(schema_version=1,separate_by=['root_id'],tasks=[task])))
    with pytest.raises(ValueError): admission.accept()


def test_parent_quarantine_between_validation_and_lock_cannot_commit_child(admission):
    from agent_harness_lab.evaluation import task_bank
    first = admission.accept(); old_case = admission.case_id; parent = admission.bank(first)
    child = admission.second_reviewed(); admission.parent = parent; admission.case_id = child
    request = admission.request(parent_revision_sha256=first['revision_sha256'])
    original = task_bank.validate; changed = [False]
    def validate_parent(path, **kwargs):
        result = original(path,**kwargs)
        if not changed[0] and path==parent:
            changed[0] = True
            admission.store.review(old_case,admission.fixture.review_file('quarantined'))
        return result
    with patch.object(task_bank,'validate',side_effect=validate_parent):
        with pytest.raises(ValueError, match='no longer eligible'):
            admission.store.accept(child,parent,request)
    assert admission.store.show(child)['state'] == 'reviewed'
    assert len(list((admission.store.root/'revisions').iterdir())) == 1


def test_descendant_preserves_external_controls_and_inherits_parent_revocation(admission):
    from agent_harness_lab.evaluation.bank_tools import export_manifest
    from agent_harness_lab.evaluation.bank_revisions import controls_for_manifest
    from agent_harness_lab.evaluation.integrity import digest
    first = admission.accept(); parent = admission.bank(first); old_parent = parent.read_bytes()
    child = admission.second_reviewed(); admission.parent = parent; admission.case_id = child
    with patch('agent_harness_lab.evaluation.bank_revisions.MAX_REVISIONS',1):
        with pytest.raises(ValueError, match='capacity'):
            admission.accept(parent_revision_sha256=first['revision_sha256'])
    assert admission.store.show(child)['state'] == 'reviewed'
    result = admission.accept(parent_revision_sha256=first['revision_sha256']); bank = admission.bank(result)
    assert result['bank_manifest_sha256'] == hashlib.sha256(bank.read_bytes()).hexdigest()
    assert validate(bank)['tasks'] == 3
    for case_id in (admission.fixture.case_id,child):
        controls = controls_for_manifest(bank,'regression-'+case_id)
        assert len(controls) == 5
        assert all(digest(c['fixture'])==c['digest'] for c in controls)
    exported = export_manifest(bank,admission.fixture.root/'child-export')
    assert exported['bank_revision_sha256'] == result['revision_sha256']
    assert parent.read_bytes() == old_parent
    accepted = next((e,h) for e,h in admission.store.history(admission.store.case(admission.fixture.case_id)) if e['kind']=='accepted')
    marker = admission.store.case(admission.fixture.case_id)/'events'/f"{accepted[0]['index']:04d}.json"
    original = marker.read_bytes(); modified = json.loads(original); modified['recorded_at'] = 'changed recorded timestamp'
    try:
        marker.write_text(json.dumps(modified))
        with pytest.raises(ValueError, match='inherited accepted marker changed'): validate(bank)
    finally: marker.write_bytes(original)
    admission.store.review(admission.fixture.case_id,admission.fixture.review_file('quarantined'))
    assert not validate(bank)['admission']['current_eligible']
    output = admission.fixture.root/'revoked-child-export'
    with pytest.raises(ValueError, match='eligible'): export_manifest(bank,output)
    assert not output.exists()


def test_opaque_provenance_cannot_be_relabelled_to_a_new_root(admission):
    first = admission.accept(); parent = admission.bank(first)
    child = admission.second_reviewed(root_id='renamed-root'); admission.parent = parent; admission.case_id = child
    with pytest.raises(ValueError, match='unresolved root/project aliases'):
        admission.accept(parent_revision_sha256=first['revision_sha256'])
    assert admission.store.show(child)['state'] == 'reviewed'
    assert len(list((admission.store.root/'revisions').iterdir())) == 1


def test_revision_capacity_is_checked_before_any_publication(admission):
    with patch('agent_harness_lab.evaluation.bank_revisions.MAX_REVISIONS',0):
        with pytest.raises(ValueError, match='capacity'): admission.accept()
    assert admission.store.show(admission.case_id)['state'] == 'reviewed'
    assert list((admission.store.root/'revisions').iterdir()) == []


def test_asset_resource_failure_cleans_staging_without_accepting(admission):
    with patch('agent_harness_lab.evaluation.bank_revisions.MAX_REVISION_BYTES',1):
        with pytest.raises(ValueError): admission.accept()
    assert admission.store.show(admission.case_id)['state'] == 'reviewed'
    assert list((admission.store.root/'revisions').iterdir()) == []
    assert not list(admission.store.root.glob('.bank-stage-*'))


def test_orphan_retry_never_overwrites_or_adopts_an_uncommitted_revision(admission):
    from agent_harness_lab.evaluation.integrity import digest
    append = admission.store.append; request = admission.request()
    def interrupt(case, events, kind, state, payload):
        if kind=='accepted': raise KeyboardInterrupt()
        return append(case,events,kind,state,payload)
    with patch.object(admission.store,'append',side_effect=interrupt),pytest.raises(KeyboardInterrupt):
        admission.store.accept(admission.case_id,admission.parent,request)
    revision = next((admission.store.root/'revisions').iterdir()); identity = digest(revision)
    with pytest.raises(ValueError, match='prepared revision already exists'):
        admission.store.accept(admission.case_id,admission.parent,request)
    assert digest(revision) == identity
    assert admission.store.show(admission.case_id)['state'] == 'reviewed'
    with pytest.raises(ValueError): validate(revision/'bank.json')


def test_concurrent_duplicate_acceptance_has_one_immutable_commit(admission):
    request = admission.request()
    def accept(_):
        try: return admission.store.accept(admission.case_id,admission.parent,request)
        except BlockingIOError: return None
    with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(accept,range(2)))
    revisions = {r['revision_sha256'] for r in results if r is not None}
    assert len(revisions) == 1
    assert len(list((admission.store.root/'revisions').iterdir())) == 1
    assert sum(e['kind']=='accepted' for e,_ in admission.store.history(admission.store.case(admission.case_id))) == 1


@pytest.mark.parametrize('variant', ['missing_catalog', 'missing_task', 'mismatched_task'])
def test_fabricated_revision_without_its_admitted_task_cannot_export(tmp_path, variant):
    from agent_harness_lab.evaluation.bank_revisions import metadata_digest, payload_tree
    from agent_harness_lab.evaluation.bank_tools import export_manifest
    from agent_harness_lab.private_journal import encoded

    def put(path, value):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = encoded(value); path.write_bytes(data); path.chmod(0o600)
        return hashlib.sha256(data).hexdigest()

    store = tmp_path/'store'; store.mkdir(mode=0o700)
    put(store/'.store.json', dict(schema_version=1, format='regression-review-store'))
    declarations = {name: {} for name in ('proposal.json', 'draft.json', 'spec.json')}
    hashes = {name: hashlib.sha256(encoded(value)).hexdigest() for name, value in declarations.items()}
    case_id = hashlib.sha256(encoded(hashes)).hexdigest(); case = store/'cases'/case_id
    for name, value in declarations.items(): put(case/name, value)
    inputs = put(case/'inputs.json', dict(schema_version=1, author='synthetic-author',
        origins={name: '/synthetic/'+name for name in declarations}, hashes=hashes, privacy='public_or_synthetic_source'))
    previous = None
    def event(kind, state, payload):
        nonlocal previous
        index = len(list((case/'events').glob('*.json'))) if (case/'events').exists() else 0
        previous = put(case/'events'/f'{index:04}.json', dict(schema_version=1, index=index,
            previous_sha256=previous, kind=kind, state=state, payload=payload, recorded_at='2000-01-01T00:00:00Z'))

    event('reported', 'reported', dict(inputs_sha256=inputs))
    event('attempt_started', 'reported', {})
    evidence = 'a'*64
    event('reproduced', 'reproduced', dict(evidence_sha256=evidence))
    event('drafted', 'drafted', dict(evidence_sha256=evidence))
    review = dict(schema_version=1, case_id=case_id, expected_event_sha256=previous, evidence_sha256=evidence,
        decision='reviewed', reviewer=dict(identity='synthetic-reviewer', source='operator_attestation', provenance='synthetic',
            basis_kind='independent_artifact_review', basis='synthetic'),
        evidence_refs=[dict(ref='synthetic:review', sha256='b'*64)], reason='synthetic')
    event('reviewed', 'reviewed', dict(review=review)); reviewed = previous
    staging = store/'revisions'/'staging'; staging.mkdir(parents=True, mode=0o700)
    source = Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1'
    bank = json.loads((source/'bank.json').read_text()); bank['tasks'] = bank['tasks'][:1]
    bank['controller_profile'] = 'regression-bank-v1'
    admitted_id = 'regression-'+case_id
    admissions = {}
    if variant != 'missing_catalog':
        admissions[admitted_id] = dict(case_id=case_id, task_revision_sha256='e'*64)
    if variant == 'mismatched_task':
        bank['tasks'][0]['task_id'] = admitted_id
    for key in ('fixture', 'grader_bundle'):
        relative = Path(bank['tasks'][0][key]); (staging/relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source/relative, staging/relative)
    bank_hash = put(staging/'bank.json', bank)
    descriptor = dict(schema_version=1, profile='regression-bank-v1', parent_revision_sha256=None,
        parent_bank_sha256='c'*64, bank_manifest_sha256=bank_hash, tree_sha256=payload_tree(staging),
        operation='accept', case_id=case_id, reviewed_event_sha256=reviewed, sealed_evidence_sha256=evidence,
        accepted_event_index=5, request_sha256='d'*64, task_revision_sha256='e'*64,
        exposure='development_regression', provenance_binding=None, admissions=admissions)
    revision = metadata_digest(descriptor); put(staging/'revision.json', descriptor)
    destination = staging.parent/revision; staging.rename(destination)
    event('accepted', 'accepted', dict(revision_sha256=revision, bank_sha256=bank_hash, request_sha256='d'*64))
    with pytest.raises(ValueError, match='admitted task'):
        export_manifest(destination/'bank.json', tmp_path/'export')
    assert not (tmp_path/'export').exists()
