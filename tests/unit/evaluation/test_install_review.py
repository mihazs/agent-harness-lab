"""The install reviewer's input is bounded, its answer is validated, and a candidate without a consistent review is never installed.

Synthetic folders and a stub runtime only; no model is called.
"""
import json
from pathlib import Path

import pytest

from agent_harness_lab.evaluation import install_review

STATE = {'before': ('a' * 64, 0o644), 'after': ('b' * 64, 0o644)}


def folders(tmp_path, baseline, candidate):
    for name, files in (('baseline', baseline), ('candidate', candidate)):
        for path, text in files.items():
            (tmp_path / name / path).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / name / path).write_text(text)
    return tmp_path / 'baseline', tmp_path / 'candidate'


def test_the_input_holds_each_changed_file_whole_in_both_arms_and_a_unified_diff(tmp_path):
    baseline, candidate = folders(tmp_path, {'AGENTS.md': 'one\ntwo\n', 'x/SKILL.md': 'old\n'}, {'AGENTS.md': 'one\nthree\n', 'x/SKILL.md': 'old\n', 'new.md': 'added\n'})
    material, truncated = install_review.review_input(baseline, candidate, ['new.md', 'AGENTS.md'])
    assert not truncated
    assert '===== baseline: AGENTS.md =====\none\ntwo\n' in material and '===== candidate: AGENTS.md =====\none\nthree\n' in material
    assert '--- baseline/AGENTS.md\n+++ candidate/AGENTS.md\n' in material and '-two\n+three\n' in material
    assert '===== baseline: new.md =====\n(absent)\n' in material and '===== candidate: new.md =====\nadded\n' in material
    assert 'x/SKILL.md' not in material  # an unchanged file is not part of the review


def test_one_file_and_the_whole_input_are_bounded_and_say_so(tmp_path, monkeypatch):
    monkeypatch.setattr(install_review, 'PART_LIMIT', 50)
    baseline, candidate = folders(tmp_path, {'a.md': 'x' * 500}, {'a.md': 'y' * 500})
    material, truncated = install_review.review_input(baseline, candidate, ['a.md'])
    assert truncated and material.count('[truncated: 450 more characters]') == 2 and 'x' * 51 not in material and 'y' * 51 not in material
    material, truncated = install_review.review_input(baseline, candidate, ['a.md'], limit=120)
    assert truncated and '[omitted: the review input limit was reached]' in material and len(material) < 120 + 3 * 80


def write(tmp_path, raw):
    path = tmp_path / 'review.json'
    path.write_text(raw if isinstance(raw, str) else json.dumps(raw))
    return path


def test_a_valid_answer_keeps_only_what_its_verdict_needs(tmp_path):
    assert install_review.load(write(tmp_path, dict(verdict='consistent', reason=' fine ', conflicts=['ignored']))) == dict(verdict='consistent', reason='fine')
    quoted = [f'rule {number}' for number in range(30)]
    answer = install_review.load(write(tmp_path, dict(verdict='contradicts', reason='r' * 5000, conflicts=quoted)))
    assert answer['verdict'] == 'contradicts' and len(answer['reason']) == install_review.TEXT_LIMIT and answer['conflicts'] == quoted[:install_review.CONFLICT_LIMIT]
    assert install_review.load(write(tmp_path, dict(verdict='test_specific', reason='magic name'))) == dict(verdict='test_specific', reason='magic name', conflicts=[])


@pytest.mark.parametrize('raw,cause', [
    ('{', 'review_not_json'), ('[]', 'review_not_an_object'), ('"consistent"', 'review_not_an_object'), ('\x00', 'review_not_json'),
    (dict(reason='x'), 'review_verdict_invalid'), (dict(verdict='Consistent', reason='x'), 'review_verdict_invalid'), (dict(verdict=None, reason='x'), 'review_verdict_invalid'),
    (dict(verdict='consistent'), 'review_reason_invalid'), (dict(verdict='consistent', reason='  '), 'review_reason_invalid'), (dict(verdict='consistent', reason=3), 'review_reason_invalid'),
    (dict(verdict='contradicts', reason='x', conflicts='a rule'), 'review_conflicts_invalid'), (dict(verdict='contradicts', reason='x', conflicts=[1]), 'review_conflicts_invalid'),
    ('x' * (install_review.ANSWER_LIMIT + 1), 'review_too_large')])
def test_anything_else_is_an_unusable_review(tmp_path, raw, cause):
    with pytest.raises(ValueError, match=f'^{cause}$'):
        install_review.load(write(tmp_path, raw))


def test_a_missing_or_linked_answer_is_unusable(tmp_path):
    with pytest.raises(ValueError, match='^review_not_written$'):
        install_review.load(tmp_path / 'review.json')
    target = write(tmp_path, dict(verdict='consistent', reason='x'))
    link = tmp_path / 'linked.json'
    link.symlink_to(target)
    with pytest.raises(ValueError, match='^review_not_written$'):
        install_review.load(link)


