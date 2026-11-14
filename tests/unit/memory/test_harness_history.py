"""Harness snapshots, deterministic diffs, timeline events and predeclared baselines (synthetic checkouts and ids only)."""
from dataclasses import replace
import hashlib
import json

import pytest

from agent_harness_lab.memory import harness_history as history
from agent_harness_lab.memory.store import MemoryStore, MemoryStoreError
from agent_harness_lab.sources import harness_inventory as inventory
from agent_harness_lab.sources.git import HarnessFile, HarnessSnapshot
from agent_harness_lab.sources.schema import digest
from tests.support import harness_surface_fixture as fixture

CLAUDE = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='.')
SKILL = '.claude/skills/alpha/SKILL.md'
HOOK = '.claude/hooks/check.sh'
POINTER = dict(type='history_run', ref='run-1')
BASE, OTHER = hashlib.sha256(b'baseline').hexdigest(), hashlib.sha256(b'other').hexdigest()


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def capture(root, profile=CLAUDE, **options):
    return inventory.capture(root, profile, **options)


def snap(tmp_path, name='c', files=None, hostile=False, profile=CLAUDE):
    return history.snapshot(capture(fixture.build(tmp_path / name, files, hostile=hostile), profile))


def moment(minute):
    return f'2026-03-01T10:{minute:02d}:00Z'


def completed(evaluation, minute, snapshot=BASE, subject='claim-1', **extra):
    return history.event('evaluation_completed', at=moment(minute), origin='controller_observed', pointer=POINTER,
                         subject=subject, evaluation=evaluation, snapshot=snapshot, **extra)


# ---- snapshot identity ---------------------------------------------------------------------------------------
def test_same_harness_bytes_in_two_checkouts_give_the_same_snapshot_id(tmp_path):
    first, second = snap(tmp_path, 'one'), snap(tmp_path, 'two')
    assert first['snapshot_id'] == second['snapshot_id'] and len(first['snapshot_id']) == 64
    assert history.verify(first) is first


@pytest.mark.parametrize('change', [
    pytest.param(lambda root: (root / SKILL).write_text('edited\n'), id='content-edit'),
    pytest.param(lambda root: (root / HOOK).chmod(0o644), id='mode-change'),
    pytest.param(lambda root: (root / SKILL).unlink(), id='surface-removed'),
    pytest.param(lambda root: (root / '.claude/skills/beta').mkdir() or (root / '.claude/skills/beta/SKILL.md').write_text('---\nname: beta\n---\n'), id='surface-added'),
    pytest.param(lambda root: (root / 'linked.md').symlink_to('CLAUDE.md'), id='refused-entry-appears'),
])
def test_any_relevant_surface_change_gives_a_new_snapshot_id(tmp_path, change):
    root = fixture.build(tmp_path / 'c', hostile=False)
    before = history.snapshot(capture(root))
    change(root)
    assert history.snapshot(capture(root))['snapshot_id'] != before['snapshot_id']


def test_a_file_outside_every_harness_surface_does_not_change_the_snapshot_id(tmp_path):
    root = fixture.build(tmp_path / 'c', hostile=False)
    before = history.snapshot(capture(root))
    (root / 'README.md').write_text('not a harness surface, edited\n')
    assert history.snapshot(capture(root))['snapshot_id'] == before['snapshot_id']


@pytest.mark.parametrize('trust', ['declared_trusted', 'declared_untrusted'])
def test_declared_project_trust_is_part_of_the_snapshot_identity(tmp_path, trust):
    root = fixture.build(tmp_path / 'c', hostile=False)
    unknown = history.snapshot(capture(root))
    declared = history.snapshot(capture(root, dict(CLAUDE, project_trust=trust)))
    assert declared['snapshot_id'] != unknown['snapshot_id']


def test_runtime_is_part_of_the_snapshot_identity(tmp_path):
    root = fixture.build(tmp_path / 'c', hostile=False)
    claude, codex = history.snapshot(capture(root)), history.snapshot(capture(root, dict(CLAUDE, runtime='codex', version='0.166.0')))
    assert claude['snapshot_id'] != codex['snapshot_id']


