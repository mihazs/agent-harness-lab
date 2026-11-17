"""The scoring manifest: only development and regression tasks reach the loop, and nothing that declares a plan or certification access (#813)."""
import json

import pytest

from agent_harness_lab.loop.adapters import SCORING_MANIFEST, scoring_manifest
from agent_harness_lab.loop.errors import LoopError
from tests.support import frozen_bank as fb


def edit(tmp_path, **changes):
    manifest = fb.write_bank(tmp_path, scoring=4, heldout=0)
    loaded = json.loads(manifest.read_text())
    manifest.write_text(json.dumps({**loaded, **changes}))
    return manifest


def task(name, split, **fields):
    return dict(id=name, root=name, split=split, fixture=f'fixtures/{name}', prompt='x', artifacts=['artifact'], grader=['grade'], **fields)


@pytest.mark.parametrize('split', ['heldout', 'transfer', 'certification', 'bogus', None, 7])
def test_a_task_outside_development_and_regression_is_left_out_and_never_read(tmp_path, split):
    manifest = fb.write_bank(tmp_path, scoring=2, heldout=0)
    loaded = json.loads(manifest.read_text())
    manifest.write_text(json.dumps(dict(loaded, tasks=[*loaded['tasks'], task('outside', split)])))
    target, left_out = scoring_manifest(manifest)
    assert (target.name, left_out, [item['id'] for item in json.loads(target.read_text())['tasks']]) == (SCORING_MANIFEST, 1, ['t0', 't1'])


def test_the_graders_and_controls_of_a_left_out_task_leave_with_it(tmp_path):
    manifest = fb.write_bank(tmp_path, scoring=2, heldout=1)
    loaded = json.loads(manifest.read_text())
    loaded.update(grader_bundles={name: dict(path=name) for name in ('t0', 't1', 'h0')},
                  grader_self_tests=[dict(task=name, state='complete', fixture=name) for name in ('t0', 't1', 'h0')])
    manifest.write_text(json.dumps(loaded))
    scoring = json.loads(scoring_manifest(manifest)[0].read_text())
    assert sorted(scoring['grader_bundles']) == ['t0', 't1'] and sorted(item['task'] for item in scoring['grader_self_tests']) == ['t0', 't1']


@pytest.mark.parametrize('key, value', [('experiment', dict(profile='descriptive_smoke')), ('cohort_manifest', dict(path='x'))])
def test_a_plan_or_cohort_declaration_does_not_reach_the_scoring_manifest(tmp_path, key, value):
    scoring = json.loads(scoring_manifest(edit(tmp_path, **{key: value}))[0].read_text())
    assert key not in scoring and len(scoring['tasks']) == 4


@pytest.mark.parametrize('changes', [
    pytest.param(dict(protected_access=dict(authority='declared')), id='protected-access'),
    pytest.param(dict(experiment=dict(profile='certification')), id='certification-plan'),
    pytest.param(dict(tasks='not a list'), id='tasks-not-a-list'),
])
def test_a_certification_or_unusable_bank_is_refused_and_no_scoring_manifest_is_written(tmp_path, changes):
    manifest = edit(tmp_path, **changes)
    with pytest.raises(LoopError) as error:
        scoring_manifest(manifest)
    assert (error.value.reason, error.value.field) == ('loop_port_invalid', 'bank') and not manifest.with_name(SCORING_MANIFEST).exists()


def test_a_bank_with_no_task_is_usable_and_is_the_no_eligible_cases_evaluation(tmp_path):
    target, left_out = scoring_manifest(edit(tmp_path, tasks=[]))
    assert (left_out, json.loads(target.read_text())['tasks']) == (0, [])


@pytest.mark.parametrize('tasks', [
    pytest.param([task('a', 'development'), task('a', 'regression')], id='duplicate-id'),
    pytest.param([task('', 'development')], id='empty-id'),
    pytest.param([dict(task('a', 'development'), id=7)], id='number-id'),
])
def test_task_ids_that_are_not_unique_strings_refuse_the_bank(tmp_path, tasks):
    manifest = edit(tmp_path, tasks=tasks)
    with pytest.raises(LoopError) as error:
        scoring_manifest(manifest)
    assert error.value.reason == 'loop_port_invalid' and not manifest.with_name(SCORING_MANIFEST).exists()


@pytest.mark.parametrize('damage', [pytest.param(lambda path: path.unlink(), id='missing'), pytest.param(lambda path: path.write_text('{'), id='not-json'),
                                    pytest.param(lambda path: path.write_text('[]'), id='not-an-object')])
def test_a_manifest_that_cannot_be_read_is_an_adapter_defect(tmp_path, damage):
    manifest = fb.write_bank(tmp_path)
    damage(manifest)
    with pytest.raises(LoopError) as error:
        scoring_manifest(manifest)
    assert (error.value.reason, error.value.field) == ('loop_port_invalid', 'bank')


def test_the_frozen_scoring_manifest_is_reused_when_identical_and_never_rewritten_when_the_bank_changed(tmp_path):
    manifest = fb.write_bank(tmp_path, scoring=3, heldout=0)
    first, _ = scoring_manifest(manifest)
    frozen = first.read_bytes()
    assert scoring_manifest(manifest)[0].read_bytes() == frozen  # the same bank freezes to the same bytes
    loaded = json.loads(manifest.read_text())
    manifest.write_text(json.dumps(dict(loaded, tasks=loaded['tasks'][:2])))
    with pytest.raises(LoopError) as error:
        scoring_manifest(manifest)
    assert error.value.reason == 'loop_port_invalid' and first.read_bytes() == frozen


def test_existing_scoring_manifest_is_not_rewritten_during_recovery(tmp_path):
    import os
    manifest = fb.write_bank(tmp_path)
    target, _ = scoring_manifest(manifest)
    os.utime(target, ns=(1_000_000_000, 1_000_000_000))
    before = target.stat()
    scoring_manifest(manifest)
    after = target.stat()
    assert (after.st_ino, after.st_mtime_ns, after.st_size) == (before.st_ino, before.st_mtime_ns, before.st_size)


def test_existing_scoring_manifest_symlink_is_refused_without_touching_target(tmp_path):
    manifest = fb.write_bank(tmp_path)
    target, _ = scoring_manifest(manifest)
    content = target.read_bytes()
    outside = tmp_path / 'outside.json'
    outside.write_bytes(content)
    target.unlink()
    target.symlink_to(outside)
    with pytest.raises(LoopError):
        scoring_manifest(manifest)
    assert outside.read_bytes() == content
