"""Calibrate the paired Bayesian decision score with the seeded power simulator (docs/bayesian-decision-score.md, issue e).

The synthetic records are the ones ``power.simulate`` draws for the Hoeffding analysis (``power._draw``, same generator and seed
handling). Each record set goes to the unchanged ``statistics.analyze`` and to ``bayes_analysis.attach``, and the verdicts are
counted with a Wilson interval. ``PROTOCOL`` fixes the models, seeds, simulation count and acceptance rule before any run;
``protocol_sha256`` is recorded with the results so a later change to the protocol is caught. Rates hold only under the declared
synthetic models and the declared plan: they are not observed error control for real tasks, and no model, network or clock is used.
"""
import argparse
from dataclasses import replace
import hashlib
import json
import random
import sys

from . import bayes_analysis, bayes_spec, power
from .bayes_decision import ROOT_FLOOR
from .plan import ExperimentSpec, schedule
from .statistics import analyze

PROFILE = 'bayesian-calibration-v1'
FLOOR = 8  # the root floor under test; a bank this small is the smallest that can reach any verdict
VERDICTS = dict(better='b', equivalent='e', worse='w', insufficient_evidence='i', blocked='x')
LAYOUTS = ((1, 0), (5, 1))  # (repeats, repeat_correlation): the pairs test_experiment_power holds the Hoeffding path to


def _cells():
    rows = (('required', 'null', 0, 1, (FLOOR, 40), LAYOUTS), ('required', 'half_epsilon', .05, 1, (FLOOR, 40), LAYOUTS),
            ('power', 'beneficial', .3, 1, (40,), LAYOUTS), ('dependent', 'clusters_of_4', 0, 4, (40,), LAYOUTS[:1]),
            ('dependent', 'one_cluster', 0, 40, (40,), LAYOUTS[:1]))
    return [dict(name=f'{model}/roots{roots}/repeats{repeats}-corr{correlation}', role=role, model=model,
                 candidate_effect=effect, cluster=cluster, roots=roots, repeats=repeats, repeat_correlation=correlation)
            for role, model, effect, cluster, banks, layouts in rows for roots in banks for repeats, correlation in layouts]


PROTOCOL = dict(
    profile='bayesian-calibration-protocol-v1',
    simulations=300, bound=.05, interval='Wilson_score_95',
    acceptance='the Wilson 95% upper bound of the better rate is at most the bound in every required cell',
    plan=dict(family='family', min_roots=3, effect_margin=.1, family_margin=.05, alpha=.05, precision_half_width=1),
    outcome=dict(baseline_success=.5, root_spread=.3),
    seeds=dict(tuning=3100, held_out=4100),  # a cell's seed is its split's seed plus its index in cells
    splits=dict(tuning=['required'], held_out=['required', 'power', 'dependent']),
    cells=_cells(),
    adjustment=dict(trigger='a required cell fails on the tuning seeds', times=1, parameters=dict(p_cut=.99, root_floor=12),
                    then='confirm on the held-out seeds; if a required held-out cell fails, stop and report'))


