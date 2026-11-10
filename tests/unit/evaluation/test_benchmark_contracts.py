import pytest

from agent_harness_lab.evaluation.benchmark import validate_pair,public_export_allowed,measured_cost,summary,render


def runtime(model='synthetic'):
    return dict(provider='local',runtime='fake',model_revision=model,reasoning='none',task_bank_revision='v1',
                sandbox_image='fixture-v1',permissions='offline',controller_version='v1',effective_settings_verified=True)


def record(model='synthetic',source='synthetic',status='not_run'):
    return dict(baseline_runtime=runtime(model),candidate_runtime=runtime(model),status=status,execution_kind='fake_agent',
                provenance=dict(source_kind=source,approved_publication=True,source_ref='synthetic fixture',derived_from_private=False))


def test_trajectory_reports_keep_all_trials_assertions_and_long_identifiers():
    assertions=[dict(assertion_id='synthetic assertion '*20+str(index),assertion_sha256='a'*64,
        status='unknown',coverage_complete=False) for index in range(33)]
    first=dict(task='synthetic task '*30,version='candidate',
        controller_grade=dict(trajectory=dict(status='unknown',assertions=assertions)))
    report=summary(dict(trajectory_profile='controller-trajectory-v1',
        trials=[first]+[dict(task='synthetic task') for _ in range(10000)]))
    assert len(report['trials']) == 10001
    assert report['trials'][0]['task'] == first['task']
    assert len(report['trials'][0]['assertions']) == 33
    assert report['trials'][0]['assertions'][-1]['assertion_id'] == assertions[-1]['assertion_id']
    assert report['analysis']['status'] == 'unverified'
    assert not report['publication_allowed']


def test_trajectory_summary_retains_nested_claims_without_private_arguments():
    child = dict(assertion_id='synthetic-group/0', assertion_sha256='b'*64, status='unknown',
                 coverage_complete=False, arguments=['SYNTHETIC_SECRET'])
    parent = dict(assertion_id='synthetic-group', assertion_sha256='a'*64, status='satisfied',
                  coverage_complete=False, children=[child])
    report = summary(dict(trajectory_profile='controller-trajectory-v1', trials=[dict(task='synthetic',
        controller_grade=dict(trajectory=dict(status='satisfied', assertions=[parent])))]))
    result = report['trials'][0]['assertions'][0]
    assert result['children'] == [dict(assertion_id='synthetic-group/0', assertion_sha256='b'*64,
                                        status='unknown', coverage_complete=False)]
    assert 'SYNTHETIC_SECRET' not in str(report)
    assert report['report_authority'] == 'imported_unverified' and not report['publication_allowed']
    child['children'] = [dict(assertion_id={'private': 'value'})]
    with pytest.raises(ValueError):
        summary(dict(trajectory_profile='controller-trajectory-v1', trials=[dict(task='synthetic',
            controller_grade=dict(trajectory=dict(assertions=[parent])))]))


def test_trajectory_summary_rejects_overdeep_assertion_tree():
    node = dict(assertion_id='synthetic', assertion_sha256='a'*64, status='unknown')
    root = node
    for _ in range(8):
        child = dict(assertion_id=node['assertion_id']+'/0', assertion_sha256='a'*64, status='unknown')
        node['children'] = [child]
        node = child
    with pytest.raises(ValueError, match='oversized trajectory assertion tree'):
        summary(dict(trajectory_profile='controller-trajectory-v1', trials=[dict(task='synthetic',
            controller_grade=dict(trajectory=dict(assertions=[root])))]))


@pytest.mark.parametrize('change', [
    {'assertion_id': 'SYNTHETIC_SECRET'},
    {'assertion_id': 'synthetic-group/0/extra'},
    {'assertion_sha256': 'SYNTHETIC_SECRET'},
    {'assertion_sha256': 'A'*64},
])
def test_trajectory_summary_rejects_untrusted_child_identity_or_digest(change):
    child = dict(assertion_id='synthetic-group/0', assertion_sha256='b'*64, status='unknown') | change
    parent = dict(assertion_id='synthetic-group', assertion_sha256='a'*64, status='unknown', children=[child])
    with pytest.raises(ValueError, match='invalid trajectory child'):
        summary(dict(trajectory_profile='controller-trajectory-v1', trials=[dict(task='synthetic',
            controller_grade=dict(trajectory=dict(assertions=[parent])))]))


@pytest.mark.parametrize('metadata', [
    dict(predicate_kind='SYNTHETIC_SECRET', selected_stage='request'),
    dict(predicate_kind='required', selected_stage='SYNTHETIC_SECRET'),
    dict(predicate_kind='all_of', selected_stage='request'),
    dict(predicate_kind='required'),
])
def test_trajectory_summary_rejects_invalid_predicate_metadata(metadata):
    assertion = dict(assertion_id='synthetic', assertion_sha256='a'*64, status='unknown') | metadata
    with pytest.raises(ValueError, match='invalid trajectory predicate'):
        summary(dict(trajectory_profile='controller-trajectory-v1', trials=[dict(task='synthetic',
            controller_grade=dict(trajectory=dict(assertions=[assertion])))]))


def test_mismatched_and_unverified_runtime_blocks():
    with pytest.raises(ValueError): validate_pair(runtime(),runtime('other-model'))
    candidate=runtime(); candidate['effective_settings_verified']=False
    assert validate_pair(runtime(),candidate)['status'] == 'blocked'
    assert validate_pair(runtime(),candidate,strict=False)['status'] == 'unverified'


