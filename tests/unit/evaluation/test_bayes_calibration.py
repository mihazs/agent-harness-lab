"""Calibration of the Bayesian decision score with the seeded power simulator.

Synthetic outcome models only. Every rate holds under the declared models, never for real tasks. The full
calibration is a documented task run through the shared lock; these tests keep the frozen protocol, the
acceptance rule and a small live guard of the null false-recommendation rate in the fast suite.
"""
import json

import pytest

from agent_harness_lab.evaluation import bayes_analysis, bayes_calibration as calibration, bayes_decision, power

MODEL = dict(schema=power.MODEL_SCHEMA, baseline_success=.5, root_spread=.3)
NULL = dict(MODEL, candidate_effect=0)
HALF = dict(MODEL, candidate_effect=.05)  # half of the frozen ROPE epsilon of the calibration plan
GUARD_SEED = 5150  # distinct from every tuning and held-out seed; asserted below


def cheap(**options):
    """The pre-registered declaration with the minimum posterior draws, so a guard runs in seconds."""
    return dict(posterior_draws=1000, **options)


def test_protocol_registers_models_seeds_simulation_count_and_acceptance_rule():
    protocol = calibration.PROTOCOL
    assert (protocol['simulations'], protocol['bound']) == (300, .05)
    required = [cell for cell in protocol['cells'] if cell['role'] == 'required']
    assert {cell['model'] for cell in required} == {'null', 'half_epsilon'}
    assert {cell['candidate_effect'] for cell in required} == {0, protocol['plan']['effect_margin'] / 2}
    assert {cell['roots'] for cell in required} == {calibration.FLOOR, 40}
    assert {(cell['repeats'], cell['repeat_correlation']) for cell in required} == {(1, 0), (5, 1)}
    assert {cell['role'] for cell in protocol['cells']} == {'required', 'power', 'dependent'}
    assert len({cell['name'] for cell in protocol['cells']}) == len(protocol['cells'])
    assert protocol['plan']['precision_half_width'] == 1  # the precision floor can only withhold, so it never helps a null
    seeds = {split: {calibration.cell_seed(split, i) for i in range(len(protocol['cells']))} for split in protocol['seeds']}
    guard = set(range(GUARD_SEED, GUARD_SEED + len(protocol['cells'])))
    assert set(seeds) == {'tuning', 'held_out'} and not seeds['tuning'] & seeds['held_out']
    assert not guard & (seeds['tuning'] | seeds['held_out'])


def test_protocol_digest_changes_with_any_registered_value():
    original = calibration.protocol_sha256()
    for key, value in (('bound', .06), ('simulations', 301), ('seeds', dict(tuning=1, held_out=2))):
        assert calibration.protocol_sha256(dict(calibration.PROTOCOL, **{key: value})) != original


def test_simulator_judges_the_records_the_power_simulator_draws():
    model = dict(schema=power.MODEL_SCHEMA, baseline_success=.1, candidate_effect=.8)
    report = calibration.simulate(model, roots=100, repeats=1, simulations=12, seed=31, bayes=cheap())
    drawn = power.simulate(calibration.cell_spec(100, 1, cheap()), model, simulations=12, seed=31)
    assert report['hoeffding_supported']['count'] == drawn['dispositions']['supported']['count'] > 0
    assert report['verdicts']['better']['count'] == 12


def test_report_is_deterministic_and_accounts_for_every_simulation():
    first = calibration.simulate(NULL, roots=8, repeats=1, simulations=20, seed=7, bayes=cheap())
    assert first == calibration.simulate(NULL, roots=8, repeats=1, simulations=20, seed=7, bayes=cheap())
    assert sum(v['count'] for v in first['verdicts'].values()) == len(first['sequence']) == 20
    assert sum(first['reasons'].values()) == 20
    assert first['simulation_seed'] == 7 and first['automatic_promotion'] is False


def test_acceptance_uses_the_wilson_upper_bound_of_the_better_rate():
    def report(count):
        return dict(verdicts=dict(better=dict(count=count, wilson_95=power.wilson(count, 300))))

    assert calibration.accepted(report(7)) and not calibration.accepted(report(8))


def stub_cells(monkeypatch, upper_bounds=()):
    """Replace the simulation of every cell by a canned report; return the (split, index) pairs that ran."""
    ran = []

    def run_cell(split, index, simulations=None):
        ran.append((split, index))
        return dict(verdicts=dict(better=dict(wilson_95=[0, .06 if index in upper_bounds else .01])))

    monkeypatch.setattr(calibration, 'run_cell', run_cell)
    return ran


