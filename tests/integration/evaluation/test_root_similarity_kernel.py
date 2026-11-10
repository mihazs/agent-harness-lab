"""Synthetic bank-evidence history through real delegated sandbox workers."""
import hashlib
import json
import shutil

import pytest

from agent_harness_lab.cli import evaluator
from agent_harness_lab.config import starter
from agent_harness_lab.evaluation.bank_tools import export_manifest
from agent_harness_lab.evaluation.oracle import identity
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.evaluation.regression_review import ReviewStore
from agent_harness_lab.evaluation.root_similarity import prepare
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from tests.integration.evaluation.test_offline_evaluation import SOLVER as COMPARISON_SOLVER
from tests.integration.evaluation.test_regression_replay import make_replay
from tests.integration.sandbox import test_sandbox_kernel as kernel


@pytest.fixture(scope='module')
def kernel_scope():
    with kernel.kernel_scope() as root:
        yield root


@pytest.mark.skipif(not kernel.ENABLED, reason='requires explicit delegated kernel scope')
def test_bank_similarity_full_synthetic_history_seals_and_reopens(tmp_path, kernel_scope):
    runtime = tmp_path/'runtime'; image = kernel.pinned_runtime(runtime)['sha256']
    source = tmp_path/'source'; source.mkdir()
    proposal, draft, replay = make_replay(source, image)
    reviews = ReviewStore(tmp_path/'reviews')
    reported = reviews.propose(proposal, draft, replay, 'synthetic-author')
    backend = SandboxBackend(cgroup_root=kernel_scope, image_root=runtime, image_sha256=image, disk_bytes=64*1024*1024)
    reproduced = reviews.reproduce(reported['case_id'], backend)
    assert reproduced['state'] == 'reproduced'
    drafted = reviews.draft(reported['case_id'])
    review = dict(schema_version=1, case_id=reported['case_id'], expected_event_sha256=drafted['event_sha256'],
        evidence_sha256=drafted['evidence_sha256'], decision='reviewed',
        reviewer=dict(identity='synthetic-reviewer', source='operator_attestation', provenance='synthetic fixture',
                      basis_kind='independent_artifact_review', basis='Synthetic assertion, not authenticated independence.'),
        evidence_refs=[dict(ref='synthetic:review', sha256='a'*64)], reason='Synthetic regression review.')
    review_path=tmp_path/'review.json'; review_path.write_text(json.dumps(review))
    reviewed=reviews.review(reported['case_id'], review_path)
    parent_dir=tmp_path/'parent'; shutil.copytree(starter().parent, parent_dir)
    parent=parent_dir/'bank.json'; data=json.loads(parent.read_text())
    selected={'resumption','tool-recovery','skill-routing'}
    data['tasks']=[task for task in data['tasks'] if task['task_id'] in selected]
    for task in data['tasks']: task['family']='regression'
    parent.write_text(json.dumps(data)); original=parent.read_bytes()
    admission=dict(schema_version=1,case_id=reported['case_id'],expected_event_sha256=reviewed['event_sha256'],
        evidence_sha256=reviewed['evidence_sha256'],parent_bank_sha256=hashlib.sha256(original).hexdigest(),
        parent_revision_sha256=None,purpose='development_regression',provenance_binding=None,
        operator=dict(identity='synthetic-curator',source='operator_attestation',provenance='synthetic fixture',
                      basis='Freeze reviewed synthetic controls.'),reason='Synthetic admission.')
    admission_path=tmp_path/'admission.json'; admission_path.write_text(json.dumps(admission))
    accepted=reviews.accept(reported['case_id'],parent,admission_path)
    bank=reviews.root/'revisions'/accepted['revision_sha256']/'bank.json'
    export=tmp_path/'export'; manifest=export_manifest(bank,export,max_trials=8)
    runner=evaluator(); baseline=tmp_path/'baseline'; candidate=tmp_path/'candidate'
    for folder, program in ((baseline,"from pathlib import Path; Path('result.json').write_text('{}')\n"),
                            (candidate,COMPARISON_SOLVER)):
        folder.mkdir(); (folder/'solve.py').write_text(program)
    tasks=manifest['tasks']
    for task in tasks: task['offline_executor']=['/usr/bin/python3','-I','/inputs/harness/solve.py']
    plan=ExperimentSpec('synthetic-bank-similarity',tuple((t['id'],t['root'],t['family']) for t in tasks),
        1,13,8,2,0.,.1,.99,(('regression',1.),),runner.digest(baseline),runner.digest(candidate),
        identity(export,tasks,manifest['grader_bundles']),image)
    prepared=prepare(plan,bank); pinned=ExperimentSpec.parse(prepared['prepared_plan'])
    from agent_harness_lab.sources.schema import digest as metadata_digest
    assert pinned.root_similarity_sha256 == metadata_digest(prepared['root_similarity_evidence'])
    assert prepared['root_similarity_evidence']['bank_sha256'] == manifest['bank_digest']
    manifest['experiment']=pinned.to_dict(); path=export/'manifest.json'; path.write_text(json.dumps(manifest))
    history=HistoryStore(tmp_path/'history')
    result=history.run(path,baseline,candidate,cgroup_root=kernel_scope,image_root=runtime,image_sha256=image,
                       disk_bytes=64*1024*1024,budget_seconds=120)
    assert result['state'] == 'sealed' and result['bank_authority'] == 'current_eligible'
    sealed=json.loads((history.case(result['run_id'])/'execution/results.json').read_text())
    assert len(sealed['trials']) == 8 and sealed['inputs_unchanged']
    for trial in sealed['trials']:
        attestation=trial['grader']['execution']['attestation']
        assert attestation['runtime_image']['sha256'] == image
        assert attestation['cgroup_v2']['empty_after_cleanup']
    reopened=history.show(result['run_id']); analysis=history.analyze(result['run_id'])
    assert reopened['state'] == 'sealed' and reopened['oracle_validity'] == 'current'
    assert analysis['primary']['independent_roots'] == 4
    assert analysis['independence'] == 'unverified' and not analysis['automatic_promotion']
    current=reviews.show(reported['case_id'])
    review.update(expected_event_sha256=current['event_sha256'],decision='quarantined')
    review_path.write_text(json.dumps(review)); reviews.review(reported['case_id'],review_path)
    with pytest.raises(ValueError): history.show(result['run_id'])
    with pytest.raises(ValueError): history.analyze(result['run_id'])
