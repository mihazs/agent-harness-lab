"""`ahl improve --resume RUN_ID` on a loop run: a finished run's receipt comes back verified and unchanged, anything else is untouched (#815).

The agent, bank and project are the synthetic ones of tests.support.loop_improve. A resume never starts the loop again: the run
directory, the round ledger and the stub agent's call log are compared before and after.
"""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path

import pytest

from agent_harness_lab import workflow
from agent_harness_lab.loop import improve as loop_improve
from tests.support import loop_improve as li

HIGH = dict(alpha=0.999)


@pytest.fixture
def stack():
    with ExitStack() as stack:
        yield stack


@pytest.fixture(autouse=True)
def three_mechanisms(monkeypatch):
    monkeypatch.setattr(loop_improve, 'mechanisms', lambda args: ['m1', 'm2', 'm3'])


def finished(stack, monkeypatch, **options):
    project = li.LoopProject(stack, monkeypatch, loop=HIGH, **{**dict(baseline=li.EVERYTHING), **options})
    code, found, _ = project.invoke('--accept-score', '0.1')
    return project, code, found


def files(folder):
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in sorted(Path(folder).rglob('*')) if path.is_file()}


def ledger_case(found):
    return next((workflow.state_root()/'loops'/Path(found['output']).name/'cases').iterdir())


def test_resuming_a_finished_loop_run_returns_its_receipt_and_changes_nothing(stack, monkeypatch):
    project, code, found = finished(stack, monkeypatch)
    run, runs = Path(found['output']), sorted(path.name for path in Path(found['output']).parent.iterdir())
    before, ledger = files(run), files(workflow.state_root()/'loops')
    assert before and any(name.endswith('head.json') for name in ledger)  # the comparison below is over a real run and a real ledger
    again_code, again, _ = project.invoke('--resume', run.name)
    assert (again_code, again) == (code, found) and again['loop']['stop_criterion'] == 'accepted_without_change'
    assert files(run) == before and files(workflow.state_root()/'loops') == ledger and sorted(path.name for path in run.parent.iterdir()) == runs and project.calls() == []


def test_resuming_with_other_execution_options_is_still_refused(stack, monkeypatch):
    project, _, found = finished(stack, monkeypatch)
    code, refused, _ = project.invoke('--resume', Path(found['output']).name, '--accept-score', '0.5')
    assert code != 0 and refused['reason'] == 'usage_conflict' and project.calls() == []


@pytest.mark.parametrize('damage', ['receipt-stop', 'receipt-stop-consistent', 'receipt-status', 'receipt-reason', 'receipt-output',
                                    'receipt-applied', 'receipt-promotion', 'receipt-publication', 'receipt-events', 'receipt-head-removed', 'receipt-version-bool', 'receipt-before-score', 'receipt-after-score', 'receipt-frozen-inputs', 'receipt-mechanisms', 'receipt-spec', 'receipt-sources', 'ledger-spec', 'both-spec-rehashed',
                                    'ledger-event', 'ledger-head', 'ledger-removed'])
def test_a_tampered_receipt_or_ledger_is_not_returned_as_the_run_result(stack, monkeypatch, damage):
    project, _, found = finished(stack, monkeypatch)
    run = Path(found['output'])
    document = json.loads((run/'workflow.json').read_text())
    if damage == 'receipt-stop': document['loop']['stop_criterion'] = 'acceptance_score_reached'
    if damage == 'receipt-stop-consistent':
        document['loop']['stop_criterion'] = 'max_rounds'
        document.update(status='not_accepted', reason='loop_max_rounds')
    if damage == 'receipt-status': document['status'] = 'not_accepted'
    if damage == 'receipt-reason': document['reason'] = 'loop_max_rounds'
    if damage == 'receipt-output': document['output'] = str(run.parent/'another-run')
    if damage == 'receipt-applied': document['changed_files'] = ['AGENTS.md']
    if damage == 'receipt-promotion': document['automatic_promotion'] = True
    if damage == 'receipt-publication': document['publication_allowed'] = True
    if damage == 'receipt-events': document['loop']['events'] += 1
    if damage == 'receipt-head-removed': document['loop'].pop('ledger_head_sha256')
    if damage == 'receipt-version-bool': document['loop']['schema_version'] = True
    if damage in ('receipt-before-score', 'receipt-after-score'):
        document['loop'][damage.split('-')[1]]['score'] = dict(point=0.2, lower=0.1, upper=0.3)
    if damage == 'receipt-frozen-inputs': document['loop']['frozen_inputs_sha256'] = '0'*64
    if damage == 'receipt-mechanisms': document['loop']['mechanisms'] = {'m1': 'accepted'}
    if damage == 'receipt-spec': document['loop']['spec']['accept_score'] = 0.99
    if damage == 'receipt-sources':
        document['loop']['spec']['sources']['accept_score'] = 'project'
        document['loop']['spec']['acceptance_source'] = 'project'
    if damage.startswith('receipt'): (run/'workflow.json').write_text(json.dumps(document))
    case = ledger_case(found)
    if damage == 'ledger-spec':
        spec_path = case/'spec.json'
        spec = json.loads(spec_path.read_text())
        spec['accept_score'] = 0.99
        spec_path.write_text(json.dumps(spec))
    if damage == 'both-spec-rehashed':
        spec = document['loop']['spec']
        spec['accept_score'] = 0.99
        identity = {key: value for key, value in spec.items() if key not in ('digest', 'sources', 'acceptance_source', 'looks')}
        spec['digest'] = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        (case/'spec.json').write_text(json.dumps(spec))
        (run/'workflow.json').write_text(json.dumps(document))
    if damage == 'ledger-event': (case/'events/0001.json').write_text((case/'events/0001.json').read_text().replace('"valid"', '"vacuous"'))
    if damage == 'ledger-head': (case/'head.json').write_text(json.dumps(dict(events=2, head_sha256='0'*64)))
    if damage == 'ledger-removed': (case/'events/0001.json').unlink()
    code, resumed, stderr = project.invoke('--resume', run.name)
    assert code != 0 and 'loop' not in (resumed or {}) and (resumed or {}).get('status') != 'accepted' and project.calls() == []
    assert 'loop_ledger_invalid' in json.dumps(resumed) + stderr or 'loop_receipt_invalid' in json.dumps(resumed) + stderr


