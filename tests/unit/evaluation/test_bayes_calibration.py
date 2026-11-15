"""Calibration of the Bayesian decision score with the seeded power simulator.

Synthetic outcome models only. Every rate holds under the declared models, never for real tasks. The full
calibration is a documented task run through the shared lock; these tests keep the frozen protocol, the
acceptance rule and a small live guard of the null false-recommendation rate in the fast suite.
"""
from collections import Counter
import json
from pathlib import Path

import pytest

from agent_harness_lab.evaluation import bayes_analysis, bayes_calibration as calibration, bayes_decision, power
from tests.support.bayes_scenarios import scenario

MODEL = dict(schema=power.MODEL_SCHEMA, baseline_success=.5, root_spread=.3)
NULL = dict(MODEL, candidate_effect=0)
HALF = dict(MODEL, candidate_effect=.05)  # half of the frozen ROPE epsilon of the calibration plan
GUARD_SEED = 5150  # distinct from every tuning and held-out seed; asserted below
RECORD = Path(__file__).parents[2] / 'fixtures' / 'bayes_calibration' / 'results.json'
# Pinned a second time on purpose: changing the protocol after results are recorded means editing the protocol, this
# literal and the record together, which is a visible decision to rerun the calibration.
FROZEN_PROTOCOL_SHA256 = '00b16f5deccbd21d638c228466a40b08f900643eef69be8a6e4b2048fae700c0'
REPLAYED = 10  # simulations replayed per cell; the stream is sequential, so they equal the head of the recorded sequence
SPLITS = dict(tuning_initial='tuning', tuning_adjusted='tuning', held_out='held_out')
REQUIRED = [pytest.param(i, id=cell['name']) for i, cell in enumerate(calibration.PROTOCOL['cells']) if cell['role'] == 'required']


GUARD_DRAWS = 4000  # not 1000: at p_cut .99 the four-standard-error Monte Carlo margin leaves no room for a verdict below ~1600


def guard(**options):
    """The pre-registered declaration with fewer posterior draws than the 20,000 default, so a guard runs in seconds."""
    return dict(posterior_draws=GUARD_DRAWS, **options)


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
    guard_seeds = set(range(GUARD_SEED, GUARD_SEED + len(protocol['cells'])))
    assert set(seeds) == {'tuning', 'held_out'} and not seeds['tuning'] & seeds['held_out']
    assert not guard_seeds & (seeds['tuning'] | seeds['held_out'])


def test_protocol_digest_changes_with_any_registered_value():
    original = calibration.protocol_sha256()
    for key, value in (('bound', .06), ('simulations', 301), ('seeds', dict(tuning=1, held_out=2))):
        assert calibration.protocol_sha256(dict(calibration.PROTOCOL, **{key: value})) != original


def test_simulator_judges_the_records_the_power_simulator_draws():
    model = dict(schema=power.MODEL_SCHEMA, baseline_success=.1, candidate_effect=.8)
    report = calibration.simulate(model, roots=100, repeats=1, simulations=8, seed=31, bayes=guard())
    drawn = power.simulate(calibration.cell_spec(100, 1, guard()), model, simulations=8, seed=31)
    assert report['hoeffding_supported']['count'] == drawn['dispositions']['supported']['count'] > 0
    assert report['verdicts']['better']['count'] == 8  # the guard declaration can reach a verdict


def test_report_is_deterministic_and_accounts_for_every_simulation():
    first = calibration.simulate(NULL, roots=8, repeats=1, simulations=20, seed=7, bayes=guard())
    assert first == calibration.simulate(NULL, roots=8, repeats=1, simulations=20, seed=7, bayes=guard())
    assert sum(v['count'] for v in first['verdicts'].values()) == len(first['sequence']) == 20
    assert sum(first['reasons'].values()) == 20
    assert first['simulation_seed'] == 7 and first['automatic_promotion'] is False


