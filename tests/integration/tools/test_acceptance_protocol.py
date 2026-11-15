"""The real-project acceptance protocol of docs/real-project-acceptance.md runs end to end on a synthetic project (#808).

Each step goes through scripts/isolated_project_check.py around `ahl improve`, with the options the how-to gives, so a change
that breaks the documented procedure fails here before anyone points it at a real repository. The project, its recorded
sessions and the stub runtime are built under tmp_path with pinned dates; nothing reads a network, a model or a real home.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

import agent_harness_lab
from tests.integration.workflow.test_vacuity_end_to_end import Mixed

ROOT = Path(__file__).resolve().parents[3]
CHECK = ROOT / 'scripts/isolated_project_check.py'
HOW_TO = ROOT / 'docs/real-project-acceptance.md'
PINNED = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.invalid', 'GIT_AUTHOR_DATE': '2020-01-01T00:00:00Z',
          'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.invalid', 'GIT_COMMITTER_DATE': '2020-01-01T00:00:00Z',
          'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull}
PINNED_DAY = 1767225600  # 2026-01-01T00:00:00Z


def git(repo, *args):
    return subprocess.run(['git', *args], cwd=repo, env={**os.environ, **PINNED}, check=True, capture_output=True, text=True).stdout.strip()


class Protocol(Mixed):
    """A committed synthetic project, three failing recorded sessions under its path and a private output directory."""

    def __init__(self, root, monkeypatch, runtime='codex'):
        super().__init__(root, monkeypatch, runtime)
        git(root, 'init', '-q', '-b', 'main', str(self.project))
        git(self.project, 'add', '.')
        git(self.project, 'commit', '-qm', 'initial')
        self.private, self.temporary = root / 'private', root / 'tmp'
        self.private.mkdir(mode=0o700)
        self.temporary.mkdir()
        monkeypatch.setenv('TMPDIR', str(self.temporary))
        monkeypatch.setenv('PYTHONPATH', str(Path(agent_harness_lab.__file__).parents[1]))
        monkeypatch.setenv('AHL_STATE_HOME', str(self.private / 'state'))  # the how-to keeps run directories beside the receipts
        for name in ('a', 'b', 'c'):
            self.session(name)

    def script(self):
        """The budget stub plus `candidate-worse`: the edited harness fails every task the baseline passed."""
        worse = "    if trial == 'candidate-worse': p('artifact').write_text('wrong' if good else 'done'); print(done); sys.exit(0)\n"
        return super().script().replace("    wrong = trial in", worse + "    wrong = trial in", 1)

    def session(self, name):
        rows = [dict(type='session_meta', cwd=str(self.project), payload=dict(id=name, cwd=str(self.project)))]
        for number in range(2):
            rows += [dict(type='response_item', payload=dict(type='function_call', name='shell', call_id=f'c{number}')),
                     dict(type='response_item', payload=dict(type='function_call_output', call_id=f'c{number}', output=dict(exit_code=1)))]
        path = self.root / 'codex/sessions' / f'{name}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        os.utime(path, (PINNED_DAY, PINNED_DAY))

    def step(self, name, *arguments, history_from=True, patch=None, shared_state=True):
        """One protocol step as the how-to runs it; returns the isolated-check receipt and the `improve` receipt."""
        command = [sys.executable, str(CHECK), str(self.project), '--ref', 'HEAD', '--timeout', '300', '--inherit-home',
                   *(['--pass-env', 'AHL_STATE_HOME'] if shared_state else []), '--pass-env', 'CODEX_HOME', '--pass-env', 'PYTHONPATH',
                   '--receipt', str(self.private / f'{name}.isolated.json'), '--log-dir', str(self.private / name),
                   *(['--patch', str(patch)] if patch else []),
                   '--', sys.executable, '-m', 'agent_harness_lab', 'improve', '{worktree}',
                   '--history', *(['--history-from', str(self.project)] if history_from else []),
                   '--agent', self.runtime, '--timeout', '120', *arguments]
        done = subprocess.run(command, capture_output=True, text=True, timeout=600)
        isolated = json.loads((self.private / f'{name}.isolated.json').read_text())
        assert isolated['command_status'] == done.returncode, done.stderr
        output = (self.private / name / 'stdout.txt').read_text()
        return isolated, json.loads(output) if output.strip() else None


@pytest.fixture
def protocol(tmp_path, monkeypatch):
    return Protocol(tmp_path, monkeypatch)


def assert_untouched(protocol, isolated):
    """The isolation verdict every step must carry: nothing in the real checkout moved."""
    assert (isolated['verdict'], isolated['drift']) == ('unchanged', [])
    assert git(protocol.project, 'status', '--porcelain') == '' and len(git(protocol.project, 'worktree', 'list').splitlines()) == 1


def test_first_use_with_recorded_history_ends_with_a_typed_reason_and_a_remediation_and_leaves_the_checkout_unchanged(protocol):
    isolated, receipt = protocol.step('first-use')

    assert_untouched(protocol, isolated)
    assert receipt['analysis']['findings'] > 0  # usable history: a bare no_change here would be the failure
    assert (receipt['status'], receipt['reason']) == ('blocked', 'no_outcome_evidence')
    assert receipt['remediation'] and receipt['next_steps'] and 'plan' not in receipt
    assert {row['reason'] for row in receipt['history']['capture_reasons']} == {'source_project_path_differs'}


def test_first_use_with_a_hypothesis_ends_with_an_evidenced_candidate_that_is_never_called_improved(protocol):
    isolated, receipt = protocol.step('first-use-hypothesis', '--focus', 'synthetic hypothesis')

    assert_untouched(protocol, isolated)
    assert receipt['plan'] and receipt['recommendation'] == 'candidate_requires_independent_certification'
    assert 'improved' not in (receipt['status'], receipt.get('outcome')) and receipt['automatic_promotion'] is False


def test_before_and_after_are_scored_on_one_frozen_set_of_inputs_and_two_different_arms(protocol):
    _, receipt = protocol.step('before-after', '--focus', 'synthetic hypothesis')

    bindings, observed = receipt['decision']['bindings'], receipt['decision']['observed']
    assert re.fullmatch(r'[0-9a-f]{64}', bindings['manifest_sha256']) and re.fullmatch(r'[0-9a-f]{64}', bindings['results_sha256'])
    assert bindings['baseline_sha256'] != bindings['candidate_sha256']
    pairs = observed['pairs']
    assert observed['trials'] == 2 * observed['tasks'] and sum(pairs.values()) == observed['tasks'] and pairs['unknown'] == 0


def test_a_worse_candidate_carries_no_plan_and_no_recommendation(protocol):
    protocol.mode.write_text(json.dumps(dict(trial='candidate-worse')))

    isolated, receipt = protocol.step('before-after-worse', '--focus', 'synthetic hypothesis')

    assert_untouched(protocol, isolated)
    pairs = receipt['decision']['observed']['pairs']
    assert pairs['baseline_only_passed'] > pairs['candidate_only_passed'], 'the stub candidate really scored worse'
    assert not {'plan', 'recommendation'} & receipt.keys() and isolated['command_status'] != 0, receipt
    assert 'improved' not in (receipt['status'], receipt.get('outcome'))


def test_a_worse_candidate_ends_blocked_as_candidate_regressed_with_an_investigation_beside_the_receipt(protocol):
    protocol.mode.write_text(json.dumps(dict(trial='candidate-worse')))

    isolated, receipt = protocol.step('before-after-regressed', '--focus', 'synthetic hypothesis')

    assert_untouched(protocol, isolated)
    assert (receipt['status'], receipt['reason'], isolated['command_status']) == ('blocked', 'candidate_regressed', 3)
    assert (receipt['before_after']['verdict'], receipt['before_after']['adoption_blocked']) == ('candidate_regressed', True)
    investigation = Path(receipt['investigation']['path'])
    assert investigation.parent == Path(receipt['output']) and (investigation.parent / 'workflow.json').is_file()
    assert str(investigation).startswith(str(protocol.private)), 'the record stays under the private state home'
    assert hashlib.sha256(investigation.read_bytes()).hexdigest() == receipt['investigation']['sha256']
    assert json.loads(investigation.read_text())['cause']['class'] == receipt['investigation']['cause'] == 'candidate_caused'


def test_a_candidate_that_is_not_worse_is_never_blocked_and_leaves_no_investigation(protocol):
    isolated, receipt = protocol.step('before-after-not-worse', '--focus', 'synthetic hypothesis')

    assert_untouched(protocol, isolated)
    assert receipt.get('reason') != 'candidate_regressed' and receipt['before_after']['adoption_blocked'] is False
    assert 'investigation' not in receipt and not list((protocol.private / 'state').rglob('investigation.json'))


def test_a_vacuous_evaluation_is_labelled_and_never_reported_as_an_improvement(protocol):
    protocol.mode.write_text(json.dumps(dict(trial='mixed-unknown')))

    isolated, receipt = protocol.step('no-fake-results', '--focus', 'synthetic hypothesis')

    assert_untouched(protocol, isolated)
    assert (receipt['status'], receipt['reason']) == ('inconclusive', 'evaluation_vacuous')
    assert receipt['vacuity']['vacuous'] is True and receipt['vacuity']['improvement_allowed'] is False
    assert not {'plan', 'recommendation'} & receipt.keys()


def test_every_reported_trial_count_traces_to_the_recorded_results_file(protocol):
    _, receipt = protocol.step('trace', '--focus', 'synthetic hypothesis')

    bindings = receipt['decision']['bindings']
    results = Path(bindings['evaluation']) / 'results.json'
    assert str(results).startswith(str(protocol.private)), 'the evaluation folder survives under the private state home'
    assert len(json.loads(results.read_text())['trials']) == receipt['decision']['observed']['trials']
    assert hashlib.sha256(results.read_bytes()).hexdigest() == bindings['results_sha256']


def test_an_edit_between_two_runs_reaches_the_second_baseline_and_never_the_real_checkout(protocol):
    first_isolated, first = protocol.step('run-1', '--focus', 'synthetic hypothesis')
    (protocol.project / 'AGENTS.md').write_text('bad\nan edit between the runs\n')
    patch = protocol.root / 'edit.patch'
    patch.write_text(git(protocol.project, 'diff') + '\n')
    git(protocol.project, 'checkout', '-q', '--', 'AGENTS.md')

    second_isolated, second = protocol.step('run-2', '--focus', 'synthetic hypothesis', patch=patch)

    assert_untouched(protocol, first_isolated)
    assert_untouched(protocol, second_isolated)
    assert (protocol.project / 'AGENTS.md').read_text() == 'bad\n' and 'AGENTS.md' in second_isolated['changed_files']
    assert first['decision']['bindings']['baseline_sha256'] != second['decision']['bindings']['baseline_sha256']


def edit_patch(protocol, text):
    """A patch that rewrites AGENTS.md, made in the real checkout and undone there, so only the isolated worktree holds it."""
    (protocol.project / 'AGENTS.md').write_text(text)
    patch = protocol.root / 'edit.patch'
    patch.write_text(git(protocol.project, 'diff') + '\n')
    git(protocol.project, 'checkout', '-q', '--', 'AGENTS.md')
    return patch


@pytest.mark.parametrize(('edit', 'disposition'), [(None, 'ignored'), ('good\n', 'applied'), ('bad\nan edit between the runs\n', 'edited')],
                         ids=['ignored', 'applied', 'edited'])
def test_a_second_run_names_the_first_run_and_says_what_became_of_its_candidate(protocol, edit, disposition):
    _, first = protocol.step('incremental-1', '--focus', 'synthetic hypothesis')
    patch = edit_patch(protocol, edit) if edit else None

    isolated, second = protocol.step('incremental-2', '--focus', 'synthetic hypothesis', patch=patch)

    assert_untouched(protocol, isolated)
    block = second['incremental']
    assert 'incremental' not in first, 'the first run has no earlier run to name'
    assert (block['disposition'], block['previous']['run']) == (disposition, Path(first['output']).name)
    assert block['stages']['trials']['action'] == 'rerun', 'earlier trials are never reused as fresh execution'
    assert block['adoption_authority'] is block['automatic_promotion'] is block['publication_allowed'] is False


def test_a_second_run_that_evaluates_again_labels_the_earlier_score_historical_beside_its_own_fresh_trials(protocol):
    protocol.step('incremental-fresh-1', '--focus', 'synthetic hypothesis')

    _, second = protocol.step('incremental-fresh-2', '--focus', 'synthetic hypothesis')

    block = second['incremental']
    assert (block['historical']['label'], block['fresh']['label']) == ('historical_observation', 'fresh_evaluation')
    assert (Path(second['output']) / 'evaluation-0/results.json').is_file(), 'this run scored its own trials'


def test_a_second_run_that_evaluates_nothing_reports_the_earlier_score_as_historical_only(protocol):
    protocol.step('incremental-applied-1', '--focus', 'synthetic hypothesis')

    _, second = protocol.step('incremental-applied-2', '--focus', 'synthetic hypothesis', patch=edit_patch(protocol, 'good\n'))

    block = second['incremental']
    assert block['historical']['label'] == 'historical_observation' and block['fresh'] is None
    assert not {'plan', 'recommendation'} & second.keys() and not (Path(second['output']) / 'evaluation-0').exists()


def test_a_candidate_the_first_run_measured_worse_is_not_evaluated_again_by_the_second(protocol):
    protocol.mode.write_text(json.dumps(dict(trial='candidate-worse')))
    _, first = protocol.step('incremental-regressed-1', '--focus', 'synthetic hypothesis')

    isolated, second = protocol.step('incremental-regressed-2', '--focus', 'synthetic hypothesis')

    assert_untouched(protocol, isolated)
    assert first['reason'] == 'candidate_regressed'
    assert (second['status'], second['reason'], isolated['command_status']) == ('blocked', 'candidate_already_regressed', 3)
    assert second['previous_run'] == Path(first['output']).name
    block = second['incremental']
    assert block['previous']['candidate_regressed'] is True and block['stages']['trials']['action'] == 'skipped'
    assert block['stages']['evaluation_construction']['action'] == 'skipped'
    assert not (Path(second['output']) / 'evaluation-0').exists() and not {'plan', 'recommendation'} & second.keys()


def test_a_second_run_cannot_see_the_first_when_the_state_home_is_not_shared_between_the_checks(protocol):
    protocol.step('incremental-private-1', '--focus', 'synthetic hypothesis', shared_state=False)

    isolated, second = protocol.step('incremental-private-2', '--focus', 'synthetic hypothesis', shared_state=False)

    assert_untouched(protocol, isolated)
    assert 'incremental' not in second and 'previous_run' not in second
    assert not list((protocol.private / 'state').rglob('workflow.json')), 'the run directories went with the temporary state'


def test_a_worktree_without_its_own_history_ends_typed_when_the_main_checkout_is_not_named(protocol):
    isolated, receipt = protocol.step('stress-no-history', history_from=False)

    assert_untouched(protocol, isolated)
    assert (receipt['status'], receipt['reason']) == ('blocked', 'history_not_found_for_worktree') and receipt['remediation']


def test_a_missing_runtime_ends_typed_and_spends_nothing(protocol, monkeypatch):
    bare = protocol.root / 'bare-bin'  # only the tools the check itself needs, so no installed runtime can be found
    bare.mkdir()
    (bare / 'git').symlink_to(shutil.which('git'))
    monkeypatch.setenv('PATH', str(bare))

    isolated, receipt = protocol.step('stress-no-runtime')

    assert_untouched(protocol, isolated)
    assert (receipt['status'], receipt['reason']) == ('blocked', 'agent_runtime_unavailable') and not protocol.calls.exists()


def test_every_private_output_sits_under_one_directory_and_deleting_it_leaves_nothing_behind(protocol):
    protocol.step('first-use', '--focus', 'synthetic hypothesis')

    assert list((protocol.private / 'state/runs').glob('*/run-*/workflow.json')), 'run directories belong beside the receipts'
    assert list(protocol.temporary.iterdir()) == [], 'the check removes its own temporary worktree and state'
    shutil.rmtree(protocol.private)
    assert not protocol.private.exists() and git(protocol.project, 'worktree', 'list').count('\n') == 0
    assert git(protocol.project, 'status', '--porcelain') == ''


def synthetic_column(text):
    """The test names the how-to's mapping table lists in its last column."""
    return set(re.findall(r'`(test_\w+)`', text.split('## Run the synthetic equivalent', 1)[1]))


def test_the_how_to_names_only_synthetic_tests_that_exist():
    named = synthetic_column(HOW_TO.read_text())

    assert named and named <= {name for name, value in globals().items() if name.startswith('test_') and callable(value)}


@pytest.mark.parametrize('step', ['first-use', 'before-after', 'no-fake-results', 'incremental', 'stress'])
def test_the_how_to_covers_every_protocol_step_with_a_heading_and_a_result_row(step):
    text = HOW_TO.read_text()

    assert f'<a id="{step}"></a>' in text and f'| `{step}` |' in text


def test_the_how_to_holds_no_machine_specific_path():
    text = HOW_TO.read_text()

    assert not re.search(r'/home/|/Users/|/tmp/|/private/var/', text)


def test_the_opt_in_task_runs_the_synthetic_equivalent_when_no_repository_is_given():
    block = re.search(r'^  test:isolated:\n((?:    .*\n)+)', (ROOT / 'Taskfile.yml').read_text(), re.M).group(1)

    assert 'tests/integration/tools/test_acceptance_protocol.py' in block and 'scripts/isolated_project_check.py' in block
