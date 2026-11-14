"""ahl analyze --incremental: verified reuse of the process-mining stage, parity, invalidation and fail-closed paths.

Synthetic events and a synthetic project only; no model, network or private history."""
import argparse
import hashlib
import json
from pathlib import Path

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.memory import incremental
from agent_harness_lab.memory.store import MemoryStore, StoreBusy

STAGE = incremental.STAGE


def events_text(cases=('a', 'b', 'c'), extra=0):
    rows = []
    for case in cases:
        for index, activity in enumerate(('prompt', 'tool_call', 'tool_result', 'answer')):
            rows.append({'case_id': case, 'event_id': f'{case}{index}', 'activity': activity, 'actor': 'human' if index == 0 else 'agent',
                         'timestamp': f'2026-01-01T00:00:0{index}Z'})
    rows += [{'case_id': 'extra', 'event_id': f'x{n}', 'activity': 'tool_call', 'timestamp': f'2026-01-01T00:01:0{n}Z'} for n in range(extra)]
    return ''.join(json.dumps(row) + '\n' for row in rows)


@pytest.fixture
def world(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'AGENTS.md').write_text('Prefer small changes.\n')
    capture = tmp_path / 'events.jsonl'
    capture.write_text(events_text())
    store = MemoryStore.init(tmp_path / 'store', excluded=[project])
    return type('World', (), {'root': tmp_path, 'project': project, 'events': capture, 'store': store, 'runs': 0})()


def analyze(world, *flags, incremental_flags=True, capsys):
    """Run `ahl analyze` and return (exit code, stdout JSON or None, stderr)."""
    world.runs += 1
    argv = ['analyze', str(world.project), '--events', str(world.events), '--output', str(world.root / f'out{world.runs}')]
    if incremental_flags:
        argv += ['--memory-store', str(world.store.root), '--incremental']
    capsys.readouterr()
    try:
        code = main([*argv, *flags])
    except SystemExit as stop:
        code = stop.code
    captured = capsys.readouterr()
    return code, json.loads(captured.out) if captured.out.strip().startswith('{') else None, captured.err


def reuse(world, *flags, capsys):
    code, result, error = analyze(world, '--explain-reuse', *flags, capsys=capsys)
    assert code == 0, error
    return result['reuse'], Path(result['output'])


def test_second_run_reuses_the_stage_and_its_output_is_byte_identical(world, capsys):
    code, plain, _ = analyze(world, incremental_flags=False, capsys=capsys)
    assert code == 0 and 'reuse' not in plain
    first, first_out = reuse(world, capsys=capsys)
    second, second_out = reuse(world, capsys=capsys)
    assert (first['outcome'], first['reason'], first['commit']) == ('executed', 'miss_no_prior', 'committed')
    assert (second['outcome'], second['reason'], second['commit']) == ('reused', 'hit_verified', 'not_needed')
    # computed from stage counts, never trials: the gate is at least 90% of eligible deterministic stages
    assert second['counts'] == {'reused_deterministic': 1, 'executed_deterministic': 0} and second['reused_fraction'] >= 0.9
    for name in ('analysis.json', 'report.md'):
        assert (Path(plain['output']) / name).read_bytes() == (first_out / name).read_bytes() == (second_out / name).read_bytes()
    assert second['receipt']['reused_live_trials'] == 0 and second['publication_allowed'] is False
    assert {row['reason'] for row in second['receipt']['excluded_historical']} == {'nonreplayable_live_trial'}


def test_counters_keep_cache_kinds_and_unknown_savings_apart(world, capsys):
    reuse(world, capsys=capsys)
    second, _ = reuse(world, capsys=capsys)
    counters = second['counters']
    assert counters['local_deterministic_cache']['avoided_stages'] == 1
    assert counters['local_deterministic_cache']['avoided_elapsed_seconds'] == 'unknown'
    assert counters['provider_cache'] == {'status': 'not_applicable'}
    assert counters['historical_reuse'] == {'reused_live_trials': 0}
    assert counters['tokens'] == {'model_calls': 0, 'billing_savings': 'unknown'}


def test_without_explain_reuse_the_block_is_compact(world, capsys):
    code, result, _ = analyze(world, capsys=capsys)
    assert code == 0
    assert 'receipt' not in result['reuse'] and 'inputs' not in result['reuse'] and result['reuse']['stage'] == STAGE


def test_one_changed_event_misses_and_the_earlier_run_is_still_reusable(world, capsys):
    reuse(world, capsys=capsys)
    world.events.write_text(events_text(extra=2))
    changed, _ = reuse(world, capsys=capsys)
    assert (changed['outcome'], changed['reason']) == ('executed', 'miss_input_changed')
    assert changed['compared_to'] == {'prior_sequence': 1, 'capture': 'changed', 'harness': 'same'}
    world.events.write_text(events_text())
    restored, _ = reuse(world, capsys=capsys)
    assert (restored['outcome'], restored['reason']) == ('reused', 'hit_verified')  # the exact key is looked up, not only the newest


