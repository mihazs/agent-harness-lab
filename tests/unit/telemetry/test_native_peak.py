"""Synthetic native captures: draining decoded rows lowers peak memory without changing any output."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tracemalloc
from unittest.mock import patch

import pytest

STAMP = '2026-01-01T00:00:00Z'
SESSIONS = tuple(f'00000000-0000-4000-8000-00000000000{n}' for n in range(1, 5))
# Never created: telemetry readers only compare it with declared cwd values, so digests are path-independent.
FIXED_PROJECT = Path('/ahl-synthetic/project')
# Computed by the implementation before rows were drained (commit d64c0e7) from fixture(..., CALLS). The Claude entries were
# refreshed when its summaries gained typed skipped-entry and version fields; its event records hash the same as before.
# The discovery digest was refreshed when 2.1.317 and again when 2.1.322 joined the declared set: the only change is verified_versions and newest_verified_version.
CALLS = 300
EXPECTED = {
    'codex_read': '0d68b2a1b4c1b012303da05a794b791970445857af8723d2baf05fc1207f0d85',
    'claude_read': '0053dffe38b7a1ed3b1ba8082c6a6b77af788681ecc0a1747dc6ca6cd73d2234',
    'codex_discovery': 'b0a1406ea7cdb2cee174d39a63a5826d0b45efe49a7935714179f8240a3b811a',
    'claude_discovery': 'a2a89b863e34e3b66d25c9f96258e1c793d93912c2e48b4940e059d215fb297f',
}


def codex_rows(project, thread, calls):
    def row(kind, payload, ordinal): return dict(type=kind, payload=payload, timestamp=STAMP, ordinal=ordinal)
    meta = dict(id=thread, session_id='root-'+thread, cli_version='0.166.0', cwd=str(project), history_mode='paginated',
        base_instructions=dict(text='synthetic'), timestamp=STAMP, originator='codex_cli_rs', source='cli', model_provider=None)
    rows = [row('session_meta', meta, 0), row('turn_context', dict(cwd=str(project), model='synthetic', turn_id='turn'), 1)]
    for i in range(calls):
        ordinal, call = len(rows), f'call-{i % (calls//2 or 1)}'  # repeated ids exercise duplicate correlation
        rows.append(row('response_item', dict(type='function_call', name='exec_command', call_id=call, arguments='x'*40), ordinal))
        rows.append(row('event_msg', dict(type='exec_command_end', call_id=call, exit_code=i % 3, aggregated_output='y'*40), ordinal+1))
        if i % 7: rows.append(row('response_item', dict(type='function_call_output', call_id=call, output='z'*40), ordinal+2))
    rows.append(row('event_msg', dict(type='turn_complete', turn_id='turn', last_agent_message='done'), len(rows)))
    return rows


def claude_rows(project, session, calls):
    def row(kind, ident, parent, message):
        return dict(type=kind, uuid=ident, parentUuid=parent, timestamp=STAMP, sessionId=session, cwd=str(project),
            version='2.1.316', isSidechain=False, message=message)
    rows, parent = [row('user', 'u0', None, dict(role='user', content='synthetic'))], 'u0'
    for i in range(calls):
        # Two assistant entries share a response so usage and stop facets are merged, and some results are missing.
        response = f'response-{i//2}'
        rows.append(row('assistant', f'a{i}', parent, dict(role='assistant', id=response, model='synthetic',
            usage=dict(input_tokens=i % 11, output_tokens=i % 5), stop_reason='tool_use',
            content=[dict(type='tool_use', id=f'call-{i}', name='Bash', input=dict(command='x'*40))])))
        if i % 7:
            rows.append(row('user', f'r{i}', f'a{i}', dict(role='user', content=[dict(type='tool_result', tool_use_id=f'call-{i}',
                is_error=i % 3 == 0, content='y'*40)])))
        parent = rows[-1]['uuid']
    return rows


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    return path


def fixture(root, project, calls):
    """Single-file and discovery captures for both pinned formats."""
    from agent_harness_lab.telemetry.claude_discovery import directory_names
    write(root/'codex.jsonl', codex_rows(project, 'thread-a', calls))
    for k in range(3): write(root/f'captures/sessions/rollout-2026-{k}.jsonl', codex_rows(project, f'thread-{k}', calls//3))
    # An archived prefix copy merges with the active capture before correlation.
    write(root/'captures/archives/rollout-2026-copy.jsonl', codex_rows(project, 'thread-0', calls//3)[:calls//4])
    write(root/'claude.jsonl', claude_rows(project, SESSIONS[0], calls))
    name = directory_names(project)[0]
    for session in SESSIONS[:3]: write(root/f'claude/projects/{name}/{session}.jsonl', claude_rows(project, session, calls//3))
    write(root/f'archive/projects/{name}/{SESSIONS[0]}.jsonl', claude_rows(project, SESSIONS[0], calls//3)[:calls//4])


def plain(value):
    if is_dataclass(value): return {k: plain(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        # Declared source order was added later and is pinned in test_claude_source_order; every other field stays pinned here.
        from agent_harness_lab.telemetry.schema import SOURCE_ORDER
        if 'source_order_profile' in value: value = {k: v for k, v in value.items() if k not in SOURCE_ORDER}
        # The working-directory class and drift counts were added later and are pinned in test_claude_cwd_drift;
        # the input fingerprint is pinned in test_native_input_fingerprints and the error class in test_native_error_classes.
        value = {k: v for k, v in value.items() if k not in ('cwd_class', 'cwd_drift', 'input_fingerprint', 'tool_error_class')}
        return {repr(k) if isinstance(k, tuple) else k: plain(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)): return sorted((plain(v) for v in value), key=repr)
    if isinstance(value, (list, tuple)): return [plain(v) for v in value]
    return value


def fingerprint(value):
    """Order-sensitive digest: dict insertion order is kept, so key or record reordering is detected."""
    return hashlib.sha256(json.dumps(plain(value)).encode()).hexdigest()


def telemetry_outputs(root, project):
    from agent_harness_lab.telemetry import claude_discovery, claude_native, codex_discovery, codex_rollout
    result = dict(codex_read=codex_rollout.read(root/'codex.jsonl', project),
        claude_read=claude_native.read(root/'claude.jsonl', project))
    revisions = {}
    with codex_discovery.capture(root/'captures', project, revisions=revisions) as (records, summary, sha256, _):
        result['codex_discovery'] = records, summary, sha256, revisions
    revisions = {}
    with claude_discovery.capture([root/'claude/projects', root/'archive/projects'], project, revisions=revisions) as captured:
        records, summary, sha256, _, sessions = captured
        result['claude_discovery'] = records, summary, sha256, revisions, sessions
    return result


def digests(root, calls=CALLS):
    """Recompute EXPECTED under an empty root; run against a previous implementation to refresh the reference."""
    fixture(root, FIXED_PROJECT, calls)
    return {name: fingerprint(value) for name, value in telemetry_outputs(root, FIXED_PROJECT).items()}


@contextmanager
def undrained():
    """Reference behaviour: decoded rows stay referenced until the whole capture has been consumed."""
    with patch('agent_harness_lab.telemetry.native.drained', lambda rows: iter(tuple(rows))):
        yield


class FixedNow:
    @staticmethod
    def now(tz=None): return datetime(2026, 2, 1, tzinfo=timezone.utc)


SOURCES = {
    'codex_v1': ('codex-rollout', '0.166.0-v1', dict(schema_version=1, project='project', capture='codex.jsonl')),
    'codex_v2': ('codex-rollout', '0.166.0-v2', dict(schema_version=1, project='project', source_root='captures', session=None)),
    'claude_v1': ('claude-jsonl', '2.1.316-v1', dict(schema_version=1, project='project', capture='claude.jsonl')),
    'claude_v2': ('claude-jsonl', '2.1.316-v2', dict(schema_version=1, project='project',
        source_roots=['claude/projects', 'archive/projects'], session=None)),
}


def test_telemetry_outputs_match_the_reference_computed_before_draining(tmp_path):
    assert digests(tmp_path) == EXPECTED


class Tree:
    """A synthetic project beside native captures holding the given number of calls."""

    def __init__(self, root, calls):
        self.root = root.resolve(); self.project = self.root/'project'; self.project.mkdir()
        fixture(self.root, self.project, calls)

    def declare(self, key):
        from agent_harness_lab.sources.schema import SourceSpec, digest
        provider, version, selected = SOURCES[key]
        source = SourceSpec('local_file', provider, version, digest(str(self.root)), digest('synthetic-project'), digest(selected),
            identity_provenance='source_declared_id', enabled=True)
        (self.root/f'{key}.selection.json').write_text(json.dumps(selected))
        manifest = self.root/f'{key}.source.json'
        manifest.write_text(json.dumps(dict(schema_version=1, source=source.to_dict(), data=f'{key}.selection.json')))
        return source, manifest

    def source_outputs(self, tag):
        from agent_harness_lab.cli import main
        from agent_harness_lab.evidence import collect
        from agent_harness_lab.sources.claude import ClaudeCapture
        from agent_harness_lab.sources.codex import CodexCapture
        result = {}
        with patch('agent_harness_lab.sources.native.datetime', FixedNow):
            for key in SOURCES:
                source, manifest = self.declare(key)
                reader = (CodexCapture if key.startswith('codex') else ClaudeCapture)(manifest)
                result['snapshot_'+key] = fingerprint((reader.read(source), reader.summary))
                cache, out = self.root/f'journal-{key}-{tag}.json', io.StringIO()
                with redirect_stdout(out), redirect_stderr(io.StringIO()):
                    code = main(['sources', 'import', '--manifest', str(manifest), '--project-sha256', source.project_sha256,
                        '--scope-sha256', source.scope_sha256, '--cache', str(cache)])
                assert code == 0
                result['journal_'+key] = cache.read_bytes(), out.getvalue()
        for name, options in (('codex', dict(events=self.root/'codex.jsonl', source_format='codex', source_version='codex-rollout-0.166.0')),
                ('codex_root', dict(source_root=self.root/'captures', source_format='codex', source_version='codex-rollout-0.166.0')),
                ('claude', dict(events=self.root/'claude.jsonl', source_format='claude', source_version='claude-jsonl-2.1.316'))):
            sink = []
            result['collect_'+name] = fingerprint((collect(self.project, event_sink=sink.append, **options), sink))
        return result


SOURCE_OUTPUTS = (*(f'{kind}_{key}' for key in SOURCES for kind in ('snapshot', 'journal')),
                  'collect_codex', 'collect_codex_root', 'collect_claude')


@pytest.fixture(scope='module')
def drained_outputs(tmp_path_factory):
    """Each source output computed once with undrained rows (the reference) and once as implemented."""
    tree = Tree(tmp_path_factory.mktemp('drained'), 120)
    with undrained(): reference = tree.source_outputs('reference')
    return reference, tree.source_outputs('candidate')


def test_snapshots_journal_bytes_and_collection_cover_the_same_outputs(drained_outputs):
    reference, candidate = drained_outputs
    assert candidate.keys() == reference.keys()
    assert sorted(reference) == sorted(SOURCE_OUTPUTS)


@pytest.mark.parametrize('name', SOURCE_OUTPUTS)
def test_snapshots_journal_bytes_and_collection_match_undrained_reference(drained_outputs, name):
    reference, candidate = drained_outputs
    assert candidate[name] == reference[name]


def test_draining_preserves_correlation_of_lists_tuples_and_deques():
    from collections import deque
    from agent_harness_lab.telemetry.adapters import correlate
    from agent_harness_lab.telemetry.native import correlated
    from agent_harness_lab.telemetry.schema import event
    common = dict(case_id='case', root_case_id='root', lineage_verified=False, capture_complete=False,
        source_format='codex', source_version='codex-rollout-0.166.0', runtime_fingerprint='f'*64)
    activities = ('tool_call', 'tool_result', 'user_message')
    for seed in range(40):
        rows = tuple(event(dict(common, event_id=str(i), capture_order=i, activity=activities[(i*seed+i//3) % 3],
            call_id=f'c{(i*7+seed) % 5}')) for i in range(seed % 17 + 1))
        # Former contract: a bounded pending queue sized by the materialized capture.
        expected = [event({**common, **row}) for row in correlate(rows, max_pending=len(rows))]
        for value in (list(rows), rows, deque(rows)):
            assert list(correlated(value, lambda: None)) == expected
        assert expected == [event({**common, **row}) for row in correlate(iter(rows))]
    drained = deque(rows); list(correlated(drained, lambda: None)); assert not drained


def peak(operation):
    tracemalloc.start()
    try:
        base = tracemalloc.get_traced_memory()[0]; value = operation()
        current, peak = tracemalloc.get_traced_memory()
    finally: tracemalloc.stop()
    del value
    return peak-base, current-base


@pytest.fixture(scope='module')
def large(tmp_path_factory):
    return Tree(tmp_path_factory.mktemp('large'), 3000)


def claude_scan(tree):
    from agent_harness_lab.telemetry import claude_discovery
    with claude_discovery.capture([tree.root/'claude/projects', tree.root/'archive/projects'], tree.project) as captured:
        return captured[0]


def codex_read(tree):
    from agent_harness_lab.telemetry import codex_rollout
    return codex_rollout.read(tree.root/'codex.jsonl', tree.project)


def codex_scan(tree):
    from agent_harness_lab.telemetry import codex_discovery
    return codex_discovery.scan(tree.root/'captures', tree.project)


# Peak over the retained events. Holding decoder rows beside the events measured 1.42-1.64 before
# draining and 1.12-1.25 after, stable from 1.5k to 20k calls; bounds sit between the two.
@pytest.mark.parametrize(('name', 'operation', 'bound'), [
    pytest.param('codex_read', codex_read, 1.27, id='codex_read'),
    pytest.param('codex_discovery', codex_scan, 1.43, id='codex_discovery'),
    pytest.param('claude_discovery', claude_scan, 1.25, id='claude_discovery')])
def test_native_reads_hold_one_copy_of_decoded_events_at_peak(large, name, operation, bound):
    peak_bytes, retained = peak(lambda: operation(large))
    assert peak_bytes/retained < bound, (name, peak_bytes, retained)


def test_native_collection_holds_fewer_copies_than_the_former_peak(large):
    from agent_harness_lab.evidence import collect
    events = peak(lambda: codex_read(large))[1]
    peak_bytes, _ = peak(lambda: collect(large.project, events=large.root/'codex.jsonl', source_format='codex',
        source_version='codex-rollout-0.166.0'))
    # Former peak: the admitted tuple plus normalized and analysed copies (1.64 of the events).
    assert peak_bytes/events < 1.35, (peak_bytes, events)


def test_native_snapshot_releases_decoder_rows_before_the_snapshot(large):
    from agent_harness_lab.sources.codex import CodexCapture
    source, manifest = large.declare('codex_v1')
    with undrained(): before, _ = peak(lambda: CodexCapture(manifest).read(source))
    after, _ = peak(lambda: CodexCapture(manifest).read(source))
    # snapshot() dominates; releasing decoder rows before it removes about 10% of the peak.
    assert after < 0.95*before, (before, after)
