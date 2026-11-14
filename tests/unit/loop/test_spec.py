"""The loop declaration: strict fields, typed reasons, a canonical immutable digest and no invented bar."""
import dataclasses
import hashlib
import json

import pytest

from agent_harness_lab.exit_guidance import REMEDIATION
from agent_harness_lab.loop.errors import REASONS, LoopError
from agent_harness_lab.loop.spec import AUTHORITY, DECLARED, DEFAULTS, MAX_ROUNDS_CAP, RELATIVE, LoopSpec, parse_declaration, resolve

LIMITS = dict(seconds=2700, trials=20)
AUTHORITY_KEYS = ('apply', 'automatic_promotion', 'certification', 'global', 'promotion', 'publication_allowed', 'recommendation_only', 'scope')
AREAS = ('coding', 'docs')
NAN, INF = float('nan'), float('inf')


def declared(**project):
    return resolve(limits=LIMITS, project=project or None, areas=AREAS)


def rejected(call):
    with pytest.raises(LoopError) as caught:
        call()
    return caught.value


REJECTIONS = [
    pytest.param(dict(bogus=1), 'loop_field_unknown', 'bogus', id='unknown-field'),
    pytest.param(dict(limits=dict(seconds=1, trials=1)), 'loop_field_unknown', 'limits', id='limits-are-referenced-not-redeclared'),
    *[pytest.param(dict(accept_score=score), 'loop_accept_score_invalid', 'accept_score', id=f'score-{name}')
      for name, score in dict(string='0.9', boolean=True, null=None, zero=0, negative=-0.1, above_one=1.5, nan=NAN, infinite=INF,
                              empty_map={}, list=[0.9]).items()],
    pytest.param(dict(accept_score=dict(coding=True)), 'loop_accept_score_invalid', 'accept_score.coding', id='area-score-boolean'),
    pytest.param(dict(accept_score=dict(coding=0)), 'loop_accept_score_invalid', 'accept_score.coding', id='area-score-zero'),
    pytest.param(dict(accept_score=dict(coding=NAN)), 'loop_accept_score_invalid', 'accept_score.coding', id='area-score-nan'),
    pytest.param(dict(accept_score=dict(elsewhere=0.9)), 'loop_area_undeclared', 'accept_score.elsewhere', id='area-undeclared'),
    pytest.param(dict(accept_score={1: 0.9}), 'loop_area_undeclared', 'accept_score.1', id='area-key-not-a-string'),
    pytest.param(dict(accept_basis='point'), 'loop_accept_basis_invalid', 'accept_basis', id='point-basis'),
    pytest.param(dict(accept_basis=['lower_bound']), 'loop_accept_basis_invalid', 'accept_basis', id='basis-not-a-string'),
    *[pytest.param({field: margin}, 'loop_margin_invalid', field, id=f'{field}-{name}')
      for field in ('regression_margin', 'noise_margin')
      for name, margin in dict(negative=-0.01, one=1, nan=NAN, string='0.1', boolean=True).items()],
    *[pytest.param(dict(max_rounds=rounds), 'loop_max_rounds_invalid', 'max_rounds', id=f'max-rounds-{name}')
      for name, rounds in dict(zero=0, above_cap=MAX_ROUNDS_CAP + 1, fractional=2.5, boolean=True, string='3').items()],
    *[pytest.param(dict(plateau_rounds=rounds), 'loop_plateau_rounds_invalid', 'plateau_rounds', id=f'plateau-rounds-{name}')
      for name, rounds in dict(zero=0, fractional=1.5, boolean=True, above_any_cap=MAX_ROUNDS_CAP + 1).items()],
    pytest.param(dict(max_rounds=2, plateau_rounds=3), 'loop_plateau_rounds_invalid', 'plateau_rounds', id='plateau-above-max-rounds'),
    pytest.param(dict(on_regression='ignore'), 'loop_on_regression_invalid', 'on_regression', id='unknown-on-regression'),
    pytest.param(dict(on_regression=1), 'loop_on_regression_invalid', 'on_regression', id='on-regression-not-a-string'),
    *[pytest.param(dict(alpha=alpha), 'loop_alpha_invalid', 'alpha', id=f'alpha-{name}')
      for name, alpha in dict(zero=0, one=1, negative=-0.05, nan=NAN, string='0.05').items()],
    *[pytest.param({key: safe}, 'loop_authority_refused', key, id=f'authority-{key}-{str(safe).lower()}')
      for key in AUTHORITY_KEYS for safe in (True, False)],
]


