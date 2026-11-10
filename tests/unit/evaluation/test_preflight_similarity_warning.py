"""Synthetic legacy and pinned precision warning controls."""
from dataclasses import replace

from agent_harness_lab.evaluation.plan import schedule
from agent_harness_lab.evaluation.precision import preflight
from tests.unit.evaluation.test_precision_solver import spec


def test_unpinned_preflight_warns_without_changing_legacy_plan_or_execution():
    plan = spec({'a': 2})
    before, trials = plan.digest, schedule(plan)
    report = preflight(plan)
    assert report['root_similarity_check'] == dict(
        status='not_checked',
        warning='Root similarity was not checked; declared roots may include related tasks.')
    assert report['root_independence'] == 'unverified' and report['effective_roots'] is None
    assert report['execution_allowed']  # Legacy no-target descriptive execution remains available.
    assert plan.digest == before and schedule(plan) == trials


def test_pinned_preflight_reports_heuristic_check_without_claiming_independence(monkeypatch):
    from agent_harness_lab.evaluation.root_similarity import evidence
    from agent_harness_lab.sources.schema import digest

    plan = spec({'a': 3})
    prompts = [(root, f'unrelated synthetic prompt {i}') for i, (_, root, _) in enumerate(plan.tasks)]
    snapshot = evidence('1'*64, '2'*64, [(t, r) for t, r, _ in plan.tasks], prompts)
    snapshot['groups'] = []
    monkeypatch.setattr('agent_harness_lab.evaluation.task_bank.inspect', lambda path: {
        'content_grouping': {'root_similarity': {'evidence': snapshot}}})
    plan = replace(plan, root_similarity_sha256=digest(snapshot))
    report = preflight(plan, root_similarity_evidence=snapshot, root_similarity_bank='synthetic')
    assert report['root_similarity_check'] == dict(
        status='checked_heuristic', warning='Similarity is heuristic; an absent match does not establish independence.')
    assert report['root_independence'] == 'unverified'


def test_standalone_cli_surfaces_the_legacy_warning(tmp_path, capsys):
    import json
    from agent_harness_lab.cli import main

    plan = spec({'a': 100})
    path = tmp_path/'plan.json'
    path.write_text(json.dumps(plan.to_dict()))
    assert main(['experiment', '--spec', str(path), '--preflight']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['root_similarity_check']['status'] == 'not_checked'
    assert 'may include related tasks' in result['root_similarity_check']['warning']
    assert result['plan_sha256'] == plan.digest and result['root_independence'] == 'unverified'