@pytest.mark.parametrize('version', ['0.0.1', '9.9.9'], ids=['older', 'newer'])
def test_an_unsupported_runtime_version_has_no_snapshot_id(tmp_path, version):
    doc = snap(tmp_path, profile=dict(CLAUDE, version=version))
    assert (doc['status'], doc['snapshot_id'], doc['files']) == ('unsupported', None, [])
    with pytest.raises(ValueError, match='declared harness snapshot'):
        history.verify(doc)


def test_a_snapshot_names_no_path_text_and_never_claims_loading_or_publication(tmp_path):
    doc = snap(tmp_path, files={'src/task/AGENTS.md': 'x\n'})
    text = json.dumps(doc)
    assert not any(word in text for word in ('AGENTS', 'SKILL', 'alpha', 'hooks', str(tmp_path)))
    assert (doc['effective'], doc['observed_loading'], doc['publication_allowed']) == ('unknown', 'unknown', False)


def test_a_snapshot_edited_after_capture_fails_verification(tmp_path):
    doc = snap(tmp_path)
    doc['files'][0]['content_sha256'] = BASE
    with pytest.raises(ValueError, match='identity does not match'):
        history.verify(doc)


@pytest.mark.parametrize('value', [None, 'text', {}, {'profile': history.PROFILE}], ids=['none', 'text', 'empty', 'bare-profile'])
def test_verify_refuses_a_value_that_is_not_a_snapshot(value):
    with pytest.raises(ValueError):
        history.verify(value)


def test_a_snapshot_needs_a_harness_inventory_and_a_git_harness_snapshot(tmp_path):
    with pytest.raises(ValueError, match='harness inventory'):
        history.snapshot({'nodes': []})
    with pytest.raises(ValueError, match='Git HarnessSnapshot'):
        history.snapshot(capture(fixture.build(tmp_path / 'c', hostile=False)), harness={'files': []})


# ---- commit relation -----------------------------------------------------------------------------------------
def bound(tmp_path, *, name='c', edit=None, extra=None, mismatch=False):
    """A snapshot of a checkout bound to a synthetic Git HarnessSnapshot; edit(file) changes the committed side."""
    root = fixture.build(tmp_path / name, extra, hostile=False)
    declared = capture(root)
    files = []
    for node in declared.nodes:
        file = HarnessFile(node.path_sha256, node.content_sha256, node.mode, node.content_sha256, 0o100755 if node.mode & 0o111 else 0o100644)
        files.append(edit(file) if edit else file)
    harness = HarnessSnapshot(sha('head-1'), tuple(files))
    return history.snapshot(replace(declared, harness_sha256=sha('other') if mismatch else harness.sha256), harness)


def test_no_git_snapshot_leaves_the_commit_unbound(tmp_path):
    assert snap(tmp_path)['commit'] == dict(commit_sha256=None, relation='unbound', ignored_state='not_captured', scope='selected_files_only')


def test_files_equal_to_head_are_related_to_the_commit_without_claiming_ignored_state(tmp_path):
    commit = bound(tmp_path)['commit']
    assert (commit['commit_sha256'], commit['relation'], commit['ignored_state']) == (sha('head-1'), 'files_match_commit', 'not_captured')


@pytest.mark.parametrize('edit', [
    pytest.param(lambda f: replace(f, committed_sha256=sha('older bytes')), id='content-differs'),
    pytest.param(lambda f: replace(f, committed_sha256=None, committed_mode=None), id='untracked'),
    pytest.param(lambda f: replace(f, committed_mode=0o100755 if f.committed_mode == 0o100644 else 0o100644), id='executable-bit-differs'),
    pytest.param(lambda f: replace(f, committed_mode=0o120000), id='committed-as-symlink'),
])
def test_a_selected_file_that_differs_from_head_is_uncommitted_state(tmp_path, edit):
    assert bound(tmp_path, edit=edit)['commit']['relation'] == 'uncommitted_changes'


def test_the_same_commit_with_uncommitted_edits_has_a_different_snapshot_id(tmp_path):
    original = sha(fixture.FILES[SKILL])
    clean = bound(tmp_path, name='clean')
    dirty = bound(tmp_path, name='dirty', extra={SKILL: 'locally edited\n'},
                  edit=lambda f: replace(f, committed_sha256=original) if f.path_sha256 == digest(SKILL) else f)
    assert (clean['commit']['commit_sha256'], dirty['commit']['relation']) == (dirty['commit']['commit_sha256'], 'uncommitted_changes')
    assert clean['snapshot_id'] != dirty['snapshot_id']