@pytest.mark.parametrize('declaration, reason, field', REJECTIONS)
def test_declaration_is_rejected_with_a_typed_reason_field_and_one_step_remediation(declaration, reason, field):
    error = rejected(lambda: parse_declaration(declaration, 'project', AREAS))
    assert (error.reason, error.field, error.source) == (reason, field, 'project')
    assert error.remediation == REMEDIATION[reason] and f'Next: {REMEDIATION[reason]}' in str(error)


@pytest.mark.parametrize('declaration', [pytest.param([], id='list'), pytest.param('accept_score', id='string'), pytest.param(1, id='number')])
def test_declaration_that_is_not_an_object_is_rejected(declaration):
    assert rejected(lambda: parse_declaration(declaration, 'user')).reason == 'loop_declaration_invalid'


@pytest.mark.parametrize('areas', [pytest.param((), id='no-areas-declared')])
def test_per_area_score_without_declared_areas_is_areas_not_declared(areas):
    error = rejected(lambda: parse_declaration(dict(accept_score=dict(coding=0.9)), 'project', areas))
    assert (error.reason, error.field) == ('areas_not_declared', 'accept_score')


def test_rejection_names_the_field_and_never_echoes_the_offending_value():
    secret = 'sk-private-value-0123'
    for declaration in (dict(accept_basis=secret), dict(on_regression=secret), dict(accept_score=secret), dict(max_rounds=secret)):
        assert secret not in str(rejected(lambda: parse_declaration(declaration, 'project', AREAS)))


def test_the_refused_authority_keys_are_exactly_the_documented_ones():
    assert AUTHORITY == frozenset(AUTHORITY_KEYS)


def test_nothing_is_declared_by_an_absent_layer_or_an_empty_object():
    assert parse_declaration(None, 'cli') == {} and parse_declaration({}, 'cli') == {}


def test_every_loop_reason_has_one_remediation_registered_for_exit_guidance():
    assert all(REMEDIATION[reason] == text and text.endswith('.') for reason, text in REASONS.items())
    assert len(set(REASONS.values())) == len(REASONS)


def test_no_declared_score_runs_the_relative_mode_and_never_invents_an_absolute_bar():
    spec = declared()
    assert (spec.mode, spec.accept_score, spec.acceptance_source) == (RELATIVE, None, 'default')
    document = spec.to_dict()
    assert (document['mode'], document['accept_score'], document['acceptance_source']) == (RELATIVE, None, 'default')
    assert all(spec.declared()[field] == DEFAULTS[field] for field in DECLARED)


@pytest.mark.parametrize('score, mode, expected', [
    pytest.param(0.8, 'absolute', 0.8, id='fraction'),
    pytest.param(1, 'absolute', 1.0, id='whole-number-one'),
    pytest.param(dict(docs=0.9, coding=0.8), 'per_area', dict(coding=0.8, docs=0.9), id='per-area-map'),
])
def test_declared_score_sets_the_mode_and_is_reported_as_declared(score, mode, expected):
    spec = declared(accept_score=score)
    assert (spec.mode, spec.to_dict()['accept_score'], spec.acceptance_source) == (mode, expected, 'project')


def test_per_area_digest_does_not_depend_on_key_order():
    assert declared(accept_score=dict(docs=0.9, coding=0.8)).digest == declared(accept_score=dict(coding=0.8, docs=0.9)).digest


def test_per_look_error_level_splits_alpha_across_the_round_cap_and_is_frozen_in_the_digest():
    spec = declared(max_rounds=4, alpha=0.04)
    assert (spec.per_look_alpha, spec.to_dict()['looks'], spec.to_dict()['per_look_alpha']) == (pytest.approx(0.01), 4, pytest.approx(0.01))
    assert declared().per_look_alpha == pytest.approx(0.05 / 3)
    assert declared(max_rounds=4, alpha=0.04).digest != declared(max_rounds=2, alpha=0.04).digest


