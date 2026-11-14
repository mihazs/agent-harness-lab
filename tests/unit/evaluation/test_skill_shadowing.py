"""Same-name skill copies outside the staged bundle; synthetic skill folders under tmp_path only."""
import hashlib
import os

import pytest

from agent_harness_lab.evaluation.skills import (
    SHADOWING_SCHEMA, UNREADABLE, ambient_skill_copies, paired_transfer, skill_availability, skill_shadowing, shadowing_state)

STAGED = skill_availability('harness_paths', 'example', {'SKILL.md': 'a' * 64})
REMOVED = skill_availability('harness_paths', 'example', {})


def make_skill(root, name, text='skill', **extra):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / 'SKILL.md').write_text(text)
    for filename, body in extra.items():
        (folder / filename).write_text(body)
    return folder


def snapshot(root):
    return sorted((str(path.relative_to(root)), path.stat().st_mtime_ns, path.read_bytes() if path.is_file() else None)
                  for path in [root, *root.rglob('*')])


def test_a_user_layer_copy_with_the_staged_name_is_reported_with_the_effective_layer_unknown():
    ambient = {'user': {'example': 'b' * 64}}
    (row,) = skill_shadowing([STAGED], ambient)
    assert (row['name'], row['route'], row['staged_state'], row['state']) == ('example', 'harness_paths', 'present', 'ambient_same_name')
    assert row['ambient'] == [dict(layer='user', bundle_sha256='b' * 64, bundle_match=False)]
    assert (row['effective_layer'], row['layers_scanned']) == ('unknown', ['user'])


def test_an_ambient_copy_with_the_staged_bundle_hash_is_a_match_not_a_difference():
    (row,) = skill_shadowing([skill_availability('skills', 'example', {'SKILL.md': 'c' * 64})],
                             {'user': {'example': skill_availability('x', 'example', {'SKILL.md': 'c' * 64})['bundle_sha256']}})
    assert row['ambient'][0]['bundle_match'] is True


def test_an_ambient_copy_beside_a_removed_skill_means_the_removal_arm_still_has_the_skill():
    (row,) = skill_shadowing([REMOVED], {'user': {'example': 'b' * 64}})
    assert (row['staged_state'], row['state']) == ('absent', 'ambient_same_name')
    assert row['ambient'][0]['bundle_match'] is None


def test_every_scanned_layer_without_a_copy_is_none_found_for_those_layers_only():
    (row,) = skill_shadowing([STAGED], {'user': {}})
    assert (row['state'], row['ambient'], row['layers_scanned']) == ('none_found', [], ['user'])


@pytest.mark.parametrize('ambient', [None, {}, {'user': None}], ids=['not_scanned', 'no_layers', 'unreadable_layer'])
def test_a_missing_or_unreadable_scan_stays_unknown_and_never_becomes_none_found(ambient):
    (row,) = skill_shadowing([STAGED], ambient)
    assert (row['state'], row['ambient'], row['layers_scanned']) == ('unknown', [], [])


def test_a_copy_in_one_layer_is_reported_even_when_another_layer_could_not_be_read():
    (row,) = skill_shadowing([STAGED], {'user': {'example': UNREADABLE}, 'managed': None})
    assert (row['state'], row['layers_scanned']) == ('ambient_same_name', ['user'])
    assert row['ambient'] == [dict(layer='user', bundle_sha256=None, bundle_match=None)]


def test_an_unreadable_ambient_bundle_keeps_its_content_unknown():
    (row,) = skill_shadowing([STAGED], {'user': {'example': UNREADABLE}})
    assert row['ambient'] == [dict(layer='user', bundle_sha256=None, bundle_match=None)]


def test_a_name_whose_existence_could_not_be_determined_is_unknown_not_clear_and_not_a_copy():
    (row,) = skill_shadowing([STAGED], {'user': {'example': None}})
    assert (row['state'], row['ambient'], row['layers_scanned']) == ('unknown', [], [])


def test_shadowing_reports_only_the_staged_names_and_the_schema_is_named():
    rows = skill_shadowing([STAGED], {'user': {'example': 'b' * 64, 'unrelated': 'd' * 64}})
    assert [row['name'] for row in rows] == ['example']
    assert SHADOWING_SCHEMA == 'skill-shadowing-v1'


def test_no_staged_skill_gives_no_rows():
    assert skill_shadowing([], {'user': {}}) == []


@pytest.mark.parametrize('states, expected', [
    (['ambient_same_name', 'none_found'], 'ambient_same_name'),
    (['unknown', 'ambient_same_name'], 'ambient_same_name'),
    (['none_found', 'none_found'], 'none_found'),
    (['none_found', 'unknown'], 'unknown'),
    ([], 'unknown'),
], ids=['copy_wins', 'copy_beats_unknown', 'all_clear', 'unknown_blocks_clear', 'no_rows_unknown'])
def test_the_trial_roll_up_never_reports_clear_unless_every_staged_skill_was_scanned(states, expected):
    assert shadowing_state([dict(state=state) for state in states]) == expected


