"""A static contradiction is installed as a verified correction when it only fixes the cited lines (#1145), never as a measured gain.

Every trial passes in both arms, so the paired evaluation has no gap to measure and the behavioural result stays
`evaluation_no_improvement`. The same fake agent plays proposer, construction, trials and install reviewer.
"""
import json
from contextlib import ExitStack
from pathlib import Path

import pytest

from agent_harness_lab.audit.cli import main as audit_main
from agent_harness_lab.workflow import improve
from tests.integration.workflow import test_workflow as workflow_fixture
from tests.integration.workflow.test_workflow import WorkflowTests
from tests.support.profile_fixture import build, package

BAD = 'Use pnpm. Run `pnpm test`, then `pnpm run deploy`.\n'
HEAD, TAIL = '# Project\n', 'Keep changes small.\n'
FILES = {'package.json': package({'test': 'vitest run'}), 'pnpm-lock.yaml': '', 'AGENTS.md': HEAD + BAD + TAIL}
FIXED = HEAD + 'Use pnpm. Run `pnpm test`.\n' + TAIL
GAP_FILES = {'package.json': package({'test': 'vitest run'}), 'pnpm-lock.yaml': '', 'AGENTS.md': 'Keep changes small.\n'}

PASSES = "'done' if good or phase.endswith('regression')"
PROPOSES = "(candidate/'AGENTS.md').write_text('worse' if mode=='refine' and c['attempt']==0 else 'good\\n')"
assert workflow_fixture.FAKE.count(PASSES) == workflow_fixture.FAKE.count(PROPOSES) == 1


COUNTED = workflow_fixture.FAKE.replace("else:\n phase=p('phase').read_text()",
    "else:\n phase=p('phase').read_text(); count=p(@COUNTER@); seen=int(count.read_text()) if count.exists() else 0; count.write_text(str(seen+1))")
assert COUNTED != workflow_fixture.FAKE


def agent(passes="True", review='consistent', counter=None):
    """The proposer writes `candidate_text_<attempt>` (else `candidate_text`) of the project; a trial passes when `passes` holds."""
    text = (workflow_fixture.FAKE if counter is None else COUNTED.replace('@COUNTER@', repr(str(counter)))).replace(PASSES, f"'done' if ({passes})").replace(PROPOSES,
        "(candidate/'AGENTS.md').write_text(next(f.read_text() for f in (project/('candidate_text_%s'%c['attempt']),project/'candidate_text') if f.exists()))")
    return text.replace("'verdict':'consistent'", f"'verdict':'{review}'")


@pytest.fixture
def lane(monkeypatch, capsys):
    """run(candidate, ...): audit the synthetic project, then `ahl improve --from-audit` it as an installing run."""
    with ExitStack() as cleanups:
        wf = WorkflowTests(cleanups.callback)

        def run(candidate=FIXED, files=FILES, trial_agent=None, later=None, trials=None):
            build(wf.project.parent, files, name=wf.project.name)
            (wf.project/'.git').mkdir(exist_ok=True)
            (wf.project/'candidate_text').write_text(candidate)
            if later is not None: (wf.project/'candidate_text_1').write_text(later)
            wf.agent.write_text(trial_agent or agent())
            monkeypatch.setenv('XDG_STATE_HOME', str(wf.root/'audit-state'))
            assert audit_main([str(wf.project), '--json', '--improvement-recheck']) == 0
            (wf.root/'audit.json').write_text(capsys.readouterr().out)
            args = wf.args()
            args.command, args.from_audit = 'improve', wf.root/'audit.json'
            if trials: args.trial_limit = trials
            return improve(args)
        yield wf, run


def test_a_line_only_correction_is_installed_as_a_verified_correction_not_a_measured_gain(lane):
    wf, run = lane
    result = run()
    assert (result['status'], result['changed_files'], result['install_basis']) == ('applied', ['AGENTS.md'], 'verified_correction')
    assert (wf.project/'AGENTS.md').read_text() == FIXED
    assert result['application']['basis'] == 'verified_correction' and result['application']['certification'] == 'not_established'
    assert result['verified_correction']['status'] == 'installed' and result['install_review']['verdict'] == 'consistent'
    assert 'confirmation' not in result and 'recommendation' not in result and result['rollback'].startswith('ahl rollback ')
    assert '`verified_correction`' in (Path(result['output'])/'report.md').read_text()
    assert json.loads((Path(result['output'])/'workflow.json').read_text())['install_basis'] == 'verified_correction'


def test_the_attempt_that_corrected_the_finding_is_installed_when_a_later_one_drifts(lane):
    wf, run = lane
    result = run(later=FIXED + 'Always add a changelog entry.\n')
    assert result['status'] == 'applied' and (wf.project/'AGENTS.md').read_text() == FIXED
    assert result['verified_correction']['attempt'] == 0


@pytest.mark.parametrize('candidate, reason', [
    (FIXED + 'Always add a changelog entry.\n', 'edits_beyond_cited_lines'),
    (HEAD + 'Use pnpm. Run `pnpm test`.\n' + 'Be brief.\n', 'edits_beyond_cited_lines'),
    (HEAD + 'Use pnpm. Run `pnpm test`, then `pnpm run deploy` twice.\n' + TAIL, 'finding_not_resolved_or_introduced'),
    (HEAD + 'Use pnpm. Run `npm test`.\n' + TAIL, 'correction_adds_a_reference'),
    # fixed-in: a dead reference replaced by an existing command that does not do what the line says was installed
    (HEAD + 'Use pnpm. Run `pnpm test`; it also deploys via `pnpm run test`.\n' + TAIL, 'correction_adds_a_reference')])