def test_an_inventory_captured_apart_from_the_git_snapshot_is_unknown(tmp_path):
    assert bound(tmp_path, mismatch=True)['commit']['relation'] == 'unknown'


def test_an_inventory_node_outside_the_selected_files_is_unknown_not_matching(tmp_path):
    root = fixture.build(tmp_path / 'c', hostile=False)
    declared = capture(root)
    selected = tuple(HarnessFile(n.path_sha256, n.content_sha256, n.mode, n.content_sha256, 0o100755 if n.mode & 0o111 else 0o100644)
                     for n in declared.nodes[1:])
    harness = HarnessSnapshot(sha('head-1'), selected)
    commit = history.snapshot(replace(declared, harness_sha256=harness.sha256), harness)['commit']
    assert commit['relation'] == 'unknown'


# ---- diff and change sets ------------------------------------------------------------------------------------
def pair(tmp_path, change):
    root = fixture.build(tmp_path / 'c', hostile=False)
    before = history.snapshot(capture(root))
    change(root)
    return before, history.snapshot(capture(root))


def test_diff_names_the_modified_surface_by_hash_and_the_field_that_changed(tmp_path):
    before, after = pair(tmp_path, lambda root: (root / SKILL).write_text('edited\n'))
    delta = history.diff(before, after)
    assert delta['changes'] == [dict(root='checkout', path_sha256=digest(SKILL), change='modified', fields=['content_sha256'])]
    assert (delta['from_snapshot'], delta['to_snapshot'], delta['cause']) == (before['snapshot_id'], after['snapshot_id'], 'not_established')
    assert SKILL not in json.dumps(delta)


def test_diff_reports_a_mode_only_change_as_a_mode_change(tmp_path):
    before, after = pair(tmp_path, lambda root: (root / HOOK).chmod(0o700))
    assert [(c['change'], c['fields']) for c in history.diff(before, after)['changes']] == [('modified', ['mode'])]


def test_diff_swaps_added_and_removed_when_the_snapshots_swap(tmp_path):
    before, after = pair(tmp_path, lambda root: (root / SKILL).unlink())
    assert [c['change'] for c in history.diff(before, after)['changes']] == ['removed']
    assert [c['change'] for c in history.diff(after, before)['changes']] == ['added']


def test_diff_is_deterministic_and_empty_for_the_same_harness(tmp_path):
    first, second = snap(tmp_path, 'one'), snap(tmp_path, 'two')
    assert history.diff(first, second) == history.diff(first, second)
    assert history.diff(first, second)['changes'] == [] and history.diff(first, second)['context_changed'] == []


def test_diff_reports_declared_context_changes_apart_from_files(tmp_path):
    root = fixture.build(tmp_path / 'c', hostile=False)
    delta = history.diff(history.snapshot(capture(root)), history.snapshot(capture(root, dict(CLAUDE, runtime='codex', version='0.166.0'))))
    assert 'runtime' in delta['context_changed'] and 'version' in delta['context_changed']


def test_diff_refuses_an_undeclared_or_edited_snapshot(tmp_path):
    good, unsupported = snap(tmp_path), snap(tmp_path, 'u', profile=dict(CLAUDE, version='0.0.1'))
    forged = json.loads(json.dumps(good))
    forged['runtime'] = 'codex'
    for bad in (unsupported, forged):
        with pytest.raises(ValueError):
            history.diff(good, bad)


def test_change_set_associates_two_snapshots_with_the_surfaces_between_them_and_no_cause(tmp_path):
    before, after = pair(tmp_path, lambda root: (root / SKILL).write_text('edited\n'))
    association = history.change_set('plan-7', before, after)
    assert association['surfaces'] == history.diff(before, after)['changes']
    assert (association['change_set'], association['cause']) == ('plan-7', 'not_established')
    assert history.check_change_set(association) is association


def test_change_set_refuses_identical_snapshots_and_unlabelled_names(tmp_path):
    first, second = snap(tmp_path, 'one'), snap(tmp_path, 'two')
    with pytest.raises(ValueError, match='two different snapshots'):
        history.change_set('plan-7', first, second)
    before, after = pair(tmp_path / 'p', lambda root: (root / SKILL).write_text('edited\n'))
    with pytest.raises(ValueError, match='label'):
        history.change_set('not a label', before, after)


