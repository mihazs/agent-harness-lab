"""The frozen bayes_decision declaration: defaults, strict rejection, digest identity and schema; synthetic only."""
from dataclasses import replace
import copy
import hashlib
import json
import math
import operator
from pathlib import Path

import pytest

from agent_harness_lab.evaluation import sequential
from agent_harness_lab.evaluation.bayes_spec import PROFILE, BayesDecisionError
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
from tests.support.bayes_spec_fixture import MINIMAL, RESOLVED, mutated, spec, trial_ids

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = json.loads((ROOT / 'docs/schemas/bayes-decision-spec.schema.json').read_text())
# Digest, trial ids and to_dict of a plan without the field, computed on the implementation before this change.
GOLDEN = {'plain': ('19bed0f1c9277bef70d329c2017084975c636043f22998598251f1a9317ca1f1',
                    'c5173e0b9e8766b3c225ca97ce0819bed83cbee09c850cb3881b67e166485206'),
          'full': ('216a646e891bccf8c099d29c742f0397f20d1261a87dc99707d480f0669e540a',
                   '5311229838ff96b02ac5394623883f6738d0ba03c4a7e4a34d2d778b6a7ef276')}


def fingerprint(value):
    return value.digest, hashlib.sha256('\n'.join(trial_ids(value)).encode()).hexdigest()


def rejected(block, field, **options):
    with pytest.raises(BayesDecisionError) as caught:
        spec(block, **options).validate()
    assert caught.value.field == field and field in str(caught.value)
    assert isinstance(caught.value, ValueError)


def test_plan_without_the_field_keeps_its_dict_digest_and_trial_ids():
    plain = spec(None, precision_half_width=None)
    full = spec(None, root_similarity_sha256='1' * 64, resource_contract_sha256='2' * 64,
                resource_quality_policy_sha256='3' * 64)
    assert 'bayes_decision' not in plain.to_dict() and 'bayes_decision' not in full.to_dict()
    assert fingerprint(plain) == GOLDEN['plain'] and fingerprint(full) == GOLDEN['full']
    assert ExperimentSpec.parse(plain.to_dict()).bayes_decision is None


def test_minimal_declaration_freezes_every_default_at_construction():
    assert spec().bayes_decision == RESOLVED
    assert spec(effect_margin=.2).bayes_decision['rope_epsilon'] == .2
    assert spec(RESOLVED).bayes_decision == RESOLVED


def test_resolved_values_stay_frozen_when_the_declared_margins_change():
    value = spec()
    changed = replace(value, effect_margin=.3, family_margin=.2)
    assert changed.bayes_decision == RESOLVED


def test_declaration_round_trips_through_to_dict_and_parse():
    value = spec(mutated(['p_cut'], .9) | {'cross_check_alpha': .1})
    again = ExperimentSpec.parse(json.loads(json.dumps(value.to_dict())))
    assert again == value and again.to_dict() == value.to_dict() and again.digest == value.digest
    assert value.to_dict()['bayes_decision']['p_cut'] == .9


def test_declaring_the_field_changes_digest_and_trial_ids():
    assert fingerprint(spec()) not in GOLDEN.values() and fingerprint(spec(None)) != fingerprint(spec())
    assert set(trial_ids(spec())).isdisjoint(trial_ids(spec(None)))


@pytest.mark.parametrize('path, value', [
    (['p_cut'], .9), (['rope_epsilon'], .2), (['regression_margin'], .1), (['posterior_draws'], 30000),
    (['cross_check_alpha'], .1), (['allow_bound_resolved_verdict'], True), (['prior', 'concentration'], 2),
], ids=lambda item: str(item))
def test_changing_any_single_parameter_changes_digest_and_trial_ids(path, value):
    base, other = spec(RESOLVED), spec(mutated(path, value))
    assert base.digest != other.digest and set(trial_ids(base)).isdisjoint(trial_ids(other))


def test_caller_mutating_its_input_cannot_change_the_frozen_spec():
    declared = copy.deepcopy(RESOLVED)
    value = spec(declared)
    before = value.digest
    declared['p_cut'] = .99
    declared['prior']['concentration'] = 9
    assert value.digest == before and value.bayes_decision == RESOLVED