def test_acceptance_uses_the_wilson_upper_bound_of_the_better_rate():
    def report(count):
        return dict(verdicts=dict(better=dict(count=count, wilson_95=power.wilson(count, 300))))

    assert calibration.accepted(report(7)) and not calibration.accepted(report(8))


def test_default_p_cut_is_the_value_calibrated_on_the_tuning_seeds():
    # .95 failed the acceptance rule on the tuning seeds; the one pre-registered raise is .99 (docs/bayesian-decision-score.md).
    declared = calibration.cell_spec(40, 1).bayes_decision
    assert (declared['p_cut'], declared['cross_check_alpha']) == (.99, .01)


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
    report = calibration.simulate(NULL, roots=8, repeats=1, simulations=60, seed=3, bayes=guard(), cluster=8)
    assert report['verdicts']['better']['wilson_95'][0] > calibration.PROTOCOL['bound']
    assert not calibration.accepted(report)


def test_mutated_parameters_fail_the_null_acceptance_rule():
    mutated = guard(p_cut=.51, cross_check_alpha=.49, regression_margin=.99)
    report = calibration.simulate(NULL, roots=8, repeats=1, simulations=40, seed=GUARD_SEED, bayes=mutated)
    assert report['verdicts']['better']['wilson_95'][0] > calibration.PROTOCOL['bound']
    assert not calibration.accepted(report)


def test_weakened_decision_rule_fails_the_null_acceptance_rule(monkeypatch):
    # Deliberate mutation of the code: the posterior cut becomes a coin flip and the cross-check always agrees.
    monkeypatch.setattr(bayes_decision, '_against_cut', lambda p, cut, se: 'met' if p > .5 else 'below')
    monkeypatch.setattr(bayes_analysis, '_cross_check',
                        lambda spec, units: dict(method='exact', p_better=0., p_worse=0., basis='point'))
    report = calibration.simulate(NULL, roots=8, repeats=1, simulations=40, seed=GUARD_SEED, bayes=guard())
    assert report['verdicts']['better']['wilson_95'][0] > calibration.PROTOCOL['bound']
    assert not calibration.accepted(report)


GUARDS = [pytest.param(GUARD_SEED + i, name, model, roots, repeats, correlation, id=f'{name}-{roots}-roots-r{repeats}')
          for i, (name, model, roots, repeats, correlation) in enumerate((
              ('null', NULL, calibration.FLOOR, 1, 0), ('null', NULL, calibration.FLOOR, 5, 1),
              ('null', NULL, 40, 1, 0), ('null', NULL, 40, 5, 1),
              ('half_epsilon', HALF, 40, 5, 1)))]  # the half-epsilon cell the tuning seeds found binding


@pytest.mark.parametrize('seed,name,model,roots,repeats,correlation', GUARDS)
def test_false_better_rate_stays_within_the_bound(seed, name, model, roots, repeats, correlation):
    # The protocol's models and acceptance rule on guard seeds, with fewer posterior draws: a tripwire for a change to the
    # decision rule, not a substitute for the recorded calibration at the registered draws.
    report = calibration.simulate(dict(model, repeat_correlation=correlation), roots=roots, repeats=repeats,
                                  simulations=calibration.PROTOCOL['simulations'], seed=seed, bayes=guard())
    assert calibration.accepted(report), report['verdicts']['better']


@pytest.fixture(scope='module')
def record():
    return json.loads(RECORD.read_text())


def test_protocol_is_frozen_once_results_are_recorded(record):
    assert calibration.protocol_sha256() == FROZEN_PROTOCOL_SHA256 == record['protocol_sha256']
    assert {run['protocol_sha256'] for run in record['runs'].values()} == {FROZEN_PROTOCOL_SHA256}