def test_scan_returns_a_hash_for_a_same_name_bundle_and_omits_names_without_one(tmp_path):
    make_skill(tmp_path, 'example', 'body', **{'extra.md': 'more'})
    make_skill(tmp_path, 'unrelated')
    found = ambient_skill_copies({'user': tmp_path}, ['example', 'missing'])
    expected = skill_availability('x', 'example', {
        'SKILL.md': hashlib.sha256(b'body').hexdigest(), 'extra.md': hashlib.sha256(b'more').hexdigest()})['bundle_sha256']
    assert found == {'user': {'example': expected}}


def test_scan_never_lists_or_returns_skills_the_trial_did_not_stage(tmp_path):
    make_skill(tmp_path, 'private-one')
    assert ambient_skill_copies({'user': tmp_path}, ['example']) == {'user': {}}


def test_scan_does_not_write_to_the_ambient_folder(tmp_path):
    make_skill(tmp_path, 'example', **{'extra.md': 'more'})
    before = snapshot(tmp_path)
    ambient_skill_copies({'user': tmp_path}, ['example'])
    assert snapshot(tmp_path) == before


def test_a_folder_without_skill_md_is_not_a_skill_copy(tmp_path):
    (tmp_path / 'example').mkdir()
    (tmp_path / 'example' / 'notes.md').write_text('x')
    assert ambient_skill_copies({'user': tmp_path}, ['example']) == {'user': {}}


def test_a_missing_skills_folder_means_none_found_in_that_layer(tmp_path):
    assert ambient_skill_copies({'user': tmp_path / 'absent'}, ['example']) == {'user': {}}


def test_a_skills_root_that_is_not_a_folder_is_an_unreadable_layer(tmp_path):
    (tmp_path / 'skills').write_text('not a folder')
    assert ambient_skill_copies({'user': tmp_path / 'skills'}, ['example']) == {'user': None}


@pytest.mark.parametrize('name', ['../escape', 'a/b', '..', '.', ''], ids=['parent', 'nested', 'dotdot', 'dot', 'empty'])
def test_a_staged_name_that_could_leave_the_folder_is_undetermined_and_never_followed(tmp_path, name):
    root = tmp_path / 'skills'
    root.mkdir()
    make_skill(tmp_path, 'escape')
    assert ambient_skill_copies({'user': root}, [name]) == {'user': {name: None}}


def refuse_stat(monkeypatch, target, error):
    """Make os.stat fail for one path, so the result does not depend on file modes, the interpreter or running as root."""
    real = os.stat

    def stat(path, *args, **kwargs):
        if os.fspath(path) == str(target):
            raise error
        return real(path, *args, **kwargs)
    monkeypatch.setattr(os, 'stat', stat)


UNINSPECTABLE = [PermissionError(13, 'denied'), OSError(5, 'input/output error'), OSError(40, 'too many symbolic links')]
UNINSPECTABLE_IDS = ['permission_denied', 'io_error', 'symlink_loop']
ABSENT = [FileNotFoundError(2, 'missing'), NotADirectoryError(20, 'parent is a file')]
ABSENT_IDS = ['missing', 'parent_is_a_file']


@pytest.fixture
def skills_root(tmp_path):
    make_skill(tmp_path / 'skills', 'example')
    return tmp_path / 'skills'


def inspected(root, level):
    """The path of one level that the scan inspects, outermost first: the skills root, the name folder, SKILL.md."""
    return {'root': root, 'folder': root / 'example', 'entry': root / 'example' / 'SKILL.md'}[level]


@pytest.mark.parametrize('error', UNINSPECTABLE, ids=UNINSPECTABLE_IDS)
def test_a_skills_root_that_cannot_be_inspected_is_an_unreadable_layer_not_an_empty_one(monkeypatch, skills_root, error):
    refuse_stat(monkeypatch, skills_root, error)
    assert ambient_skill_copies({'user': skills_root}, ['example']) == {'user': None}


@pytest.mark.parametrize('level', ['folder', 'entry'])
@pytest.mark.parametrize('error', UNINSPECTABLE, ids=UNINSPECTABLE_IDS)
def test_a_same_name_folder_or_entry_point_that_cannot_be_inspected_leaves_the_name_undetermined(monkeypatch, skills_root, level, error):
    refuse_stat(monkeypatch, inspected(skills_root, level), error)
    assert ambient_skill_copies({'user': skills_root}, ['example']) == {'user': {'example': None}}


@pytest.mark.parametrize('level', ['root', 'folder', 'entry'])
@pytest.mark.parametrize('error', UNINSPECTABLE, ids=UNINSPECTABLE_IDS)
def test_an_uninspectable_skill_location_never_becomes_none_found_or_a_claimed_copy(monkeypatch, skills_root, level, error):
    refuse_stat(monkeypatch, inspected(skills_root, level), error)
    (row,) = skill_shadowing([STAGED], ambient_skill_copies({'user': skills_root}, ['example']))
    assert (row['state'], row['ambient'], row['layers_scanned']) == ('unknown', [], [])
    assert shadowing_state([row]) == 'unknown'