def test_an_edited_change_set_fails_its_check(tmp_path):
    before, after = pair(tmp_path, lambda root: (root / SKILL).write_text('edited\n'))
    association = history.change_set('plan-7', before, after)
    association['surfaces'] = []
    with pytest.raises(ValueError, match='identity does not match'):
        history.check_change_set(association)


# ---- events --------------------------------------------------------------------------------------------------
KINDS = {
    'harness_changed': dict(from_snapshot=BASE, to_snapshot=OTHER),
    'evaluation_completed': dict(evaluation='eval-1', snapshot=BASE),
    'evaluation_invalidated': dict(evaluation='eval-1', reason='oracle_quarantined'),
    'source_gap': dict(reason='source_unavailable'),
    'grader_quarantined': dict(grader=sha('grader')),
    'baseline_reselected': dict(from_snapshot=BASE, to_snapshot=OTHER, policy=sha('policy')),
    'regression_retested': dict(evaluation='eval-2', snapshot=BASE),
}


def make(kind, **overrides):
    return history.event(kind, at=moment(1), origin='controller_observed', pointer=POINTER, subject='claim-1', **{**KINDS[kind], **overrides})


@pytest.mark.parametrize('kind', sorted(KINDS))
def test_every_event_kind_records_instant_origin_pointer_and_no_cause(kind):
    doc = make(kind)
    assert (doc['kind'], doc['at'], doc['origin'], doc['pointer'], doc['causality']) == (
        kind, '2026-03-01T10:01:00.000000Z', 'controller_observed', POINTER, 'not_established')
    assert history.check_event(doc) is doc and set(history.EVENT_FIELDS) == set(KINDS)


@pytest.mark.parametrize('extra', [dict(cause='the edit'), dict(caused_by='eval-1'), dict(because='edit')], ids=['cause', 'caused-by', 'because'])
def test_an_event_has_no_field_that_could_state_a_cause(extra):
    with pytest.raises(ValueError, match='no cause'):
        make('harness_changed', **extra)


@pytest.mark.parametrize('kind,missing', [('harness_changed', 'to_snapshot'), ('evaluation_completed', 'evaluation'), ('source_gap', 'reason')])
def test_an_event_missing_a_required_field_is_refused(kind, missing):
    fields = {k: v for k, v in KINDS[kind].items() if k != missing}
    with pytest.raises(ValueError):
        history.event(kind, at=moment(1), origin='controller_observed', pointer=POINTER, subject='claim-1', **fields)


def test_an_unknown_event_kind_is_refused():
    with pytest.raises(ValueError):
        history.event('baseline_upgraded', at=moment(1), origin='controller_observed', pointer=POINTER)


@pytest.mark.parametrize('field,value', [
    pytest.param('origin', 'guessed', id='unknown-origin'),
    pytest.param('origin', None, id='no-origin'),
    pytest.param('pointer', None, id='no-pointer'),
    pytest.param('pointer', dict(type='history_run'), id='pointer-without-ref'),
    pytest.param('pointer', dict(type='path', ref='run-1'), id='unknown-pointer-type'),
    pytest.param('pointer', dict(type='history_run', ref='/abs/path'), id='pointer-ref-is-a-path'),
    pytest.param('at', '2026-03-01T10:00:00', id='naive-timestamp'),
    pytest.param('at', 'yesterday', id='not-a-timestamp'),
    pytest.param('at', None, id='no-timestamp'),
])
def test_an_event_without_a_known_origin_pointer_and_instant_is_refused(field, value):
    arguments = dict(at=moment(1), origin='controller_observed', pointer=POINTER, subject='claim-1', **KINDS['harness_changed'])
    arguments[field] = value
    with pytest.raises(ValueError):
        history.event('harness_changed', **arguments)


def test_equivalent_instants_in_different_offsets_give_the_same_event():
    local = history.event('source_gap', at='2026-03-01T07:01:00-03:00', origin='caller_declared', pointer=POINTER, reason='gap')
    utc = history.event('source_gap', at='2026-03-01T10:01:00Z', origin='caller_declared', pointer=POINTER, reason='gap')
    assert local == utc


