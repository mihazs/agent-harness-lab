"""Stage reuse planner: key stability, domain separation, invalidation scope and receipt completeness (synthetic data)."""
import copy
import hashlib

import pytest

from agent_harness_lab.memory import reuse


def h(text):
    return hashlib.sha256(text.encode()).hexdigest()


def request(stage, version='1', inputs=(), config=None):
    return {'stage_version': version, 'external_inputs': list(inputs), 'scope': {'project': 'p'},
            'config': config or {}, 'policy': {'role': 'operator'}}


def requests():
    out = {s: request(s) for s in reuse.GRAPH}
    out['source_status'] = request('source_status', inputs=[h('rev-1')])
    out['harness_inventory'] = request('harness_inventory', inputs=[h('harness-1')])
    out['deterministic_grade'] = request('deterministic_grade', config={'grader': h('grader-1')})
    return out


def committed(reqs):
    """Run the planner as if every stage executed, committing a record per stage; returns the prior ledger."""
    ledger, outputs = {}, {}
    for stage in reuse.order():
        r = reqs[stage]
        inputs = sorted({*r['external_inputs'], *(outputs[d] for d in reuse.GRAPH[stage])})
        parts = reuse.components(stage, r['stage_version'], inputs, r['scope'], r['config'], r['policy'])
        outputs[stage] = h('out-' + reuse.node_key(parts))
        ledger[stage] = {'node_key': reuse.node_key(parts), 'components': parts, 'output': outputs[stage],
                         'status': 'complete'}
    return ledger


GOOD = {'bytes': True, 'oracle': True, 'bank': True, 'role': True}


def run(reqs, ledger, live=None, freshness='verified'):
    live = live if live is not None else {s: GOOD for s in reuse.GRAPH}
    return reuse.plan(reqs, ledger, live, source_freshness=freshness)


def reasons(receipt):
    return {r['stage']: r['reason'] for b in ('reused', 'executed', 'excluded', 'unknown') for r in receipt[b]}


def test_key_is_stable_and_order_independent():
    a = reuse.components('s', '1', [h('b'), h('a')], {'x': 1, 'y': 2}, {}, {})
    b = reuse.components('s', '1', [h('a'), h('b')], {'y': 2, 'x': 1}, {}, {})
    assert reuse.node_key(a) == reuse.node_key(b) == reuse.node_key(copy.deepcopy(a))
    assert reuse.node_key(a) != reuse.node_key({**a, 'stage_version': '2'})


def test_domain_separation_and_non_ascii_bytes():
    parts = reuse.components('s', '1', [], {'n': 'é'}, {}, {})
    assert reuse.node_key(parts) != hashlib.sha256(reuse.canonical_json(parts)).hexdigest()
    assert b'\xc3\xa9' in reuse.canonical_json(parts)


@pytest.mark.parametrize('bad', [{'t': 1.5}, {'captured_at': 'now'}, {'n': {'ttl': 5}}, {'k': object()}])
def test_unrepresentable_or_volatile_values_are_rejected(bad):
    with pytest.raises(ValueError):
        reuse.node_key(reuse.components('s', '1', [], bad, {}, {}))


def test_bad_inputs_and_canonicalizer_are_rejected():
    for inputs in (['nothex'], [h('a'), h('a')]):
        with pytest.raises(ValueError):
            reuse.components('s', '1', inputs, {}, {}, {})
    with pytest.raises(ValueError, match='canonicalizer'):
        reuse.components('s', '1', [], {}, {}, {}, canonicalizer=2)


def test_cycles_and_undeclared_edges_are_rejected():
    with pytest.raises(ValueError, match='cycle'):
        reuse.order({'a': ('b',), 'b': ('a',)})
    with pytest.raises(ValueError, match='undeclared'):
        reuse.order({'a': ('z',)})


