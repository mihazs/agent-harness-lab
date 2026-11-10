"""Synthetic private pretrial quality-policy binding and legacy plan identity."""
from contextlib import ExitStack
from dataclasses import replace
import hashlib
import json

import pytest

from agent_harness_lab.evaluation import benchmark, resource_quality_policy
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
from tests.integration.evaluation.test_oracle_history import OracleHistoryTests
from tests.unit.evaluation.test_benchmark_contracts import record
from tests.unit.evaluation.test_precision_solver import spec


def policy():
    return dict(schema_version=1,profile=resource_quality_policy.PROFILE,
                quality_criteria_sha256='a'*64,required_status='supported')


def pin(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',', ':')).encode()).hexdigest()


def test_legacy_plans_remain_identical_and_opt_in_changes_only_new_ids():
    old = spec({'a': 2}); document = old.to_dict()
    assert 'resource_quality_policy_sha256' not in document
    assert ExperimentSpec.parse(document).digest == old.digest
    assert schedule(replace(old,resource_quality_policy_sha256=None)) == schedule(old)
    new = replace(old,resource_contract_sha256='b'*64,resource_quality_policy_sha256=pin(policy()))
    assert ExperimentSpec.parse(new.to_dict()).digest == new.digest != old.digest
    assert {r['trial_id'] for r in schedule(new)}.isdisjoint({r['trial_id'] for r in schedule(old)})


def test_private_binding_requires_exact_policy_and_never_computes_a_decision():
    original = replace(spec({'a':2}),resource_contract_sha256='b'*64)
    value = policy(); target = replace(original,resource_quality_policy_sha256=pin(value))
    assert resource_quality_policy.bind(original,value)['status'] == 'unbound'
    report = resource_quality_policy.bind(target,value)
    assert report['status'] == 'matched' and report['quality_status'] == 'unknown'
    assert report['policy_source'] == 'caller_supplied_unverified' and report['root_grouping'] == 'not_checked'
    assert report['resource_decision'] == 'not_computed' and report['resource_gate'] == 'not_evaluated'
    assert not report['publication_allowed'] and not report['automatic_promotion']
    assert resource_quality_policy.bind(target,dict(value,quality_criteria_sha256='c'*64))['status'] == 'mismatch'
    with pytest.raises(ValueError,match='private'): resource_quality_policy.bind(target,value,public=True)
    item = record(); item['resource_quality_policy_sha256'] = pin(value)
    assert not benchmark.public_export_allowed([item])


@pytest.mark.parametrize('quality', ['A'*64, 'a'*63, 'x'*64, True])
def test_invalid_pins_and_missing_contract_are_rejected(quality):
    with pytest.raises(ValueError,match='quality policy'):
        replace(spec({'a':2}),resource_contract_sha256='b'*64,resource_quality_policy_sha256=quality).validate()
    with pytest.raises(ValueError,match='quality policy'):
        replace(spec({'a':2}),resource_quality_policy_sha256='a'*64).validate()


def test_policy_cannot_be_attached_or_used_without_pretrial_binding():
    with ExitStack() as cleanups:
        fixture = OracleHistoryTests(cleanups.callback)
        original = ExperimentSpec.parse(fixture.manifest['experiment'])
        fixture.manifest['experiment'] = replace(original,resource_contract_sha256='b'*64,
            resource_quality_policy_sha256=pin(policy())).to_dict(); fixture.write_manifest()
        with pytest.raises(ValueError,match='pretrial policy and root grouping'):
            fixture.store.run(fixture.args.manifest,fixture.args.baseline,fixture.args.candidate,
                cgroup_root=fixture.root/'delegation',image_root=fixture.root/'image',image_sha256=fixture.image_hash,
                disk_bytes=64*1024*1024,resource_quality_policy=policy())