def test_private_and_mixed_provenance_cannot_export_even_aggregates():
    assert public_export_allowed([record()])
    for records in ([record(source='private')],[record(),record(source='private')],[]):
        assert not public_export_allowed(records)
        with pytest.raises(ValueError): summary(records,public=True)
    derived=record(); derived['provenance']['derived_from_private']=True
    assert not public_export_allowed([derived])


@pytest.mark.parametrize('marker', ['audit_sha256', 'harness_inventory_sha256', 'instruction_precedence'])
@pytest.mark.parametrize('wrapper', [None, 'receipt', 'analysis', 'context', 'binding'])
def test_static_audit_markers_block_public_export_in_supported_wrappers(marker, wrapper):
    item = record()
    item.update({wrapper: {marker: None, 'publication_allowed': True, 'profile': 'forged'}} if wrapper
                else {marker: None, 'publication_allowed': True, 'profile': 'forged'})
    assert not public_export_allowed([item])


def test_reports_keep_failures_abstentions_strata_and_escape_html():
    records=[record(status='failed'),record(status='inconclusive'),record(model='<script>',status='skipped')]
    result=summary(records,public=True)
    assert len(result['strata']) == 2
    assert result['pooled_treatment_effect'] is None
    assert result['strata'][0]['capability_claim'] == 'synthetic_or_replay_only'
    markdown,html=render(result)
    assert 'inconclusive' in markdown; assert 'skipped' in html
    assert '<script>' not in html; assert '&lt;script&gt;' in html


PRIVATE_CONTROLLER_CLAIMS=(dict(publication_allowed=False),dict(publication_allowed=None),
    dict(controller_run_id='1'*64),dict(cohort_manifest_sha256='2'*64),
    dict(preservation={}),dict(receipt_sha256='3'*64),
    dict(algorithm='private-preservation-decision-v1'),
    dict(receipt=dict(algorithm='private-preservation-decision-v1')),
    dict(analysis=dict(controller_run_id='4'*64)),
    dict(context=dict(algorithm='guarded-preservation-search-v1')),
    dict(algorithm='corrected-cohort-qualification-v1'),
    dict(cohort_binding={}),dict(controller_qualification={}),dict(qualification_sha256=None),
    dict(controller_grade={}),dict(trajectory_profile='controller-trajectory-v1'),
    dict(trajectory_analysis={}),dict(policy_sha256='5'*64),
    dict(receipt=dict(profile='controller-trajectory-v1')),
    dict(report_kind='controller_trajectory'),dict(context=dict(outcome_mapping='artifact_and_required_v1')),
    dict(evidence_source='controller_verified_development_only'))


def claim_id(fields):
    """A readable id such as receipt.algorithm=private-preservation-decision-v1."""
    (key,value),=fields.items()
    if isinstance(value,dict) and value:
        (inner,value),=value.items(); key=key+'.'+inner
    text=value if isinstance(value,str) else repr(value)
    return key+'='+(text[:8]+'...' if len(text)>40 else text)


@pytest.mark.parametrize('fields',[pytest.param(fields,id=claim_id(fields)) for fields in PRIVATE_CONTROLLER_CLAIMS])
def test_private_controller_claims_cannot_be_relabelled_public(fields):
    item=dict(record(),publication_allowed=True)
    item.update(fields)
    assert not public_export_allowed([item])
    with pytest.raises(ValueError): summary([item],public=True)


INTERCHANGE_CLAIMS=[dict.fromkeys([key]) for key in (
    'protected_access','protected_access_sha256','controller_source_profile',
    'source_capture','live_verification','verified_execution','import_sha256',
    'foreign_claims','proposal_id')]
INTERCHANGE_CLAIMS.extend(dict(profile=profile) for profile in (
    'protected-diagnostic-access-v1','ahl-controller-live-v1','external-evaluation-data-v1'))
INTERCHANGE_CLAIMS.extend(dict(format=profile) for profile in (
    'promptfoo-cases-json-v1','promptfoo-results-jsonl-v1','openai-evals-match-jsonl-v1'))


@pytest.mark.parametrize(('claim','wrapper'),[
    pytest.param(claim,wrapper,id=claim_id(claim)+'@'+(wrapper or 'top'))
    for claim in INTERCHANGE_CLAIMS for wrapper in (None,'receipt','analysis','context','binding')])
def test_interchange_preserves_existing_private_controller_export_guards(claim,wrapper):
    item=record()
    item.update(claim if wrapper is None else {wrapper:claim})
    assert not public_export_allowed([item])


def test_cost_is_unknown_without_measured_usage_or_dated_pricing():
    assert measured_cost({'input_tokens':100}) is None
    prices=dict(as_of='2026-10-31',currency='test-units',input_per_token=1,output_per_token=2)
    assert measured_cost({'input_tokens':100},prices) is None
    assert measured_cost({'input_tokens':2,'output_tokens':3},prices)['amount'] == 8
    with pytest.raises(ValueError): measured_cost({},dict(as_of='unknown'))


def test_treatment_harness_hashes_can_differ_within_matched_runtime():
    baseline=runtime(); candidate=runtime()
    baseline['harness_fingerprint']='baseline-hash'
    candidate['harness_fingerprint']='candidate-hash'
    assert validate_pair(baseline,candidate)['status'] == 'matched'