def test_recorded_runs_cover_the_protocol_cells_on_their_registered_seeds(record):
    assert set(record['runs']) == set(SPLITS)
    for name, run in record['runs'].items():
        split = SPLITS[name]
        expected = [i for i, cell in enumerate(calibration.PROTOCOL['cells'])
                    if cell['role'] in calibration.PROTOCOL['splits'][split]]
        assert run['split'] == split and [c['index'] for c in run['cells']] == expected
        for cell in run['cells']:
            registered = calibration.PROTOCOL['cells'][cell['index']]
            assert (cell['name'], cell['role']) == (registered['name'], registered['role'])
            assert cell['simulation_seed'] == calibration.cell_seed(split, cell['index'])
            assert cell['simulations'] == calibration.PROTOCOL['simulations'] == len(cell['sequence'])


def test_recorded_rates_and_acceptance_follow_from_the_recorded_sequences(record):
    letters = {letter: verdict for verdict, letter in calibration.VERDICTS.items()}
    for run in record['runs'].values():
        for cell in run['cells']:
            counts = Counter(letters[letter] for letter in cell['sequence'])
            for verdict, row in cell['verdicts'].items():
                assert row == dict(count=counts[verdict], rate=counts[verdict] / 300, wilson_95=power.wilson(counts[verdict], 300))
            assert cell['accepted'] == (calibration.accepted(cell) if cell['role'] == 'required' else None)
        required = [cell['accepted'] for cell in run['cells'] if cell['role'] == 'required']
        assert run['accepted'] == all(required)


def test_recorded_history_is_one_failed_tuning_run_one_raise_and_a_passing_confirmation(record):
    runs = record['runs']
    initial, adjusted, held_out = (runs[name] for name in ('tuning_initial', 'tuning_adjusted', 'held_out'))
    assert not initial['accepted'] and adjusted['accepted'] and held_out['accepted']
    failed = [c['name'] for c in initial['cells'] if c['accepted'] is False]
    assert failed == ['half_epsilon/roots40/repeats5-corr1'] and initial['cells'][-1]['verdicts']['better']['count'] == 9
    for run in (adjusted, held_out):
        for cell in run['cells']:
            assert cell['parameters'] == calibration.parameters(calibration.cell_spec(cell['roots'], cell['repeats']))
    assert {c['parameters']['p_cut'] for c in initial['cells']} == {.95}
    assert {c['parameters']['p_cut'] for c in held_out['cells']} == {.99}
    assert {c['parameters']['root_floor'] for run in runs.values() for c in run['cells']} == {calibration.FLOOR}
    assert all(c['verdicts']['better']['wilson_95'][1] <= calibration.PROTOCOL['bound']
               for c in held_out['cells'] if c['role'] == 'required')


@pytest.mark.parametrize('index', REQUIRED)
def test_recorded_held_out_head_replays_with_the_current_code(record, index):
    recorded = record['runs']['held_out']['cells'][index]['sequence']
    assert calibration.run_cell('held_out', index, simulations=REPLAYED)['sequence'] == recorded[:REPLAYED]


def test_recorded_failure_at_the_first_default_replays_with_the_original_p_cut(record):
    index = 7  # half_epsilon/roots40/repeats5-corr1, the cell whose tuning rate exceeded the bound at .95
    recorded = record['runs']['tuning_initial']['cells'][index]['sequence']
    again = calibration.run_cell('tuning', index, simulations=REPLAYED, bayes=dict(p_cut=.95))
    assert again['sequence'] == recorded[:REPLAYED] and again['parameters']['p_cut'] == .95


def test_too_few_posterior_draws_cannot_reach_a_verdict_at_the_default_p_cut():
    # Four binomial standard errors around .99 leave no room below 1 until about 1,600 draws; the guards use 4,000.
    def verdict(draws):
        case = scenario('clear_win', draws=draws)
        return bayes_decision.decide(case['spec'], case['roots'])['verdict']

    assert (verdict(1000), verdict(GUARD_DRAWS)) == ('insufficient_evidence', 'better')
