"""One stage record layout and one index: `ahl analyze --incremental` and `memory.commit` read each other's commits.

Synthetic events and a synthetic project only; no model, network or private history."""
import argparse
import json

from agent_harness_lab.evidence import collect
from agent_harness_lab.memory import commit, incremental
from tests.integration.workflow import test_incremental_analyze as analyzed

world = analyzed.world  # the shared fixture: a synthetic project, a capture and an initialized store
STAGE = analyzed.STAGE


def planned(world):
    """The Incremental run for the world's current inputs, before anything is committed."""
    args = argparse.Namespace(incremental=True, explain_reuse=False, memory_store=world.store.root, events=world.events,
                              format='normalized', source_version=None, project=world.project, output=None)
    return incremental.Incremental(args)


def mining_of(world):
    return collect(world.project, events=world.events)['analysis']['process_mining']


def test_a_stage_committed_through_commit_stage_is_reused_by_analyze(world, capsys):
    stage = planned(world)
    commit.commit_stage(world.store, stage.project_id, stage.parts, incremental.encode(mining_of(world)), graph=incremental.GRAPH)
    block, _ = analyzed.reuse(world, capsys=capsys)
    assert (block['outcome'], block['reason'], block['commit']) == ('reused', 'hit_verified', 'not_needed')


def test_a_stage_committed_by_analyze_is_a_complete_row_under_its_exact_key(world, capsys):
    analyzed.reuse(world, capsys=capsys)
    stage = planned(world)
    rows = world.store.stage_rows(stage.project_id, STAGE, node_key=stage.key, status='complete')
    assert [row['sequence'] for row in rows] == [1]
    record = json.loads(world.store.read(rows[0]['record']))
    assert (record['stage'], record['sequence'], record['node_key']) == (STAGE, 1, stage.key)


def test_sequence_continues_across_the_two_ways_of_committing(world, capsys):
    stage = planned(world)
    commit.commit_stage(world.store, stage.project_id, stage.parts, incremental.encode(mining_of(world)), graph=incremental.GRAPH)
    world.events.write_text(analyzed.events_text(extra=2))
    changed, _ = analyzed.reuse(world, capsys=capsys)  # a miss against the commit_stage record, committed by analyze
    assert (changed['reason'], changed['compared_to']['prior_sequence']) == ('miss_input_changed', 1)
    assert [row['sequence'] for row in world.store.stage_rows(stage.project_id, STAGE)] == [2, 1]
    assert commit.record_event(world.store, stage.project_id, stage.parts, 'interrupted', 'killed', graph=incremental.GRAPH)
    assert [row['sequence'] for row in world.store.stage_rows(stage.project_id, STAGE)] == [3, 2, 1]


def test_an_event_recorded_through_commit_is_never_a_prior_for_analyze(world, capsys):
    stage = planned(world)
    commit.record_event(world.store, stage.project_id, stage.parts, 'failed', 'parser_error', graph=incremental.GRAPH)
    block, _ = analyzed.reuse(world, capsys=capsys)
    assert (block['outcome'], block['reason'], block['commit']) == ('executed', 'miss_no_prior', 'committed')
