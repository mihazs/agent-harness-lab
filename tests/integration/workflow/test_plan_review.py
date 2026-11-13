"""`ahl apply` binds an exact reviewed plan to its application and never turns approval into certification (#77).

Synthetic projects only: the deterministic rows come from a real `ahl improve --repair-contracts` run that is then
rolled back; the behavioral and forged rows are hand-staged runs. Nothing calls a model.
"""
import contextlib
import io
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_harness_lab import config
from agent_harness_lab.cli import main
from agent_harness_lab.harness_change_plan import attach, produce
from agent_harness_lab.promotion.journal import GuardedTransactionError, Journal
from agent_harness_lab import workflow
from agent_harness_lab.workflow import candidate_states, snapshot, write_json
from tests.support.one_call import one as one  # noqa: F401


def apply(*args):
    output, errors = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        code = main(['apply', *map(str, args)])
    return code, json.loads(output.getvalue()) if output.getvalue() else errors.getvalue() or None


def tree(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mode) for p in sorted(root.rglob('*')) if p.is_file()}


@pytest.fixture
def rolled_back(one):
    """A real contract-repair run whose repair was rolled back: the plan is complete and the project matches it again."""
    code, result = one.invoke('--repair-contracts')
    assert code == 0 and result['status'] == 'applied'
    assert main(['rollback', result['deployment'], '--project', str(one.project)]) == 0
    one.run, one.plan_sha256 = Path(result['output']), result['plan_sha256']
    one.rollback = journal(one).active  # the rollback record is the active deployment; an apply must not add another
    assert one.skill.read_bytes() == b'\xef\xbb\xbf' + one.valid
    return one


def journal(one):
    project = one.project.resolve()  # the journal is keyed by the resolved project path
    return Journal(project, workflow.state_root() / 'promotions' / workflow.identity(project))


def test_review_is_read_only_and_names_the_exact_plan(rolled_back):
    one = rolled_back
    project_before, run_before = tree(one.project), tree(one.run)
    code, packet = apply(one.run, '--project', one.project)
    assert code == 0
    assert packet['status'] == 'awaiting_review' and packet['blockers'] == []
    assert packet['plan_sha256'] == one.plan_sha256
    assert set(packet['binding'].values()) == {'matched'}
    [operation] = packet['operations']
    assert (operation['path'], operation['action'], operation['surface']) == ('.agents/skills/example/SKILL.md', 'update', 'skill')
    assert operation['lines'] == dict(added=1, removed=1) and operation['before'] != operation['after']
    assert packet['evaluation']['source'] == 'saved_report_declared_unverified'
    assert packet['certification'] == 'not_established' and packet['adoption_authority'] is False
    assert packet['authorize_with'].endswith(f'--authorize-plan {one.plan_sha256}')
    assert (tree(one.project), tree(one.run)) == (project_before, run_before)


def test_exact_authorization_applies_through_the_journal_then_replay_is_stale(rolled_back):
    one = rolled_back
    code, packet = apply(one.run, '--project', one.project, '--authorize-plan', one.plan_sha256)
    assert code == 0 and packet['status'] == 'applied_verified'
    assert one.skill.read_bytes() == one.valid and one.skill.stat().st_mode & 0o777 == 0o640
    assert packet['receipt']['operations'] == [dict(path='.agents/skills/example/SKILL.md', action='update',
        sha256=packet['receipt']['operations'][0]['sha256'], mode=0o640, verified=True)]
    assert packet['activation'] == 'unknown' and packet['behavioral_effect'] == 'not_established'
    deployment = journal(one).active
    record = journal(one).load(deployment)
    assert record['metadata'] == dict(source='reviewed_plan_apply', plan_sha256=one.plan_sha256)
    code, replay = apply(one.run, '--project', one.project, '--authorize-plan', one.plan_sha256)
    assert (code, replay['status']) == (3, 'stale')
    assert journal(one).active == deployment
    assert main(['rollback', deployment, '--project', str(one.project)]) == 0
    assert one.skill.read_bytes() == b'\xef\xbb\xbf' + one.valid


def test_authorization_naming_another_plan_is_refused_before_any_write(rolled_back):
    one = rolled_back
    before = tree(one.project)
    code, message = apply(one.run, '--project', one.project, '--authorize-plan', '0' * 64)
    assert code == 2 and 'different plan' in message
    assert tree(one.project) == before and journal(one).active == one.rollback


def interrupted_write(*, after):
    """Fail the journal's target write before the replace (the target is untouched) or after it (the target holds the repair)."""
    original = Journal._write_snapshot

    def write(self, *args, **kwargs):
        if after:
            original(self, *args, **kwargs)
        raise OSError(f'injected interruption {"after" if after else "before"} the target write')
    return patch.object(Journal, '_write_snapshot', write)


