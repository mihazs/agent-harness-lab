"""Synthetic all-assigned grouped resource bounds, never adoption authority."""
from copy import deepcopy
from dataclasses import replace
import itertools
import math

import pytest

from agent_harness_lab.evaluation import resource_decision, resource_grouped_bound, root_similarity
from agent_harness_lab.evaluation.plan import schedule
from agent_harness_lab.private_journal import encoded
from agent_harness_lab.sources.schema import digest
from tests.unit.evaluation.test_precision_solver import spec
from tests.unit.evaluation.test_resource_decision import token_scope_contract, token_scope_rows


@pytest.fixture
def inputs(tmp_path):
    original = spec({'a':4})
    prompts = ['Perform identical synthetic operation.', 'Perform identical synthetic operation.',
               'Reject invalid dates from calendar input.', 'Sort numbers and remove duplicates.']
    manifest = {'tasks':[dict(id=task,root=root,prompt=prompt) for (task,root,_),prompt in zip(original.tasks,prompts)]}
    evidence = root_similarity.manifest_evidence(manifest); path = tmp_path/'manifest.json'; path.write_bytes(encoded(manifest))
    contract = token_scope_contract(); contract['alpha'] = .9; contract['min_roots'] = 2
    pin = resource_decision.decide(contract,[],None)['contract_sha256']
    plan = replace(original,root_similarity_sha256=digest(evidence),resource_contract_sha256=pin)
    template = token_scope_rows()[0]
    pairs = [dict(task=task,repetition=0,baseline=deepcopy(template['baseline']),candidate=deepcopy(template['candidate']))
             for task,_,_ in original.tasks]
    return plan,contract,pairs,evidence,path


def test_exhaustive_root_weighting_and_related_group_radius(inputs):
    plan,contract,pairs,evidence,path = inputs
    groups = root_similarity.binding(plan,evidence,path)
    roots = [root for _,root,_ in plan.tasks]
    assert groups[roots[0]] == groups[roots[1]] and len(groups) == 2
    horizon = contract['endpoints'][0]['horizon']
    for assignments in itertools.product((0,1),repeat=len(pairs)):
        rows = deepcopy(pairs)
        for index,value in enumerate(assignments):
            rows[index]['baseline']['measurements']['tokens'] = horizon if not value else 0
            rows[index]['candidate']['measurements']['tokens'] = horizon if value else 0
        result = resource_grouped_bound.compute(plan,contract,'tokens',rows,evidence,path)
        assert result['status'] == 'bounded' and result['grouped_roots'] == 3
        mean = sum(2*value-1 for value in assignments)/4
        width = math.sqrt(2*math.log(2/contract['alpha'])*(.5**2+2*.25**2))
        assert result['mean'] == pytest.approx(mean*horizon)
        assert result['low'] == pytest.approx(max(-1,mean-width)*horizon)
        assert result['high'] == pytest.approx(min(1,mean+width)*horizon)
        assert result['resource_decision'] == 'not_computed' and not result['automatic_promotion']


def test_missing_stale_incomplete_or_asymmetric_evidence_stays_unknown(inputs):
    plan,contract,pairs,evidence,path = inputs
    assert resource_grouped_bound.compute(plan,contract,'tokens',pairs,None,path)['reason'] == 'root_similarity_unavailable'
    assert resource_grouped_bound.compute(replace(plan,root_similarity_sha256=None),contract,'tokens',pairs,None,None)['status'] == 'unknown'
    assert resource_grouped_bound.compute(plan,contract,'tokens',pairs[:-1],evidence,path)['reason'] == 'assigned_pairs_incomplete'
    altered = deepcopy(pairs); altered[0]['candidate']['provenance']['cache_state'] = 'provider_cache_used'
    assert resource_grouped_bound.compute(plan,contract,'tokens',altered,evidence,path)['reason'] == 'resource_measurement_unknown'
    with pytest.raises(ValueError,match='duplicate'): resource_grouped_bound.compute(plan,contract,'tokens',pairs+pairs[:1],evidence,path)
    path.write_bytes(encoded({'tasks':[]}))
    assert resource_grouped_bound.compute(plan,contract,'tokens',pairs,evidence,path)['status'] == 'unknown'


def test_missing_token_coverage_and_public_export_are_blocked(inputs):
    plan,contract,pairs,evidence,path = inputs
    pairs[0]['baseline']['measurement_provenance']['tokens']['unknown_requests'] = 1
    assert resource_grouped_bound.compute(plan,contract,'tokens',pairs,evidence,path)['reason'] == 'resource_measurement_unknown'
    with pytest.raises(ValueError,match='private'): resource_grouped_bound.compute(plan,contract,'tokens',pairs,evidence,path,public=True)
    assert schedule(plan)


