"""A new synthetic harness file invalidates a recommendation made from an older snapshot."""
import pytest

from agent_harness_lab import cli
from agent_harness_lab.workflow import improve
from tests.integration.workflow.test_workflow import wf as wf


@pytest.mark.parametrize('added',['CLAUDE.md','.agents/skills/new-skill/SKILL.md',
                                  '.agents/skills/existing/references/new.md'])
def test_new_harness_file_during_evaluation_blocks_stale_recommendation(wf,monkeypatch,added):
    if '/existing/' in added:
        entry = wf.project/'.agents/skills/existing/SKILL.md'
        entry.parent.mkdir(parents=True); entry.write_text('---\nname: existing\ndescription: Synthetic existing skill\n---\n')
    original = cli.evaluator
    def evaluator():
        runner = original(); evaluate = runner.evaluate
        def add_after_evaluation(options):
            result = evaluate(options)
            path = wf.project/added; path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text('synthetic concurrent user content\n')
            return result
        runner.evaluate = add_after_evaluation
        return runner
    monkeypatch.setattr(cli,'evaluator',evaluator)
    with pytest.raises(ValueError,match='Project harness changed during evaluation'):
        improve(wf.args())
    assert (wf.project/added).read_text() == 'synthetic concurrent user content\n'
    assert (wf.project/'AGENTS.md').read_text() == 'bad'
