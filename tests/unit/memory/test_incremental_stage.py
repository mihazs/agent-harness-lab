"""The reusable stage's identity: a pinned output digest per stage version, and parity of the reuse seam (synthetic data)."""
import hashlib
import json
import random

import pytest

from agent_harness_lab.evidence import collect
from agent_harness_lab.memory import incremental
from agent_harness_lab.diagnostics.process_mining import ProcessMiner
from tools.diagnose import analyze

# Output digest of the process-mining stage for GOLDEN_EVENTS, per STAGE_VERSION. A change to normalization, ProcessMiner
# or analyze that alters this output fails the test below: bump STAGE_VERSION (it is part of every node key, so stored
# outputs stop matching) and pin the new digest under the new version.
PINNED = {'process-graph-1': '1084c728efd5325f47ed9b6ddf2da9346ce023893ba2f808cd2d415b3f829009'}
GOLDEN_EVENTS = ''.join(json.dumps(row) + '\n' for row in (
    {'case_id': 'a', 'event_id': '1', 'activity': 'prompt', 'actor': 'human', 'timestamp': '2026-01-01T00:00:00Z'},
    {'case_id': 'a', 'event_id': '2', 'activity': 'tool_call', 'actor': 'agent', 'timestamp': '2026-01-01T00:00:01Z'},
    {'case_id': 'a', 'event_id': '3', 'activity': 'tool_call', 'actor': 'agent', 'timestamp': '2026-01-01T00:00:00Z'},
    {'case_id': 'a', 'event_id': '4', 'activity': 'answer', 'actor': 'robot', 'timestamp': 'not a time'},
    {'case_id': 'b', 'event_id': '1', 'activity': 'prompt', 'root_case_id': 'a'},
    {'case_id': 'b', 'event_id': '2', 'activity': 'answer', 'root_case_id': 'a', 'timestamp': '2026-01-01T00:00:05Z'}))


def test_the_stage_output_matches_the_pin_for_its_version(tmp_path):
    events = tmp_path / 'events.jsonl'
    events.write_text(GOLDEN_EVENTS)
    mining = collect(tmp_path, events=events)['analysis']['process_mining']
    digest = hashlib.sha256(incremental.encode(mining)).hexdigest()
    assert PINNED.get(incremental.STAGE_VERSION) == digest, (
        f'process-mining output changed (digest {digest}); bump STAGE_VERSION and pin the digest under the new version')


def stream(seed):
    rng, rows = random.Random(seed), []
    for case in range(rng.randint(1, 6)):
        root = f'r{case % 2}' if rng.random() < 0.3 else None  # one lineage per case
        for index in range(rng.randint(1, 8)):
            row = {'case_id': f'c{case}', 'event_id': str(index), 'activity': rng.choice('abcd'),
                   'actor': rng.choice(('human', 'agent', 'tool', 'collaborator', 'other', None))}
            if rng.random() < 0.7:
                row['timestamp'] = rng.choice((f'2026-01-01T00:00:{rng.randint(0, 59):02d}Z', 'bad', '2026-01-01T00:00:00'))
            if root:
                row['root_case_id'] = root
            if row['actor'] is None:
                del row['actor']
            rows.append(row)
    return rows


@pytest.mark.parametrize('seed', range(40))
def test_a_reused_stage_output_leaves_the_whole_analysis_byte_identical(seed):
    rows = stream(seed)
    fresh = analyze(iter(rows), select_methods=True)
    stored = json.loads(incremental.encode(fresh['process_mining']))
    reused = analyze(iter(rows), select_methods=True, reused_process_mining=stored)
    assert json.dumps(reused) == json.dumps(fresh)


def test_the_miner_is_not_run_for_a_reused_output(monkeypatch):
    def forbidden(self, item):
        raise AssertionError('the miner ran')
    monkeypatch.setattr(ProcessMiner, 'observe', forbidden)
    rows = [{'case_id': 'a', 'event_id': '1', 'activity': 'x'}]
    assert analyze(iter(rows), reused_process_mining={'profile': 'stub'})['process_mining'] == {'profile': 'stub'}
    with pytest.raises(AssertionError):
        analyze(iter(rows))


def test_collect_names_the_reuse_seam_only_when_a_result_is_supplied(tmp_path, monkeypatch):
    """The frozen reference analyzer takes no such keyword: without the flags the call to analyze must stay unchanged."""
    events = tmp_path / 'events.jsonl'
    events.write_text(GOLDEN_EVENTS)
    seen = []

    def spy(rows, **options):
        seen.append(options)
        return analyze(rows, **options)

    monkeypatch.setattr('agent_harness_lab.evidence.analyze', spy)
    first = collect(tmp_path, events=events)
    assert 'reused_process_mining' not in seen[0]
    collect(tmp_path, events=events, reused_process_mining=first['analysis']['process_mining'])
    assert seen[1]['reused_process_mining'] == first['analysis']['process_mining']
