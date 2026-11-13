import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

spec = importlib.util.spec_from_file_location('diagnose', Path(__file__).parents[3] / 'tools/diagnose.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


def event(case, ident, **extra):
    return dict(case_id=case, event_id=str(ident), activity='tool_result', **extra)


class TestDiagnosis:
    def test_existing_output_directory_is_refused_with_fresh_path_guidance(self, tmp_path):
        events = tmp_path / 'events.jsonl'
        events.write_text(json.dumps(event('a', 1)) + '\n')
        output = tmp_path / 'existing'
        output.mkdir()
        sentinel = output / 'sentinel.txt'
        sentinel.write_text('keep')
        proc = subprocess.run([sys.executable, str(Path(__file__).parents[3] / 'tools/diagnose.py'),
            '--events', str(events), '--output', str(output)], capture_output=True, text=True)
        assert proc.returncode == 2, proc.stderr
        assert 'fresh' in proc.stderr, proc.stderr
        assert 'Traceback' not in proc.stderr
        assert sorted(p.name for p in output.iterdir()) == ['sentinel.txt']
        assert sentinel.read_text() == 'keep'

    def test_duplicate_observation_requires_state_and_is_not_waste(self):
        signature = dict(tool_name='read', input_fingerprint='i', result_fingerprint='r', state_revision='s')
        result = d.analyze([event('a', 1, **signature), event('a', 2, **signature)])
        assert result['findings'][0]['status'] == 'observed_pattern'
        signature['state_revision'] = 'new'
        assert d.analyze([event('a', 1), event('a', 2, **signature)])['findings'] == []

    def test_related_cases_do_not_inflate_support(self):
        rows = [event(str(i), j, root_case_id='root') for i in range(4) for j in range(3)]
        assert d.analyze(rows)['recurring_sequences'] == []

    def test_invalid_duplicate_and_limits_fail(self):
        for rows in ([None], [{}], [event('a', 1), event('a', 1)]):
            with pytest.raises(ValueError): d.analyze(rows)
        with pytest.raises(ValueError): d.analyze([event('a', 1), event('a', 2)], 1)

    def test_native_normalization_does_not_export_bodies(self):
        rows = list(d.normalize([dict(kind='stop', body='SECRET', session_id='s')], 'ai-memory', 'default/project'))
        assert 'SECRET' not in str(rows)
        assert rows[0]['activity'] == 'turn_stop'
        assert d.analyze(rows)['findings'] == []

    def test_native_session_metadata_preserves_case_identity(self):
        rows = list(d.normalize([{'type': 'session_meta', 'payload': {'id': 's'}},
                               {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'read'}}], 'codex', 'default/project'))
        assert all(r['case_id'] == 's' for r in rows)
        assert rows[1]['tool_name'] == 'read'

    def test_interleaving_does_not_create_sequences(self):
        assert d.analyze([event('a', 1), event('b', 1)])['recurring_sequences'] == []

    def test_claude_tools_are_linked_without_content_or_thinking(self):
        rows = [dict(type='assistant', uuid='a', sessionId='session', message={'content':[
            {'type':'thinking','thinking':'PRIVATE'},
            {'type':'tool_use','id':'call','name':'Read','input':{'path':'PRIVATE'}}]}),
            dict(type='user', uuid='b', sessionId='session', message={'content':[
            {'type':'tool_result','tool_use_id':'call','content':'PRIVATE','is_error':True}]}),
            dict(type='assistant',uuid='c',sessionId='other',message={'content':'PRIVATE'})]
        normalized=list(d.normalize(rows,'claude','synthetic'))
        assert [e['activity'] for e in normalized] == ['tool_call','tool_result','assistant']
        assert [e['call_id'] for e in normalized[:2]] == ['call','call']
        assert normalized[0]['event_id'] == 'a:1'
        assert 'PRIVATE' not in str(normalized)
        assert d.analyze(normalized)['cases'] == 2
        assert d.analyze(normalized)['findings'] == []

    def test_result_call_result_and_interleaved_readonly_tools(self):
        sig = dict(tool_name='read', input_fingerprint='i', result_fingerprint='r', state_revision='s')
        rows = [event('a', 1, **sig), dict(case_id='a', event_id='2', activity='tool_call',
                call_id='c', tool_effect='read_only', state_revision='s'),
                event('b', 1, **sig), event('a', 3, call_id='c', **sig)]
        finding = d.analyze(rows)['findings'][0]
        assert [e['event_id'] for e in finding['evidence']] == ['1', '3']
        assert finding['kind'] == 'duplicate_observation'

    def test_revision_aba_unknown_calls_and_concurrency_invalidate(self):
        sig = dict(tool_name='read', input_fingerprint='i', result_fingerprint='r', state_revision='A')
        for middle in (
            [dict(case_id='a', event_id='m', activity='metadata', state_revision='B')],
            [dict(case_id='a', event_id='m', activity='tool_call', call_id='u')],
            [dict(case_id='a', event_id='m', activity='metadata', mutation=True)],
            [event('a', 'm', call_id='unmatched', **sig)],
        ):
            assert (d.analyze([event('a', 1, **sig), *middle,
                event('a', 3, **sig)])['findings']) == []

    def test_cache_eviction_is_bounded_and_deterministic(self):
        from tools.observations import ObservationCache
        cache = ObservationCache(per_case=2, total=5, max_age=10)
        for i in range(10000):
            cache.observe(event(str(i % 11), i, tool_name='read', input_fingerprint=str(i),
                                result_fingerprint='r', state_revision='s'), i)
            assert len(cache.entries) <= 5
            assert len(cache.cases) <= 5
        sig = dict(tool_name='read', input_fingerprint='i', result_fingerprint='r', state_revision='s')
        rows = [event('a', 1, **sig), dict(case_id='a', event_id='2', activity='metadata'), event('a', 3, **sig)]
        assert d.analyze(rows, observation_max_age=1)['findings'] == []


