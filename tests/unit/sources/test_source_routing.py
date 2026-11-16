"""REST/GraphQL bucket routing, GraphQL cost charging and routing receipts over fake transports only."""
from dataclasses import replace
from datetime import datetime, timezone
import json

import pytest

from tests.unit.sources.test_source_github import GitHubCase, Response

from agent_harness_lab.sources.github import MAX_ALIASES, GitHubAPI, comments_query
from agent_harness_lab.sources.ratelimit import MAX_ROUTES, Limiter, graphql_cost, identity_digest
from agent_harness_lab.sources.routing import choose
from agent_harness_lab.sources.transport import RateLimited, TransportError

NOW = 1_000_000
TOKEN = 'Bearer SYNTHETIC_SECRET'
HOST = 'api.github.com'
WHO = identity_digest(TOKEN)


def stamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class WaitClock:
    """A clock a limiter wait advances, so a waited-out block really lapses without sleeping."""
    def __init__(self): self.now, self.slept = NOW, []
    def __call__(self): return self.now
    def sleep(self, seconds): self.slept.append(seconds); self.now += seconds


def limiter_with(max_wait=0, **buckets):
    """A limiter that has already seen ``bucket=(remaining, reset)`` observations, 5,000 requests per window."""
    limiter = Limiter(clock=lambda: NOW, max_wait=max_wait)
    for name, (remaining, reset) in buckets.items():
        limiter.observe(HOST, WHO, name, 200, dict(limit=5000, remaining=remaining, reset=reset), charged=False)
    return limiter


def route(limiter, supports=('rest', 'graphql'), **options):
    return choose(limiter, HOST, WHO, 'synthetic_read', supports, **options)