def test_an_extra_edit_an_unresolved_or_an_introduced_finding_installs_nothing(lane, candidate, reason):
    wf, run = lane
    result = run(candidate)
    assert (result['status'], result['reason'], result['changed_files']) == ('no_change', 'evaluation_no_improvement', [])
    assert result['verified_correction']['reason'] == reason and 'install_basis' not in result
    assert (wf.project/'AGENTS.md').read_text() == FILES['AGENTS.md']


def test_a_gap_finding_never_uses_the_lane(lane):
    wf, run = lane
    result = run('Keep changes small. Use pnpm; run `pnpm test`.\n', files=GAP_FILES)
    assert (result['status'], result['reason']) == ('no_change', 'evaluation_no_improvement')
    assert result['verified_correction']['reason'] == 'target_not_a_contradiction' and result['changed_files'] == []
    assert (wf.project/'AGENTS.md').read_text() == GAP_FILES['AGENTS.md']


@pytest.mark.parametrize('verdict', ['contradicts', 'test_specific'])
def test_a_contradicting_or_test_specific_review_installs_nothing(lane, verdict):
    wf, run = lane
    result = run(trial_agent=agent(review=verdict))
    assert (result['status'], result['reason'], result['changed_files']) == ('no_change', 'evaluation_no_improvement', [])
    assert result['verified_correction']['reason'] == 'install_review_' + verdict and result['install_review']['verdict'] == verdict
    assert (wf.project/'AGENTS.md').read_text() == FILES['AGENTS.md']


def test_a_candidate_that_failed_a_task_the_baseline_passed_installs_nothing(lane):
    # The corrected file (the candidate arm) fails the development task in the first run of each evaluation (trials 0-5 and 24-29, after the
    # repeats of attempt 0), so the loss is not reproduced and the run still ends without a gain; the raw paired trials of the attempt still show it.
    wf, run = lane
    fails = f"not (p('AGENTS.md').read_text() == {FIXED!r} and phase.endswith('development') and (seen < 6 or 24 <= seen < 30))"
    result = run(trial_agent=agent(fails, counter=wf.root/'count'), trials=200)
    assert (result['status'], result['reason'], result['changed_files']) == ('no_change', 'evaluation_no_improvement', [])
    assert result['regression_check']['confirmed'] == [] and result['verified_correction']['reason'] == 'evaluation_regressed'
    assert (wf.project/'AGENTS.md').read_text() == FILES['AGENTS.md']


def test_a_run_without_a_profile_target_does_not_report_the_lane(lane):
    wf, run = lane
    assert 'verified_correction' not in improve(wf.args())


@pytest.mark.parametrize('trials, kept, fine', [
    ([('a', 'baseline', 'development', 'passed'), ('a', 'candidate', 'development', 'passed')], True, True),
    ([('a', 'baseline', 'development', 'passed'), ('a', 'candidate', 'development', 'failed')], True, False),
    ([('a', 'baseline', 'development', 'passed'), ('a', 'candidate', 'development', 'passed')], False, False),
    ([('a', 'baseline', 'regression', 'failed'), ('a', 'candidate', 'regression', 'failed')], True, False),
    ([('a', 'baseline', 'development', 'failed'), ('a', 'candidate', 'development', 'passed')], True, True)])
def test_the_paired_gate_reads_losses_and_regression_tasks(trials, kept, fine):
    from agent_harness_lab.evaluation.verified_correction import lost_nothing
    rows = [dict(task=task, version=version, split=split, status=status) for task, version, split, status in trials]
    assert lost_nothing(rows, kept) is fine


# fixed-in: a candidate that also corrected a second contradiction's line was refused as `edits_beyond_cited_lines`
def test_a_candidate_correcting_two_contradictions_at_once_is_still_a_verified_correction(lane):
    wf, run = lane
    second = 'Read `docs/gone.md` first.\n'
    files = dict(FILES, **{'AGENTS.md': HEAD + BAD + second + TAIL})
    fixed = HEAD + 'Use pnpm. Run `pnpm test`.\n' + 'Read the README first.\n' + TAIL
    result = run(fixed, files=files)
    assert (result['status'], result['install_basis']) == ('applied', 'verified_correction')
    assert (wf.project/'AGENTS.md').read_text() == fixed


# fixed-in: a correction whose first-run gain the repeats did not confirm ended `improvement_not_confirmed` without the lane
def test_a_correction_whose_gain_is_not_confirmed_still_installs_as_a_verified_correction(lane):
    wf, run = lane
    # The uncorrected file fails the development task in the first run only (trials 0-5); every repeat passes in both arms.
    flaky = f"not (p('AGENTS.md').read_text() != {FIXED!r} and phase.endswith('development') and seen < 6)"
    result = run(trial_agent=agent(flaky, counter=wf.root/'count'), trials=200)
    assert result['confirmation']['outcome'] == 'improvement_not_confirmed', result.get('confirmation')
    assert (result['status'], result['install_basis'], result['changed_files']) == ('applied', 'verified_correction', ['AGENTS.md'])
    assert (wf.project/'AGENTS.md').read_text() == FIXED
