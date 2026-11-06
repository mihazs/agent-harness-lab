"""Synthetic native captures: draining decoded rows lowers peak memory without changing any output."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

STAMP = '2026-01-01T00:00:00Z'
SESSIONS = tuple(f'00000000-0000-4000-8000-00000000000{n}' for n in range(1, 5))
# Never created: telemetry readers only compare it with declared cwd values, so digests are path-independent.
FIXED_PROJECT = Path('/ahl-synthetic/project')
# Computed by the implementation before rows were drained (commit d64c0e7) from fixture(..., CALLS).
CALLS = 300
EXPECTED = {
    'codex_read': '0d68b2a1b4c1b012303da05a794b791970445857af8723d2baf05fc1207f0d85',
    'claude_read': 'db2e68e46b8095370dcafbfab65c87d058684b31a63d6768ef19a7e93a9fdc4c',
    'codex_discovery': 'a7acb38facb4833f8e3a928ff27b1a3d0c1a8c388d03e413a92fbb05164e2d2c',
    'claude_discovery': '6847021ce2464e3edc9139bd1dbe1d07ac7da7207f24c5c9e860df1663ea86ea',
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


def digests(calls=CALLS):
    """Recompute EXPECTED; run against a previous implementation to refresh the reference."""
    with tempfile.TemporaryDirectory() as temp:
        fixture(Path(temp), FIXED_PROJECT, calls)
        return {name: fingerprint(value) for name, value in telemetry_outputs(Path(temp), FIXED_PROJECT).items()}


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


class NativePeakTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.project = self.root/'project'; self.project.mkdir()

    def test_telemetry_outputs_match_the_reference_computed_before_draining(self):
        self.assertEqual(digests(), EXPECTED)

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
                self.assertEqual(code, 0)
                result['journal_'+key] = cache.read_bytes(), out.getvalue()
        for name, options in (('codex', dict(events=self.root/'codex.jsonl', source_format='codex', source_version='codex-rollout-0.166.0')),
                ('codex_root', dict(source_root=self.root/'captures', source_format='codex', source_version='codex-rollout-0.166.0')),
                ('claude', dict(events=self.root/'claude.jsonl', source_format='claude', source_version='claude-jsonl-2.1.316'))):
            sink = []
            result['collect_'+name] = fingerprint((collect(self.project, event_sink=sink.append, **options), sink))
        return result

    def test_snapshots_journal_bytes_and_collection_match_undrained_reference(self):
        fixture(self.root, self.project, 120)
        with undrained(): reference = self.source_outputs('reference')
        candidate = self.source_outputs('candidate')
        self.assertEqual(candidate.keys(), reference.keys())
        for name in reference:
            with self.subTest(name): self.assertEqual(candidate[name], reference[name])

    def test_draining_preserves_correlation_of_lists_tuples_and_deques(self):
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
                self.assertEqual(list(correlated(value, lambda: None)), expected)
            self.assertEqual(expected, [event({**common, **row}) for row in correlate(iter(rows))])
        drained = deque(rows); list(correlated(drained, lambda: None)); self.assertFalse(drained)

    def peak(self, operation):
        tracemalloc.start()
        try:
            base = tracemalloc.get_traced_memory()[0]; value = operation()
            current, peak = tracemalloc.get_traced_memory()
        finally: tracemalloc.stop()
        del value
        return peak-base, current-base

    def test_native_reads_hold_one_copy_of_decoded_events_at_peak(self):
        from agent_harness_lab.evidence import collect
        from agent_harness_lab.sources.codex import CodexCapture
        from agent_harness_lab.telemetry import claude_discovery, codex_discovery, codex_rollout
        fixture(self.root, self.project, 3000)
        def claude_scan():
            with claude_discovery.capture([self.root/'claude/projects', self.root/'archive/projects'], self.project) as captured:
                return captured[0]
        # Peak over the retained events. Holding decoder rows beside the events measured 1.42-1.64 before
        # draining and 1.12-1.25 after, stable from 1.5k to 20k calls; bounds sit between the two.
        bounds = dict(codex_read=(lambda: codex_rollout.read(self.root/'codex.jsonl', self.project), 1.27),
            codex_discovery=(lambda: codex_discovery.scan(self.root/'captures', self.project), 1.43),
            claude_discovery=(claude_scan, 1.25))
        for name, (operation, bound) in bounds.items():
            with self.subTest(name):
                peak, retained = self.peak(operation)
                self.assertLess(peak/retained, bound, (name, peak, retained))
        events = self.peak(bounds['codex_read'][0])[1]
        with self.subTest('collect'):
            peak, _ = self.peak(lambda: collect(self.project, events=self.root/'codex.jsonl', source_format='codex',
                source_version='codex-rollout-0.166.0'))
            # Former peak: the admitted tuple plus normalized and analysed copies (1.64 of the events).
            self.assertLess(peak/events, 1.35, (peak, events))
        with self.subTest('snapshot'):
            source, manifest = self.declare('codex_v1')
            with undrained(): before, _ = self.peak(lambda: CodexCapture(manifest).read(source))
            after, _ = self.peak(lambda: CodexCapture(manifest).read(source))
            # snapshot() dominates; releasing decoder rows before it removes about 10% of the peak.
            self.assertLess(after, 0.95*before, (before, after))

if __name__ == '__main__':
    unittest.main()
