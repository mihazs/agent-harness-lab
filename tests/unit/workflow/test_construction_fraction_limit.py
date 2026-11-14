"""The construction share of the shared budget may be set in ahl.json limits; absent, nothing about the limits changes (#739)."""
import pytest

from agent_harness_lab import config


def limited(**limits):
    return {**config.defaults(), 'limits': {'seconds': 600, 'trials': 20, **limits}}


def test_limits_without_a_construction_fraction_stay_valid_and_unchanged():
    assert config.validate(config.defaults())['limits'] == dict(seconds=2700, trials=20)


@pytest.mark.parametrize('value', [pytest.param(0.5, id='float'), pytest.param(1e-6, id='tiny'), pytest.param(0.999, id='near-one')])
def test_construction_fraction_strictly_between_zero_and_one_is_accepted(value):
    assert config.validate(limited(construction_fraction=value))['limits']['construction_fraction'] == value


@pytest.mark.parametrize('value', [
    pytest.param(0, id='zero'), pytest.param(1, id='one'), pytest.param(-0.25, id='negative'), pytest.param(1.5, id='above-one'),
    pytest.param(float('nan'), id='nan'), pytest.param(float('inf'), id='infinity'),
    pytest.param(True, id='boolean'), pytest.param('0.5', id='string'), pytest.param(None, id='null'), pytest.param([0.5], id='list'),
])
def test_construction_fraction_outside_the_open_unit_interval_or_not_a_number_is_rejected(value):
    with pytest.raises(ValueError, match='construction fraction'):
        config.validate(limited(construction_fraction=value))


@pytest.mark.parametrize('limits', [
    pytest.param({'seconds': 600}, id='trials-missing'), pytest.param({'trials': 20}, id='seconds-missing'),
    pytest.param({'seconds': 600, 'trials': 20, 'weight': 0.5}, id='unknown-key'),
    pytest.param({'seconds': 600, 'trials': 20, 'construction_fraction': 0.5, 'weight': 0.5}, id='unknown-key-beside-fraction'),
])
def test_other_limit_shapes_are_still_rejected_beside_the_construction_fraction(limits):
    with pytest.raises(ValueError):
        config.validate({**config.defaults(), 'limits': limits})


def test_configured_fraction_fills_an_invocation_that_gave_none():
    options = config.execution_options('/p', limited(construction_fraction=0.4), dict(timeout=None, construction_fraction=None), set())
    assert options['construction_fraction'] == 0.4 and options['timeout'] == 600


def test_explicit_invocation_fraction_wins_over_the_configured_one():
    options = config.execution_options('/p', limited(construction_fraction=0.4), dict(timeout=None, construction_fraction=0.7), {'--construction-fraction'})
    assert options['construction_fraction'] == 0.7


@pytest.mark.parametrize('settings', [pytest.param(None, id='no-configuration'), pytest.param(limited(), id='limits-without-fraction')])
def test_without_a_configured_fraction_the_invocation_keeps_none(settings):
    assert config.execution_options('/p', settings, dict(timeout=None, construction_fraction=None), set())['construction_fraction'] is None


def test_the_fraction_is_an_invocation_option_the_shared_resolver_forwards():
    assert 'construction_fraction' in config.INVOCATION_OPTIONS