class TestRoutingPolicy:
    @pytest.mark.parametrize(('buckets', 'supports', 'options', 'transport', 'reason'), [
        pytest.param({}, ('rest', 'graphql'), dict(rest_requests=3, graphql_cost=1), 'graphql', 'cheaper_graphql', id='first-aggregate-fetch-uses-graphql'),
        pytest.param({}, ('rest', 'graphql'), dict(cached=True, rest_requests=3, graphql_cost=1), 'rest', 'cached_refresh', id='cached-refresh-uses-rest-conditional-requests'),
        pytest.param({}, ('rest', 'graphql'), dict(rest_requests=1, graphql_cost=1), 'rest', 'rest_default', id='equal-cost-stays-on-rest'),
        pytest.param({}, ('rest', 'graphql'), dict(rest_requests=2, graphql_cost=5), 'rest', 'rest_default', id='costlier-graphql-query-is-never-preferred'),
        pytest.param({'core': (10, NOW+600)}, ('rest', 'graphql'), {}, 'graphql', 'rest_reserve_floor', id='rest-at-reserve-yields-to-graphql'),
        pytest.param({'graphql': (10, NOW+600)}, ('rest', 'graphql'), dict(rest_requests=3), 'rest', 'graphql_reserve_floor', id='graphql-at-reserve-yields-to-rest'),
        pytest.param({'core': (0, NOW+600)}, ('rest', 'graphql'), {}, 'graphql', 'rest_primary_limit', id='exhausted-rest-yields-to-graphql'),
        pytest.param({'core': (4000, NOW+600), 'graphql': (4000, NOW+600)}, ('graphql',), {}, 'graphql', 'only_graphql', id='graphql-only-read-uses-graphql'),
        pytest.param({'core': (10, NOW+600)}, ('graphql',), {}, 'graphql', 'only_graphql', id='graphql-only-read-ignores-rest-pressure'),
        pytest.param({'graphql': (10, NOW+600)}, ('rest',), {}, 'rest', 'only_rest', id='rest-only-read-ignores-graphql-pressure'),
    ])
    def test_route_follows_the_preference_and_the_bucket_with_headroom(self, buckets, supports, options, transport, reason):
        limiter = limiter_with(**buckets)

        decision = route(limiter, supports, **options)

        assert (decision['transport'], decision['reason']) == (transport, reason)
        assert limiter.receipt()['routes'] == [dict(read='synthetic_read', transport=transport,
            bucket={'rest': 'core', 'graphql': 'graphql'}[transport], reason=reason)]

    def test_both_buckets_at_reserve_halt_with_the_earliest_reset(self):
        limiter = limiter_with(core=(10, NOW+600), graphql=(10, NOW+300))

        with pytest.raises(RateLimited) as halt:
            route(limiter)

        assert (halt.value.reason, halt.value.reset, halt.value.resource) == ('reserve_floor', NOW+300, 'graphql')
        receipt = limiter.receipt()
        assert (receipt['halt_reason'], receipt['reset_epoch']) == ('reserve_floor', NOW+300)
        assert receipt['routes'][-1]['reason'] == 'all_buckets_blocked'

    @pytest.mark.parametrize(('buckets', 'supports', 'transport', 'reason'), [
        pytest.param({'graphql': (10, NOW+30)}, ('graphql',), 'graphql', 'only_graphql', id='graphql-only-reserve-block-within-max-wait'),
        pytest.param({'graphql': (0, NOW+30)}, ('graphql',), 'graphql', 'only_graphql', id='graphql-only-exhausted-block-within-max-wait'),
        pytest.param({'graphql': (10, NOW+60)}, ('graphql',), 'graphql', 'only_graphql', id='graphql-only-block-exactly-max-wait'),
        pytest.param({'core': (10, NOW+30)}, ('rest',), 'rest', 'only_rest', id='rest-only-reserve-block-within-max-wait'),
        pytest.param({'core': (10, NOW+30), 'graphql': (10, NOW+20)}, ('rest', 'graphql'), 'graphql', 'all_buckets_wait',
            id='both-short-blocks-wait-for-the-earlier-reset'),
        pytest.param({'core': (10, NOW+600), 'graphql': (10, NOW+30)}, ('rest', 'graphql'), 'graphql', 'all_buckets_wait',
            id='only-the-short-block-is-waited-for'),
    ])
    def test_a_block_within_max_wait_is_left_to_the_limiter_wait_instead_of_halting(self, buckets, supports, transport, reason):
        limiter = limiter_with(max_wait=60, **buckets)

        decision = route(limiter, supports)

        assert (decision['transport'], decision['reason']) == (transport, reason)
        receipt = limiter.receipt()
        assert receipt['halt_reason'] is None
        assert receipt['routes'] == [dict(read='synthetic_read', transport=transport,
            bucket={'rest': 'core', 'graphql': 'graphql'}[transport], reason=reason)]

    @pytest.mark.parametrize(('buckets', 'supports', 'options', 'resource', 'reset'), [
        pytest.param({'graphql': (10, NOW+61)}, ('graphql',), {}, 'graphql', NOW+61, id='graphql-only-block-one-second-beyond-max-wait'),
        pytest.param({'graphql': (10, NOW+30)}, ('graphql',), dict(patience=10), 'graphql', NOW+30, id='graphql-only-block-beyond-patience'),
        pytest.param({'core': (10, NOW+600), 'graphql': (10, NOW+300)}, ('rest', 'graphql'), {}, 'graphql', NOW+300,
            id='both-blocks-beyond-max-wait'),
        pytest.param({'core': (10, NOW+30), 'graphql': (10, NOW+20)}, ('rest', 'graphql'), dict(patience=5), 'graphql', NOW+20,
            id='both-blocks-beyond-patience'),
    ])
    def test_a_block_beyond_max_wait_or_patience_still_halts_as_blocked_access(self, buckets, supports, options, resource, reset):
        limiter = limiter_with(max_wait=60, **buckets)

        with pytest.raises(RateLimited) as halt:
            route(limiter, supports, **options)

        assert (halt.value.resource, halt.value.reset) == (resource, reset)
        assert limiter.receipt()['routes'][-1]['transport'] == 'none'

    @pytest.mark.parametrize(('supports', 'blocked'), [
        pytest.param(('graphql',), 'graphql', id='graphql-only-read-with-graphql-at-reserve'),
        pytest.param(('rest',), 'core', id='rest-only-read-with-rest-at-reserve'),
    ])
    def test_a_read_only_one_api_supports_halts_instead_of_borrowing_the_other_bucket(self, supports, blocked):
        limiter = limiter_with(**{blocked: (10, NOW+600)})

        with pytest.raises(RateLimited) as halt:
            route(limiter, supports)

        assert halt.value.resource == blocked
        assert [r['transport'] for r in limiter.receipt()['routes']] == ['none']

    def test_a_query_costing_more_than_the_headroom_leaves_the_reserve_to_rest(self):
        limiter = limiter_with(graphql=(255, NOW+600))  # floor 250: one point fits, six do not

        assert route(limiter, rest_requests=9, graphql_cost=1)['transport'] == 'graphql'
        assert route(limiter, rest_requests=9, graphql_cost=6)['transport'] == 'rest'

    @pytest.mark.parametrize('supports', [
        pytest.param((), id='empty'),
        pytest.param(('soap',), id='unknown-transport'),
        pytest.param(('rest', 'rest'), id='duplicate'),
    ])
    def test_unknown_support_never_routes(self, supports):
        with pytest.raises(ValueError):
            route(limiter_with(), supports)

    def test_the_decision_list_is_bounded_and_counts_what_it_drops(self):
        limiter = limiter_with()
        for _ in range(MAX_ROUTES+3): route(limiter)

        receipt = limiter.receipt()

        assert (len(receipt['routes']), receipt['routes_omitted']) == (MAX_ROUTES, 3)


