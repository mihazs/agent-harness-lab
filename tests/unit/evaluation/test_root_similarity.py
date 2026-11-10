"""Public synthetic candidate and noncandidate controls."""
from agent_harness_lab.evaluation.root_similarity import suspected_components


def test_renamed_identifiers_flagged_unrelated_task_not_flagged():
    prompts = [('one', 'Compute total_value from input_rows and return the result sorted by date.'),
               ('two', 'Compute final_value from record_rows and return the result sorted by date.'),
               ('three', 'Repair the calendar parser to reject invalid leap years and time zones.')]
    assert suspected_components(prompts) == (('one', 'two'),)


def test_empty_and_short_inputs_do_not_imply_relationship():
    assert suspected_components([('a', ''), ('b', ''), ('c', 'x'), ('d', 'y')]) == ()


def test_pinned_evidence_changes_preflight_and_standard_final_bound(monkeypatch):
    from dataclasses import replace
    import pytest
    from agent_harness_lab.evaluation.root_similarity import evidence
    from agent_harness_lab.evaluation.precision import preflight
    from agent_harness_lab.evaluation.statistics import analyze
    from agent_harness_lab.sources.schema import digest
    from tests.unit.evaluation.test_precision_solver import spec
    from tests.unit.evaluation.test_preservation_analysis import records

    plan = spec({'a': 60}, target=.7)
    prompts = [(root, 'Compute total_value from input_rows and return the result sorted by date.'
                if index < 2 else f'unique unrelated task {index} describes operation number {index}')
               for index, (_, root, _) in enumerate(plan.tasks)]
    # Explicitly construct one known candidate component to isolate the endpoint math.
    snapshot = evidence('1'*64, '2'*64, [(t, r) for t, r, _ in plan.tasks], prompts)
    snapshot['groups'] = [[plan.tasks[0][1], plan.tasks[1][1]]]
    snapshot['groups'][0].sort()
    monkeypatch.setattr('agent_harness_lab.evaluation.task_bank.inspect', lambda path: {'content_grouping': {'root_similarity': {'evidence': snapshot}}})
    bound_plan = replace(plan, root_similarity_sha256=digest(snapshot))
    nominal = preflight(plan)
    checked = preflight(bound_plan, root_similarity_evidence=snapshot, root_similarity_bank="synthetic")
    assert checked['declared_roots'] == 60 and checked['effective_roots'] == 59
    assert checked['endpoints']['primary']['half_width'] > nominal['endpoints']['primary']['half_width']
    assert checked['sample_size_plan']['status'] == 'unavailable'
    with pytest.raises(ValueError, match='current bank required'):
        preflight(bound_plan)
    data = records(bound_plan, lambda row: row['version'] == 'candidate')
    result = analyze(bound_plan, data, root_similarity_evidence=snapshot, root_similarity_bank="synthetic")
    assert result['primary']['independent_roots'] == 59
    assert analyze(bound_plan, data)['status'] == 'blocked'
    changed = dict(snapshot, groups=[])
    assert analyze(bound_plan, data, root_similarity_evidence=changed, root_similarity_bank="synthetic")['status'] == 'blocked'


def test_pinned_evidence_is_applied_to_preservation_cohort(monkeypatch):
    from dataclasses import replace
    from agent_harness_lab.evaluation.root_similarity import evidence
    from agent_harness_lab.evaluation.statistics import analyze
    from agent_harness_lab.sources.schema import digest
    from tests.unit.evaluation import test_preservation_analysis as fixture

    plan, cohort = fixture.freeze(fixture.plan(40, 1), members=[f't{i:04d}' for i in range(20)])
    prompts = [(r, f'unrelated prompt {i}') for i, (_, r, _) in enumerate(plan.tasks)]
    snapshot = evidence('1'*64, '2'*64, [(t, r) for t, r, _ in plan.tasks], prompts)
    snapshot['groups'] = [['r0000', 'r0001']]
    monkeypatch.setattr('agent_harness_lab.evaluation.task_bank.inspect', lambda path: {'content_grouping': {'root_similarity': {'evidence': snapshot}}})
    plan = replace(plan, root_similarity_sha256=digest(snapshot))
    result = analyze(plan, fixture.records(plan, lambda row: True), cohort_binding=cohort,
                     root_similarity_evidence=snapshot, root_similarity_bank="synthetic")
    assert result['primary']['independent_roots'] == 39
    assert result['preservation']['cohorts']['protected']['endpoint']['independent_roots'] == 19


