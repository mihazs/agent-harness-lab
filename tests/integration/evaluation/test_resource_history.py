"""Synthetic validation of sealed resource artifact structure and legacy refusal."""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace

import pytest

from agent_harness_lab.evaluation import comparison_custody, resource_collection, resource_decision, resource_history
from agent_harness_lab.evaluation.plan import ExperimentSpec
from tests.integration.evaluation.test_oracle_history import OracleHistoryTests
from tests.integration.evaluation.test_resource_collection import finish_trial, transport
from tests.integration.evaluation.test_trial_usage import comparison_fixture
from tests.unit.evaluation.test_resource_decision import token_scope_contract


def artifact_fixture(tmp_path):
    original,client,workspace,harness,deadline = comparison_fixture(tmp_path,transport())
    contract = token_scope_contract(); pin = resource_decision.decide(contract,[], 'supported')['contract_sha256']
    spec = replace(original.spec,resource_contract_sha256=pin)
    comparison = comparison_custody.Comparison(spec,original.fixtures,client,deadline,bundles=original.bundles)
    rows = []
    with comparison:
        with resource_collection.UsageCollector(comparison,contract) as collector:
            for index in range(len(comparison.expected)):
                rows.append(finish_trial(comparison,client,workspace,harness,deadline,missing=index == 1))
            result = dict(trials=rows); custody = comparison.finish(result)
            artifact = dict(schema_version=1,profile=resource_history.PROFILE,candidate=collector.evidence_candidate(result),
                            publication_allowed=False,certification_authority=False,automatic_promotion=False)
            return artifact,spec,result,custody


def test_private_resource_artifact_rejects_tamper_and_keeps_missing_unknown(tmp_path):
    artifact,spec,result,custody = artifact_fixture(tmp_path)
    assert resource_history.validate(artifact,spec,result,custody) == artifact
    assert artifact['candidate']['samples'][1]['usage']['request_binding_coverage'] == 'unknown'
    changes = [lambda a:a['candidate']['resource_contract']['endpoints'][0].update(horizon=1),
               lambda a:a['candidate']['samples'][0].update(scheduled_trial_sha256='a'*64),
               lambda a:a['candidate']['samples'][0]['usage']['usage']['input_tokens'].update(total=0),
               lambda a:a['candidate']['samples'][0]['usage'].update(raw_prompt='synthetic'),
               lambda a:a.update(publication_allowed=True)]
    for change in changes:
        altered = deepcopy(artifact); change(altered)
        with pytest.raises(ValueError): resource_history.validate(altered,spec,result,custody)


def test_resource_history_requires_controlled_pinned_comparison_before_writes():
    with ExitStack() as cleanups:
        fixture = OracleHistoryTests(cleanups.callback)
        original = ExperimentSpec.parse(fixture.manifest['experiment'])
        contract = token_scope_contract(); pin = resource_decision.decide(contract,[], 'supported')['contract_sha256']
        fixture.manifest['experiment'] = replace(original,resource_contract_sha256=pin).to_dict(); fixture.write_manifest()
        with pytest.raises(ValueError,match='pinned Responses comparison'):
            fixture.store.run(fixture.args.manifest,fixture.args.baseline,fixture.args.candidate,
                cgroup_root=fixture.root/'delegation',image_root=fixture.root/'image',image_sha256=fixture.image_hash,
                disk_bytes=64*1024*1024,resource_contract=contract)