class Runner:
    """The runtime seam: records how the session was started and answers as told."""
    def __init__(self, answer=None, outcome='completed', timeout=False):
        self.answer, self.outcome, self.timeout, self.started = answer, outcome, timeout, []

    def preparation_command(self, agent, binary, plugin, skills, stage, parent, inherit=False, readable=()):
        self.started.append(dict(plugin=plugin, readable=readable, stage=stage))
        return ['agent'], {'HOME': 'x'}

    def execute_session(self, argv, cwd, timeout, stdin=None, *, env):
        self.cwd, self.stdin, self.budget = cwd, stdin, timeout
        if self.answer is not None: (cwd / 'review.json').write_text(self.answer)
        return dict(stdout='{"type":"turn.completed","usage":{"output_tokens":4}}\n', stderr='', timeout=self.timeout, exit_code=0), 'not_staged'

    def environment_receipt(self, environment, inherited, run_root, credentials=None):
        return dict(names=sorted(environment))

    def trace_result(self, run, agent='codex'):
        return self.outcome, None


def gate(tmp_path, runner, remaining=lambda: 100):
    output = tmp_path / 'run'
    output.mkdir()
    baseline, candidate = folders(tmp_path, {'AGENTS.md': 'bad\n'}, {'AGENTS.md': 'good\n'})
    result = dict(status='inconclusive', recommendation='candidate_requires_independent_certification', confirmation=dict(outcome='confirmed'))
    progress, calls = [], []
    ok = install_review.passes(result, object(), output, baseline, candidate, {'AGENTS.md': STATE['before']}, {'AGENTS.md': STATE['after']},
                               runner=runner, agent='codex', binary='codex', remaining=remaining, model_calls=calls,
                               progress=lambda stage, **counts: progress.append(stage))
    return ok, result, calls, progress, output


def test_a_consistent_review_lets_the_install_go_on_and_its_session_is_isolated_and_charged(tmp_path):
    runner = Runner(json.dumps(dict(verdict='consistent', reason='fits')))
    ok, result, calls, progress, output = gate(tmp_path, runner)
    assert ok and result['install_review'] == dict(verdict='consistent', reason='fits') and result['status'] == 'inconclusive'
    assert progress == ['install_review'] and [call['stage'] for call in calls] == ['install_review']
    assert calls[0]['provider_reported']['usage'] == dict(output_tokens=4)
    # Regression (fixed-in: 0.234.1): the reviewer worked inside the run output, one relative path away from tasks and results.
    assert not Path(runner.cwd).is_relative_to(output) and not Path(runner.cwd).exists() and runner.budget == 100  # capped by what the run has left
    assert json.loads((output / 'install-review/review.json').read_text())['verdict'] == 'consistent'
    assert (runner.started[0]['plugin'], runner.started[0]['readable']) == (None, ())  # no AHL skills and no Read grant outside its own folder
    assert '===== candidate: AGENTS.md =====\ngood\n' in runner.stdin and str(output) not in runner.stdin
    assert (output / 'install-review.jsonl').is_file() and (output / 'install-review.environment.json').is_file()


@pytest.mark.parametrize('verdict', ['contradicts', 'test_specific'])
def test_a_review_that_is_not_consistent_ends_the_run_without_a_recommendation(tmp_path, verdict):
    ok, result, _, _, _ = gate(tmp_path, Runner(json.dumps(dict(verdict=verdict, reason='no', conflicts=['a rule']))))
    assert not ok and (result['status'], result['reason']) == ('no_change', 'candidate_inconsistent') and 'recommendation' not in result
    assert result['install_review'] == dict(verdict=verdict, reason='no', conflicts=['a rule']) and result['confirmation'] == dict(outcome='confirmed')


@pytest.mark.parametrize('runner,cause', [(Runner(), 'review_not_written'), (Runner('{}'), 'review_verdict_invalid'),
                                          (Runner('{}', outcome='execution_error'), 'session_failed'),
                                          (Runner('{}', outcome='execution_error', timeout=True), 'session_timeout')])
def test_no_usable_review_fails_closed(tmp_path, runner, cause):
    ok, result, _, _, _ = gate(tmp_path, runner)
    assert not ok and (result['status'], result['reason']) == ('no_change', 'candidate_inconsistent')
    assert result['install_review'] == dict(verdict='not_reviewed', reason=cause)


def test_without_budget_no_session_starts_and_the_candidate_is_not_installed(tmp_path):
    runner = Runner(json.dumps(dict(verdict='consistent', reason='fits')))
    ok, result, calls, _, output = gate(tmp_path, runner, remaining=lambda: 0)
    assert not ok and result['install_review'] == dict(verdict='not_reviewed', reason='budget_exhausted') and result['reason'] == 'candidate_inconsistent'
    assert runner.started == [] and calls == [] and not (output / 'install-review').exists()
