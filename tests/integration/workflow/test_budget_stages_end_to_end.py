"""Stage budgets, construction time box, feasibility, uninformative stop and refinement reuse through the real `ahl improve` entry (#739).

A stub runtime on PATH plays the construction, proposer and trial agents. A virtual clock (real monotonic time plus an
offset the stub scripts advance through a file) makes every overrun deterministic without sleeping. Everything is synthetic.
"""
import json
import time

import pytest

from agent_harness_lab import budget_stages
from agent_harness_lab.evaluation.integrity import digest
from tests.integration.workflow.test_authored_digest_end_to_end import Stub

# Modes are a JSON object: construct_seconds / grade_seconds advance the virtual clock, trial selects the trial behavior,
# refine makes the first candidate no better than the baseline so a refinement attempt runs.
STUB = r'''#!@PYTHON@
import json, pathlib, sys
p = pathlib.Path
if '--version' in sys.argv: print('stub-runtime'); sys.exit(0)
prompt = sys.stdin.read()
mode = json.loads(p('@MODE@').read_text()) if p('@MODE@').exists() else {}
def note(**row):
    with open('@CALLS@', 'a') as log: log.write(json.dumps(row) + '\n')
def advance(path, seconds):
    clock = p(path); clock.write_text(str(float(clock.read_text() if clock.exists() else 0) + seconds))
done = json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'usage': {'output_tokens': 1}}
                  if p(sys.argv[0]).name == 'claude' else {'type': 'turn.completed', 'usage': {'output_tokens': 1}})
if p('context.json').exists():
    c = json.loads(p('context.json').read_text()); evaluation = p(c['evaluation'])
    if c['phase'] == 'candidate':
        note(kind='proposal', attempt=c['attempt'])
        (p(c['candidate']) / 'AGENTS.md').write_text('worse\n' if mode.get('refine') and c['attempt'] == 0 else 'good\n')
        (evaluation / 'proposal.json').write_text(json.dumps({'status': 'candidate', 'mechanisms': ['synthetic'], 'falsifiers': ['artifact check']}))
    else:
        reuse = c.get('reuse')
        note(kind='construction', attempt=c['attempt'], reused=len(reuse['tasks']) if reuse else 0,
             remaining_in_prompt='seconds remain' in prompt and 'time box' in prompt.lower(), repair='overran its time box' in prompt)
        seconds = mode.get('construct_seconds', 0)
        if mode.get('construct_overrun_once') and not p('@MARK@').exists():
            p('@MARK@').write_text('x'); seconds = mode['construct_overrun_once']
        advance('@CLOCK@', seconds)
        tasks, controls, bundles = [], [], {}
        for split in (('heldout',) if reuse else ('development', 'regression', 'heldout')):
            fixture = evaluation / split; fixture.mkdir(exist_ok=True); (fixture / 'seed').write_text(c['harness_paths'][0] + split + str(c['attempt']))
            bundle = evaluation / ('oracle-' + split); bundle.mkdir(exist_ok=True)
            slow = ("    cl = pathlib.Path(%r); cl.write_text(str(float(cl.read_text() if cl.exists() else 0) + %r))\n"
                    % ('@CLOCK@', mode['grade_seconds'])) if mode.get('grade_seconds') else '    pass\n'
            (bundle / 'grade.py.txt').write_text(
                "import json, pathlib, sys\n"
                "ok = (pathlib.Path(sys.argv[1]) / 'artifact').read_text() == 'done'\n"
                "if ok:\n" + slow +
                "print(json.dumps(dict(schema_version=1, valid=True, outcome=ok, grader_id='synthetic', grader_version='1')))\n"
                "sys.exit(0 if ok else 1)\n")
            (bundle / 'expected.json').write_text('{}')
            tasks.append(dict(id=split, split=split, fixture=split, prompt='Produce the synthetic artifact', artifacts=['artifact'],
                grader=[sys.executable, str(bundle / 'grade.py.txt'), '{workspace}', str(bundle / 'expected.json')]))
            bundles[split] = dict(path='oracle-' + split)
            for state in ('broken', 'partial', 'complete'):
                control = evaluation / (split + '-' + state); control.mkdir(exist_ok=True)
                (control / 'artifact').write_text('done' if state == 'complete' else 'wrong')
                controls.append(dict(task=split, state=state, fixture=control.name))
        note(kind='constructed', attempt=c['attempt'], tasks=[t['id'] for t in tasks])
        (evaluation / 'manifest.json').write_text(json.dumps(dict(harness_paths=c['harness_paths'], skills=[], tasks=tasks,
            grader_self_tests=controls, grader_bundles=bundles)))
else:
    note(kind='trial')
    trial = mode.get('trial', 'normal')
    good = p('AGENTS.md').read_text().strip() == 'good'
    if trial == 'crash-all' or trial == 'candidate-crashes' and good: sys.exit(3)
    wrong = trial in ('fail-all', 'candidate-crashes')
    p('artifact').write_text('done' if not wrong and (good or 'regression' in p('seed').read_text()) else 'wrong')
print(done)
'''


