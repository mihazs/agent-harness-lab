"""Where a loop declaration may come from: precedence, the project ahl.json object, the user file, and no authority."""
import json
import os

import pytest

from agent_harness_lab import config, user_config
from agent_harness_lab.loop.errors import LoopError
from agent_harness_lab.loop.spec import DEFAULTS, resolve

LIMITS = dict(seconds=2700, trials=20)
AREAS = [dict(id='coding', label='Coding', description='Change and verify source code.')]


def rejected(call):
    with pytest.raises(LoopError) as caught:
        call()
    return caught.value


LAYERS = ('cli', 'user', 'project')
AUTHORITY_KEYS = ('apply', 'automatic_promotion', 'certification', 'global', 'promotion', 'publication_allowed', 'recommendation_only', 'scope')
PRECEDENCE = [
    pytest.param('max_rounds', dict(cli=4, user=5, project=6), 4, 'cli', id='max-rounds-cli-over-user-and-project'),
    pytest.param('max_rounds', dict(user=5, project=6), 5, 'user', id='max-rounds-user-over-project'),
    pytest.param('max_rounds', dict(project=6), 6, 'project', id='max-rounds-project-alone'),
    pytest.param('max_rounds', dict(cli=4, project=6), 4, 'cli', id='max-rounds-cli-over-project'),
    pytest.param('max_rounds', {}, DEFAULTS['max_rounds'], 'default', id='max-rounds-default'),
    pytest.param('on_regression', dict(cli='rollback_continue', user='stop'), 'rollback_continue', 'cli', id='on-regression-cli-over-user'),
    pytest.param('on_regression', dict(user='rollback_continue', project='stop'), 'rollback_continue', 'user', id='on-regression-user-over-project'),
    pytest.param('on_regression', {}, 'stop', 'default', id='on-regression-default'),
    pytest.param('accept_score', dict(cli=0.9, user=0.8, project=0.7), 0.9, 'cli', id='accept-score-cli-over-user-and-project'),
    pytest.param('accept_score', dict(user=0.8, project=0.7), 0.8, 'user', id='accept-score-user-over-project'),
    pytest.param('accept_score', dict(project=0.7), 0.7, 'project', id='accept-score-project-alone'),
    pytest.param('accept_score', {}, None, 'default', id='accept-score-default-is-relative-mode'),
    pytest.param('alpha', dict(cli=0.01, project=0.04), 0.01, 'cli', id='alpha-cli-over-project'),
    pytest.param('noise_margin', dict(user=0.1, project=0.2), 0.1, 'user', id='noise-margin-user-over-project'),
]


@pytest.mark.parametrize('field, given, value, source', PRECEDENCE)
def test_cli_overrides_user_overrides_project_overrides_default_and_the_source_is_named(field, given, value, source):
    spec = resolve(limits=LIMITS, **{layer: {field: item} for layer, item in given.items()})
    assert spec.declared()[field] == value
    assert spec.to_dict()['sources'][field] == source


def test_each_field_resolves_independently_and_names_its_own_source():
    spec = resolve(limits=LIMITS, cli=dict(accept_score=0.9), user=dict(max_rounds=5), project=dict(on_regression='rollback_continue', max_rounds=2))
    assert spec.to_dict()['sources'] == dict(accept_score='cli', accept_basis='default', regression_margin='default', noise_margin='default',
                                           max_rounds='user', plateau_rounds='default', on_regression='project', alpha='default', limits='project')
    assert (spec.accept_score, spec.max_rounds, spec.on_regression, spec.acceptance_source) == (0.9, 5, 'rollback_continue', 'cli')


def test_the_round_bounds_are_checked_across_layers_and_blame_the_declared_plateau():
    error = rejected(lambda: resolve(limits=LIMITS, cli=dict(max_rounds=2), project=dict(plateau_rounds=3)))
    assert (error.reason, error.field, error.source) == ('loop_plateau_rounds_invalid', 'plateau_rounds', 'project')
    assert resolve(limits=LIMITS, cli=dict(max_rounds=8), project=dict(plateau_rounds=5)).plateau_rounds == 5