def test_live_bank_snapshot_detects_candidates_and_rejects_stale_fixture():
    from contextlib import ExitStack
    from dataclasses import replace
    import json
    import pytest
    from agent_harness_lab.evaluation.integrity import digest as fixture_digest
    from agent_harness_lab.evaluation.task_bank import inspect
    from agent_harness_lab.evaluation.precision import preflight
    from agent_harness_lab.sources.schema import digest
    from tests.integration.evaluation.test_evaluation_foundation import TaskBank
    from tests.unit.evaluation.test_precision_solver import spec

    with ExitStack() as stack:
        bank = TaskBank(stack.callback)
        for index, text in enumerate(('Compute total_value from input_rows and return the result sorted by date.',
                                      'Compute final_value from record_rows and return the result sorted by date.')):
            folder = bank.root / f'fixture{index}'
            (folder / 'task.json').write_text(json.dumps({'prompt': text}))
            bank.tasks[index]['fixture_digest'] = fixture_digest(folder)
        bank.write()
        report = inspect(bank.path)
        similarity = report['content_grouping']['root_similarity']
        assert similarity['nominal_roots'] == 2 and similarity['effective_roots'] == 1
        assert any(issue['status'] == 'independence_unverified' for issue in report['issues'])
        snapshot = similarity['evidence']
        original = spec({'a': 2})
        plan = replace(original, tasks=tuple((row['task_id'], row['root_id'], 'a') for row in bank.tasks),
                       root_similarity_sha256=digest(snapshot))
        assert preflight(plan, root_similarity_evidence=snapshot, root_similarity_bank=bank.path)['effective_roots'] == 1
        from agent_harness_lab.evaluation.root_similarity import project_evidence
        selected = [(bank.tasks[0]['task_id'], bank.tasks[0]['root_id'])]
        projected = project_evidence(snapshot, selected)
        subset = replace(plan, tasks=((selected[0][0], selected[0][1], 'a'),), root_similarity_sha256=digest(projected))
        assert preflight(subset, root_similarity_evidence=projected, root_similarity_bank=bank.path)['declared_roots'] == 1
        assert bank.tasks[1]['task_id'] not in str(projected)
        (bank.root / 'fixture0' / 'task.json').write_text(json.dumps({'prompt': 'Changed synthetic task prompt.'}))
        bank.tasks[0]['fixture_digest'] = fixture_digest(bank.root / 'fixture0')
        bank.write()
        with pytest.raises(ValueError, match='differs'):
            preflight(plan, root_similarity_evidence=snapshot, root_similarity_bank=bank.path)


def test_power_simulation_rejects_pinned_similarity_instead_of_using_nominal_roots():
    from dataclasses import replace
    import pytest
    from agent_harness_lab.evaluation.power import simulate
    from tests.unit.evaluation.test_precision_solver import spec

    with pytest.raises(ValueError, match='does not support root similarity'):
        simulate(replace(spec({'a': 2}), root_similarity_sha256='a'*64), {})


def test_plain_variable_renames_and_unrelated_control():
    prompts = [('a', 'Set customer from records then return customer.'),
               ('b', 'Set account from records then return account.'),
               ('c', 'Delete account records and reject expired credentials.')]
    assert suspected_components(prompts) == (('a', 'b'),)
    code = [('a', 'customer = records; return customer + customer'),
            ('b', 'account = records; return account + account')]
    assert suspected_components(code) == (('a', 'b'),)


def test_projected_snapshot_hides_other_task_ids_and_retains_transitive_group(monkeypatch):
    from dataclasses import replace
    import pytest
    from agent_harness_lab.evaluation.root_similarity import evidence, project_evidence
    from agent_harness_lab.evaluation.precision import preflight
    from agent_harness_lab.sources.schema import digest
    from tests.unit.evaluation.test_precision_solver import spec

    plan = spec({'a': 4})
    rows = [(task, root) for task, root, _ in plan.tasks]
    full = evidence('1'*64, '2'*64, rows, [(root, f'prompt {i}') for i, (_, root) in enumerate(rows)])
    full['groups'] = [[rows[0][1], rows[1][1], rows[2][1]]]
    selected = [rows[0], rows[2], rows[3]]
    projected = project_evidence(full, selected)
    assert projected['groups'] == [[rows[0][1], rows[2][1]]]
    assert rows[1][0] not in str(projected) and rows[1][1] not in str(projected)
    monkeypatch.setattr('agent_harness_lab.evaluation.task_bank.inspect', lambda path: {'content_grouping': {'root_similarity': {'evidence': full}}})
    selected_plan = replace(plan, tasks=tuple((task, root, 'a') for task, root in selected),
                            root_similarity_sha256=digest(projected))
    report = preflight(selected_plan, root_similarity_evidence=projected, root_similarity_bank='synthetic')
    assert report['declared_roots'] == 3 and report['effective_roots'] == 2
    changed = replace(selected_plan, tasks=((selected[0][0], 'wrong-root', 'a'), *selected_plan.tasks[1:]))
    with pytest.raises(ValueError, match='does not match'):
        preflight(changed, root_similarity_evidence=projected, root_similarity_bank='synthetic')
