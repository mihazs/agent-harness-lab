"""Fake-runtime matrix from a staged candidate to its terminal state, one row per supported surface operation (#77).

Every row builds the common change plan, applies through the journal only when the plan is resolved, then checks the
target bytes and the rollback. Rows that must not apply prove the target stayed untouched. Synthetic; nothing executes.
"""
import shutil
from unittest.mock import patch

import pytest

from agent_harness_lab.harness_change_plan import HarnessChangePlan, REFERENCE_NAMES
from agent_harness_lab.hooks_contract import contracts_for, stage
from agent_harness_lab.promotion.journal import Journal
from agent_harness_lab.sources import skill_validation
from agent_harness_lab.application_receipt import application_receipt
from agent_harness_lab.workflow import adopt, adopt_with_receipt, candidate_states, snapshot
from tests.unit.surfaces.test_hooks_contract import SCRIPT, SETTINGS, hooks

ENTRY = '---\nname: {name}\ndescription: Summarize release notes\n---\nRead [the guide](references/guide.md).\n'
ALPHA, BETA = '.claude/skills/alpha', '.claude/skills/beta'
SETTINGS_PATH = '.claude/settings.json'


def write(base, name, text):
    path = base / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def plan_for(original, proposed, contracts=()):
    return HarnessChangePlan.create(original, proposed, references={name: None for name in REFERENCE_NAMES},
        diagnosis=['synthetic'], falsifiers=['synthetic'], dependencies=[], controls=[], deferred=[], contracts=contracts)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'
    write(root, 'AGENTS.md', 'rules\n')
    write(root, f'{ALPHA}/SKILL.md', ENTRY.format(name='alpha'))
    write(root, f'{ALPHA}/references/guide.md', 'guide\n')
    (root / '.claude/settings.json').write_bytes(SETTINGS)
    with patch('agent_harness_lab.workflow.state_root', return_value=tmp_path / 'state'), \
            patch('agent_harness_lab.promotion.journal.ignored', return_value=False):
        yield root


class Run:
    """Baseline snapshot plus an editable candidate copy of one synthetic project."""

    def __init__(self, project):
        self.project, self.root = project, project.parent
        self.original = snapshot(project, self.root / 'baseline')
        shutil.copytree(self.root / 'baseline', self.root / 'candidate')
        self.candidate = self.root / 'candidate'

    def edit(self, name, text):
        write(self.candidate, name, text)
        return self

    def delete(self, name):
        shutil.rmtree(self.candidate / name) if (self.candidate / name).is_dir() else (self.candidate / name).unlink()
        return self

    def plan(self):
        self.proposed = candidate_states(self.candidate, self.original)
        return plan_for(self.original, self.proposed)

    def apply(self):
        plan = self.plan()
        assert not plan.unresolved_operations, plan.unresolved_operations
        return plan, adopt(self.project, self.root / 'baseline', self.candidate, self.original, self.proposed)

    def tree(self):
        return {str(p.relative_to(self.project)): p.read_bytes() for p in sorted(self.project.rglob('*')) if p.is_file()}


def journal_for(run):
    from agent_harness_lab.workflow import identity, state_root
    return Journal(run.project, state_root() / 'promotions' / identity(run.project))


ROWS = {
    'skill-create': (lambda r: r.edit(f'{BETA}/SKILL.md', ENTRY.format(name='beta')).edit(f'{BETA}/references/guide.md', 'g\n'),
                     [f'{BETA}/SKILL.md', f'{BETA}/references/guide.md'], 'create'),
    'skill-update': (lambda r: r.edit(f'{ALPHA}/references/guide.md', 'better guide\n'), [f'{ALPHA}/references/guide.md'], 'update'),
    'skill-remove': (lambda r: r.delete(ALPHA), [f'{ALPHA}/SKILL.md', f'{ALPHA}/references/guide.md'], 'remove'),
    'instruction-rewrite': (lambda r: r.edit('AGENTS.md', 'tighter rules\n'), ['AGENTS.md'], 'update'),
    'instruction-remove': (lambda r: r.delete('AGENTS.md'), ['AGENTS.md'], 'remove'),
}


@pytest.mark.parametrize('row', [pytest.param(name, id=name) for name in ROWS])
def test_resolved_operations_reach_the_target_with_a_receipt_and_roll_back(project, row):
    stage_row, names, action = ROWS[row]
    run = stage_row(Run(project))
    before = run.tree()
    plan, applied = run.apply()
    assert sorted(applied) == sorted(names)
    assert {o.action for o in plan.operations} == {action}
    assert plan.document()['adoption_authority'] is False  # a plan never carries authority; adopt() was explicit
    for name in names:
        path = project / name
        want = run.candidate / name
        assert path.read_bytes() == want.read_bytes() if want.exists() else not path.exists()
    journal = journal_for(run)
    deployment = journal.active
    assert journal.load(deployment)['status'] == 'committed'
    journal.rollback(deployment)
    after_rollback = run.tree()
    assert after_rollback == before


def test_no_change_candidate_is_a_no_op_that_leaves_the_target_untouched(project):
    run = Run(project)
    plan = run.plan()
    assert plan.operations == () and plan.document()['disposition'] == 'no_change'
    assert adopt(project, run.root / 'baseline', run.candidate, run.original, run.proposed) == []
    assert journal_for(run).active is None


def test_mixed_change_set_with_an_unresolved_operation_is_not_applied(project):
    run = Run(project).edit('AGENTS.md', 'tighter rules\n').edit('.claude/agents/review.md', '---\nname: r\ndescription: d\nhooks: {}\n---\n')
    before = run.tree()
    with pytest.raises(ValueError):
        run.apply()
    assert run.tree() == before