def test_unchanged_rerun_reuses_everything_and_lists_non_replayable_work():
    reqs = requests()
    receipt = run(reqs, committed(reqs))
    assert set(reasons(receipt).values()) == {'hit_verified'}
    assert receipt['counts'] == {'reused_deterministic': 8, 'executed_deterministic': 0}
    assert receipt['reused_live_trials'] == 0 and receipt['publication_allowed'] is False
    assert {r['stage'] for r in receipt['excluded_historical']} == {'candidate_proposal', 'agent_trial'}
    assert receipt['schema'] == 'ahl-stage-reuse-v1' and receipt['source_freshness'] == 'verified'


def test_changed_source_revision_invalidates_only_dependents():
    reqs = requests()
    ledger = committed(reqs)
    reqs['source_status'] = request('source_status', inputs=[h('rev-2')])
    got = reasons(run(reqs, ledger))
    assert got['source_status'] == 'miss_input_changed'
    assert got['harness_inventory'] == 'hit_verified'
    downstream = {'normalized_capture', 'diagnostic_process_mining', 'deterministic_grade', 'statistical_report',
                  'trend_view', 'wiki_projection'}
    assert {s for s, r in got.items() if r != 'hit_verified'} == downstream | {'source_status'}


def test_changed_parser_version_misses_only_dependent_stages():
    reqs = requests()
    ledger = committed(reqs)
    reqs['normalized_capture'] = request('normalized_capture', version='2')
    got = reasons(run(reqs, ledger))
    assert got['normalized_capture'] == 'miss_algorithm_changed'
    assert got['source_status'] == got['harness_inventory'] == 'hit_verified'
    assert got['trend_view'] == 'miss_input_changed'


def test_changed_grader_hash_keeps_raw_evidence_but_rescores():
    reqs = requests()
    ledger = committed(reqs)
    reqs['deterministic_grade'] = request('deterministic_grade', config={'grader': h('grader-2')})
    receipt = run(reqs, ledger)
    got = reasons(receipt)
    assert got['normalized_capture'] == 'hit_verified'
    assert got['deterministic_grade'] == 'miss_algorithm_changed'
    assert got['statistical_report'] == 'miss_input_changed'
    assert 'deterministic_grade' not in {r['stage'] for r in receipt['reused']}


def test_policy_and_scope_changes_are_typed():
    reqs = requests()
    ledger = committed(reqs)
    reqs['harness_inventory']['policy'] = {'role': 'other'}
    reqs['source_status']['scope'] = {'project': 'q'}
    got = reasons(run(reqs, ledger))
    assert got['harness_inventory'] == 'miss_policy_changed' and got['source_status'] == 'miss_scope_mismatch'


def test_missing_failed_and_interrupted_priors_are_not_reusable():
    reqs = requests()
    ledger = committed(reqs)
    del ledger['harness_inventory']
    ledger['source_status']['status'] = 'incomplete'
    got = reasons(run(reqs, ledger))
    assert got['harness_inventory'] == got['source_status'] == 'miss_no_prior'


def test_unknown_freshness_is_unknown_never_a_hit_and_ttl_cannot_authenticate():
    reqs = requests()
    ledger = committed(reqs)
    receipt = run(reqs, ledger, freshness='unknown')
    stale = ['deterministic_grade', 'diagnostic_process_mining', 'normalized_capture', 'source_status',
             'statistical_report', 'trend_view', 'wiki_projection']
    assert [r['stage'] for r in receipt['unknown']] == stale  # dependents are unknown, not changed
    assert set(reasons(receipt).values()) == {'miss_freshness_unknown', 'hit_verified'}
    assert [r['stage'] for r in receipt['reused']] == ['harness_inventory']
    assert not receipt['executed'] and receipt['counts']['executed_deterministic'] == 0
    assert receipt['source_freshness'] == 'unknown'
    assert reasons(run(reqs, ledger, freshness='ttl-ok'))['source_status'] == 'miss_freshness_unknown'
    with pytest.raises(ValueError):
        reuse.node_key(reuse.components('s', '1', [], {'ttl': 60}, {}, {}))


