"""The probe reviewer's input is bounded, its answer is validated, and a review that cannot be had never stops a run.

Synthetic claims, tasks and a stub runtime only; no model is called.
"""
import json
from pathlib import Path

import pytest

from agent_harness_lab.evaluation import probe_review

CLAIM = dict(mechanisms=['installs tools inside job scripts'], falsifiers=['a job that uses the pinned image'])
TASKS = [dict(id='dev', split='development', prompt='Add a lint job. Follow docs/ci.md and use the pinned image.', fixture='dev', grader=['grade.py']),
         dict(id='held', split='heldout', prompt='Add a shellcheck job\nto the lint stage.'),
         dict(id='reg', split='regression', prompt='Keep the build green.')]
MANIFEST = dict(tasks=TASKS)


def test_the_input_holds_the_claim_and_the_development_and_heldout_task_texts_only(tmp_path):
    runner = Runner(answer(dev=('leaking', 'Follow docs/ci.md'), held=('fair', '')))
    found = probe_review.review(runner, 'codex', 'codex', tmp_path, CLAIM, MANIFEST, remaining=lambda: 100, model_calls=[])
    assert found['tasks']['dev'] == dict(verdict='leaking', quote='Follow docs/ci.md')
    prompt = runner.stdin
    assert 'independent probe-review session' in prompt
    assert '- installs tools inside job scripts' in prompt and '- a job that uses the pinned image' in prompt
    assert '===== task: dev =====\nAdd a lint job. Follow docs/ci.md and use the pinned image.\n' in prompt
    assert '===== task: held =====\nAdd a shellcheck job\nto the lint stage.\n' in prompt
    for private in ('Keep the build green', 'task: reg', 'grade.py', 'fixture', str(tmp_path)):  # a regression task, a grader, a fixture, a path
        assert private not in prompt, private


def test_each_part_is_bounded_and_says_so(monkeypatch):
    monkeypatch.setattr(probe_review, 'TASK_LIMIT', 20)
    monkeypatch.setattr(probe_review, 'ITEM_LIMIT', 10)
    monkeypatch.setattr(probe_review, 'ITEM_COUNT', 2)
    claim = dict(mechanisms=['m' * 50] * 5, falsifiers='not a list')
    material, shown, truncated = probe_review.review_input(claim, [dict(id='a', prompt='x' * 50)])
    assert truncated and shown == dict(a='x' * 20)
    assert material.count('- ' + 'm' * 10 + '\n') == 2 and 'm' * 11 not in material and 'x' * 21 not in material and 'falsifiers' not in material
    assert not probe_review.review_input(CLAIM, [dict(id='a', prompt='short')])[2]


def write(tmp_path, raw):
    path = tmp_path / 'probe-review.json'
    path.write_text(raw if isinstance(raw, str) else json.dumps(raw))
    return path


SHOWN = dict(dev='Add a lint job. Follow docs/ci.md and use the pinned image.', held='Add a shellcheck job\nto the lint stage.')


def answer(**tasks):
    return json.dumps(dict(tasks={name: dict(verdict=verdict, quote=quote) for name, (verdict, quote) in tasks.items()}, reason=' because '))


def test_a_valid_answer_keeps_each_verdict_and_a_quote_only_for_a_leaking_task(tmp_path):
    loaded = probe_review.load(write(tmp_path, answer(dev=('leaking', 'Follow docs/ci.md'), held=('fair', 'ignored words'))), SHOWN)
    assert loaded == dict(tasks=dict(dev=dict(verdict='leaking', quote='Follow docs/ci.md'), held=dict(verdict='fair', quote='')), reason='because')


def test_a_quote_may_break_lines_differently_from_the_text_it_comes_from(tmp_path):
    loaded = probe_review.load(write(tmp_path, answer(dev=('fair', ''), held=('leaking', 'shellcheck job to\nthe lint'))), SHOWN)
    assert loaded['tasks']['held'] == dict(verdict='leaking', quote='shellcheck job to the lint')


@pytest.mark.parametrize('quote', ['', '   ', 'Follow docs/CI.md', 'a rule the text never states', None])
def test_a_leaking_verdict_whose_quote_is_not_in_the_text_is_taken_as_fair(tmp_path, quote):
    # An unsupported accusation must not cost the author a repair pass.
    dev = dict(verdict='leaking', **({} if quote is None else dict(quote=quote)))
    loaded = probe_review.load(write(tmp_path, dict(tasks=dict(dev=dev, held=dict(verdict='fair')), reason='r')), SHOWN)
    assert loaded['tasks']['dev'] == dict(verdict='fair', quote='')


