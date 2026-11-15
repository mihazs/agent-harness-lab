"""The loop flags of `ahl improve` and `ahl skills invoke`: a valid case and a typed invalid case that exits 2 for each flag (#814).

Every project here is an empty temporary directory, so an invalid value must end before anything runs, with the loop's own reason.
"""
import contextlib
import io
import json
from types import SimpleNamespace

import pytest

from agent_harness_lab import config, typed_exit, user_config
from agent_harness_lab.cli import main
from agent_harness_lab.loop import entry
from agent_harness_lab.loop.errors import REASONS, LoopError
from agent_harness_lab.monitoring.state import atomic_json

ENTRIES = (pytest.param(('improve',), id='improve'), pytest.param(('skills', 'invoke'), id='skills-invoke'))
FLAGS = ('--accept-score', '--max-rounds', '--plateau-rounds', '--on-regression')


def invoke(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try: code = main(argv)
        except SystemExit as stop: code = stop.code
    try: receipt = json.loads(out.getvalue())
    except ValueError: receipt = None
    return code, receipt, err.getvalue()


def arguments(**flags):
    """The namespace cli.main hands the loop entry, with the given flag texts and no project settings."""
    base = dict(accept_score=None, max_rounds=None, plateau_rounds=None, on_regression=None, project_settings=None, timeout=600, trial_limit=40,
                repair_contracts=False, _configuration_explicit=frozenset())
    return SimpleNamespace(**{**base, **flags})


VALID = [
    pytest.param(dict(accept_score='0.8'), dict(accept_score=0.8), id='accept-score'),
    pytest.param(dict(accept_score='1'), dict(accept_score=1.0), id='accept-score-one'),
    pytest.param(dict(max_rounds='4'), dict(max_rounds=4), id='max-rounds'),
    pytest.param(dict(max_rounds='10'), dict(max_rounds=10), id='max-rounds-cap'),
    pytest.param(dict(plateau_rounds='1'), dict(plateau_rounds=1), id='plateau-rounds'),
    pytest.param(dict(on_regression='rollback_continue'), dict(on_regression='rollback_continue'), id='on-regression-rollback'),
    pytest.param(dict(on_regression='stop'), dict(on_regression='stop'), id='on-regression-stop'),
    pytest.param(dict(accept_score='0.5', max_rounds='5', plateau_rounds='3', on_regression='rollback_continue'),
                 dict(accept_score=0.5, max_rounds=5, plateau_rounds=3, on_regression='rollback_continue'), id='all-four'),
]


@pytest.mark.parametrize('flags, declared', VALID)
def test_a_valid_flag_opts_into_the_loop_and_resolves_to_a_cli_sourced_spec(flags, declared):
    args = arguments(**flags)
    found = entry.prepare(args)
    spec = entry.spec_for(args, found)
    assert args._loop is found and {name: spec.declared()[name] for name in declared} == declared
    assert {name: dict(spec.sources)[name] for name in declared} == {name: 'cli' for name in declared}
    assert (spec.recommendation_only, spec.automatic_promotion, spec.certification) == (True, False, 'unverified')


def test_without_a_flag_or_a_declaration_the_run_is_not_a_loop_run():
    args = arguments()
    assert entry.prepare(args) is None and args._loop is None


def test_a_loop_object_in_the_project_file_opts_in_and_names_its_source():
    args = arguments(project_settings=dict(loop=dict(max_rounds=4, accept_score=0.7)))
    spec = entry.spec_for(args, entry.prepare(args))
    assert (spec.max_rounds, spec.accept_score, spec.acceptance_source, dict(spec.sources)['max_rounds']) == (4, 0.7, 'project', 'project')


def test_a_loop_object_in_the_user_file_opts_in_and_a_flag_still_wins(tmp_path):
    user_config.remember('codex')
    value = json.loads(user_config.path().read_text())
    atomic_json(user_config.path(), dict(value, loop=dict(max_rounds=6, plateau_rounds=3)))
    args = arguments(max_rounds='8')
    spec = entry.spec_for(args, entry.prepare(args))
    assert (spec.max_rounds, spec.plateau_rounds) == (8, 3) and dict(spec.sources)['max_rounds'] == 'cli' and dict(spec.sources)['plateau_rounds'] == 'user'


@pytest.mark.parametrize('flags, ceiling', [pytest.param(dict(max_rounds='4'), 26, id='four-rounds'), pytest.param(dict(accept_score='0.5'), 27, id='the-default-three-rounds'),
                                            pytest.param(dict(max_rounds='1'), 29, id='one-round')])
def test_the_trial_ceiling_the_spec_references_leaves_one_preparation_role_per_round(flags, ceiling):
    args = arguments(trial_limit=30, **flags)
    assert entry.spec_for(args, entry.prepare(args)).limit_trials == ceiling


INVALID = [
    pytest.param('--accept-score', 'abc', 'loop_accept_score_invalid', id='accept-score-not-a-number'),
    pytest.param('--accept-score', '', 'loop_accept_score_invalid', id='accept-score-empty'),
    pytest.param('--accept-score', '0', 'loop_accept_score_invalid', id='accept-score-zero'),
    pytest.param('--accept-score', '-0.5', 'loop_accept_score_invalid', id='accept-score-negative'),
    pytest.param('--accept-score', '1.5', 'loop_accept_score_invalid', id='accept-score-above-one'),
    pytest.param('--accept-score', 'nan', 'loop_accept_score_invalid', id='accept-score-nan'),
    pytest.param('--accept-score', 'inf', 'loop_accept_score_invalid', id='accept-score-infinite'),
    pytest.param('--max-rounds', '0', 'loop_max_rounds_invalid', id='max-rounds-zero'),
    pytest.param('--max-rounds', '11', 'loop_max_rounds_invalid', id='max-rounds-above-the-cap'),
    pytest.param('--max-rounds', '2.5', 'loop_max_rounds_invalid', id='max-rounds-fractional'),
    pytest.param('--max-rounds', 'many', 'loop_max_rounds_invalid', id='max-rounds-text'),
    pytest.param('--max-rounds', '99999999999999999999', 'loop_max_rounds_invalid', id='max-rounds-oversized'),
    pytest.param('--plateau-rounds', '0', 'loop_plateau_rounds_invalid', id='plateau-rounds-zero'),
    pytest.param('--plateau-rounds', 'x', 'loop_plateau_rounds_invalid', id='plateau-rounds-text'),
    pytest.param('--plateau-rounds', '4', 'loop_plateau_rounds_invalid', id='plateau-rounds-above-the-default-rounds'),
    pytest.param('--on-regression', 'bogus', 'loop_on_regression_invalid', id='on-regression-unknown'),
    pytest.param('--on-regression', 'STOP', 'loop_on_regression_invalid', id='on-regression-wrong-case'),
    pytest.param('--on-regression', '', 'loop_on_regression_invalid', id='on-regression-empty'),
]


@pytest.mark.parametrize('entry_words', ENTRIES)
@pytest.mark.parametrize('flag, value, reason', INVALID)
def test_an_invalid_flag_value_exits_2_with_its_own_reason_before_anything_runs(tmp_path, entry_words, flag, value, reason):
    code, receipt, stderr = invoke([*entry_words, str(tmp_path), f'{flag}={value}'])
    assert code == 2 and (receipt['status'], receipt['reason']) == ('invalid_input', reason)
    assert receipt['exit_receipt'] == dict(profile=typed_exit.PROFILE, code=2, stage='usage', next_step=REASONS[reason])
    assert any(line.startswith(f'ahl: {reason}:') for line in stderr.splitlines()) and 'Private run artifacts' not in stderr and 'output' not in receipt


@pytest.mark.parametrize('entry_words', ENTRIES)
def test_a_plateau_above_the_declared_rounds_is_refused_across_flags(tmp_path, entry_words):
    code, receipt, _ = invoke([*entry_words, str(tmp_path), '--max-rounds', '2', '--plateau-rounds', '3'])
    assert (code, receipt['reason']) == (2, 'loop_plateau_rounds_invalid')


def test_the_loop_options_do_not_combine_with_a_contract_repair(tmp_path):
    code, receipt, _ = invoke(['improve', str(tmp_path), '--repair-contracts', '--max-rounds', '2'])
    assert (code, receipt['status'], receipt['reason']) == (2, 'invalid_input', 'invalid_usage')


def test_the_loop_options_do_not_combine_with_a_resume(tmp_path):
    code, receipt, _ = invoke(['improve', str(tmp_path), '--resume', 'run-abc', '--accept-score', '0.8'])
    assert (code, receipt['reason']) == (2, 'usage_conflict')


@pytest.mark.parametrize('loop, reason', [
    pytest.param(dict(scope='global'), 'loop_authority_refused', id='cannot-select-a-scope'),
    pytest.param(dict(apply=True), 'loop_authority_refused', id='cannot-enable-apply'),
    pytest.param(dict(recommendation_only=False), 'loop_authority_refused', id='cannot-lift-recommendation-only'),
    pytest.param(dict(automatic_promotion=True), 'loop_authority_refused', id='cannot-promote'),
    pytest.param(dict(max_rounds=0), 'loop_max_rounds_invalid', id='an-invalid-value-is-not-ignored'),
])
def test_a_project_loop_object_never_grants_authority_and_a_bad_one_exits_2(tmp_path, loop, reason):
    (tmp_path/'ahl.json').write_text(json.dumps({**config.defaults(), 'loop': loop}))
    code, receipt, stderr = invoke(['improve', str(tmp_path)])
    assert (code, receipt['status'], receipt['reason']) == (2, 'invalid_input', reason) and f'ahl: {reason}' in stderr


@pytest.mark.parametrize('entry_words', ENTRIES)
def test_the_help_of_both_entries_documents_every_flag_and_the_exit_mapping(entry_words):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit) as stop:
        main([*entry_words, '--help'])
    text = ' '.join(out.getvalue().split())
    assert stop.value.code == 0 and all(flag in text for flag in FLAGS)
    for stopped in ('accepted_without_change', 'acceptance_score_reached', 'budget_exhausted', 'interrupted', 'comparison_invalid', 'max_rounds',
                    'plateau', 'regression', 'no_actionable_mechanism', 'evidence_insufficient', 'loop_requires_frozen_bank'):
        assert stopped in text, stopped
    assert 'Loop exits' in text and '2 invalid declaration' in text and 'not certification' in text


def test_a_loop_error_is_typed_by_the_entry_point_as_an_input_error_unless_it_is_an_ahl_defect():
    assert typed_exit.classify(LoopError('loop_max_rounds_invalid', 'max_rounds', 'cli'), True) == ('loop_max_rounds_invalid', 'invalid_input')
    assert typed_exit.classify(LoopError('loop_receipt_invalid', 'stop_criterion', 'declared'), True) == ('loop_receipt_invalid', 'requires_intervention')
    assert typed_exit.classify(LoopError('loop_order_violation', 'declared.stopped', None), True) == ('loop_order_violation', 'requires_intervention')