def test_instruction_change_is_a_new_harness_identity_and_reuses_nothing(world, capsys):
    before, _ = reuse(world, capsys=capsys)
    (world.project / 'AGENTS.md').write_text('Prefer small changes, then test.\n')
    after, _ = reuse(world, capsys=capsys)
    assert after['inputs']['harness_sha256'] != before['inputs']['harness_sha256']
    assert after['inputs']['events_sha256'] == before['inputs']['events_sha256']
    assert (after['outcome'], after['reason']) == ('executed', 'miss_input_changed')
    assert after['compared_to']['harness'] == 'changed' and after['compared_to']['capture'] == 'same'
    assert after['receipt']['reused'] == [] and after['receipt']['reused_live_trials'] == 0


def test_added_instruction_file_changes_the_identity(world, capsys):
    before, _ = reuse(world, capsys=capsys)
    (world.project / 'CLAUDE.md').write_text('@AGENTS.md\n')
    after, _ = reuse(world, capsys=capsys)
    assert after['inputs']['harness_sha256'] != before['inputs']['harness_sha256'] and after['outcome'] == 'executed'


def test_runs_commit_once_and_survive_a_rebuilt_index(world, capsys):
    reuse(world, capsys=capsys)
    reuse(world, capsys=capsys)
    pid = hashlib.sha256(str(world.project).encode()).hexdigest()
    assert [r['status'] for r in world.store.stage_rows(pid, STAGE)] == ['complete']
    assert world.store.reindex()['rebuilt'] and world.store.verify()['ok']
    again, _ = reuse(world, capsys=capsys)
    assert again['outcome'] == 'reused'


def test_flags_absent_leave_analyze_unchanged_and_never_open_a_store(world, capsys):
    code, result, _ = analyze(world, incremental_flags=False, capsys=capsys)
    assert code == 0 and 'reuse' not in result and world.store.artifacts(hashlib.sha256(str(world.project).encode()).hexdigest())['artifacts'] == []


@pytest.mark.parametrize('flags, message', [
    (['--incremental'], 'explicit --memory-store'),
    (['--memory-store', '/x'], 'require --incremental'),
    (['--explain-reuse'], 'require --incremental'),
])
def test_flag_contract_is_checked_before_any_output_exists(world, capsys, flags, message):
    argv = ['analyze', str(world.project), '--events', str(world.events), '--output', str(world.root / 'never'), *flags]
    with pytest.raises(SystemExit) as stop:
        main(argv)
    assert stop.value.code == 2 and message in capsys.readouterr().err and not (world.root / 'never').exists()


def test_discovery_and_pinned_native_profiles_are_not_incremental(world, capsys):
    argv = ['analyze', str(world.project), '--memory-store', str(world.store.root), '--incremental']
    with pytest.raises(SystemExit) as stop:
        main(argv)
    assert stop.value.code == 2 and 'explicit --events' in capsys.readouterr().err
    from agent_harness_lab.telemetry.codex_rollout import VERSION
    code, _, error = analyze(world, '--format', 'codex', '--source-version', VERSION, capsys=capsys)
    assert code == 2 and 'pinned native' in error


def test_isolated_roles_are_denied_without_touching_the_store(world, capsys, monkeypatch):
    monkeypatch.setenv('AHL_SANDBOX_ROLE', 'worker')
    code, _, error = analyze(world, capsys=capsys)
    assert code == 2 and 'isolated roles' in error
    assert world.store.stage_rows(hashlib.sha256(str(world.project).encode()).hexdigest(), STAGE) == []


def test_a_store_overlapping_the_project_is_refused(world, capsys):
    world.project = world.root  # the project now contains the store
    code, _, error = analyze(world, capsys=capsys)
    assert code == 2 and 'overlaps' in error


def test_a_symlinked_harness_file_or_capture_is_refused(world, capsys):
    real = world.root / 'real-agents.md'
    real.write_text('x')
    (world.project / 'AGENTS.md').unlink()
    (world.project / 'AGENTS.md').symlink_to(real)
    code, _, error = analyze(world, capsys=capsys)
    assert code == 2 and 'Symlinked harness' in error
    (world.project / 'AGENTS.md').unlink()
    (world.project / 'AGENTS.md').write_text('x')
    link = world.root / 'linked.jsonl'
    link.symlink_to(world.events)
    world.events = link
    code, _, error = analyze(world, capsys=capsys)
    assert code == 2 and 'symlink' in error