@pytest.mark.parametrize('kind', ['evaluation_completed', 'evaluation_invalidated', 'regression_retested', 'baseline_reselected'])
def test_events_that_bear_on_a_claim_name_its_subject(kind):
    with pytest.raises(ValueError, match='subject'):
        history.event(kind, at=moment(1), origin='controller_observed', pointer=POINTER, **KINDS[kind])


def test_an_unknown_snapshot_is_recorded_as_none_but_an_unknown_evaluation_is_refused():
    assert make('evaluation_completed', snapshot=None)['fields']['snapshot'] is None
    with pytest.raises(ValueError):
        make('evaluation_completed', evaluation=None)


@pytest.mark.parametrize('edit', [
    pytest.param(lambda d: d.update(at='2020-01-01T00:00:00.000000Z'), id='instant'),
    pytest.param(lambda d: d.update(causality='established'), id='causality'),
    pytest.param(lambda d: d['fields'].update(reason='other'), id='field'),
    pytest.param(lambda d: d.update(extra='x'), id='added-key'),
])
def test_an_edited_event_fails_its_check(edit):
    doc = make('source_gap')
    edit(doc)
    with pytest.raises(ValueError):
        history.check_event(doc)


# ---- baseline policy -----------------------------------------------------------------------------------------
def fixed(declared_at=moment(0), events=(), subject='claim-1'):
    return history.declare_baseline('predeclared_fixed_reference', subject=subject, declared_at=declared_at, baseline=BASE, events=events)


def test_a_fixed_reference_declared_before_any_outcome_is_inferential_eligible():
    policy = fixed(events=[completed('eval-1', 5)])
    assert (policy['claim_basis'], policy['baseline'], policy['supersedes']) == ('inferential_eligible', BASE, None)
    assert history.check_policy(policy, [completed('eval-1', 5)]) is policy


@pytest.mark.parametrize('events', [
    pytest.param([completed('eval-1', 5)], id='outcome-before-declaration'),
    pytest.param([completed('eval-1', 10)], id='outcome-at-the-declaration-instant'),
    pytest.param([history.event('regression_retested', at=moment(3), origin='controller_observed', pointer=POINTER,
                                subject='claim-1', evaluation='eval-9', snapshot=BASE)], id='retest-outcome'),
])
def test_a_fixed_reference_chosen_after_an_outcome_of_its_subject_is_refused(events):
    with pytest.raises(history.BaselineRefused, match='after outcomes'):
        fixed(declared_at=moment(10), events=events)


@pytest.mark.parametrize('events', [
    pytest.param([completed('eval-1', 5, subject='claim-2')], id='outcome-of-another-subject'),
    pytest.param([completed('eval-1', 20)], id='outcome-after-declaration'),
    pytest.param([history.event('evaluation_invalidated', at=moment(1), origin='controller_observed', pointer=POINTER,
                                subject='claim-1', evaluation='eval-1', reason='oracle_quarantined')], id='invalidation-is-not-an-outcome'),
    pytest.param([history.event('source_gap', at=moment(1), origin='controller_observed', pointer=POINTER, reason='gap')], id='source-gap'),
], )
def test_events_that_are_not_earlier_outcomes_of_the_subject_do_not_block_a_fixed_reference(events):
    assert fixed(declared_at=moment(10), events=events)['claim_basis'] == 'inferential_eligible'


@pytest.mark.parametrize('kind,window', [('previous_valid', None), ('rolling_window', 3)])
def test_dynamic_policies_are_descriptive_even_after_outcomes(kind, window):
    policy = history.declare_baseline(kind, subject='claim-1', declared_at=moment(10), window=window, events=[completed('eval-1', 5)])
    assert (policy['claim_basis'], policy['baseline'], policy['window']) == ('descriptive', None, window)


@pytest.mark.parametrize('arguments', [
    pytest.param(dict(policy='predeclared_fixed_reference'), id='fixed-without-baseline'),
    pytest.param(dict(policy='predeclared_fixed_reference', baseline='not-a-digest'), id='fixed-with-bad-digest'),
    pytest.param(dict(policy='predeclared_fixed_reference', baseline=BASE, window=3), id='fixed-with-window'),
    pytest.param(dict(policy='previous_valid', baseline=BASE), id='previous-with-baseline'),
    pytest.param(dict(policy='previous_valid', window=2), id='previous-with-window'),
    pytest.param(dict(policy='rolling_window'), id='rolling-without-window'),
    pytest.param(dict(policy='rolling_window', window=1), id='rolling-window-of-one'),
    pytest.param(dict(policy='rolling_window', window=True), id='rolling-window-bool'),
    pytest.param(dict(policy='rolling_window', window=history.MAX_WINDOW + 1), id='rolling-window-oversized'),
    pytest.param(dict(policy='chosen_after_the_fact', baseline=BASE), id='unknown-policy'),
])
def test_a_malformed_policy_declaration_is_refused(arguments):
    with pytest.raises(ValueError):
        history.declare_baseline(subject='claim-1', declared_at=moment(0), **arguments)