@pytest.mark.parametrize('check, code', [('bytes', 'invalid_integrity'), ('oracle', 'invalid_oracle_quarantine'),
                                         ('bank', 'invalid_bank_authority')])
def test_failed_live_authority_blocks_reuse(check, code):
    reqs = requests()
    live = {s: dict(GOOD) for s in reuse.GRAPH}
    live['deterministic_grade'][check] = False
    receipt = run(reqs, committed(reqs), live)
    assert reasons(receipt)['deterministic_grade'] == code
    assert reasons(receipt)['statistical_report'] == 'miss_input_changed'


def test_unchecked_authority_is_unknown_not_a_pass():
    reqs = requests()
    live = {s: dict(GOOD) for s in reuse.GRAPH}
    del live['deterministic_grade']['oracle']
    receipt = run(reqs, committed(reqs), live)
    unknown = ['deterministic_grade', 'statistical_report', 'trend_view', 'wiki_projection']
    assert [(r['stage'], r['reason']) for r in receipt['unknown']] == [(s, 'invalid_oracle_quarantine') for s in unknown]
    assert 'deterministic_grade' not in {r['stage'] for r in receipt['reused']}
    assert not receipt['executed'] and receipt['counts']['executed_deterministic'] == 0


def test_role_access_exclusion_propagates_downstream():
    reqs = requests()
    live = {s: dict(GOOD) for s in reuse.GRAPH}
    live['normalized_capture']['role'] = False
    receipt = run(reqs, committed(reqs), live)
    excluded = {r['stage'] for r in receipt['excluded']}
    assert excluded == {'normalized_capture', 'diagnostic_process_mining', 'deterministic_grade', 'statistical_report',
                        'trend_view', 'wiki_projection'}
    assert {r['reason'] for r in receipt['excluded']} == {'blocked_role_access'}


def test_tampered_prior_record_is_invalid_integrity():
    reqs = requests()
    ledger = committed(reqs)
    ledger['harness_inventory']['node_key'] = h('forged')
    assert reasons(run(reqs, ledger))['harness_inventory'] == 'invalid_integrity'


def test_receipt_accounts_for_every_stage_exactly_once_with_a_known_reason():
    reqs = requests()
    ledger = committed(reqs)
    reqs['trend_view'] = request('trend_view', version='9')
    receipt = run(reqs, ledger, freshness='unknown')
    listed = [r['stage'] for b in ('reused', 'executed', 'excluded', 'unknown') for r in receipt[b]]
    assert sorted(listed) == sorted(reuse.GRAPH)
    assert set(reasons(receipt).values()) <= set(reuse.REASONS)


def test_plan_rejects_unknown_or_missing_stages():
    reqs = requests()
    del reqs['trend_view']
    with pytest.raises(ValueError):
        run(reqs, {})


def test_cross_stage_record_is_invalid_integrity():
    reqs = requests()
    ledger = committed(reqs)
    parts = {**ledger['source_status']['components'], 'inputs': sorted(reqs['harness_inventory']['external_inputs'])}
    ledger['harness_inventory'] = {**ledger['source_status'], 'components': parts, 'node_key': reuse.node_key(parts)}
    receipt = run(reqs, ledger)
    assert reasons(receipt)['harness_inventory'] == 'invalid_integrity'
    assert 'harness_inventory' not in {r['stage'] for r in receipt['reused']}


def test_record_equal_only_under_python_equality_is_not_a_hit():
    reqs = requests()
    ledger = committed(reqs)
    reqs['harness_inventory']['config'] = {'a': True}
    parts = {**ledger['harness_inventory']['components'], 'config': {'a': 1}}
    ledger['harness_inventory'].update(components=parts, node_key=reuse.node_key(parts))
    assert reasons(run(reqs, ledger))['harness_inventory'] == 'invalid_integrity'


def test_float_in_current_request_is_rejected():
    reqs = requests()
    ledger = committed(reqs)
    reqs['harness_inventory']['config'] = {'a': 1.0}
    with pytest.raises(ValueError):
        run(reqs, ledger)