def test_a_quote_that_is_not_text_is_unusable(tmp_path):
    with pytest.raises(ValueError, match='^probe_review_quote_invalid$'):
        probe_review.load(write(tmp_path, dict(tasks=dict(dev=dict(verdict='leaking', quote=3), held=dict(verdict='fair')))), SHOWN)


def test_the_answer_and_the_quote_are_bounded(tmp_path):
    long = 'Follow ' + 'x' * 5000
    loaded = probe_review.load(write(tmp_path, dict(tasks=dict(dev=dict(verdict='leaking', quote=long), held=dict(verdict='fair')), reason='r' * 5000)),
                               dict(SHOWN, dev='Please ' + long))
    assert len(loaded['reason']) == probe_review.TEXT_LIMIT and len(loaded['tasks']['dev']['quote']) == probe_review.TEXT_LIMIT


@pytest.mark.parametrize('raw,cause', [
    ('{', 'probe_review_not_json'), ('[]', 'probe_review_not_an_object'), ('"fair"', 'probe_review_not_an_object'),
    (dict(reason='x'), 'probe_review_tasks_invalid'), (dict(tasks=[]), 'probe_review_tasks_invalid'),
    (dict(tasks=dict(dev=dict(verdict='fair'))), 'probe_review_task_missing'),  # held is not answered
    (dict(tasks=dict(dev=dict(verdict='fair'), held='fair')), 'probe_review_task_missing'),
    (dict(tasks=dict(dev=dict(verdict='fair'), held=dict(verdict='Fair'))), 'probe_review_verdict_invalid'),
    (dict(tasks=dict(dev=dict(verdict='fair'), held=dict())), 'probe_review_verdict_invalid'),
    (dict(tasks=dict(dev=dict(verdict='unfair'), held=dict(verdict='fair'))), 'probe_review_verdict_invalid'),
    (dict(tasks=dict(dev=dict(verdict='fair'), held=dict(verdict='fair')), reason=3), 'probe_review_reason_invalid'),
    ('x' * (probe_review.ANSWER_LIMIT + 1), 'probe_review_too_large')])
def test_anything_else_is_an_unusable_review(tmp_path, raw, cause):
    with pytest.raises(ValueError, match=f'^{cause}$'):
        probe_review.load(write(tmp_path, raw), SHOWN)


def test_a_missing_or_linked_answer_is_unusable(tmp_path):
    with pytest.raises(ValueError, match='^probe_review_not_written$'):
        probe_review.load(tmp_path / 'probe-review.json', SHOWN)
    link = tmp_path / 'linked.json'
    link.symlink_to(write(tmp_path, answer(dev=('fair', ''), held=('fair', ''))))
    with pytest.raises(ValueError, match='^probe_review_not_written$'):
        probe_review.load(link, SHOWN)


class Runner:
    """The runtime seam: records how the session was started and answers as told."""
    def __init__(self, text=None, outcome='completed'):
        self.text, self.outcome, self.started = text, outcome, []

    def preparation_command(self, agent, binary, plugin, skills, stage, parent, inherit=False, readable=()):
        self.started.append(dict(plugin=plugin, readable=readable, stage=stage))
        return ['agent'], {'HOME': 'x'}

    def execute_session(self, argv, cwd, timeout, stdin=None, *, env):
        self.cwd, self.stdin, self.budget = cwd, stdin, timeout
        if self.text is not None: (cwd / 'probe-review.json').write_text(self.text)
        return dict(stdout='{"type":"turn.completed","usage":{"output_tokens":4}}\n', stderr='', timeout=False, exit_code=0), 'not_staged'

    def environment_receipt(self, environment, inherited, run_root, credentials=None):
        return dict(names=sorted(environment))

    def trace_result(self, run, agent='codex'):
        return self.outcome, None


def run(tmp_path, runner, remaining=lambda: 100, claim=CLAIM, manifest=MANIFEST, made=(), reserve=0):
    output = tmp_path / 'run'
    output.mkdir(exist_ok=True)
    result, progress, calls, made = {}, [], [], list(made)
    instruction = probe_review.probe(object(), output, claim, manifest, 0, result, made, remaining, runner=runner, agent='codex', binary='codex',
                                     model_calls=calls, progress=lambda stage, **counts: progress.append((stage, counts)), reserve=reserve, elapsed=1.5)
    return instruction, result, made, calls, progress, output