@pytest.mark.parametrize('field, value', [
    ('bayes_decision.schema_version', 2), ('bayes_decision.schema_version', True), ('bayes_decision.schema_version', 1.0),
    ('bayes_decision.schema_version', '1'), ('bayes_decision.profile', 'bayesian-rope-decision-v2'),
    ('bayes_decision.profile', None),
    ('bayes_decision.p_cut', .5), ('bayes_decision.p_cut', 1), ('bayes_decision.p_cut', 1.0),
    ('bayes_decision.p_cut', .49), ('bayes_decision.p_cut', math.nan), ('bayes_decision.p_cut', math.inf),
    ('bayes_decision.p_cut', True), ('bayes_decision.p_cut', '0.95'), ('bayes_decision.p_cut', None),
    ('bayes_decision.rope_epsilon', 0), ('bayes_decision.rope_epsilon', 1), ('bayes_decision.rope_epsilon', -.1),
    ('bayes_decision.rope_epsilon', math.nan), ('bayes_decision.rope_epsilon', True),
    ('bayes_decision.rope_epsilon', '0.1'), ('bayes_decision.rope_epsilon', None),
    ('bayes_decision.regression_margin', -.01), ('bayes_decision.regression_margin', 1),
    ('bayes_decision.regression_margin', math.nan), ('bayes_decision.regression_margin', math.inf),
    ('bayes_decision.regression_margin', True), ('bayes_decision.regression_margin', '0.05'),
    ('bayes_decision.posterior_draws', 999), ('bayes_decision.posterior_draws', 200001),
    ('bayes_decision.posterior_draws', 20000.0), ('bayes_decision.posterior_draws', True),
    ('bayes_decision.posterior_draws', '20000'), ('bayes_decision.posterior_draws', math.nan),
    ('bayes_decision.cross_check_alpha', 0), ('bayes_decision.cross_check_alpha', .5),
    ('bayes_decision.cross_check_alpha', -.1), ('bayes_decision.cross_check_alpha', math.nan),
    ('bayes_decision.cross_check_alpha', True), ('bayes_decision.cross_check_alpha', '0.05'),
    ('bayes_decision.allow_bound_resolved_verdict', 'false'), ('bayes_decision.allow_bound_resolved_verdict', 0),
    ('bayes_decision.allow_bound_resolved_verdict', None),
    ('bayes_decision.prior.kind', 'beta_binomial'), ('bayes_decision.prior.kind', None),
    ('bayes_decision.prior.concentration', 0), ('bayes_decision.prior.concentration', -1),
    ('bayes_decision.prior.concentration', math.nan), ('bayes_decision.prior.concentration', math.inf),
    ('bayes_decision.prior.concentration', True), ('bayes_decision.prior.concentration', '1'),
    ('bayes_decision.prior.anchor', 1), ('bayes_decision.prior.anchor', .5), ('bayes_decision.prior.anchor', -1),
    ('bayes_decision.prior.anchor', False), ('bayes_decision.prior.anchor', None),
], ids=lambda item: str(item))
def test_out_of_range_or_mistyped_value_names_its_field(field, value):
    rejected(mutated(field.split('.')[1:], value), field)


@pytest.mark.parametrize('field', ['schema_version', 'profile', 'prior.kind', 'prior.concentration', 'prior.anchor'])
def test_missing_required_key_is_rejected_by_name(field):
    block = copy.deepcopy(RESOLVED)
    target = block
    *parents, leaf = field.split('.')
    for key in parents:
        target = target[key]
    del target[leaf]
    rejected(block, f'bayes_decision.{field}')


def test_rope_epsilon_must_be_explicit_when_effect_margin_is_zero():
    rejected(MINIMAL, 'bayes_decision.rope_epsilon', effect_margin=0)
    assert spec(MINIMAL | {'rope_epsilon': .05}, effect_margin=0).bayes_decision['rope_epsilon'] == .05