def test_default_plateau_rounds_never_exceeds_a_smaller_max_rounds():
    spec = declared(max_rounds=1)
    assert (spec.plateau_rounds, dict(spec.sources)['plateau_rounds']) == (1, 'default')
    assert rejected(lambda: declared(max_rounds=1, plateau_rounds=2)).reason == 'loop_plateau_rounds_invalid'


def test_digest_is_the_sha256_of_the_canonical_declared_values():
    document = declared(accept_score=0.9).to_dict()
    identity = {key: value for key, value in document.items() if key not in ('digest', 'sources', 'acceptance_source', 'looks')}
    assert hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest() == document['digest']
    assert declared(accept_score=0.9).digest == document['digest'] == declared(accept_score=0.9).digest


@pytest.mark.parametrize('field, value', [
    pytest.param('accept_score', 0.5, id='accept_score'), pytest.param('accept_basis', 'point', id='accept_basis'),
    pytest.param('regression_margin', 0.2, id='regression_margin'), pytest.param('noise_margin', 0.2, id='noise_margin'),
    pytest.param('max_rounds', 5, id='max_rounds'), pytest.param('plateau_rounds', 3, id='plateau_rounds'),
    pytest.param('on_regression', 'rollback_continue', id='on_regression'), pytest.param('alpha', 0.01, id='alpha'),
    pytest.param('limit_seconds', 60, id='limit_seconds'), pytest.param('limit_trials', 7, id='limit_trials'),
])
def test_changing_any_declared_field_changes_the_digest(field, value):
    base = declared()
    changed = dataclasses.replace(base, **{field: value})
    if field == 'accept_basis':  # an unsupported basis is refused before it can be identified
        assert rejected(lambda: changed.digest).reason == 'loop_accept_basis_invalid'
    else:
        assert changed.digest != base.digest


def test_the_source_of_a_value_is_a_label_and_not_part_of_the_identity():
    from_cli = resolve(limits=LIMITS, cli=dict(max_rounds=5), areas=AREAS)
    from_project = resolve(limits=LIMITS, project=dict(max_rounds=5), areas=AREAS)
    assert from_cli.digest == from_project.digest and from_cli.to_dict()['sources'] != from_project.to_dict()['sources']


def test_the_resolved_spec_is_immutable():
    spec = declared()
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.max_rounds = 9
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.automatic_promotion = True


@pytest.mark.parametrize('changes, reason, field', [
    pytest.param(dict(automatic_promotion=True), 'loop_authority_refused', 'automatic_promotion', id='promotion-enabled'),
    pytest.param(dict(recommendation_only=False), 'loop_authority_refused', 'recommendation_only', id='recommendation-only-off'),
    pytest.param(dict(certification='verified'), 'loop_authority_refused', 'certification', id='certification-claimed'),
    pytest.param(dict(profile='other'), 'loop_declaration_invalid', 'profile', id='unknown-profile'),
    pytest.param(dict(max_rounds=0), 'loop_max_rounds_invalid', 'max_rounds', id='constructed-with-zero-rounds'),
    pytest.param(dict(limit_trials=0), 'loop_limits_invalid', 'limits', id='zero-trial-limit'),
    pytest.param(dict(limit_seconds=NAN), 'loop_limits_invalid', 'limits', id='nan-time-limit'),
    pytest.param(dict(sources=()), 'loop_declaration_invalid', 'sources', id='sources-missing'),
])
def test_a_directly_constructed_spec_cannot_carry_authority_or_invalid_values(changes, reason, field):
    changed = dataclasses.replace(declared(), **changes)
    for call in (changed.validate, lambda: changed.digest, changed.to_dict):
        error = rejected(call)
        assert (error.reason, error.field) == (reason, field)


def test_the_spec_is_a_loop_spec_that_always_recommends_and_never_promotes():
    spec = declared(accept_score=0.9)
    assert isinstance(spec, LoopSpec)
    assert (spec.recommendation_only, spec.automatic_promotion, spec.certification) == (True, False, 'unverified')
    assert spec.to_dict()['recommendation_only'] is True and spec.to_dict()['automatic_promotion'] is False