class TestGraphqlCost:
    @pytest.mark.parametrize(('value', 'expected'), [
        pytest.param(dict(data=dict(rateLimit=dict(cost=2, remaining=4990, limit=5000, resetAt='2026-01-01T00:00:00Z'))),
            dict(cost=2, remaining=4990, limit=5000, reset=1767225600), id='complete'),
        pytest.param(dict(data=dict(rateLimit=dict(cost=1))), dict(cost=1), id='cost-only'),
        pytest.param(dict(data=dict(rateLimit=dict(cost=1, remaining='4', limit=-1, resetAt='tomorrow'))), dict(cost=1), id='invalid-optionals-are-dropped'),
        pytest.param(dict(data=dict(rateLimit=dict(cost=True))), None, id='boolean-cost'),
        pytest.param(dict(data=dict(rateLimit=dict(cost=-1))), None, id='negative-cost'),
        pytest.param(dict(data=dict(rateLimit=dict(cost='1'))), None, id='string-cost'),
        pytest.param(dict(data=dict(rateLimit=dict(cost=2**41))), None, id='oversized-cost'),
        pytest.param(dict(data=dict(rateLimit=None)), None, id='null-report'),
        pytest.param(dict(data=dict()), None, id='missing-report'),
        pytest.param(dict(errors=[]), None, id='no-data'),
        pytest.param([], None, id='not-an-object'),
    ])
    def test_reported_cost_is_validated_and_unknown_stays_unknown(self, value, expected):
        assert graphql_cost(value) == expected

    def test_charge_counts_points_and_shares_the_reported_remaining(self):
        limiter = limiter_with()

        limiter.charge(HOST, WHO, 'graphql', dict(cost=3, remaining=100, limit=5000, reset=NOW+60))
        limiter.charge(HOST, WHO, 'graphql', dict(cost=2, remaining=98, limit=5000, reset=NOW+60))

        bucket = limiter.receipt()['buckets']['graphql']
        assert (bucket['points'], bucket['remaining_end'], bucket['requests']) == (5, 98, 0)
        assert limiter.blocked(HOST, 'graphql', WHO) == ('reserve_floor', NOW+60)

    def test_a_response_without_a_report_leaves_the_points_unknown(self):
        limiter = limiter_with()

        limiter.observe(HOST, WHO, 'graphql', 200, {})

        assert 'points' not in limiter.receipt()['buckets']['graphql']

    def test_the_reserve_floor_must_survive_the_declared_spend(self):
        limiter = limiter_with(graphql=(254, NOW+600))  # floor 250

        limiter.before(HOST, 'graphql', WHO, need=4)
        with pytest.raises(RateLimited) as halt:
            limiter.before(HOST, 'graphql', WHO, need=5)

        assert (halt.value.reason, halt.value.reset) == ('reserve_floor', NOW+600)

    def test_headroom_checks_spend_and_wait_for_nothing(self):
        limiter = limiter_with(core=(4000, NOW+600))

        for _ in range(5): assert limiter.blocked(HOST, 'core', WHO) is None

        receipt = limiter.receipt()
        assert (receipt['requests_used'], receipt['waits']['count']) == (0, 0)


