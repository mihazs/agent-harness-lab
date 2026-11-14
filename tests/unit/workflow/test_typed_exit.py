"""The closed-vocabulary exit helpers: sealing a result, classifying an exception and printing the receipt (#837).

Everything here is synthetic; no run, project or runtime is involved.
"""
import argparse
import copy
import json
import ssl
import urllib.error

import pytest

from agent_harness_lab import budget_stages, typed_exit
from agent_harness_lab.evaluation import manifest_contract
from agent_harness_lab.monitoring.state import EXIT_CODES

NONZERO = sorted(status for status, code in EXIT_CODES.items() if code)
ZERO = sorted(status for status, code in EXIT_CODES.items() if not code)
ERROR_PREFIX = 'ahl: exit_receipt='


@pytest.mark.parametrize('result', [{}, dict(status=None), dict(status=''), dict(status='unheard_of'), dict(status='Analyzed')],
                         ids=['missing', 'none', 'empty', 'unknown', 'wrong-case-of-a-success'])
def test_a_missing_or_unknown_status_is_sealed_incomplete_and_never_exits_zero(result):
    status = result.get('status')
    sealed = typed_exit.seal(result)
    assert sealed.get('status') == status  # the unknown name is kept, never rewritten into a known one
    assert sealed['reason'] == 'run_incomplete'
    assert sealed['exit_receipt']['code'] == 1 and sealed['exit_receipt']['next_step']


@pytest.mark.parametrize('status', NONZERO)
def test_every_nonzero_status_is_sealed_with_its_default_reason_and_its_exit_code(status):
    sealed = typed_exit.seal(dict(status=status))
    assert sealed['reason'] == typed_exit.DEFAULT_REASON[status] and sealed['reason'] in typed_exit.REASONS
    assert sealed['exit_receipt']['profile'] == typed_exit.PROFILE
    assert sealed['exit_receipt']['code'] == EXIT_CODES[status]
    assert sealed['exit_receipt']['stage'] in typed_exit.STAGES and sealed['exit_receipt']['next_step']


@pytest.mark.parametrize('status', ZERO)
def test_a_zero_exit_status_is_left_untouched(status):
    assert typed_exit.seal(dict(status=status)) == dict(status=status)


def test_a_recorded_reason_is_kept_and_a_sealed_result_is_not_sealed_again():
    first = typed_exit.seal(dict(status='conflict', reason='workspace_busy'))
    assert first['reason'] == 'workspace_busy' and first['exit_receipt']['stage'] == 'workspace'
    assert typed_exit.seal(copy.deepcopy(first)) == first


def test_the_stage_that_consumed_the_budget_becomes_the_receipt_stage():
    sealed = typed_exit.seal(dict(status='budget_exhausted', budget_exhaustion=dict(consumed_most_by='trials')))
    assert sealed['exit_receipt']['stage'] == 'trials'


def test_a_stage_outside_the_vocabulary_is_never_copied_into_the_receipt():
    sealed = typed_exit.seal(dict(status='budget_exhausted', budget_exhaustion=dict(consumed_most_by='/private/path')))
    assert sealed['exit_receipt']['stage'] == 'run'


def test_no_budget_is_invented_when_the_invocation_fixed_none():
    assert typed_exit.budget(argparse.Namespace()) is None
    assert 'budget_stages' not in typed_exit.seal(dict(status='budget_exhausted'), argparse.Namespace())


def test_a_fixed_timeout_adds_the_stage_budget_to_the_receipt():
    sealed = typed_exit.seal(dict(status='budget_exhausted'), argparse.Namespace(timeout=60))
    assert sealed['budget_stages']['total_seconds'] == 60


@pytest.mark.parametrize('timeout', [0, -5, True, 'x', None, float('nan')],
                         ids=['zero', 'negative', 'boolean', 'text', 'null', 'not-a-number'])
def test_no_budget_is_invented_for_a_timeout_that_is_not_a_positive_number(timeout):
    assert typed_exit.budget(argparse.Namespace(timeout=timeout)) is None
    assert 'budget_stages' not in typed_exit.seal(dict(status='budget_exhausted'), argparse.Namespace(timeout=timeout))