@pytest.mark.parametrize('layer', LAYERS)
@pytest.mark.parametrize('key', AUTHORITY_KEYS)
def test_no_source_can_declare_an_authority_field(layer, key):
    error = rejected(lambda: resolve(limits=LIMITS, **{layer: {key: True}}))
    assert (error.reason, error.field, error.source) == ('loop_authority_refused', key, layer)


def test_a_declared_bar_leaves_the_loop_recommendation_only_and_unpromoted():
    document = resolve(limits=LIMITS, project=dict(accept_score=0.99, max_rounds=10)).to_dict()
    assert (document['recommendation_only'], document['automatic_promotion'], document['certification']) == (True, False, 'unverified')


def test_the_project_limits_are_referenced_and_their_source_recorded():
    document = resolve(limits=dict(seconds=60, trials=9), limits_source='default').to_dict()
    assert (document['limits'], document['sources']['limits']) == (dict(seconds=60, trials=9), 'default')


@pytest.mark.parametrize('limits', [pytest.param(None, id='missing'), pytest.param(dict(seconds=0, trials=5), id='zero-seconds'),
                                    pytest.param(dict(seconds=5, trials=0), id='zero-trials'), pytest.param(dict(seconds=5, trials=True), id='boolean-trials'),
                                    pytest.param(dict(seconds=5), id='missing-trials'), pytest.param(dict(seconds=5, trials=5, extra=1), id='extra-key')])
def test_limits_outside_the_project_contract_are_rejected(limits):
    assert rejected(lambda: resolve(limits=limits)).reason == 'loop_limits_invalid'


def test_a_per_area_bar_in_any_layer_needs_the_declared_areas_passed_to_the_resolver():
    for layer in LAYERS:
        error = rejected(lambda: resolve(limits=LIMITS, **{layer: dict(accept_score=dict(coding=0.9))}))
        assert (error.reason, error.source) == ('areas_not_declared', layer)
        assert resolve(limits=LIMITS, areas=('coding',), **{layer: dict(accept_score=dict(coding=0.9))}).mode == 'per_area'


def project_value(**loop):
    return {**config.defaults(), 'loop': loop}


def test_project_configuration_accepts_a_valid_loop_object_and_it_changes_the_configuration_digest(tmp_path):
    plain, declared = tmp_path/'plain.json', tmp_path/'declared.json'
    plain.write_text(json.dumps(config.defaults()))
    declared.write_text(json.dumps(project_value(accept_score=0.8, max_rounds=4, on_regression='rollback_continue')))
    (value, digest), (_, plain_digest) = config.load(declared), config.load(plain)
    assert value['loop'] == dict(accept_score=0.8, max_rounds=4, on_regression='rollback_continue') and digest != plain_digest


@pytest.mark.parametrize('loop, reason', [
    pytest.param(dict(bogus=1), 'loop_field_unknown', id='unknown-field'),
    pytest.param(dict(accept_score=1.2), 'loop_accept_score_invalid', id='score-above-one'),
    pytest.param(dict(accept_score=True), 'loop_accept_score_invalid', id='boolean-score'),
    pytest.param(dict(max_rounds=11), 'loop_max_rounds_invalid', id='rounds-above-the-cap'),
    pytest.param(dict(recommendation_only=True), 'loop_authority_refused', id='recommendation-only-restated'),
    pytest.param(dict(automatic_promotion=True), 'loop_authority_refused', id='promotion-enabled'),
    pytest.param(dict(apply=True), 'loop_authority_refused', id='apply-enabled'),
    pytest.param(dict(accept_score=dict(coding=0.9)), 'areas_not_declared', id='per-area-map-without-registry'),
])
def test_project_configuration_rejects_an_invalid_loop_object_with_the_typed_reason(loop, reason):
    error = rejected(lambda: config.validate(project_value(**loop)))
    assert (error.reason, error.source) == (reason, 'project') and isinstance(error, ValueError)


def test_project_configuration_checks_a_per_area_bar_against_its_own_declared_areas():
    declared = {**project_value(accept_score=dict(coding=0.9)), 'areas': AREAS}
    assert config.validate(declared)['loop'] == dict(accept_score=dict(coding=0.9))
    declared['loop'] = dict(accept_score=dict(docs=0.9))
    assert rejected(lambda: config.validate(declared)).reason == 'loop_area_undeclared'