def conversation_page(rows, after):
    """The GraphQL comment connection of one number: pages of 100 whose cursor is the offset."""
    start = int(after or 0)
    nodes = [dict(id=f'NODE_{row["id"]}', fullDatabaseId=str(row['id']), body=row['body'], createdAt=row['created_at'],
        updatedAt=row['updated_at'], author=None if row['user'] is None else dict(__typename='User', databaseId=row['user']['id']))
        for row in rows[start:start+100]]
    more = start+100 < len(rows)
    return dict(totalCount=len(rows), pageInfo=dict(hasNextPage=more, endCursor=str(start+100) if more else None), nodes=nodes)


class TestRoutedComments(GitHubCase):
    @pytest.fixture(autouse=True)
    def _routed(self, _github, tmp_path):
        self.source = replace(self.source, auth_mode='configured')
        self.state = str(tmp_path/'limits.json')
        self.conversations = {7: [self.comment], 8: [self.pull_comment]}
        self.graph_calls, self.graph_failure = [], None
        rest_request = self.connection.request.side_effect
        def request(method, target, body=None, headers=None):
            if method == 'GET': return rest_request(method, target, body, headers)
            assert (method, target) == ('POST', '/graphql')
            assert headers['Authorization'] == TOKEN and headers['Content-Type'] == 'application/json'
            payload = json.loads(body)
            assert payload['query'].startswith('query IssueComments(') and 'mutation' not in payload['query']
            assert payload['query'] == comments_query(len(payload['variables'])//2-1)
            self.targets.append('/graphql'); self.graph_calls.append(payload)
        self.connection.request.side_effect = request

    def respond(self):
        if self.targets[-1] != '/graphql': return super().respond()
        variables = self.graph_calls[-1]['variables']
        count = (len(variables)-2)//2
        data = dict(rateLimit=dict(cost=1, remaining=4000-len(self.graph_calls), limit=5000, resetAt=stamp(NOW+600)),
            repository=dict(databaseId=42))
        for index in range(count):
            number = variables[f'n{index}']
            data['repository'][f'c{index}'] = dict(__typename='Issue', number=number,
                comments=conversation_page(self.conversations[number], variables[f'a{index}']))
        if self.graph_failure is not None: self.graph_failure(data)
        return Response(dict(data=data), headers={'Content-Type': 'application/json'})

    def serve(self, number, rows):
        """Serve the same comments through both APIs: the REST listing here, the GraphQL pages above."""
        self.conversations[number] = rows
        for index in range(0, max(len(rows), 1), 100):
            headers = dict(self.headers)
            if index+100 < len(rows): headers['Link'] = '<'+self.base+f'/issues/{number}/comments?per_page=100&page={index//100+2}>; rel="next"'
            self.routes[f'/issues/{number}/comments?per_page=100&page={index//100+1}'] = (
                lambda rows=rows[index:index+100], meta=headers: Response(rows, headers=meta))
        (self.issue if number == 7 else self.pull_issue)['comments'] = len(rows)
        if number == 8: self.pull.update(comments=len(rows))

    def limiter_state(self, **buckets):
        seed = Limiter(path=self.state, clock=lambda: NOW)
        for name, (remaining, reset) in buckets.items():
            seed.observe(HOST, WHO, name, 200, dict(limit=5000, remaining=remaining, reset=reset), charged=False)

    def client(self, **options):
        return GitHubAPI(self.base, self.selection, authorization=TOKEN, routing='auto', path=self.state, clock=lambda: NOW, **options)

    def state_for(self):
        return dict(requests=0, used=0, validators={}, conditional=0, not_modified=0, deadline=None)

    def capture(self, **options):
        client = self.client(**options)
        return client, client.read(self.source)

    def identities(self, client, snapshot):
        return ([(r.native_id_sha256, r.revision_sha256) for r in snapshot.records],
            client.summary['nodes'], client.summary['relations'])

    def comment_requests(self):
        return [t for t in self.targets if '/comments?' in t and '/issues/' in t]

    def test_a_graphql_batch_yields_the_same_normalized_records_and_digests_as_the_rest_listing(self):
        self.serve(7, [self.comment, dict(self.comment, id=702, url=self.base+'/issues/comments/702', body='SYNTHETIC_OTHER_BODY')])
        rest = self.client(); rest.routing = 'rest'
        expected = self.identities(rest, rest.read(self.source))
        self.targets.clear()

        client, snapshot = self.capture()

        assert self.identities(client, snapshot) == expected
        assert len(self.graph_calls) == 1 and self.comment_requests() == []
        assert client.transport.limiter.receipt()['routes'] == [
            dict(read='issue_comments', transport='graphql', bucket='graphql', reason='cheaper_graphql')]

    def test_more_than_one_hundred_comments_page_through_cursors_into_identical_records(self):
        rows = [dict(self.comment, id=1000+n, url=self.base+f'/issues/comments/{1000+n}') for n in range(150)]
        self.serve(7, rows)
        rest = self.client(); rest.routing = 'rest'
        expected = self.identities(rest, rest.read(self.source))
        self.targets.clear()

        client, snapshot = self.capture()

        assert self.identities(client, snapshot) == expected
        assert [call['variables']['a0'] for call in self.graph_calls] == [None, '100']
        assert self.comment_requests() == []

    def test_rest_at_reserve_serves_the_read_through_graphql(self):
        self.limiter_state(core=(10, NOW+600))
        client, state = self.client(), self.state_for()

        client._route_comments([7], state)

        assert state['graphql_comments'][7] == [dict(id=701, url=self.base+'/issues/comments/701', issue_url=self.base+'/issues/7',
            body='SYNTHETIC_PRIVATE_BODY', created_at=self.stamp, updated_at=self.stamp, user=dict(id=99))]
        assert self.comment_requests() == []
        assert client.transport.limiter.receipt()['routes'] == [
            dict(read='issue_comments', transport='graphql', bucket='graphql', reason='rest_reserve_floor')]

    def test_graphql_at_reserve_with_a_healthy_rest_bucket_reads_through_rest(self):
        self.limiter_state(graphql=(10, NOW+600))

        client, _ = self.capture()

        assert self.graph_calls == [] and len(self.comment_requests()) == 2
        assert client.transport.limiter.receipt()['routes'] == [
            dict(read='issue_comments', transport='rest', bucket='core', reason='graphql_reserve_floor')]

    def test_both_buckets_at_reserve_halt_as_blocked_access_with_the_earliest_reset(self):
        self.limiter_state(core=(10, NOW+600), graphql=(10, NOW+300))
        client = self.client()

        with pytest.raises(RateLimited) as halt:
            client._route_comments([7, 8], self.state_for())

        assert halt.value.reset == NOW+300 and self.targets == []
        assert client.transport.limiter.receipt()['halt_reason'] == 'reserve_floor'

    def test_both_buckets_briefly_blocked_wait_for_the_earlier_reset_and_read_through_graphql(self):
        self.limiter_state(core=(10, NOW+30), graphql=(10, NOW+20))
        clock, state = WaitClock(), self.state_for()
        client = GitHubAPI(self.base, self.selection, authorization=TOKEN, routing='auto', path=self.state,
            clock=clock, sleep=clock.sleep, max_wait=60)

        client._route_comments([7, 8], state)

        assert clock.slept == [20] and sorted(state['graphql_comments']) == [7, 8]
        assert self.comment_requests() == []
        receipt = client.transport.limiter.receipt()
        assert (receipt['halt_reason'], receipt['waits']['count']) == (None, 1)
        assert receipt['routes'] == [dict(read='issue_comments', transport='graphql', bucket='graphql', reason='all_buckets_wait')]

    def test_a_capture_with_both_buckets_at_reserve_is_blocked_access_not_a_checkpoint(self):
        self.limiter_state(core=(10, NOW+600), graphql=(10, NOW+300))
        client = self.client()

        with pytest.raises(TransportError):
            client.read(self.source)

        receipt = client.transport.limiter.receipt()
        assert (receipt['halt_reason'], receipt['requests_used']) == ('reserve_floor', 0)
        assert self.targets == [] and not hasattr(client, 'summary')

    def test_graphql_spend_is_charged_from_the_reported_cost_and_beats_the_rest_equivalent(self):
        client, _ = self.capture()

        receipt = client.transport.limiter.receipt()

        assert receipt['buckets']['graphql']['points'] == 1
        assert receipt['buckets']['graphql']['remaining_end'] == 4000-1
        assert receipt['buckets']['graphql']['points'] < len(self.selection.issues)+len(self.selection.pulls)
        assert not any(t == '/issues/7/comments?per_page=100&page=1' for t in self.targets)

    def test_a_cached_refresh_goes_through_rest_conditional_requests(self):
        self.headers['ETag'] = 'W/"synthetic-validator"'
        client = GitHubAPI(self.base, self.selection, authorization=TOKEN, routing='rest', conditional=True)
        client.read(self.source)
        client.routing = 'auto'
        self.targets.clear()

        client.read(self.source)

        assert self.graph_calls == [] and len(self.comment_requests()) == 2
        assert client.transport.limiter.receipt()['routes'] == [
            dict(read='issue_comments', transport='rest', bucket='core', reason='cached_refresh')]

    def test_an_author_graphql_cannot_name_leaves_that_conversation_to_rest_without_a_default(self):
        self.serve(8, [dict(self.pull_comment, user=None)])
        client, state = self.client(), self.state_for()

        client._route_comments([7, 8], state)

        assert sorted(state['graphql_comments']) == [7]
        assert [r['transport'] for r in client.transport.limiter.receipt()['routes']] == ['graphql', 'rest']
        assert client.transport.limiter.receipt()['routes'][1]['reason'] == 'graphql_cannot_supply_all_fields'
        assert client._comments(8, state) == [dict(self.pull_comment, user=None)]

    def test_rest_remains_the_default_and_never_calls_graphql(self):
        client = GitHubAPI(self.base, self.selection, authorization=TOKEN)

        client.read(self.source)

        assert self.graph_calls == [] and client.routing == 'rest'
        assert client.transport.limiter.receipt()['routes'] == []

    def test_without_authorization_the_read_is_rest_only_and_says_so(self):
        client = GitHubAPI(self.base, self.selection, routing='auto')

        client.read(replace(self.source, auth_mode='none'))

        assert self.graph_calls == []
        assert client.transport.limiter.receipt()['routes'] == [dict(read='issue_comments', transport='rest', bucket='core', reason='only_rest')]

    @pytest.mark.parametrize('routing', [pytest.param('graphql', id='graphql'), pytest.param('', id='empty'), pytest.param(None, id='none')])
    def test_an_unknown_routing_mode_fails_closed_before_connecting(self, routing):
        with pytest.raises(TransportError):
            GitHubAPI(self.base, self.selection, authorization=TOKEN, routing=routing)

    @pytest.mark.parametrize('corrupt', [
        pytest.param(lambda data: data['repository'].update(databaseId=999), id='foreign-repository'),
        pytest.param(lambda data: data['repository']['c0']['comments'].update(totalCount=2), id='total-exceeds-nodes'),
        pytest.param(lambda data: data['repository']['c0']['comments']['nodes'].append(data['repository']['c0']['comments']['nodes'][0]), id='duplicate-identity'),
        pytest.param(lambda data: data['repository']['c0'].update(number=99), id='another-number'),
        pytest.param(lambda data: data['repository'].update(c0=None), id='missing-conversation'),
        pytest.param(lambda data: data['repository']['c0']['comments']['pageInfo'].update(hasNextPage=True), id='next-page-without-cursor'),
    ])
    def test_a_malformed_graphql_batch_blocks_the_capture_and_never_looks_complete(self, corrupt):
        self.graph_failure = corrupt

        client = self.client()
        with pytest.raises(TransportError):
            client.read(self.source)

        assert not hasattr(client, 'summary')

    def test_a_manifest_selects_auto_routing_and_rejects_an_unknown_mode(self, tmp_path):
        from agent_harness_lab.sources.github_config import GitHubConfigured
        value = dict(schema_version=1, source=self.source.to_dict(), endpoint=self.base, selection=self.selection.to_dict())
        scope = dict(project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        manifest = tmp_path/'synthetic-github.json'

        manifest.write_text(json.dumps(dict(value, limits=dict(routing='auto'))))
        assert GitHubConfigured(manifest).declaration(**scope)[0] == self.source
        manifest.write_text(json.dumps(dict(value, limits=dict(routing='graphql'))))
        with pytest.raises(ValueError):
            GitHubConfigured(manifest).declaration(**scope)

    def test_the_query_names_only_read_fields_and_stays_inside_one_batch_cap(self):
        query = comments_query(MAX_ALIASES)

        assert 'mutation' not in query and query.count('issueOrPullRequest(') == MAX_ALIASES
        assert 'rateLimit { cost remaining resetAt limit }' in query
        assert 'first: 100' in query and 'first: 101' not in query