def test_reselecting_a_baseline_records_the_event_and_yields_a_descriptive_policy():
    prior = fixed()
    result = history.reselect(prior, baseline=OTHER, at=moment(30), origin='caller_declared', pointer=POINTER, events=[completed('eval-1', 20)])
    new, marker = result['policy'], result['event']
    assert (new['claim_basis'], new['baseline'], new['supersedes']) == ('descriptive', OTHER, prior['policy_id'])
    assert (marker['kind'], marker['fields']) == ('baseline_reselected', dict(from_snapshot=BASE, to_snapshot=OTHER, policy=new['policy_id']))
    assert prior['claim_basis'] == 'inferential_eligible' and prior['baseline'] == BASE
    assert history.check_policy(new) is new and history.check_event(marker) is marker


def test_reselecting_before_any_outcome_is_still_descriptive():
    result = history.reselect(fixed(), baseline=OTHER, at=moment(1), origin='caller_declared', pointer=POINTER)
    assert result['policy']['claim_basis'] == 'descriptive'


def test_a_reselected_policy_edited_to_inferential_fails_its_check():
    new = history.reselect(fixed(), baseline=OTHER, at=moment(30), origin='caller_declared', pointer=POINTER)['policy']
    upgraded = dict(new, claim_basis='inferential_eligible')
    with pytest.raises(ValueError, match='claim basis was edited'):
        history.check_policy(upgraded)
    with pytest.raises(ValueError):
        history.check_policy(dict(upgraded, supersedes=None))


def test_a_new_fixed_reference_cannot_be_declared_once_outcomes_exist_so_reselection_is_the_only_late_route():
    with pytest.raises(history.BaselineRefused):
        fixed(declared_at=moment(40), events=[completed('eval-1', 20)])


@pytest.mark.parametrize('prior,at', [
    pytest.param(dict(policy='previous_valid'), moment(30), id='dynamic-policy-has-no-fixed-reference'),
    pytest.param(dict(policy='predeclared_fixed_reference', baseline=BASE), '2026-03-01T09:00:00Z', id='before-the-declaration'),
])
def test_reselection_refuses_a_policy_without_a_fixed_reference_and_a_time_before_the_declaration(prior, at):
    policy = history.declare_baseline(subject='claim-1', declared_at=moment(10), **prior)
    with pytest.raises(ValueError):
        history.reselect(policy, baseline=OTHER, at=at, origin='caller_declared', pointer=POINTER)


def test_a_policy_edited_after_declaration_fails_its_check():
    policy = fixed()
    with pytest.raises(ValueError, match='does not match'):
        history.check_policy(dict(policy, baseline=OTHER))
    with pytest.raises(ValueError):
        history.check_policy({'policy': 'previous_valid'})


def test_a_policy_checked_against_events_that_show_an_earlier_outcome_is_refused():
    policy = fixed(declared_at=moment(10))
    with pytest.raises(history.BaselineRefused):
        history.check_policy(policy, [completed('eval-1', 2)])


# ---- resolving a policy against the recorded events ----------------------------------------------------------
SNAP = {n: hashlib.sha256(f'snapshot-{n}'.encode()).hexdigest() for n in range(1, 5)}


def runs(*specs):
    return [completed(f'eval-{n}', minute, snapshot=SNAP.get(n)) for n, minute in specs]


def dynamic(kind='previous_valid', window=None):
    return history.declare_baseline(kind, subject='claim-1', declared_at=moment(0), window=window)


def test_resolving_a_fixed_reference_returns_its_declared_snapshot():
    assert history.resolve(fixed(), [])['snapshots'] == [BASE]


