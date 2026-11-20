"""`ahl improve` installs the evaluated winner through the rollback journal, on synthetic cooperative runs only.

Regression (fixed-in: 0.232.0): the favorable branch always ended `inconclusive` with a recommendation, so no model-proposed
change ever reached the project. The same fake agent plays proposer, construction and trials.
"""
import json
from contextlib import ExitStack

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.workflow import improve
from tests.integration.workflow import test_workflow as workflow_fixture

# The candidate fixes the development task and still fails the held-out task the baseline also fails.
PARTIAL = workflow_fixture.FAKE.replace("'done' if good or phase.endswith('regression')",
                                        "'done' if good and not phase.endswith('heldout') or phase.endswith('regression')")
assert PARTIAL != workflow_fixture.FAKE


@pytest.fixture
def cli(tmp_path, capsys):
    """Run `ahl COMMAND` on the synthetic project; returns (exit code, receipt or None, fixture)."""
    with ExitStack() as cleanups:
        base = workflow_fixture.WorkflowTests(cleanups.callback)

        def go(command='improve', *extra, agent=None, original=None):
            if agent is not None: base.agent.write_text(agent)
            if original is not None: (base.project / 'AGENTS.md').write_text(original)
            code = main([command, str(base.project), '--agent', 'codex', '--codex', str(base.agent), '--claude', 'missing',
                         '--timeout', '30', '--output', str(tmp_path / command), *extra])
            out = capsys.readouterr().out
            return code, json.loads(out) if out.strip() else None, base
        yield go


def test_the_evaluated_winner_is_installed_with_a_verified_receipt_and_a_working_rollback(cli):
    code, receipt, base = cli()
    assert (code, receipt['status'], receipt['changed_files']) == (0, 'applied', ['AGENTS.md'])
    assert (base.project / 'AGENTS.md').read_text() == 'good\n'
    application = receipt['application']
    assert (application['status'], application['authority'], application['certification']) == ('applied_verified', 'operator_invocation', 'not_established')
    assert application['basis'] == 'improvement_observed'
    assert 'recommendation' not in receipt and receipt['automatic_promotion'] is False
    assert receipt['decision']['application_eligible'] is False  # the evidence labels are untouched by the install
    assert receipt['rollback'].startswith(f'ahl rollback {receipt["deployment"]} --project ')
    assert main(['rollback', receipt['deployment'], '--project', str(base.project)]) == 0
    assert (base.project / 'AGENTS.md').read_text() == 'bad'


def test_a_candidate_that_gains_one_task_and_loses_none_is_installed_as_a_partial_improvement(cli):
    code, receipt, base = cli(agent=PARTIAL)
    assert (code, receipt['status']) == (0, 'applied')
    assert receipt['partial_improvement'] == dict(gained=['development'], still_failing=['heldout'])
    assert (base.project / 'AGENTS.md').read_text() == 'good\n'


def test_a_tie_is_installed_and_labelled_no_regression_observed(cli):
    # "No change" is justified only when the baseline scored better; on a tie the candidate goes in without an improvement claim.
    code, receipt, base = cli(original='good')
    assert (code, receipt['status'], receipt['application']['basis']) == (0, 'applied', 'no_regression_observed')
    assert 'partial_improvement' not in receipt and (base.project / 'AGENTS.md').read_text() == 'good\n'


def test_watch_and_the_programmatic_entry_keep_the_recommendation(cli):
    _, _, base = cli('watch', '--once')
    assert (base.project / 'AGENTS.md').read_text() == 'bad'
    result = improve(base.args())
    assert (result['status'], result['recommendation']) == ('inconclusive', 'candidate_requires_independent_certification')
    assert (base.project / 'AGENTS.md').read_text() == 'bad'


def test_the_proposer_of_an_installing_run_is_told_that_the_evaluation_decides(cli, tmp_path):
    # Regression (fixed-in: 0.232.0): with no verified outcome in the evidence the proposer stopped with no_change, so nothing was evaluated.
    seen = tmp_path / 'prompts'
    recording = workflow_fixture.FAKE.replace('prompt=sys.stdin.read()', f"prompt=sys.stdin.read(); open({str(seen)!r},'a').write(prompt)")
    assert recording != workflow_fixture.FAKE
    _, _, base = cli(agent=recording)
    assert 'the evaluation stage decides whether your change is kept' in seen.read_text() and 'If none is supported, write' not in seen.read_text()
    seen.unlink()
    (base.project / 'AGENTS.md').write_text('bad')
    improve(base.args())  # a run that only recommends keeps the earlier instruction
    assert 'If none is supported, write' in seen.read_text() and 'the evaluation stage decides' not in seen.read_text()