def protocol_sha256(protocol=PROTOCOL):
    return hashlib.sha256(json.dumps(protocol, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def cell_seed(split, index):
    return PROTOCOL['seeds'][split] + index


def cell_spec(roots, repeats, bayes=None):
    """A frozen plan of ``roots`` single-task roots in one family; ``bayes`` overrides pre-registered parameters."""
    plan = PROTOCOL['plan']
    tasks = tuple((f't{i}', f'r{i}', plan['family']) for i in range(roots))
    return ExperimentSpec('synthetic-calibration', tasks, repeats, 13, 2 * roots * repeats, plan['min_roots'],
                          plan['effect_margin'], plan['family_margin'], plan['alpha'], ((plan['family'], 1.0),),
                          'b' * 64, 'c' * 64, 'g' * 64, 'i' * 64, precision_half_width=plan['precision_half_width'],
                          bayes_decision={'schema_version': 1, 'profile': bayes_spec.PROFILE, **(bayes or {})})


def _rates(counts, total):
    return {key: dict(count=n, rate=n / total, wilson_95=power.wilson(n, total)) for key, n in counts.items()}


def _clustered(spec, trials, records, cluster):
    """Every root of a cluster of consecutive roots takes the outcomes of the cluster's first root: full dependence."""
    roots = [root for _, root, _ in spec.tasks]
    leader = {root: roots[i - i % cluster] for i, root in enumerate(roots)}
    outcome = {(t['root'], t['version'], t['repetition']): r.outcome for t, r in zip(trials, records)}
    return [replace(r, outcome=outcome[leader[t['root']], t['version'], t['repetition']]) for t, r in zip(trials, records)]


def simulate(outcome_model, *, roots, repeats, simulations, seed, bayes=None, cluster=1):
    """Verdict rates of the Bayesian decision under one declared outcome model, beside the Hoeffding ``supported`` rate."""
    spec = cell_spec(roots, repeats, bayes)
    declared = power.model(outcome_model, [PROTOCOL['plan']['family']])
    trials, rng = schedule(spec), random.Random(seed)
    counts, reasons, supported, sequence = dict.fromkeys(VERDICTS, 0), {}, 0, []
    for _ in range(simulations):
        records = power._draw(spec, trials, declared, rng)
        records = records if cluster == 1 else _clustered(spec, trials, records, cluster)
        analysis = analyze(spec, records)
        block = bayes_analysis.attach(spec, records, analysis)['bayesian_decision']
        counts[block['verdict']] += 1
        reasons[block['reason']] = reasons.get(block['reason'], 0) + 1
        supported += analysis['status'] == 'supported'
        sequence.append(VERDICTS[block['verdict']])
    return dict(profile=PROFILE, plan_sha256=spec.digest, model=declared, roots=roots, repeats=repeats, cluster=cluster,
                parameters=parameters(spec), simulation_seed=seed, simulations=simulations, verdicts=_rates(counts, simulations),
                reasons=dict(sorted(reasons.items())), hoeffding_supported=_rates(dict(supported=supported), simulations)['supported'],
                sequence=''.join(sequence), automatic_promotion=False, publication_allowed=False)


def parameters(spec):
    """The pre-registered parameters a cell ran under, as resolved and frozen in its plan."""
    declared = spec.bayes_decision
    return dict(p_cut=declared['p_cut'], posterior_draws=declared['posterior_draws'], rope_epsilon=declared['rope_epsilon'],
                regression_margin=declared['regression_margin'], cross_check_alpha=declared['cross_check_alpha'],
                root_floor=max(spec.min_roots, ROOT_FLOOR))


def accepted(report):
    """The pre-registered rule: the Wilson upper bound of the false-recommendation (better) rate is within the bound."""
    return report['verdicts']['better']['wilson_95'][1] <= PROTOCOL['bound']


def run_cell(split, index, simulations=None, bayes=None):
    """One protocol cell on one split's seed; ``simulations`` shortens it to a prefix of the same simulation stream."""
    cell = PROTOCOL['cells'][index]
    declared = dict(schema=power.MODEL_SCHEMA, **PROTOCOL['outcome'], candidate_effect=cell['candidate_effect'],
                    repeat_correlation=cell['repeat_correlation'])
    return simulate(declared, roots=cell['roots'], repeats=cell['repeats'], simulations=simulations or PROTOCOL['simulations'],
                    seed=cell_seed(split, index), cluster=cell['cluster'], bayes=bayes)


def calibrate(split):
    """Run every cell of one split and report each cell's verdict rates and, for required cells, the acceptance result."""
    if split not in PROTOCOL['seeds']:
        raise ValueError(f'split must be one of {sorted(PROTOCOL["seeds"])}')
    cells = []
    for index, cell in enumerate(PROTOCOL['cells']):
        if cell['role'] in PROTOCOL['splits'][split]:
            report = run_cell(split, index)
            cells.append(dict(report, name=cell['name'], role=cell['role'], index=index,
                              accepted=accepted(report) if cell['role'] == 'required' else None))
    return dict(profile=PROFILE, protocol_sha256=protocol_sha256(), split=split, cells=cells,
                accepted=all(c['accepted'] for c in cells if c['role'] == 'required'),
                limitations=['Rates hold only under the declared synthetic outcome models and plan, not for real tasks.',
                             'Simulated roots are independent except in the dependent cells, which are reported, not required.',
                             'The precision floor is set to 1 so it never withholds; a tighter floor can only lower a rate.',
                             'The power cells carry no pass or fail; the Wilson lower bound of the better rate is their power.'])


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m agent_harness_lab.evaluation.bayes_calibration', description=__doc__.splitlines()[0])
    parser.add_argument('--split', choices=sorted(PROTOCOL['seeds']), required=True,
                        help='Seed split to simulate: tuning to choose p_cut, held_out to confirm it once; runs offline with no model or network.')
    print(json.dumps(calibrate(parser.parse_args(argv).split), indent=1, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