def test_tuning_runs_the_required_cells_and_held_out_runs_every_cell(monkeypatch):
    ran = stub_cells(monkeypatch)
    cells = calibration.PROTOCOL['cells']
    tuning, held_out = calibration.calibrate('tuning'), calibration.calibrate('held_out')
    assert [i for s, i in ran if s == 'tuning'] == [i for i, c in enumerate(cells) if c['role'] == 'required']
    assert [i for s, i in ran if s == 'held_out'] == list(range(len(cells)))
    assert tuning['accepted'] and held_out['accepted']
    assert {c['accepted'] for c in held_out['cells'] if c['role'] != 'required'} == {None}
    assert tuning['protocol_sha256'] == held_out['protocol_sha256'] == calibration.protocol_sha256()


@pytest.mark.parametrize('role,fails', (('required', True), ('power', False), ('dependent', False)))
def test_only_a_failing_required_cell_fails_the_split(monkeypatch, role, fails):
    index = next(i for i, c in enumerate(calibration.PROTOCOL['cells']) if c['role'] == role)
    stub_cells(monkeypatch, upper_bounds=(index,))
    assert calibration.calibrate('held_out')['accepted'] is not fails


def test_unknown_split_is_refused():
    with pytest.raises(ValueError, match='split'):
        calibration.calibrate('validation')


def test_command_prints_the_report_of_one_split(monkeypatch, capsys):
    monkeypatch.setattr(calibration, 'calibrate', lambda split: dict(split=split))
    assert calibration.main(['--split', 'tuning']) == 0
    assert json.loads(capsys.readouterr().out) == dict(split='tuning')


def test_fully_dependent_roots_exceed_the_bound_and_are_reported_not_required():
    report = calibration.simulate(NULL, roots=8, repeats=1, simulations=60, seed=3, bayes=cheap(), cluster=8)
    assert report['verdicts']['better']['wilson_95'][0] > calibration.PROTOCOL['bound']
    assert not calibration.accepted(report)


def test_mutated_parameters_fail_the_null_acceptance_rule():
    mutated = cheap(p_cut=.51, cross_check_alpha=.49, regression_margin=.99)
    report = calibration.simulate(NULL, roots=8, repeats=1, simulations=40, seed=GUARD_SEED, bayes=mutated)
    assert report['verdicts']['better']['wilson_95'][0] > calibration.PROTOCOL['bound']
    assert not calibration.accepted(report)


def test_weakened_decision_rule_fails_the_null_acceptance_rule(monkeypatch):
    # Deliberate mutation of the code: the posterior cut becomes a coin flip and the cross-check always agrees.
    monkeypatch.setattr(bayes_decision, '_against_cut', lambda p, cut, se: 'met' if p > .5 else 'below')
    monkeypatch.setattr(bayes_analysis, '_cross_check',
                        lambda spec, units: dict(method='exact', p_better=0., p_worse=0., basis='point'))
    report = calibration.simulate(NULL, roots=8, repeats=1, simulations=40, seed=GUARD_SEED, bayes=cheap())
    assert report['verdicts']['better']['wilson_95'][0] > calibration.PROTOCOL['bound']
    assert not calibration.accepted(report)


GUARDS = [pytest.param(GUARD_SEED + i, model, roots, repeats, correlation, id=f'{name}-{roots}-roots-r{repeats}')
          for i, (name, model, roots, repeats, correlation) in enumerate(
              (name, model, roots, repeats, correlation) for name, model in (('null', NULL), ('half_epsilon', HALF))
              for roots in (calibration.FLOOR, 40) for repeats, correlation in ((1, 0), (5, 1)))]


@pytest.mark.parametrize('seed,model,roots,repeats,correlation', GUARDS)
def test_false_better_rate_stays_within_the_bound(seed, model, roots, repeats, correlation):
    # Same models and acceptance rule as the full calibration, on guard seeds with the minimum posterior draws:
    # a tripwire for a change to the decision rule, not a substitute for the recorded calibration.
    report = calibration.simulate(dict(model, repeat_correlation=correlation), roots=roots, repeats=repeats,
                                  simulations=calibration.PROTOCOL['simulations'], seed=seed, bayes=cheap())
    assert calibration.accepted(report), report['verdicts']['better']