def test_reported_key_is_the_current_requests_key():
    reqs = requests()
    receipt = run(reqs, committed(reqs))
    row = next(r for r in receipt['reused'] if r['stage'] == 'source_status')
    assert row['node_key'] == reuse.node_key(reuse.components('source_status', '1', reqs['source_status']['external_inputs'],
                                                              reqs['source_status']['scope'], {}, reqs['source_status']['policy']))


def test_unchecked_upstream_role_stays_unknown_downstream():
    reqs = requests()
    live = {s: dict(GOOD) for s in reuse.GRAPH}
    del live['normalized_capture']['role']
    receipt = run(reqs, committed(reqs), live)
    assert not receipt['excluded']
    assert {r['stage'] for r in receipt['unknown']} == {'normalized_capture', 'diagnostic_process_mining',
                                                         'deterministic_grade', 'statistical_report', 'trend_view',
                                                         'wiki_projection'}


def role_denied(stage, **gone):
    live = {s: dict(GOOD) for s in reuse.GRAPH}
    live[stage]['role'] = False
    for name in gone:
        del live[stage][name]
    return live


def test_role_denial_without_prior_is_excluded_with_dependents():
    reqs = requests()
    ledger = committed(reqs)
    del ledger['normalized_capture']
    receipt = run(reqs, ledger, role_denied('normalized_capture'))
    assert {r['stage'] for r in receipt['excluded']} == {'normalized_capture', 'diagnostic_process_mining',
                                                         'deterministic_grade', 'statistical_report', 'trend_view',
                                                         'wiki_projection'}
    assert {r['reason'] for r in receipt['excluded']} == {'blocked_role_access'}
    assert not receipt['executed'] and not receipt['unknown']


def test_role_denial_wins_over_changed_input_and_version():
    reqs = requests()
    ledger = committed(reqs)
    reqs['normalized_capture'] = request('normalized_capture', version='2')
    receipt = run(reqs, ledger, role_denied('normalized_capture'))
    assert reasons(receipt)['normalized_capture'] == 'blocked_role_access'
    assert reasons(receipt)['wiki_projection'] == 'blocked_role_access'
    reqs = requests()
    reqs['source_status'] = request('source_status', inputs=[h('rev-2')])
    got = reasons(run(reqs, ledger, role_denied('harness_inventory')))
    assert got['source_status'] == 'miss_input_changed'
    assert got['harness_inventory'] == got['diagnostic_process_mining'] == got['wiki_projection'] == 'blocked_role_access'


def test_role_denial_wins_over_unchecked_bytes_and_unknown_freshness():
    reqs = requests()
    ledger = committed(reqs)
    receipt = run(reqs, ledger, role_denied('harness_inventory', bytes=None))
    assert {r['stage'] for r in receipt['excluded']} == {'harness_inventory', 'diagnostic_process_mining',
                                                         'statistical_report', 'trend_view', 'wiki_projection'}
    assert not receipt['executed'] and not receipt['unknown']
    # normalized_capture is unknown for freshness, harness_inventory is denied: the denial wins downstream
    got = reasons(run(reqs, ledger, role_denied('harness_inventory'), freshness='unknown'))
    assert got['normalized_capture'] == 'miss_freshness_unknown'
    assert got['diagnostic_process_mining'] == 'blocked_role_access'
    assert reasons(run(reqs, ledger, role_denied('normalized_capture'), freshness='unknown'))['normalized_capture'] \
        == 'blocked_role_access'


def test_unchecked_bytes_make_dependents_unknown_not_executed():
    reqs = requests()
    live = {s: dict(GOOD) for s in reuse.GRAPH}
    live['harness_inventory']['bytes'] = None
    receipt = run(reqs, committed(reqs), live)
    assert {r['stage'] for r in receipt['unknown']} == {'harness_inventory', 'diagnostic_process_mining',
                                                         'statistical_report', 'trend_view', 'wiki_projection'}
    assert not receipt['executed'] and receipt['counts']['executed_deterministic'] == 0
