"""Refinement reuse keeps frozen tasks byte for byte and accepts only fresh held-out tasks (#739), on synthetic trees."""

import pytest

from agent_harness_lab import refinement_reuse as reuse
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.manifest_contract import ContractError


def frozen(root):
    """A controller-validated first-attempt manifest over three tasks, with a shared grader program in the root."""
    (root / 'grade.py').write_text('print(1)\n')
    tasks, controls, bundles = [], [], {}
    for split in ('development', 'regression', 'heldout'):
        for name in (split, 'oracle-' + split, split + '-complete'):
            (root / name).mkdir()
            (root / name / 'data').write_text(name)
        tasks.append(dict(id=split, split=split, fixture=split, prompt='p', artifacts=['a'], grader=[str(root / 'grade.py'), '{workspace}']))
        controls.append(dict(task=split, state='complete', fixture=split + '-complete', digest=digest(root / (split + '-complete'))))
        bundles[split] = dict(path='oracle-' + split, digest=digest(root / ('oracle-' + split)))
    return dict(tasks=tasks, grader_self_tests=controls, grader_bundles=bundles)


def fresh(root, ident='heldout-2', split='heldout', fixture='fresh'):
    (root / fixture).mkdir()
    (root / fixture / 'data').write_text('new')
    (root / 'fresh-oracle').mkdir()
    return dict(skills=[], harness_paths=[], tasks=[dict(id=ident, split=split, fixture=fixture, prompt='p', artifacts=['a'], grader=['g'])],
                grader_self_tests=[dict(task=ident, state='complete', fixture=fixture)], grader_bundles={ident: dict(path='fresh-oracle')})


@pytest.fixture
def world(tmp_path):
    first, second = tmp_path / 'one', tmp_path / 'two'
    first.mkdir(); second.mkdir()
    manifest = frozen(first)
    return first, second, manifest, reuse.plan(first, manifest)


def test_plan_names_only_the_non_heldout_parts_with_their_digests(world):
    first, _, manifest, plan = world
    assert [task['id'] for task in plan['tasks']] == ['development', 'regression'] and set(plan['bundles']) == {'development', 'regression'}
    assert set(plan['frozen']) == {'development', 'regression', 'oracle-development', 'oracle-regression',
                                   'development-complete', 'regression-complete', 'grade.py'}
    assert plan['frozen']['oracle-development'] == manifest['grader_bundles']['development']['digest']
    assert 'heldout' not in plan['frozen'] and 'oracle-heldout' not in plan['frozen']


def test_merge_copies_the_frozen_parts_and_moves_reused_grader_programs_beside_them(world):
    first, second, manifest, plan = world
    merged, facts = reuse.merge(plan, second, fresh(second))
    assert facts == dict(reused_tasks=2, fresh_heldout_tasks=1, frozen_digests_verified=True)
    assert [task['id'] for task in merged['tasks']] == ['development', 'regression', 'heldout-2']
    assert merged['tasks'][0]['grader'][0] == str(second.resolve() / 'grade.py') and merged['tasks'][0]['grader'][1] == '{workspace}'
    assert all(digest(second / name) == plan['frozen'][name] for name in plan['frozen'] if (second / name).is_dir())
    assert (second / 'grade.py').read_text() == 'print(1)\n'
    assert all('digest' not in entry for entry in [*merged['grader_self_tests'], *merged['grader_bundles'].values()])  # the controller recomputes
    assert set(merged['grader_bundles']) == {'development', 'regression', 'heldout-2'} and len(merged['grader_self_tests']) == 3
    assert digest(first) == digest(first)  # the source stays untouched


@pytest.mark.parametrize('kind, field', [('split', 'tasks[0].split'), ('id', 'tasks[0].id')])
def test_a_fresh_task_that_is_not_held_out_or_reuses_an_id_is_a_typed_agent_defect(world, kind, field):
    _, second, _, plan = world
    authored = fresh(second, **(dict(split='development') if kind == 'split' else dict(ident='development')))
    with pytest.raises(ContractError) as caught:
        reuse.merge(plan, second, authored)
    assert (caught.value.reason, caught.value.field, caught.value.repairable) == ('evaluation_manifest_field_invalid', field, True)
    assert not (second / 'grade.py').exists()  # nothing was copied before the check passed


def test_a_fresh_path_that_collides_with_a_reserved_name_is_rejected_before_any_copy(world):
    _, second, _, plan = world
    authored = fresh(second, fixture='development')
    with pytest.raises(ContractError) as caught:
        reuse.merge(plan, second, authored)
    assert caught.value.field == 'tasks[*].fixture' and not (second / 'oracle-development').exists()


def test_a_frozen_part_changed_since_it_was_frozen_fails_as_a_changed_frozen_input(world):
    first, second, _, plan = world
    (first / 'oracle-regression' / 'data').write_text('tampered')
    with pytest.raises(ContractError) as caught:
        reuse.merge(plan, second, fresh(second))
    assert (caught.value.reason, caught.value.field, caught.value.repairable) == ('grader_input_changed_after_freeze', 'reused_tasks', False)


def test_the_prompt_line_lists_the_reused_ids_and_reserved_names_but_never_fixture_content(world):
    first, _, _, plan = world
    line = reuse.prompt_line(plan)
    assert 'development, regression' in line and 'oracle-development' in line and 'heldout' in line
    assert 'tampered' not in line and str(first) not in line