@pytest.mark.parametrize('fraction', [5, -1, 0.0, 'x', True], ids=['above-one', 'negative', 'zero', 'text', 'boolean'])
def test_an_invalid_construction_fraction_never_stops_the_receipt_from_carrying_the_budget(fraction):
    # The exit path must not raise on the option that may itself have caused the exit; the default split is reported instead.
    found = typed_exit.budget(argparse.Namespace(timeout=60, construction_fraction=fraction))
    assert found['total_seconds'] == 60 and found['construction_fraction'] == budget_stages.DEFAULT_CONSTRUCTION_FRACTION


def test_an_unregistered_reason_still_exits_nonzero_with_the_fallback_next_step():
    out = typed_exit.receipt('reason_nobody_registered')
    assert out['exit_receipt']['code'] == EXIT_CODES['execution_error'] and out['exit_receipt']['next_step'] == typed_exit.LOCAL


CONTRACT = manifest_contract.ContractError('evaluation_manifest_unreadable', 'evaluation_construction')


class FreeTextReason(ValueError):
    """Any exception may carry a `reason` attribute; only a manifest contract error registers one."""
    reason = 'some free text that is not in the vocabulary'


def refused_certificate():
    error = ssl.SSLError(1, 'certificate verify failed')
    error.reason = 'CERTIFICATE_VERIFY_FAILED'
    return error


CLASSIFIED = {
    'typed': (typed_exit.TypedExit('workspace_busy'), False, ('workspace_busy', 'conflict')),
    'contract': (CONTRACT, True, ('evaluation_manifest_unreadable', 'invalid_input')),
    'timeout': (TimeoutError('synthetic'), True, ('budget_exhausted', 'budget_exhausted')),
    'oserror': (OSError(28, 'synthetic'), True, ('system_error', 'setup_error')),
    'missing-file': (FileNotFoundError(2, 'synthetic'), False, ('system_error', 'setup_error')),
    'tls-error-with-a-free-text-reason': (refused_certificate(), True, ('system_error', 'setup_error')),
    'url-error-with-a-free-text-reason': (urllib.error.URLError('connection refused'), True, ('system_error', 'setup_error')),
    'plain-value-error-in-a-run': (ValueError('synthetic'), True, ('run_input_rejected', 'invalid_input')),
    'plain-value-error-before-a-run': (ValueError('synthetic'), False, ('invalid_usage', 'invalid_input')),
    'value-error-with-a-free-text-reason': (FreeTextReason('synthetic'), True, ('run_input_rejected', 'invalid_input')),
}


@pytest.mark.parametrize('name', CLASSIFIED)
def test_an_exception_at_the_entry_point_maps_into_the_closed_vocabulary(name):
    exc, started, expected = CLASSIFIED[name]
    reason, status = typed_exit.classify(exc, started)
    assert (reason, status) == expected
    assert reason in typed_exit.REASONS or reason in manifest_contract.REASONS
    assert EXIT_CODES[status]  # no exception becomes a success


def exit_of(reason, **options):
    with pytest.raises(SystemExit) as stop:
        typed_exit.fail(argparse.ArgumentParser(prog='ahl'), reason, 'synthetic message', **options)
    return stop.value.code


def test_a_stdout_failure_prints_the_receipt_object_and_keeps_the_message_on_stderr(capsys):
    code = exit_of('workspace_busy')
    captured = capsys.readouterr()
    receipt = json.loads(captured.out)
    assert code == 1 == receipt['exit_receipt']['code']
    assert (receipt['status'], receipt['reason']) == ('conflict', 'workspace_busy')
    assert captured.err == 'ahl: synthetic message\n'


def test_a_failure_without_stdout_keeps_stdout_empty_and_prints_the_receipt_as_one_stderr_line(capsys):
    code = exit_of('nested_invocation_refused', stdout=False)
    captured = capsys.readouterr()
    line, message = captured.err.splitlines()
    receipt = json.loads(line.removeprefix(ERROR_PREFIX))
    assert code == 3 == receipt['exit_receipt']['code'] and captured.out == ''
    assert (receipt['status'], receipt['reason']) == ('blocked', 'nested_invocation_refused')
    assert line.startswith(ERROR_PREFIX) and message == 'ahl: synthetic message'