def test_a_failed_analysis_leaves_a_diagnostic_that_is_never_reused(world, capsys):
    code, _, _ = analyze(world, '--max-events', '3', capsys=capsys)
    assert code == 2
    pid = hashlib.sha256(str(world.project).encode()).hexdigest()
    assert [r['status'] for r in world.store.stage_rows(pid, STAGE)] == ['incomplete']
    first, _ = reuse(world, capsys=capsys)  # the interrupted record is not a prior: nothing to reuse
    assert (first['outcome'], first['reason'], first['commit']) == ('executed', 'miss_no_prior', 'committed')
    assert sorted(r['status'] for r in world.store.stage_rows(pid, STAGE)) == ['complete', 'incomplete']


def test_a_corrupted_stored_output_is_not_reused_and_not_overwritten(world, capsys):
    first, _ = reuse(world, capsys=capsys)
    pid = hashlib.sha256(str(world.project).encode()).hexdigest()
    output = world.store.stage_rows(pid, STAGE)[0]['output']
    path = world.store.root / 'objects/sha256' / output[:2] / output
    path.chmod(0o600)
    path.write_bytes(b'{"profile":"ahl-analysis-process-graph-v1","events":999}')
    code, result, error = analyze(world, '--explain-reuse', capsys=capsys)
    assert code == 0, error
    block = result['reuse']
    assert (block['outcome'], block['reason']) == ('executed', 'invalid_integrity') and block['commit'].startswith('withheld')
    assert (Path(result['output']) / 'analysis.json').read_bytes() == (world.root / 'out1/analysis.json').read_bytes()


def publish_record(world, output, *, sequence):
    """Write a complete record for the current key whose output differs from what the miner produces."""
    pid = hashlib.sha256(str(world.project).encode()).hexdigest()
    row = world.store.stage_rows(pid, STAGE, limit=1)[0]
    record = json.loads(world.store.read(row['record']))
    raw = incremental.encode(dict(record, output=output, sequence=sequence), sort=True)
    entry = {'record': hashlib.sha256(raw).hexdigest(), 'stage': STAGE, 'node_key': record['node_key'], 'status': 'complete',
             'output': output, 'sequence': sequence}
    world.store.publish(pid, 'manifest', raw, dependencies=[output], records=[{'table': 'stage_records', 'row': entry}])


def test_a_reused_output_that_disagrees_with_the_events_fails_closed(world, capsys):
    reuse(world, capsys=capsys)
    pid = hashlib.sha256(str(world.project).encode()).hexdigest()
    good = json.loads(world.store.read(world.store.stage_rows(pid, STAGE)[0]['output']))
    forged = world.store.publish(pid, 'analysis', incremental.encode(dict(good, events=good['events'] + 1)))
    publish_record(world, forged, sequence=2)
    code, _, error = analyze(world, capsys=capsys)
    assert code == 2 and 'disagrees with the analyzed events' in error


def test_a_capture_that_changes_while_analyzed_fails_closed(world, capsys, monkeypatch):
    monkeypatch.setattr(incremental, 'file_sha256', lambda path: 'f' * 64)  # the digest planned on is not the bytes then read
    code, _, error = analyze(world, capsys=capsys)
    assert code == 2 and 'changed while it was analyzed' in error


def test_an_unavailable_store_write_keeps_the_analysis_and_says_so(world, capsys, monkeypatch):
    def busy(*args, **options):
        raise StoreBusy('another writer holds the memory store')
    monkeypatch.setattr(MemoryStore, 'publish', busy)
    code, result, _ = analyze(world, capsys=capsys)
    assert code == 0 and result['reuse']['commit'].startswith('failed') and result['reuse']['outcome'] == 'executed'


def test_a_missing_store_is_an_input_error(world, capsys):
    world.store.root.rename(world.root / 'moved')
    code, _, error = analyze(world, capsys=capsys)
    assert code == 2 and 'does not exist' in error and not (world.root / 'out1').exists()



def test_committing_the_same_key_twice_is_idempotent(world):
    args = argparse.Namespace(incremental=True, explain_reuse=False, memory_store=world.store.root, events=world.events,
                              format='normalized', source_version=None, project=world.project, output=None)
    stage = incremental.Incremental(args)
    mining = {'profile': incremental.PROFILE, 'events': 0}
    assert [stage._commit(mining), stage._commit(mining)] == ['committed', 'already_committed']
    assert len(world.store.stage_rows(stage.project_id, STAGE)) == 1


def test_an_output_without_a_stable_canonical_form_is_refused_not_stored(world):
    args = argparse.Namespace(incremental=True, explain_reuse=False, memory_store=world.store.root, events=world.events,
                              format='normalized', source_version=None, project=world.project, output=None)
    stage = incremental.Incremental(args)
    for unstable in ({'profile': incremental.PROFILE, 'edges': (1, 2)}, {'profile': incremental.PROFILE, 'rate': float('nan')}):
        assert stage._commit(unstable).startswith('refused')
    assert world.store.stage_rows(stage.project_id, STAGE) == []