def test_unvalidated_skill_bundle_is_blocked_with_the_target_untouched(project):
    run = Run(project).edit(f'{ALPHA}/SKILL.md', 'no frontmatter\n')
    before = run.tree()
    assert skill_validation.review_tree(run.root / 'baseline', run.candidate, run.original, run.plan() and run.proposed).verdict != 'adoptable'
    with pytest.raises(ValueError, match='nothing was applied'):
        adopt(project, run.root / 'baseline', run.candidate, run.original, run.proposed)
    assert run.tree() == before


def test_stale_source_change_invalidates_application_and_preserves_user_work(project):
    run = Run(project).edit('AGENTS.md', 'tighter rules\n')
    run.plan()
    (project / 'AGENTS.md').write_text('user edit\n')
    with pytest.raises(ValueError, match='changed before adoption'):
        adopt(project, run.root / 'baseline', run.candidate, run.original, run.proposed)
    assert (project / 'AGENTS.md').read_text() == 'user edit\n'


def test_candidate_modified_after_review_invalidates_application(project):
    run = Run(project).edit('AGENTS.md', 'tighter rules\n')
    run.plan()
    write(run.candidate, 'AGENTS.md', 'swapped after review\n')
    with pytest.raises(ValueError, match='candidate changed'):
        adopt(project, run.root / 'baseline', run.candidate, run.original, run.proposed)
    assert (project / 'AGENTS.md').read_text() == 'rules\n'


@pytest.mark.parametrize('row', [pytest.param(name, id=name) for name in ROWS])
def test_receipt_rereads_the_target_and_claims_no_activation_or_improvement(project, row):
    stage_row, names, action = ROWS[row]
    run = stage_row(Run(project))
    run.plan()
    receipt = adopt_with_receipt(project, run.root / 'baseline', run.candidate, run.original, run.proposed)
    assert receipt['status'] == 'applied_verified' and receipt['rollback'] == 'rollback_eligible'
    assert [o['path'] for o in receipt['operations']] == sorted(names)
    assert {o['action'] for o in receipt['operations']} == {action} and all(o['verified'] for o in receipt['operations'])
    assert (receipt['activation'], receipt['behavioral_effect']) == ('unknown', 'not_established')
    assert receipt['deployment'] == journal_for(run).active


def test_receipt_flags_a_target_edited_after_commit_and_a_replay_is_refused(project):
    run = Run(project).edit('AGENTS.md', 'tighter rules\n')
    run.plan()
    receipt = adopt_with_receipt(project, run.root / 'baseline', run.candidate, run.original, run.proposed)
    with pytest.raises(ValueError, match='changed before adoption'):  # replay never writes or journals twice
        adopt(project, run.root / 'baseline', run.candidate, run.original, run.proposed)
    assert journal_for(run).active == receipt['deployment']
    (project / 'AGENTS.md').write_text('user edit after\n')
    journal = journal_for(run)
    again = application_receipt(journal, journal.load(receipt['deployment']))
    assert again['status'] == 'applied_unverified' and not again['operations'][0]['verified']
    assert again['rollback'] == 'conflict'


class HookRun:
    def __init__(self, project):
        self.project = project
        self.journal = Journal(project, project.parent / 'private/journal')
        self.script, self.deployments = b'#!/bin/sh\nexit 0\n', []

    def staged(self, value, scripts):
        before, after = stage(self.project, SETTINGS_PATH, value, scripts)
        return before, after, contracts_for(self.project, before, after)

    def apply(self, before, after, contracts):
        (contract,) = contracts
        states = lambda rows: {n: (r['sha256'], r['mode']) if r else None for n, r in rows.items()}
        plan = plan_for({n: s for n, s in states(before).items() if s}, {n: s for n, s in states(after).items() if s}, contracts)
        assert not plan.unresolved_operations
        record = self.journal.prepare(before, after, {'source': 'synthetic'}, hook_contract=contract)
        self.deployments.append(record['deployment'])
        return plan, self.journal.execute(record['deployment'])


def test_hook_create_and_update_apply_only_through_a_passed_contract_and_roll_back(project):
    run = HookRun(project)
    settings = project / SETTINGS_PATH
    before, after, contracts = run.staged(hooks(), {SCRIPT: (run.script, 0o755)})
    _, receipt = run.apply(before, after, contracts)
    assert receipt['status'] == 'committed' and (project / SCRIPT).read_bytes() == run.script
    run.journal.rollback(run.deployments[0])
    assert settings.read_bytes() == SETTINGS and not (project / SCRIPT).exists()
    before, after, contracts = run.staged(hooks(), {SCRIPT: (run.script, 0o755)})
    run.apply(before, after, contracts)
    created = settings.read_bytes()
    before, after, contracts = run.staged(hooks(matcher='Edit|Write'), {})
    _, receipt = run.apply(before, after, contracts)
    assert receipt['status'] == 'committed' and settings.read_bytes() != created
    run.journal.rollback(run.deployments[-1])
    assert settings.read_bytes() == created


def test_hook_change_without_a_passed_contract_stays_unresolved_and_unwritten(project):
    run = HookRun(project)
    before, after, contracts = run.staged(hooks(), {SCRIPT: (run.script, 0o777)})  # world-writable script: blocked
    (contract,) = contracts
    assert contract.status == 'blocked'
    with pytest.raises(ValueError):
        run.journal.prepare(before, after, hook_contract=contract)
    with pytest.raises(ValueError):
        run.journal.prepare(before, after)  # no admission at all
    assert (project / SETTINGS_PATH).read_bytes() == SETTINGS and not (project / SCRIPT).exists()
    unresolved = plan_for({n: (r['sha256'], r['mode']) for n, r in before.items() if r},
                          {n: (r['sha256'], r['mode']) for n, r in after.items() if r}, contracts)
    assert unresolved.unresolved_operations
