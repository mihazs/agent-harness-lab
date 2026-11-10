"""Synthetic request-bound cache attestations remain private and unverified."""
from copy import deepcopy

import pytest

from agent_harness_lab.evaluation import resource_cache_attestation, resource_history
from tests.integration.evaluation.test_resource_history import artifact_fixture


def statements(artifact,custody):
    return [dict(scheduled_trial_sha256=sample['scheduled_trial_sha256'],request_ordinal=ordinal,cache_state='uncached')
            for sample,row in zip(artifact['candidate']['samples'],custody['rows']) for ordinal in row['custody']['requests']]


def test_explicit_complete_statements_and_missing_coverage(tmp_path):
    legacy,spec,result,custody = artifact_fixture(tmp_path)
    artifact = resource_history.coverage_artifact(legacy); supplied = statements(artifact,custody)
    view = resource_cache_attestation.bind(artifact,spec,result,custody,supplied)
    assert view['trials'][0]['cache_state'] == 'unknown'
    assert view['trials'][2]['cache_state'] == 'uncached'
    assert view['trials'][1]['cache_state'] == 'unknown'  # unmatched request coverage
    assert view['source'] == 'caller_attested_unverified' and view['quality'] == 'unverified'
    assert not view['publication_allowed'] and not view['automatic_promotion']
    asymmetric = deepcopy(supplied); asymmetric[-1]['cache_state'] = 'provider_cache_used'
    view = resource_cache_attestation.bind(artifact,spec,result,custody,asymmetric)
    assert view['trials'][2]['cache_state'] == view['trials'][3]['cache_state'] == 'unknown'
    view = resource_cache_attestation.bind(artifact,spec,result,custody,[])
    assert all(row['cache_state'] == 'unknown' for row in view['trials'])
    assert artifact['candidate']['samples'][0]['usage']['usage']['input_tokens']['total'] == 5


def test_bad_duplicate_and_cross_trial_statements_are_refused(tmp_path):
    legacy,spec,result,custody = artifact_fixture(tmp_path)
    artifact = resource_history.coverage_artifact(legacy); supplied = statements(artifact,custody)
    for bad in (supplied + supplied[:1], [dict(supplied[0],request_ordinal=-1)],
                [dict(supplied[0],cache_state='unknown')], [dict(supplied[0],scheduled_trial_sha256=[])],
                [dict(supplied[0],request_ordinal=True)]):
        with pytest.raises(ValueError): resource_cache_attestation.bind(artifact,spec,result,custody,bad)
    altered = deepcopy(artifact); altered['candidate']['samples'][0]['usage']['usage']['input_tokens']['total'] = 99
    with pytest.raises(ValueError): resource_cache_attestation.bind(altered,spec,result,custody,supplied)
    with pytest.raises(ValueError,match='v8'): resource_cache_attestation.bind(legacy,spec,result,custody,[])


def test_contradictory_cached_count_cannot_validate_uncached_pair(tmp_path):
    legacy,spec,result,custody = artifact_fixture(tmp_path)
    usage = legacy['candidate']['samples'][2]['usage']
    assert usage['request_attempts'] == 1 and usage['request_binding_coverage'] == 'matched'
    usage['usage']['cached_input_tokens'].update(known_subtotal=1,known_requests=1,unknown_requests=0)
    artifact = resource_history.coverage_artifact(legacy)
    view = resource_cache_attestation.bind(artifact,spec,result,custody,statements(artifact,custody))
    assert view['trials'][2]['cache_state'] == view['trials'][3]['cache_state'] == 'unknown'