def random_value(rng, depth=0):
    if depth > 3 or rng.random() < 0.25:
        return rng.choice([None, True, False, 0, -3, 10**20, 1.5, -0.0, 1e22, float('inf'), '', 'a\nb', 'é\u2028😀"\\', rng.random()])
    if rng.random() < 0.6:
        return [random_value(rng, depth + 1) for _ in range(rng.choice([0, 0, 1, 2, 5]))]
    return {rng.choice(['a', 'b', 'é', 'k']) + str(number): random_value(rng, depth + 1) for number in range(rng.choice([0, 1, 3]))}


class TestFindingsWriting:
    """findings.json is streamed to disk without changing a byte of it or leaving a truncated file behind."""

    def test_pieces_concatenate_to_exactly_what_dumps_produced(self):
        import random
        rng = random.Random(7)
        for _ in range(500):
            shape = {f'key{number}': random_value(rng) for number in range(rng.choice([0, 1, 2, 3]))}
            for value in (shape, random_value(rng)):
                assert ''.join(d.findings_pieces(value)) == json.dumps(value, indent=2)

    @pytest.mark.parametrize('value', [{}, [], 'text', None, {'a': []}, {'a': {}}, {'a': [[]]}, {'a': [{}]}, {'a': [1]},
                                       {1: [2]}, {'a': None, 'b': [1, {'c': [2, 3]}]}])
    def test_edge_shapes_match_dumps(self, value):
        assert ''.join(d.findings_pieces(value)) == json.dumps(value, indent=2)

    def test_cli_file_is_byte_identical_to_the_previous_dumps_output(self, tmp_path):
        events = tmp_path / 'events.jsonl'
        rows = [event('a', number, tool_name='read_file', input_fingerprint='i', result_fingerprint='r', state_revision='s')
                for number in range(1, 7)]
        events.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        output = tmp_path / 'out'
        proc = subprocess.run([sys.executable, str(Path(__file__).parents[3] / 'tools/diagnose.py'),
            '--events', str(events), '--output', str(output)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        expected = d.analyze(d.normalize((json.loads(line) for line in events.read_text().splitlines()), 'normalized', None))
        expected['input_sha256'] = hashlib.sha256(events.read_bytes()).hexdigest()
        assert (output / 'findings.json').read_text() == json.dumps(expected, indent=2) + '\n'

    def test_a_failed_write_removes_the_partial_file_and_reports_the_error(self, tmp_path, monkeypatch, capsys):
        events = tmp_path / 'events.jsonl'
        events.write_text(json.dumps(event('a', 1)) + '\n')

        def broken(result):
            yield '{"partial"'
            raise TypeError('cannot serialize')
        monkeypatch.setattr(d, 'findings_pieces', broken)
        monkeypatch.setattr(sys, 'argv', ['diagnose', '--events', str(events), '--output', str(tmp_path / 'out')])
        with pytest.raises(SystemExit) as stop:
            d.main()
        assert stop.value.code == 2
        assert 'diagnosis error: cannot serialize' in capsys.readouterr().err
        assert not (tmp_path / 'out' / 'findings.json').exists()

    def test_the_whole_text_is_never_held_in_memory(self, tmp_path):
        import tracemalloc
        evidence = [dict(case_id=f'c{number}', event_id=str(number)) for number in range(4)]
        result = dict(findings=[dict(kind='k', evidence=evidence) for _ in range(30000)])
        target = tmp_path / 'findings.json'
        tracemalloc.start()
        try:
            d.write_findings(target, result)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        assert target.stat().st_size > 8_000_000
        assert peak < target.stat().st_size // 20