def test_project_configuration_still_rejects_an_unknown_top_level_field_and_a_non_object_loop():
    with pytest.raises(ValueError, match='unsupported project configuration schema'):
        config.validate({**config.defaults(), 'loops': {}})
    assert rejected(lambda: config.validate({**config.defaults(), 'loop': ['accept_score']})).reason == 'loop_declaration_invalid'


def test_project_configuration_without_a_loop_object_is_unchanged():
    assert 'loop' not in config.defaults() and config.validate(config.defaults()) == config.defaults()


@pytest.fixture
def user_file(tmp_path, monkeypatch):
    monkeypatch.setenv('AHL_STATE_HOME', str(tmp_path/'state'))
    target = user_config.path()
    target.parent.mkdir(parents=True)

    def write(value, mode=0o600):
        target.write_text(value if isinstance(value, str) else json.dumps(value))
        target.chmod(mode)
        return target
    return write


def test_the_user_file_supplies_a_loop_declaration_without_changing_the_remembered_runtime(user_file):
    user_file(dict(schema_version=1, runtime='codex', loop=dict(accept_score=0.85)))
    assert user_config.load() == dict(runtime='codex', reason=None)
    assert user_config.load_loop() == dict(loop=dict(accept_score=0.85), reason=None)
    spec = resolve(limits=LIMITS, user=user_config.load_loop()['loop'], project=dict(accept_score=0.5))
    assert (spec.accept_score, spec.acceptance_source) == (0.85, 'user')


def test_a_user_file_without_a_loop_declares_nothing(user_file):
    user_file(dict(schema_version=1, runtime='claude'))
    assert user_config.load_loop() == dict(loop=None, reason=None)
    assert resolve(limits=LIMITS, user=user_config.load_loop()['loop']).mode == 'relative_supported_improvement'


def test_no_user_file_declares_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv('AHL_STATE_HOME', str(tmp_path/'missing'))
    assert user_config.load_loop() == dict(loop=None, reason=None)


@pytest.mark.parametrize('content, mode, reason', [
    pytest.param('{not json', 0o600, 'corrupt', id='not-json'),
    pytest.param(dict(schema_version=1, runtime='codex', loop='accept_score'), 0o600, 'corrupt', id='loop-not-an-object'),
    pytest.param(dict(schema_version=1, runtime='codex', loop={}, token='x'), 0o600, 'corrupt', id='foreign-key-beside-loop'),
    pytest.param(dict(schema_version=1, loop=dict(accept_score=0.9)), 0o600, 'corrupt', id='loop-without-the-required-runtime'),
    pytest.param(dict(schema_version=1, runtime='codex', loop=dict(accept_score=0.9)), 0o644, 'not_owner_only', id='readable-by-others'),
])
def test_an_unsafe_or_foreign_user_file_declares_nothing_and_names_why(user_file, content, mode, reason):
    user_file(content, mode)
    assert user_config.load_loop() == dict(loop=None, reason=reason)
    assert user_config.load() == dict(runtime=None, reason=reason)


def test_an_invalid_user_declaration_is_a_typed_error_and_not_a_silent_default(user_file):
    user_file(dict(schema_version=1, runtime='codex', loop=dict(accept_score=2)))
    error = rejected(lambda: resolve(limits=LIMITS, user=user_config.load_loop()['loop']))
    assert (error.reason, error.source) == ('loop_accept_score_invalid', 'user')


def test_remembering_a_runtime_keeps_the_declared_loop_bar(user_file):
    target = user_file(dict(schema_version=1, runtime='codex', loop=dict(accept_score=0.85, max_rounds=5)))
    user_config.remember('claude')
    assert json.loads(target.read_text()) == dict(schema_version=1, runtime='claude', loop=dict(accept_score=0.85, max_rounds=5))
    assert oct(os.stat(target).st_mode & 0o777) == '0o600'


def test_remembering_a_runtime_adds_no_loop_key_when_none_was_declared(user_file):
    target = user_file(dict(schema_version=1, runtime='codex'))
    user_config.remember('claude')
    assert json.loads(target.read_text()) == dict(schema_version=1, runtime='claude')