def test_previous_valid_selects_the_latest_valid_evaluation_before_the_instant():
    chosen = history.resolve(dynamic(), runs((1, 5), (2, 6), (3, 7)), before=moment(7))
    assert (chosen['snapshots'], chosen['evaluations'], chosen['claim_basis']) == ([SNAP[2]], ['eval-2'], 'descriptive')


def test_an_evaluation_invalidated_at_any_time_is_never_selected():
    invalidation = history.event('evaluation_invalidated', at=moment(50), origin='controller_observed', pointer=POINTER,
                                 subject='claim-1', evaluation='eval-2', reason='oracle_quarantined')
    chosen = history.resolve(dynamic(), [*runs((1, 5), (2, 6)), invalidation], before=moment(20))
    assert chosen['evaluations'] == ['eval-1']


def test_rolling_window_selects_the_last_n_valid_evaluations_in_time_order():
    chosen = history.resolve(dynamic('rolling_window', 2), runs((3, 7), (1, 5), (2, 6), (4, 30)), before=moment(10))
    assert chosen['evaluations'] == ['eval-2', 'eval-3']


def test_an_evaluation_of_another_subject_is_not_selected():
    other = completed('eval-9', 6, subject='claim-2')
    assert history.resolve(dynamic(), [other], before=moment(20))['status'] == 'unknown'


def test_a_completed_evaluation_with_an_unknown_snapshot_is_counted_and_never_guessed():
    events = [completed('eval-1', 5, snapshot=SNAP[1]), completed('eval-2', 6, snapshot=None)]
    chosen = history.resolve(dynamic(), events, before=moment(20))
    assert (chosen['evaluations'], chosen['skipped_unknown_snapshot']) == (['eval-1'], 1)


def test_with_no_valid_evaluation_the_baseline_is_unknown_not_empty_success():
    chosen = history.resolve(dynamic(), [], before=moment(20))
    assert (chosen['status'], chosen['snapshots'], chosen['evaluations']) == ('unknown', [], [])


def test_a_dynamic_policy_needs_the_instant_it_selects_before():
    with pytest.raises(ValueError, match='instant'):
        history.resolve(dynamic(), [])


# ---- memory store --------------------------------------------------------------------------------------------
@pytest.fixture
def store(tmp_path):
    return MemoryStore.init(tmp_path / 'store')


def test_documents_round_trip_through_the_store_and_the_timeline_is_in_time_order(store, tmp_path):
    late, early = completed('eval-2', 9), completed('eval-1', 3)
    first = history.publish(store, 'harness-history', late)
    assert history.publish(store, 'harness-history', late) == first  # idempotent
    history.publish(store, 'harness-history', early)
    history.publish(store, 'harness-history', fixed())
    history.publish(store, 'harness-history', snap(tmp_path))
    assert history.timeline(store, 'harness-history') == [early, late]
    assert history.timeline(store, 'another-project') == []
    assert {row['kind'] for row in store.artifacts('harness-history')['artifacts']} == {'capture', 'manifest'}


@pytest.mark.parametrize('document', [
    pytest.param(None, id='none'),
    pytest.param({'profile': 'something-else'}, id='unknown-profile'),
    pytest.param({'profile': ['list']}, id='unhashable-profile'),
    pytest.param(dict(completed('eval-1', 3), at='2020-01-01T00:00:00.000000Z'), id='edited-event'),
])
def test_publish_refuses_a_document_that_does_not_check(store, document):
    with pytest.raises(ValueError):
        history.publish(store, 'harness-history', document)
    assert store.artifacts('harness-history')['artifacts'] == []


class Listing:
    def __init__(self, truncated=False, error=None):
        self.truncated, self.error = truncated, error

    def artifacts(self, project_id, limit):
        return dict(artifacts=[dict(digest=BASE, kind='manifest', size=1)], truncated=self.truncated)

    def read(self, digest):
        if self.error:
            raise self.error
        return b'{}'


def test_a_timeline_longer_than_one_listing_is_refused_rather_than_truncated():
    with pytest.raises(ValueError, match='dedicated project id'):
        history.timeline(Listing(truncated=True), 'harness-history')


def test_an_unreadable_object_fails_the_timeline_instead_of_being_skipped():
    with pytest.raises(MemoryStoreError):
        history.timeline(Listing(error=MemoryStoreError('object failed integrity verification')), 'harness-history')