def test_a_leaking_task_is_recorded_and_asks_for_one_repair_naming_it_and_its_quote(tmp_path):
    runner = Runner(answer(dev=('leaking', 'Follow docs/ci.md'), held=('fair', '')))
    instruction, result, made, calls, progress, output = run(tmp_path, runner)
    assert "- dev: 'Follow docs/ci.md'" in instruction and 'held' not in instruction and 'plain request' in instruction and 'Keep the fixture and the grader' in instruction
    assert result['probe_review'] == [dict(attempt=0, tasks=dict(dev=dict(verdict='leaking', quote='Follow docs/ci.md'), held=dict(verdict='fair', quote='')),
                                           reason='because', repair_requested=True)]
    assert [(row['reason'], row['stage'], row['outcome'], row['elapsed_seconds']) for row in made] == [('task_text_gives_the_rule_away', 'probe_review', 'requested', 1.5)]
    assert progress == [('probe_review', dict(attempt=1))] and [call['stage'] for call in calls] == ['probe_review']
    assert calls[0]['provider_reported']['usage'] == dict(output_tokens=4)
    # The session works outside the run output, with no AHL skills and no Read grant, and its files are numbered.
    assert not Path(runner.cwd).is_relative_to(output) and (runner.started[0]['plugin'], runner.started[0]['readable']) == (None, ())
    assert json.loads((output / 'probe-review/probe-review.json').read_text())['tasks']['dev']['verdict'] == 'leaking'
    assert (output / 'probe-review.jsonl').is_file() and (output / 'probe-review.environment.json').is_file()
    again = run(tmp_path, Runner(answer(dev=('fair', ''), held=('fair', ''))))
    assert (again[5] / 'probe-review-2.jsonl').is_file()


def test_when_every_task_is_fair_no_repair_is_asked_and_the_verdicts_are_recorded(tmp_path):
    instruction, result, made, *_ = run(tmp_path, Runner(answer(dev=('fair', ''), held=('fair', ''))))
    assert instruction == '' and made == []
    assert result['probe_review'] == [dict(attempt=0, tasks=dict(dev=dict(verdict='fair', quote=''), held=dict(verdict='fair', quote='')), reason='because', repair_requested=False)]


def test_a_leaking_task_is_recorded_but_not_repaired_when_no_repair_is_affordable(tmp_path):
    raw = answer(dev=('leaking', 'Follow docs/ci.md'), held=('fair', ''))
    for kwargs in (dict(made=[dict(), dict()]), dict(remaining=lambda: 50, reserve=50)):
        instruction, result, made, *_ = run(tmp_path, Runner(raw), **kwargs)
        assert instruction == '' and len(made) == len(kwargs.get('made', ()))
        assert result['probe_review'][0]['repair_requested'] is False and result['probe_review'][0]['repair_skipped'] == 'not_affordable'
        assert result['probe_review'][0]['tasks']['dev']['verdict'] == 'leaking'


@pytest.mark.parametrize('runner,cause', [(Runner(), 'probe_review_not_written'), (Runner('not json'), 'probe_review_not_json'),
                                          (Runner('{}'), 'probe_review_tasks_invalid'),
                                          (Runner('{}', outcome='execution_error'), 'session_failed')])
def test_no_usable_review_proceeds_and_says_why(tmp_path, runner, cause):
    instruction, result, made, *_ = run(tmp_path, runner)
    assert instruction == '' and made == []
    assert result['probe_review'] == [dict(attempt=0, verdict='not_reviewed', reason=cause)]


def test_a_review_that_raises_an_os_error_proceeds(tmp_path):
    class Broken(Runner):
        def preparation_command(self, *args, **kwargs): raise PermissionError('no session folder')
    instruction, result, made, *_ = run(tmp_path, Broken())
    assert instruction == '' and result['probe_review'] == [dict(attempt=0, verdict='not_reviewed', reason='session_not_started')]


@pytest.mark.parametrize('kwargs,cause', [(dict(remaining=lambda: 0), 'budget_exhausted'), (dict(claim=dict(mechanisms=[], falsifiers=[])), 'no_claim'),
                                          (dict(manifest=dict(tasks=[TASKS[2]])), 'nothing_to_review')])
def test_no_session_starts_without_budget_a_claim_or_a_task_to_review(tmp_path, kwargs, cause):
    runner = Runner(answer(dev=('fair', ''), held=('fair', '')))
    instruction, result, made, calls, _, output = run(tmp_path, runner, **kwargs)
    assert instruction == '' and result['probe_review'] == [dict(attempt=0, verdict='not_reviewed', reason=cause)]
    assert runner.started == [] and calls == [] and not (output / 'probe-review').exists()


def test_the_reviewer_is_told_that_an_output_field_naming_the_behaviour_gives_it_away():
    # Regression (fixed-in: 0.236.1): live, tasks asked for output fields such as "staleBadge (whether the stale badge is
    # visible)" that were the claimed behaviour itself; the reviewer called them fair and the baseline passed by filling them in.
    from agent_harness_lab.evaluation import probe_review
    assert 'whose name or description is\nthe claimed behaviour itself' in probe_review.PROMPT
