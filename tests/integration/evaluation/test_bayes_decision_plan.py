"""A plan that declares bayes_decision stays a frozen plan: the analysis, schedule and CLI treat it as one."""
import contextlib
from dataclasses import asdict, replace
import io
import json

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.plan import schedule
from agent_harness_lab.evaluation.statistics import TrialRecord, analyze
from agent_harness_lab.promotion.policy import decide
from tests.support.bayes_spec_fixture import MINIMAL, RESOLVED, mutated, spec

UNFIT = dict(status='incomplete', reason='unexpected_trial_set')


def records(plan):
    return [TrialRecord(trial['trial_id'], True, 'graded', 'executable_grader', plan.baseline_hash,
                        plan.candidate_hash, plan.grader_hash, plan.runtime_hash) for trial in schedule(plan)]


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as exit_info:
            code = exit_info.code
    return code, out.getvalue(), err.getvalue()


def test_the_existing_analysis_and_promotion_ignore_the_declaration():
    plain, declared = spec(None), spec()
    before, after = analyze(plain, records(plain)), analyze(declared, records(declared))
    assert before == after and before['status'] == 'inconclusive' and 'bayesian_decision' not in after
    assert asdict(decide(plain, before)) == asdict(decide(declared, after))
    assert asdict(decide(declared, after))['automatic'] is False


@pytest.mark.parametrize('path, value', [
    (['p_cut'], .9), (['rope_epsilon'], .2), (['regression_margin'], .1), (['posterior_draws'], 30000),
    (['cross_check_alpha'], .1), (['allow_bound_resolved_verdict'], True), (['prior', 'concentration'], 2),
], ids=lambda item: str(item))
def test_records_of_one_plan_do_not_fit_a_plan_with_a_changed_parameter(path, value):
    frozen = spec(RESOLVED)
    rows = records(frozen)
    assert analyze(frozen, rows)['status'] == 'inconclusive'
    assert analyze(spec(mutated(path, value)), rows) == UNFIT


def test_records_do_not_fit_across_a_plan_with_and_without_the_declaration():
    plain, declared = spec(None), spec()
    assert analyze(declared, records(plain)) == UNFIT and analyze(plain, records(declared)) == UNFIT
    assert analyze(replace(declared, bayes_decision=None), records(declared)) == UNFIT


def test_editing_a_frozen_declaration_in_place_breaks_the_plan_digest_check():
    frozen = spec()
    rows = records(frozen)
    frozen.bayes_decision['p_cut'] = .99
    assert analyze(frozen, rows) == UNFIT


def test_experiment_command_accepts_a_minimal_declaration_and_reports_the_resolved_plan_digest(tmp_path):
    path = tmp_path / 'plan.json'
    value = spec(None).to_dict()
    value['bayes_decision'] = dict(MINIMAL)
    path.write_text(json.dumps(value))
    code, out, err = run('experiment', '--spec', str(path), '--preflight')
    assert code in (0, 3) and err == '' and json.loads(out)['plan_sha256'] == spec().digest


@pytest.mark.parametrize('field, value', [('p_cut', 1.0), ('rope_epsilon', 0), ('posterior_draws', 999),
                                          ('unknown', 1)], ids=str)
def test_experiment_command_rejects_an_invalid_declaration_naming_the_field(tmp_path, field, value):
    path = tmp_path / 'plan.json'
    declared = spec().to_dict()
    declared['bayes_decision'][field] = value
    path.write_text(json.dumps(declared))
    code, out, err = run('experiment', '--spec', str(path), '--preflight')
    assert code == 2 and out == '' and f'bayes_decision.{field}' in err


def test_experiment_command_rejects_a_nonfinite_number_in_the_declaration_before_parsing_the_plan(tmp_path):
    path = tmp_path / 'plan.json'
    declared = spec().to_dict()
    declared['bayes_decision']['rope_epsilon'] = float('nan')
    path.write_text(json.dumps(declared))
    code, out, err = run('experiment', '--spec', str(path), '--preflight')
    assert code == 2 and out == '' and 'nonfinite' in err