def unfinished(one):
    folder = journal(one).folder
    return [p.stem for p in folder.glob('*.json') if p.name != 'active.json' and json.loads(p.read_text())['status'] == 'applying']


def test_interruption_before_the_target_write_leaves_the_project_and_a_retry_applies_once(rolled_back):
    one = rolled_back
    with interrupted_write(after=False):
        code, message = apply(one.run, '--project', one.project, '--authorize-plan', one.plan_sha256)
    assert code == 1 and 'before the target write' in message
    assert one.skill.read_bytes() == b'\xef\xbb\xbf' + one.valid and journal(one).active == one.rollback
    [pending] = unfinished(one)
    code, packet = apply(one.run, '--project', one.project, '--authorize-plan', one.plan_sha256)
    assert (code, packet['status']) == (0, 'applied_verified') and one.skill.read_bytes() == one.valid
    assert journal(one).active == packet['receipt']['deployment'] != pending
    assert apply(one.run, '--project', one.project, '--authorize-plan', one.plan_sha256)[1]['status'] == 'stale'


def test_interruption_after_the_target_write_is_never_applied_twice_and_recovers_to_the_reviewed_bytes(rolled_back):
    one = rolled_back
    with interrupted_write(after=True):
        code, message = apply(one.run, '--project', one.project, '--authorize-plan', one.plan_sha256)
    assert code == 1 and 'after the target write' in message
    assert one.skill.read_bytes() == one.valid and journal(one).active == one.rollback
    [pending] = unfinished(one)
    code, packet = apply(one.run, '--project', one.project, '--authorize-plan', one.plan_sha256)
    assert (code, packet['status'], packet['binding']['project_inventory']) == (3, 'stale', 'changed')
    assert unfinished(one) == [pending] and journal(one).active == one.rollback
    with contextlib.redirect_stdout(io.StringIO()):
        assert main(['promotion', str(one.project), '--deployment', pending, '--action', 'recover']) == 0
    assert journal(one).active == pending and journal(one).load(pending)['metadata']['plan_sha256'] == one.plan_sha256
    assert one.skill.read_bytes() == one.valid and apply(one.run, '--project', one.project, '--authorize-plan', one.plan_sha256)[0] == 3
    assert main(['rollback', pending, '--project', str(one.project)]) == 0
    assert one.skill.read_bytes() == b'\xef\xbb\xbf' + one.valid


def test_a_guarded_transaction_that_needs_intervention_prints_its_receipt_and_exits_one(rolled_back):
    one = rolled_back
    error = GuardedTransactionError(SimpleNamespace(folder='journal'), dict(deployment='d' * 32), ['.agents/skills/example/SKILL.md'])
    output = io.StringIO()
    with patch('agent_harness_lab.workflow.adopt_with_receipt', side_effect=error), contextlib.redirect_stdout(output):
        assert main(['apply', str(one.run), '--project', str(one.project), '--authorize-plan', one.plan_sha256]) == 1
    receipt = json.loads(output.getvalue())
    assert (receipt['status'], receipt['deployment'], receipt['publication_allowed']) == ('requires_intervention', 'd' * 32, False)
    assert receipt['changed_files'] == ['.agents/skills/example/SKILL.md']


@pytest.mark.parametrize('change,field', [
    pytest.param(lambda one: one.skill.write_bytes(b'\xef\xbb\xbf' + one.valid + b'edited\n'), 'project_inventory', id='source-edit'),
    pytest.param(lambda one: (one.project / 'AGENTS.md').write_text('new\n'), 'project_inventory', id='harness-file-added'),
    pytest.param(lambda one: config.create(one.project, config.defaults()), 'configuration', id='configuration-added'),
    pytest.param(lambda one: (one.run / 'candidate/.agents/skills/example/SKILL.md').write_bytes(b'---\nname: example\ndescription: Other.\n---\n'),
                 'candidate', id='candidate-modified'),
    pytest.param(lambda one: (one.run / 'baseline/AGENTS.md').write_text('x\n'), 'baseline', id='baseline-modified'),
])
def test_a_changed_bound_input_makes_the_plan_stale_and_the_target_is_untouched(rolled_back, change, field):
    one = rolled_back
    change(one)
    before = tree(one.project)
    code, packet = apply(one.run, '--project', one.project, '--authorize-plan', one.plan_sha256)
    assert (code, packet['status'], packet['binding'][field]) == (3, 'stale', 'changed')
    assert tree(one.project) == before and journal(one).active == one.rollback