def test_an_interrupted_loop_run_is_not_continued_by_the_command_and_says_so(stack, monkeypatch):
    def interrupt(self, best_snapshot, mechanism):
        raise KeyboardInterrupt
    monkeypatch.setattr(loop_improve.PreparationProposer, 'propose', interrupt)
    project = li.LoopProject(stack, monkeypatch, loop=HIGH, script={0: dict(harness=li.EVERYTHING)})
    code, found, _ = project.invoke('--accept-score', '0.5')
    assert (code, found['loop']['stop_criterion']) == (1, 'interrupted')
    resumed_code, resumed, _ = project.invoke('--resume', Path(found['output']).name)
    assert (resumed_code, resumed['status'], resumed['reason']) == (3, 'blocked', 'resume_requires_intervention') and 'loop' not in resumed


def test_a_loop_run_blocked_before_it_started_has_no_receipt_to_return(stack, monkeypatch):
    project = li.LoopProject(stack, monkeypatch, loop=HIGH, pinned=False)
    code, found, _ = project.invoke('--accept-score', '0.5')
    assert (code, found['reason']) == (3, 'loop_requires_frozen_bank')
    resumed_code, resumed, _ = project.invoke('--resume', Path(found['output']).name)
    assert (resumed_code, resumed['reason']) == (3, 'resume_requires_intervention')


@pytest.mark.parametrize('field', ['score', 'decision', 'candidate'])
def test_a_tampered_candidate_round_is_not_returned(stack, monkeypatch, field):
    project, _, found = finished(stack, monkeypatch, baseline=li.NOTHING, script={0: dict(harness=li.EVERYTHING)})
    run = Path(found['output'])
    code, unchanged, _ = project.invoke('--resume', run.name)
    assert code == 0 and unchanged == found
    document = json.loads((run/'workflow.json').read_text())
    item = document['loop']['rounds'][0]
    item[field] = {'score': dict(point=0.2, lower=0.1, upper=0.3), 'decision': 'improved', 'candidate': '0'*64}[field]
    (run/'workflow.json').write_text(json.dumps(document))
    calls = project.calls()
    code, resumed, stderr = project.invoke('--resume', run.name)
    assert code != 0 and 'loop' not in (resumed or {}) and project.calls() == calls
    assert 'loop_receipt_invalid' in json.dumps(resumed) + stderr


@pytest.mark.parametrize('criterion', ['max_rounds', 'regression', 'no_actionable_mechanism', 'evidence_insufficient', 'comparison_invalid', 'budget_exhausted'])
def test_resuming_other_finished_stops_returns_the_original_receipt(stack, monkeypatch, criterion):
    from tests.integration.loop.test_improve_loop_cli import make
    project, flags = make(stack, monkeypatch, criterion)
    code, found, _ = project.invoke(*flags)
    assert found['loop']['stop_criterion'] == criterion
    calls = project.calls()
    resumed_code, resumed, _ = project.invoke('--resume', Path(found['output']).name)
    assert (resumed_code, resumed) == (code, found) and project.calls() == calls