@pytest.mark.parametrize('level', ['root', 'folder', 'entry'])
@pytest.mark.parametrize('error', ABSENT, ids=ABSENT_IDS)
def test_a_location_that_is_absent_is_a_scanned_layer_with_no_copy(monkeypatch, skills_root, level, error):
    refuse_stat(monkeypatch, inspected(skills_root, level), error)
    ambient = ambient_skill_copies({'user': skills_root}, ['example'])
    assert ambient == {'user': {}}
    assert skill_shadowing([STAGED], ambient)[0]['state'] == 'none_found'


def test_a_same_name_entry_point_that_is_a_folder_is_not_a_skill_copy(tmp_path):
    (tmp_path / 'example' / 'SKILL.md').mkdir(parents=True)
    assert ambient_skill_copies({'user': tmp_path}, ['example']) == {'user': {}}


def test_a_same_name_file_where_a_folder_belongs_is_not_a_skill_copy(tmp_path):
    (tmp_path / 'example').write_text('not a folder')
    assert ambient_skill_copies({'user': tmp_path}, ['example']) == {'user': {}}


def test_an_ambient_bundle_over_the_file_bound_is_unreadable_not_truncated(tmp_path):
    folder = make_skill(tmp_path, 'example')
    for index in range(3):
        (folder / f'f{index}.md').write_text('x')
    assert ambient_skill_copies({'user': tmp_path}, ['example'], max_files=2) == {'user': {'example': UNREADABLE}}


def test_an_ambient_bundle_over_the_byte_bound_is_unreadable_not_truncated(tmp_path):
    make_skill(tmp_path, 'example', 'x' * 10)
    assert ambient_skill_copies({'user': tmp_path}, ['example'], max_bytes=9) == {'user': {'example': UNREADABLE}}


def test_a_bundle_exactly_at_both_bounds_is_still_hashed(tmp_path):
    make_skill(tmp_path, 'example', 'x' * 10)
    (found,) = ambient_skill_copies({'user': tmp_path}, ['example'], max_files=1, max_bytes=10).values()
    assert found['example'] not in (None, UNREADABLE)


def test_a_symlinked_skill_folder_is_read_without_writing_through_it(tmp_path):
    target = make_skill(tmp_path / 'source', 'example', 'linked')
    root = tmp_path / 'skills'
    root.mkdir()
    os.symlink(target, root / 'example')
    before = snapshot(tmp_path / 'source')
    (found,) = ambient_skill_copies({'user': root}, ['example']).values()
    assert found['example'] not in (None, UNREADABLE)
    assert snapshot(tmp_path / 'source') == before


def trial(arm, task, outcome, **extra):
    return dict(condition='natural' if task.startswith('t') else 'negative_routing', arm=arm, task_id=task, root_id='root-' + task,
                outcome=outcome, outcome_provenance='executable_grader', **extra)


def matched(**extra_by_arm):
    rows = []
    for task, absent, present in (('t1', False, True), ('c1', True, True)):
        rows += [trial('absent', task, absent, **extra_by_arm.get('absent', {})), trial('present', task, present, **extra_by_arm.get('present', {}))]
    return rows


POLICY = dict(target_conditions=['natural'], control_conditions=['negative_routing'], max_control_loss=0.0)


def test_baseline_without_shadowing_flags_stays_uncertified():
    assert paired_transfer(matched(), policy=POLICY)['reason'] == 'requires_frozen_paired_experiment'


@pytest.mark.parametrize('arm', ['absent', 'present'])
def test_a_shadowed_copy_in_either_arm_makes_the_comparison_inconclusive(arm):
    report = paired_transfer(matched(**{arm: dict(shadowing='ambient_same_name')}), policy=POLICY)
    assert (report['disposition'], report['reason']) == ('inconclusive', 'shadowed_skill_copy')
    assert report['conditions']['natural'][f'{arm}_shadowed_records'] == 1


@pytest.mark.parametrize('label', ['unknown', 'none_found', None, 'garbage'], ids=['unknown', 'none_found', 'missing', 'unrecognized'])
def test_unknown_or_clear_shadowing_never_blocks_or_counts_as_a_shadowed_copy(label):
    report = paired_transfer(matched(present=dict(shadowing=label)), policy=POLICY)
    assert report['reason'] == 'requires_frozen_paired_experiment'
    assert report['conditions']['natural']['present_shadowed_records'] == 0


def test_a_critical_regression_still_rejects_when_a_copy_is_also_shadowed():
    rows = matched(present=dict(shadowing='ambient_same_name'))
    rows[1]['critical_regression'] = True
    assert paired_transfer(rows, policy=POLICY)['reason'] == 'critical_regression'


def test_a_shadowing_flag_on_an_excluded_record_is_ignored():
    rows = matched()
    rows.append(dict(trial('present', 't9', True, shadowing='ambient_same_name'), outcome_provenance='model_claim'))
    report = paired_transfer(rows, policy=POLICY)
    assert report['reason'] == 'unmatched_or_unverified_roots'
    assert report['conditions']['natural']['present_shadowed_records'] == 0