def test_repeated_tasks_do_not_gain_root_weight(tmp_path):
    original = spec({'a':4},extra_tasks=(('extra-task','a-r0','a'),))
    prompts = ['Perform identical synthetic operation.', 'Perform identical synthetic operation.',
               'Reject invalid dates from calendar input.', 'Sort numbers and remove duplicates.',
               'Perform identical synthetic operation.']
    manifest = {'tasks':[dict(id=task,root=root,prompt=prompt) for (task,root,_),prompt in zip(original.tasks,prompts)]}
    evidence = root_similarity.manifest_evidence(manifest); path = tmp_path/'manifest.json'; path.write_bytes(encoded(manifest))
    contract = token_scope_contract(); contract.update(alpha=.9,min_roots=2)
    plan = replace(original,root_similarity_sha256=digest(evidence),
                   resource_contract_sha256=resource_decision.decide(contract,[],None)['contract_sha256'])
    template = token_scope_rows()[0]; horizon = contract['endpoints'][0]['horizon']
    rows = [dict(task=task,repetition=0,baseline=deepcopy(template['baseline']),candidate=deepcopy(template['candidate']))
            for task,_,_ in original.tasks]
    for row in rows:
        row['baseline']['measurements']['tokens'] = 0
        row['candidate']['measurements']['tokens'] = horizon if row['task'] == 'extra-task' else 0
    result = resource_grouped_bound.compute(plan,contract,'tokens',rows,evidence,path)
    assert result['status'] == 'bounded' and result['mean'] == pytest.approx(horizon/8)
    assert result['grouped_roots'] == 3


def test_alpha_underflow_and_grouped_minimum_fail_closed(inputs):
    plan,contract,pairs,evidence,path = inputs
    too_small = deepcopy(contract); too_small['min_roots'] = 4
    plan = replace(plan,resource_contract_sha256=resource_decision.decide(too_small,[],None)['contract_sha256'])
    assert resource_grouped_bound.compute(plan,too_small,'tokens',pairs,evidence,path)['reason'] == 'insufficient_grouped_roots'
    tiny = deepcopy(contract); tiny['alpha'] = 5e-324
    second = deepcopy(tiny['endpoints'][0]); second['name'] = 'tokens_second'
    tiny['endpoints'].append(second)
    plan = replace(plan,resource_contract_sha256=resource_decision.decide(tiny,[],None)['contract_sha256'])
    assert resource_grouped_bound.compute(plan,tiny,'tokens',pairs,evidence,path)['reason'] == 'numeric_bound_unavailable'


def test_unequal_family_weights_and_cross_family_related_roots(tmp_path):
    original = spec({'a':1,'b':3},weights={'a':.9,'b':.1})
    prompts = ['Perform identical synthetic operation.', 'Perform identical synthetic operation.',
               'Reject invalid dates from calendar input.', 'Sort numbers and remove duplicates.']
    manifest = {'tasks':[dict(id=task,root=root,prompt=prompt) for (task,root,_),prompt in zip(original.tasks,prompts)]}
    evidence = root_similarity.manifest_evidence(manifest); path = tmp_path/'manifest.json'; path.write_bytes(encoded(manifest))
    contract = token_scope_contract(); contract.update(alpha=.9,min_roots=2)
    plan = replace(original,root_similarity_sha256=digest(evidence),
                   resource_contract_sha256=resource_decision.decide(contract,[],None)['contract_sha256'])
    template = token_scope_rows()[0]; horizon = contract['endpoints'][0]['horizon']
    rows = [dict(task=task,repetition=0,baseline=deepcopy(template['baseline']),candidate=deepcopy(template['candidate']))
            for task,_,_ in original.tasks]
    for row in rows:
        row['baseline']['measurements']['tokens'] = 0
        row['candidate']['measurements']['tokens'] = horizon if row['task'].startswith('a-') else 0
    result = resource_grouped_bound.compute(plan,contract,'tokens',rows,evidence,path)
    width = math.sqrt(2*math.log(2/contract['alpha'])*((.9+.1/3)**2+2*(.1/3)**2))
    assert result['status'] == 'bounded' and result['mean'] == pytest.approx(.9*horizon)
    assert result['grouped_roots'] == 3
    assert result['low'] == pytest.approx(max(-1,.9-width)*horizon)
    assert result['high'] == pytest.approx(min(1,.9+width)*horizon)
    assert result['estimand'] == 'frozen_family_weighted_mean_of_selected_root_mean_paired_resource_differences'