def test_a_different_project_is_stale(rolled_back, tmp_path):
    one = rolled_back
    other = tmp_path / 'other'
    shutil.copytree(one.project, other)
    code, packet = apply(one.run, '--project', other, '--authorize-plan', one.plan_sha256)
    assert (code, packet['status'], packet['binding']['project']) == (3, 'stale', 'changed')
    assert (other / '.agents/skills/example/SKILL.md').read_bytes() == b'\xef\xbb\xbf' + one.valid


def test_a_project_configuration_keeps_the_staged_repair_recommendation_only(one):
    settings = config.defaults()
    settings['harness_paths'] = ['.agents/skills/example/SKILL.md']
    config.create(one.project, settings)
    code, result = one.invoke('--repair-contracts')
    assert code == 3 and result['reason'] == 'configuration_requires_recommendation_only'
    before = tree(one.project)
    code, packet = apply(result['output'], '--project', one.project, '--authorize-plan', result['plan_sha256'])
    assert (code, packet['status']) == (3, 'blocked')
    assert packet['blockers'] == ['configuration_requires_recommendation_only']
    assert tree(one.project) == before and journal(one).active is None


def stage(one, kind, *, body=b'Keep the body intact.\n', states=None):
    """Hand-stage a run directory: baseline and candidate trees, a bound plan, and a forged-prerequisites report."""
    run = one.root / f'run-{kind}'
    run.mkdir()
    original = snapshot(one.project, run / 'baseline')
    shutil.copytree(run / 'baseline', run / 'candidate')
    (run / 'candidate/.agents/skills/example/SKILL.md').write_bytes(b'---\nname: example\ndescription: Use for synthetic checks.\n---\n' + body)
    proposed = candidate_states(run / 'candidate', original)
    args = SimpleNamespace(project=str(one.project))
    plan = produce(args, original, proposed, analysis={}, evidence={}, diagnosis=[], falsifiers=[], controls={}, deferred=[])
    write_json(run / 'change-plan.json', attach(dict(kind=kind), plan))
    write_json(run / 'workflow.json', dict(status='inconclusive', recommendation='candidate_requires_independent_certification',
        implementation_prerequisites=dict(mandatory_certification={'role_isolation': dict(status='verified')})))
    return run, plan.sha256


def test_behavioral_plan_stays_blocked_whatever_the_saved_report_or_authorization_claims(one):
    one.skill.write_bytes(one.valid)
    run, plan_sha256 = stage(one, 'behavioral_candidate', body=b'Rewritten body.\n')
    before = tree(one.project)
    code, review = apply(run, '--project', one.project)
    assert (code, review['status']) == (3, 'blocked')
    assert review['blockers'] == ['behavioral_application_requires_live_certification']
    assert review['evaluation']['certification_prerequisites'] == {'role_isolation': 'verified'}
    assert review['evaluation']['source'] == 'saved_report_declared_unverified' and 'authorize_with' not in review
    code, approved = apply(run, '--project', one.project, '--authorize-plan', plan_sha256)
    assert (code, approved['status'], approved['certification']) == (3, 'blocked', 'not_established')
    assert tree(one.project) == before and journal(one).active is None


def test_a_deterministic_label_does_not_apply_a_change_the_independent_check_does_not_reproduce(one):
    one.skill.write_bytes(one.valid)
    run, plan_sha256 = stage(one, 'deterministic_contract', body=b'Rewritten body.\n')
    before = tree(one.project)
    code, packet = apply(run, '--project', one.project, '--authorize-plan', plan_sha256)
    assert (code, packet['status']) == (3, 'blocked')
    assert packet['blockers'] == ['repair_not_reproduced_by_independent_check']
    assert tree(one.project) == before and journal(one).active is None


def test_a_plan_without_operations_is_reported_as_no_change(one):
    one.skill.write_bytes(one.valid)
    run, plan_sha256 = stage(one, 'deterministic_contract', body=b'Keep the body intact.\n')
    code, packet = apply(run, '--project', one.project, '--authorize-plan', plan_sha256)
    assert (code, packet['status'], packet['operations']) == (0, 'no_change', [])
    assert journal(one).active is None


@pytest.mark.parametrize('damage', [
    pytest.param(lambda run: (run / 'change-plan.json').unlink(), id='missing-plan'),
    pytest.param(lambda run: (run / 'change-plan.json').write_text('[]'), id='not-an-object'),
    pytest.param(lambda run: write_json(run / 'change-plan.json', dict(json.loads((run / 'change-plan.json').read_text()), plan_sha256='0' * 64)), id='identity-forged'),
])
def test_an_unreadable_or_forged_plan_is_invalid_input(one, damage):
    one.skill.write_bytes(one.valid)
    run, _ = stage(one, 'behavioral_candidate', body=b'Rewritten body.\n')
    damage(run)
    before = tree(one.project)
    assert apply(run, '--project', one.project)[0] == 2
    assert tree(one.project) == before