@pytest.mark.parametrize('block, field', [
    (RESOLVED | {'extra': 1}, 'bayes_decision.extra'), (MINIMAL | {'rope': .1}, 'bayes_decision.rope'),
    (mutated(['prior'], RESOLVED['prior'] | {'strength': 1}), 'bayes_decision.prior.strength')],
    ids=['top', 'near-miss', 'prior'])
def test_unknown_keys_are_rejected_by_name(block, field):
    rejected(block, field)


@pytest.mark.parametrize('block', [[], 'on', '', True, False, 0, 1, .5, ()], ids=repr)
def test_a_non_object_declaration_is_rejected(block):
    rejected(block, 'bayes_decision')


def test_empty_object_is_not_an_implicit_opt_in():
    rejected({}, 'bayes_decision.schema_version')


def test_declaration_without_a_precision_target_is_rejected():
    rejected(MINIMAL, 'precision_half_width', precision_half_width=None)


def test_a_sequential_plan_is_rejected():
    rejected(MINIMAL, 'sequential', precision_half_width=None, sequential=sequential.config(2))


def test_a_cohort_pinned_plan_is_rejected():
    rejected(MINIMAL, 'cohort_manifest_sha256', cohort_manifest_sha256='4' * 64)


def test_invalid_declaration_cannot_produce_a_digest_or_a_schedule():
    for call in (lambda v: v.digest, schedule):
        with pytest.raises(BayesDecisionError):
            call(spec(mutated(['p_cut'], 1)))


@pytest.mark.parametrize('edit', [.5, 1, 0, 'x'])
def test_in_place_edit_of_a_frozen_declaration_is_caught_by_validation(edit):
    value = spec()
    value.bayes_decision['p_cut'] = edit
    with pytest.raises(BayesDecisionError):
        value.validate()


def accepts(node, value):
    """Minimal checker for the JSON Schema keywords the shipped schema uses, strict about Python types."""
    kind = node.get('type')
    if kind == 'object' and not isinstance(value, dict):
        return False
    if kind == 'string' and not isinstance(value, str) or kind == 'boolean' and type(value) is not bool:
        return False
    if kind == 'integer' and type(value) is not int:
        return False
    if kind == 'number' and (type(value) not in (int, float) or not math.isfinite(value)):
        return False
    if 'const' in node and value != node['const']:
        return False
    if type(value) in (int, float) and any(key in node and not check(value, node[key]) for key, check in (
            ('minimum', operator.ge), ('exclusiveMinimum', operator.gt),
            ('maximum', operator.le), ('exclusiveMaximum', operator.lt))):
        return False
    if kind != 'object':
        return True
    props = node.get('properties', {})
    return (set(node.get('required', ())) <= set(value) and (node.get('additionalProperties', True) or set(value) <= set(props))
            and all(accepts(props[key], item) for key, item in value.items() if key in props))


def test_schema_covers_every_field_and_agrees_with_the_validator():
    assert SCHEMA['type'] == 'object' and SCHEMA['additionalProperties'] is False
    assert set(SCHEMA['required']) == set(SCHEMA['properties']) == set(RESOLVED)
    assert SCHEMA['properties']['profile']['const'] == PROFILE
    assert set(SCHEMA['properties']['prior']['required']) == set(RESOLVED['prior'])
    probes = [([key], RESOLVED[key]) for key in RESOLVED] + [(['prior', key], RESOLVED['prior'][key]) for key in RESOLVED['prior']]
    values = (-1, 0, 1, .5, .49, .51, .999, 999, 1000, 200000, 200001, 20000.0, 0.0, True, False, None, '1', 'x', math.nan, math.inf)
    checked = 0
    for path, current in probes:
        for value in (current, *values):
            block = mutated(path, value)
            try:
                spec(block).validate()
                valid = True
            except BayesDecisionError:
                valid = False
            assert accepts(SCHEMA, block) is valid, (path, value)
            checked += 1
    assert checked > 200
    assert not accepts(SCHEMA, {**RESOLVED, 'extra': 1}) and not accepts(SCHEMA, {k: v for k, v in RESOLVED.items() if k != 'p_cut'})
