"""Deterministic, explainable choice of the REST or GraphQL bucket for one logical forge read.

Standard library only. GitHub meters REST (``core``) and GraphQL separately, so a read that both APIs can
serve goes to the one with headroom; a read only one API can supply is never sent to the other.
"""
BUCKETS = {'rest': 'core', 'graphql': 'graphql'}


def choose(limiter, host, identity, read, supports, *, cached=False, rest_requests=1, graphql_cost=1, patience=None):
    """Pick the transport for ``read`` and record the decision in the limiter receipt.

    ``supports`` lists the transports that can supply the read's complete field set. Preference, in order:
    a read only one API supports; a cached refresh (REST conditional requests, whose 304 costs no primary
    quota); a first or aggregate fetch when its GraphQL points are fewer than the REST requests it replaces;
    otherwise REST. A preferred bucket at its reserve floor yields to the other supported one. When every
    supported bucket is blocked, a block the limiter would wait out (within ``max_wait`` and ``patience``) is
    not terminal: the read is routed to the earliest such reset and ``Limiter.before`` takes the wait. Only
    when no block is waitable does the earliest reset halt the read as blocked access.
    """
    if not supports or len(set(supports)) != len(supports) or not set(supports) <= set(BUCKETS):
        raise ValueError('invalid routing support')
    if len(supports) == 1: order, why = tuple(supports), 'only_'+supports[0]
    elif cached: order, why = ('rest', 'graphql'), 'cached_refresh'
    elif graphql_cost < rest_requests: order, why = ('graphql', 'rest'), 'cheaper_graphql'
    else: order, why = ('rest', 'graphql'), 'rest_default'
    blocks = {}
    for transport in order:
        verdict = limiter.blocked(host, BUCKETS[transport], identity, need=graphql_cost if transport == 'graphql' else 1)
        if verdict is None:
            reason = why if not blocks else f'{order[0]}_{blocks[order[0]][0]}'
            limiter.note_route(read, transport, BUCKETS[transport], reason)
            return dict(read=read, transport=transport, bucket=BUCKETS[transport], reason=reason)
        blocks[transport] = verdict
    now = limiter.clock()
    waitable = {t: v for t, v in blocks.items() if limiter._can_wait(v[1]-now, patience)}
    if waitable:
        transport = min(waitable, key=lambda t: waitable[t][1])
        reason = why if len(supports) == 1 else 'all_buckets_wait'
        limiter.note_route(read, transport, BUCKETS[transport], reason)
        return dict(read=read, transport=transport, bucket=BUCKETS[transport], reason=reason)
    transport, (reason, until) = min(blocks.items(), key=lambda item: item[1][1])
    limiter.note_route(read, 'none', BUCKETS[transport], 'all_buckets_blocked')
    raise limiter._halt(reason, until, BUCKETS[transport])