class Budgeted(Stub):
    def __init__(self, root, monkeypatch, runtime):
        self.calls, self.clock = root / 'calls.jsonl', root / 'clock'
        super().__init__(root, monkeypatch, runtime)
        real = time.monotonic
        monkeypatch.setattr(budget_stages, 'clock', lambda: real() + (float(self.clock.read_text()) if self.clock.exists() else 0.0))

    def script(self):
        text = STUB.replace('@PYTHON@', __import__('sys').executable).replace('@MODE@', str(self.mode))
        return text.replace('@CALLS@', str(self.calls)).replace('@CLOCK@', str(self.clock)).replace('@MARK@', str(self.root / 'mark'))

    def run(self, capsys, mode, *extra):
        code, receipt, run = self.improve(capsys, json.dumps(mode), *extra)
        rows = [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []
        return code, receipt, run, rows


@pytest.fixture(params=['codex', 'claude'])
def stub(tmp_path, monkeypatch, request):
    return Budgeted(tmp_path, monkeypatch, request.param)


def kinds(rows, kind):
    return [row for row in rows if row['kind'] == kind]


def assert_breakdown(receipt, total=120):
    budget = receipt['budget_stages']
    assert budget['total_seconds'] == total and set(budget['stages']) == set(budget_stages.STAGES)
    assert all(set(stage) == {'allotted_seconds', 'spent_seconds'} for stage in budget['stages'].values())
    assert budget['raise_budget_option'] == '--timeout SECONDS' and budget['raise_construction_option'].startswith('--construction-fraction')
    return budget


def test_a_construction_overrun_ends_with_the_typed_timeout_and_a_stage_breakdown(stub, capsys):
    code, receipt, run, rows = stub.run(capsys, dict(construct_seconds=100))  # box = 0.25 * 120 = 30s; too little is left for another box
    assert (code, receipt['status'], receipt['reason']) == (1, 'budget_exhausted', 'evaluation_construction_timeout'), receipt
    violation = receipt['contract_violation']
    assert (violation['stage'], violation['file'], violation['origin']) == ('evaluation_construction', 'manifest.json', 'agent_output_defect')
    assert 'raise' in violation['next_step'] and '--timeout' in violation['next_step']
    budget = assert_breakdown(receipt)
    assert budget['stages']['construction']['spent_seconds'] >= 100 and budget['remaining_seconds'] < 120 - 100 + 1
    assert budget['remaining_seconds'] > 0 and budget['stages']['trials']['spent_seconds'] == 0  # stopped within the budget, before any trial
    assert len(kinds(rows, 'construction')) == 1 and not kinds(rows, 'trial') and not (run / 'evaluation-0').exists()
    assert receipt['budget_exhaustion']['consumed_most_by'] == 'construction' and receipt['budget_exhaustion']['conclusion'] == 'not_established'
    exhausted = receipt['budget_exhaustion']
    assert (exhausted['evidence_label'], exhausted['observed']['trials_recorded']) == ('insufficient', 0)
    assert exhausted['controls_not_run'] == ['adjacent_regression', 'transfer'] and receipt['evidence_strength']['label'] == 'insufficient'
    assert 'evaluation_construction_seconds' in receipt


def test_an_overrun_is_repaired_once_when_the_remaining_budget_still_covers_a_second_box_and_the_trials(stub, capsys):
    code, receipt, run, rows = stub.run(capsys, dict(construct_overrun_once=35))  # 85s remain: a 30s box plus the 48s trials allotment fit
    assert (code, receipt['status']) == (3, 'inconclusive'), receipt
    repair = receipt['evaluation_construction_repairs']
    assert [(r['reason'], r['outcome']) for r in repair] == [('evaluation_construction_timeout', 'repaired')]
    assert [row['repair'] for row in kinds(rows, 'construction')] == [False, True] and len(kinds(rows, 'trial')) == 6


def test_a_second_overrun_after_the_one_repair_ends_the_run_with_the_typed_reason(stub, capsys):
    code, receipt, run, rows = stub.run(capsys, dict(construct_seconds=35))
    assert (code, receipt['status'], receipt['reason']) == (1, 'budget_exhausted', 'evaluation_construction_timeout'), receipt
    assert [r['outcome'] for r in receipt['evaluation_construction_repairs']] == ['requested'] and len(kinds(rows, 'construction')) == 2
    assert not kinds(rows, 'trial') and assert_breakdown(receipt)['stages']['construction']['spent_seconds'] >= 70


def test_the_construction_prompt_states_the_time_box_and_the_remaining_seconds(stub, capsys):
    _, _, _, rows = stub.run(capsys, {})
    assert kinds(rows, 'construction')[0]['remaining_in_prompt'] is True


def test_a_task_whose_reference_completion_cannot_fit_the_per_trial_timeout_is_rejected_before_any_trial(stub, capsys):
    code, receipt, run, rows = stub.run(capsys, dict(grade_seconds=500))
    assert (code, receipt['status'], receipt['reason']) == (2, 'invalid_input', 'evaluation_task_infeasible'), receipt
    violation = receipt['contract_violation']
    assert (violation['stage'], violation['field']) == ('manifest_validation', 'tasks[0]') and violation['next_step']
    assert not kinds(rows, 'trial') and not (run / 'evaluation-0').exists()
    assert assert_breakdown(receipt)['stages']['trials']['spent_seconds'] == 0


@pytest.mark.parametrize('trial, kind', [('fail-all', 'agent_failed'), ('crash-all', 'runtime_failure')])
def test_a_run_where_both_arms_fail_every_task_alike_stops_uninformative_without_a_second_construction(stub, capsys, trial, kind):
    code, receipt, run, rows = stub.run(capsys, dict(trial=trial))
    assert (code, receipt['status'], receipt['reason']) == (3, 'inconclusive', 'evaluation_uninformative'), receipt
    block = receipt['evaluation_uninformative']
    assert (block['classification'], block['trials'], block['tasks'], block['class_counts']) == (kind, 6, 3, {kind: 6})
    assert block['next_step'] and 'grader' not in json.dumps(block['class_counts'])
    assert len(kinds(rows, 'construction')) == 1 and len(kinds(rows, 'proposal')) == 1 and len(kinds(rows, 'trial')) == 6
    assert not (run / 'preparation/evaluation-1').exists()
    assert assert_breakdown(receipt)['stages']['refinement']['spent_seconds'] == 0
    assert 'recommendation' not in receipt


def test_a_stub_that_fails_one_arm_differently_continues_to_a_refinement(stub, capsys):
    code, receipt, run, rows = stub.run(capsys, dict(trial='candidate-crashes'))
    assert receipt.get('reason') != 'evaluation_uninformative' and 'evaluation_uninformative' not in receipt, receipt
    assert len(kinds(rows, 'construction')) == 2 and (run / 'preparation/evaluation-1').is_dir()


def test_a_refinement_reuses_the_frozen_tasks_and_constructs_only_fresh_heldout_inputs(stub, capsys):
    code, receipt, run, rows = stub.run(capsys, dict(refine=True))
    assert (code, receipt['status']) == (3, 'inconclusive'), receipt
    first, second = kinds(rows, 'construction')
    assert (first['reused'], second['reused']) == (0, 2)
    built = kinds(rows, 'constructed')
    assert built[0]['tasks'] == ['development', 'regression', 'heldout'] and built[1]['tasks'] == ['heldout']  # call counts: only heldout again
    old, new = (json.loads((run / f'preparation/evaluation-{n}/manifest.json').read_text()) for n in (0, 1))
    for task in ('development', 'regression'):  # digests: the frozen graders, controls and fixtures are byte-identical
        assert new['grader_bundles'][task]['digest'] == old['grader_bundles'][task]['digest']
        assert digest(run / 'preparation/evaluation-1' / task) == digest(run / 'preparation/evaluation-0' / task)
        assert [c['digest'] for c in new['grader_self_tests'] if c['task'] == task] == [c['digest'] for c in old['grader_self_tests'] if c['task'] == task]
    assert digest(run / 'preparation/evaluation-1/heldout') != digest(run / 'preparation/evaluation-0/heldout')
    assert receipt['evaluation_reuse'] == dict(attempt=1, reused_tasks=2, fresh_heldout_tasks=1, frozen_digests_verified=True)
    assert assert_breakdown(receipt)['stages']['refinement']['spent_seconds'] >= 0


def test_a_refinement_that_would_alter_a_reused_path_or_pass_off_a_reused_split_is_rejected(stub, capsys):
    stub.mode.write_text(json.dumps(dict(refine=True)))
    text = stub.bin.joinpath(stub.runtime).read_text()
    stub.bin.joinpath(stub.runtime).write_text(text.replace("for split in (('heldout',) if reuse else", "for split in (('heldout', 'development') if reuse else"))
    code, receipt, _, _ = stub.run(capsys, dict(refine=True))
    assert (code, receipt['status']) == (2, 'invalid_input'), receipt
    assert receipt['reason'] == 'evaluation_manifest_field_invalid' and receipt['contract_violation']['field'] == 'tasks[1].split'


def test_a_normal_finish_carries_the_stage_breakdown_and_the_option_to_raise_it(stub, capsys):
    _, receipt, _, _ = stub.run(capsys, {})
    budget = assert_breakdown(receipt)
    assert budget['stages']['trials']['spent_seconds'] >= 0 and budget['stages']['construction']['allotted_seconds'] == 30.0


def test_a_result_blocked_before_any_stage_ran_still_carries_the_breakdown(stub, capsys):
    _, receipt, _ = stub.improve(capsys, '{}', '--history', events=False)  # no captured sessions: blocked before preparation
    assert receipt['status'] == 'blocked' and assert_breakdown(receipt)['stages']['construction']['spent_seconds'] == 0


def test_the_construction_fraction_option_moves_the_time_box_and_rejects_values_outside_the_range(stub, capsys):
    _, receipt, _, _ = stub.run(capsys, {}, '--construction-fraction', '0.5')
    assert receipt['budget_stages']['construction_fraction'] == 0.5 and receipt['budget_stages']['stages']['construction']['allotted_seconds'] == 60.0
    assert stub.improve(capsys, '{}', '--construction-fraction', '1')[0] == 2
