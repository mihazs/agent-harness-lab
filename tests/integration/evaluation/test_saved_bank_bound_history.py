"""Read-only analysis of a saved bank-bound history writes nothing to the history or its bank store (synthetic data)."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

import pytest

from agent_harness_lab import private_journal
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.bank_revisions import validate_admission
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from tests.integration.evaluation.test_oracle_bank_authority import OracleBankAuthority
from tests.integration.evaluation.test_review_authority import ReviewAuthorityFixture, needs_openssh


@pytest.fixture
def bound():
    with ExitStack() as cleanups:
        yield OracleBankAuthority(cleanups.callback)


def state(root):
    """Names, modes, bytes and mtimes; a transient scratch directory still moves its parent's mtime."""
    rows = {}
    for path in (root, *sorted(root.rglob('*'))):
        info = path.lstat()
        rows[str(path.relative_to(root))] = (info.st_mode, info.st_mtime_ns,
                                             path.read_bytes() if path.is_file() and not path.is_symlink() else None)
    return rows


def bank_store(bound):
    return bound.admission.store.root


def analyze_saved(bound, run_id, *, output='analysis-output', environment=None):
    project = bound.root/'analysis-project'; project.mkdir(exist_ok=True)
    events = bound.root/'empty-events.jsonl'; events.write_text('')
    argv = ['analyze', str(project), '--events', str(events),
            '--history-store', str(bound.store.root), '--history-run', run_id]
    if output is not None: argv += ['--output', str(output if isinstance(output, Path) else bound.root/output)]
    out = io.StringIO()
    with patch.dict(os.environ, environment or {}), redirect_stdout(out), redirect_stderr(io.StringIO()), \
            patch.object(SandboxBackend, 'run', side_effect=AssertionError('saved analysis must not dispatch')), \
            patch.object(HistoryStore, 'run', side_effect=AssertionError('saved analysis must not create a run')):
        code = main(argv)
    return code, json.loads(out.getvalue())


def test_bank_bound_saved_history_is_analyzed_without_writing_to_either_store(bound):
    run, _ = bound.run_history()
    history_before, bank_before = state(bound.store.root), state(bank_store(bound))
    code, receipt = analyze_saved(bound, run['run_id'])
    assert code == 0
    method = receipt['analysis']['algorithms'][-1]
    assert (method['id'], method['execution'], method['authority']) == ('paired_history_analysis', 'completed', 'conditional_analysis_only')
    result = method['result']
    assert result['bank_authority'] == 'current_eligible' and result['oracle_validity'] == 'current'
    assert result['independence'] == 'unverified' and result['oracle_review'] == 'unverified'
    assert result['automatic_promotion'] is False and result['publication_allowed'] is False
    assert state(bound.store.root) == history_before
    assert state(bank_store(bound)) == bank_before


def test_revoked_bank_rejects_saved_analysis_instead_of_reporting_current_authority(bound):
    run, _ = bound.run_history()
    bound.revoke()
    history_before, bank_before = state(bound.store.root), state(bank_store(bound))
    with pytest.raises(SystemExit) as stopped:
        analyze_saved(bound, run['run_id'])
    assert stopped.value.code == 2
    assert state(bound.store.root) == history_before and state(bank_store(bound)) == bank_before


def test_forged_bank_binding_on_an_unbound_history_is_rejected_without_writing(bound):
    run, _ = bound.run_history()
    path = bound.store.case(run['run_id'])/'inputs.json'
    forged = json.loads(path.read_text()); forged.pop('bank_authority'); forged.pop('bank_tasks')
    path.write_text(json.dumps(forged))
    history_before, bank_before = state(bound.store.root), state(bank_store(bound))
    with pytest.raises(SystemExit) as stopped:
        analyze_saved(bound, run['run_id'])
    assert stopped.value.code == 2
    assert state(bound.store.root) == history_before and state(bank_store(bound)) == bank_before


@pytest.mark.parametrize('destination', [
    pytest.param('output', id='explicit-output'),
    pytest.param('state', id='state-root'),
    pytest.param('temporary', id='temporary-root'),
])
def test_destinations_inside_the_bank_store_are_rejected_before_anything_is_written(bound, destination, monkeypatch):
    run, _ = bound.run_history()
    bank = bank_store(bound); bank_before = state(bank); history_before = state(bound.store.root)
    output, environment = 'analysis-output', {}
    if destination == 'output': output = bank/'analysis-output'
    if destination == 'state': environment = dict(AHL_STATE_HOME=str(bank/'state'))
    if destination == 'temporary': monkeypatch.setattr(tempfile, 'tempdir', str(bank/'scratch'))
    with pytest.raises(SystemExit) as stopped:
        analyze_saved(bound, run['run_id'], output=output, environment=environment)
    assert stopped.value.code == 2
    assert state(bank) == bank_before and state(bound.store.root) == history_before


@pytest.mark.parametrize('destination', [
    pytest.param('output', id='explicit-output'),
    pytest.param('temporary', id='temporary-root'),
])
def test_destinations_inside_the_selected_history_are_rejected_before_anything_is_written(bound, destination, monkeypatch):
    run, _ = bound.run_history()
    history_before = state(bound.store.root); bank_before = state(bank_store(bound))
    output = bound.root/'analysis-output'
    if destination == 'output': output = bound.store.root/'analysis-output'
    if destination == 'temporary': monkeypatch.setattr(tempfile, 'tempdir', str(bound.store.root/'scratch'))
    with pytest.raises(SystemExit) as stopped:
        analyze_saved(bound, run['run_id'], output=output)
    assert stopped.value.code == 2
    assert state(bound.store.root) == history_before and state(bank_store(bound)) == bank_before


def test_saved_analysis_anchors_validation_scratch_in_its_private_output(bound):
    run, _ = bound.run_history()
    seen = []
    original = HistoryStore.analyze
    def spy(self, *args, **kwargs):
        seen.append(private_journal.scratch_dir(None))
        return original(self, *args, **kwargs)
    with patch.object(HistoryStore, 'analyze', spy):
        code, _ = analyze_saved(bound, run['run_id'])
    assert code == 0 and seen == [(bound.root/'analysis-output').resolve()]
    assert private_journal.scratch_dir(None) is None


@needs_openssh
def test_signature_scratch_for_an_authenticated_bank_review_stays_outside_the_bank_store(tmp_path):
    with ExitStack() as cleanups:
        authority = ReviewAuthorityFixture(cleanups.callback)
        bank = authority.admit_signed_review()
        store = authority.store.root
        scratch = tmp_path/'scratch'; scratch.mkdir(mode=0o700)
        before = state(store)
        with private_journal.scratch_in(scratch):
            admission = validate_admission(bank)
        assert admission['current_eligible'] is True
        assert state(store) == before and not any(scratch.iterdir())


@needs_openssh
def test_unredirected_signature_verification_does_move_the_bank_store_state():
    """Control: proves the snapshot above can see the scratch directory it is meant to catch."""
    with ExitStack() as cleanups:
        authority = ReviewAuthorityFixture(cleanups.callback)
        bank = authority.admit_signed_review()
        before = state(authority.store.root)
        assert validate_admission(bank)['current_eligible'] is True
        assert state(authority.store.root) != before
