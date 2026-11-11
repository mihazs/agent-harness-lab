"""Private operator-attested links over exact cache revisions, never native proof."""
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from agent_harness_lab.evaluation.integrity import contained, open_nofollow, read_regular, regular_sha256
from agent_harness_lab.private_journal import PrivateJournal, encoded, record, record_bytes, sync_directory, write_bytes
from .execution import RecordRef, frozen, objects, unpack
from .graph import STATUSES
from .schema import Contract, EvidenceRecord, digest, fingerprint, label
from .store import SourceStore

RELATIONS = ('same_observation', 'derived_from', 'same_execution', 'test_result_for', 'independent_case')


def text(value):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError('review text must be nonempty and single-line')


def refs(value):
    frozen(value, RecordRef)
    if len({r.key for r in value}) != len(value): raise ValueError('duplicate lineage evidence')


@dataclass(frozen=True)
class LineageClaim(Contract):
    left: RecordRef
    right: RecordRef
    relation: str
    author: str
    evidence: tuple
    schema_version: int = 1

    def __post_init__(self):
        self.validate((), {'relation':RELATIONS}); label(self.author); refs(self.evidence)
        if (not isinstance(self.left, RecordRef) or not isinstance(self.right, RecordRef)
                or self.left.key == self.right.key or not self.evidence):
            raise ValueError('lineage claim needs distinct exact endpoints and evidence')

    @classmethod
    def parse(cls, value):
        value = unpack(cls, value)
        for name in ('left', 'right'): value[name] = RecordRef.parse(value[name])
        value['evidence'] = objects(value['evidence'], RecordRef)
        return cls(**value)


@dataclass(frozen=True)
class LineageReview(Contract):
    case_id: str
    expected_event_sha256: str
    cache_sha256: str
    decision: str
    reviewer: str
    provenance: str
    basis_kind: str
    basis: str
    evidence: tuple
    reason: str
    source: str = 'operator_attestation'
    schema_version: int = 1

    def __post_init__(self):
        self.validate(('case_id', 'expected_event_sha256', 'cache_sha256'), {
            'decision':('reviewed', 'quarantined', 'retired', 'rejected'), 'source':('operator_attestation',)})
        label(self.reviewer); label(self.basis_kind); refs(self.evidence)
        for value in (self.provenance, self.basis, self.reason): text(value)
        if self.decision == 'reviewed' and (self.basis_kind != 'independent_metadata_review' or not self.evidence):
            raise ValueError('a fresh session alone is not independent metadata review')

    @classmethod
    def parse(cls, value):
        value = unpack(cls, value); value['evidence'] = objects(value['evidence'], RecordRef)
        return cls(**value)


def transition(state, kind, target, previous_kind=None):
    if state is None: return kind == target == 'proposed'
    if kind == target == 'reviewed': return state == 'proposed'
    return kind == target and target in ('quarantined', 'retired', 'rejected') and state not in (target, 'retired', 'rejected')


def members(state, claim, evidence=()):
    records = { (r['record']['source_sha256'], r['record']['native_id_sha256'], r['record']['revision_sha256']):
                EvidenceRecord.parse(r['record']) for r in state['records'] }
    superseded = {tuple(edge[:3]) for edge in state['supersessions']}
    leaves = {}
    for key, value in records.items():
        if key not in superseded: leaves.setdefault(key[:2], set()).add(key)
    for endpoint in (claim.left, claim.right):
        if (endpoint.key not in records or records[endpoint.key].state != 'present' or endpoint.key in superseded
                or len(leaves.get(endpoint.key[:2], ())) != 1):
            raise ValueError('lineage endpoint is missing, removed, superseded or ambiguous')
    for ref in (*claim.evidence, *evidence):
        if (ref.key not in records or records[ref.key].state != 'present' or ref.key in superseded
                or len(leaves.get(ref.key[:2], ())) != 1):
            raise ValueError('lineage evidence is missing, removed, superseded or ambiguous')


def representative(parent, key):
    parent.setdefault(key, key)
    while parent[key] != key: key = parent[key]
    return key


class LineageStore(PrivateJournal):
    def __init__(self, root, *, project_sha256, scope_sha256):
        fingerprint(project_sha256); fingerprint(scope_sha256)
        self.project, self.scope = project_sha256, scope_sha256
        super().__init__(root, 'lineage-v1:'+digest((self.project, self.scope)), transition,
            max_events=None, max_metadata=None, max_entries=None)

    def cache(self, path):
        path = Path(path).absolute()
        if '..' in path.parts or path.is_relative_to(self.root) or self.root.is_relative_to(path):
            raise ValueError('source cache and lineage store must not overlap')
        store = SourceStore(path, project_sha256=self.project, scope_sha256=self.scope)
        state, raw = store.read_snapshot()
        return path, state, raw, hashlib.sha256(raw).hexdigest()

    def unchanged(self, path, cache_sha256):
        """Same outcome as self.cache(path)[3] == cache_sha256 without a second parsed copy.

        Matching bytes were already validated, so only a mismatch or read failure needs the full
        read_snapshot path to raise exactly what it always raised."""
        store = SourceStore(path, project_sha256=self.project, scope_sha256=self.scope)
        try:
            store._safe_path()
            if regular_sha256(store.path, store.path.stat().st_size) == cache_sha256: return True
        except (OSError, ValueError): pass
        return self.cache(path)[3] == cache_sha256

    def case_identity(self, claim, cache_sha256):
        value = claim.to_dict(); value.pop('author')
        return digest(dict(project_sha256=self.project, scope_sha256=self.scope, cache_sha256=cache_sha256, claim=value))

    def cases(self):
        return self.case_ids(None)

    def snapshot(self, raw, identity):
        folder = contained(self.root, 'snapshots'); folder.mkdir(mode=0o700, exist_ok=True)
        path = contained(folder, identity+'.json')
        with os.scandir(folder) as values:
            for entry in values:
                if not entry.name.endswith('.json'): raise ValueError('unknown lineage snapshot entry')
                fingerprint(entry.name[:-5]); info = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise ValueError('unsafe lineage snapshot')
        if path.exists():
            if regular_sha256(path, None) != identity:
                raise ValueError('immutable lineage snapshot changed')
        else:
            write_bytes(path, raw)
        descriptor = open_nofollow(path)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
        sync_directory(folder); sync_directory(self.root)
        return 'snapshots/'+identity+'.json'

    def propose(self, cache_path, claim_path):
        raw_claim = read_regular(claim_path, None)
        value, claim_hash = record_bytes(raw_claim, None); claim = LineageClaim.parse(value)
        origin, state, raw, cache_hash = self.cache(cache_path); members(state, claim)
        case_id = self.case_identity(claim, cache_hash)
        with self.locked(create=True):
            self.cases()
            if self.case(case_id).exists(): raise ValueError('lineage claim already recorded')
            snapshot = self.snapshot(raw, cache_hash)
            inputs = dict(schema_version=1, project_sha256=self.project, scope_sha256=self.scope,
                cache_origin=str(origin), cache_sha256=cache_hash, snapshot=snapshot, claim_sha256=claim_hash)
            with tempfile.TemporaryDirectory(dir=self.root, prefix='.lineage-stage-') as temporary:
                stage = Path(temporary)/'case'; stage.mkdir(mode=0o700); (stage/'events').mkdir(mode=0o700)
                write_bytes(stage/'claim.json', raw_claim); write_bytes(stage/'inputs.json', encoded(inputs)); sync_directory(stage)
                if not self.unchanged(origin, cache_hash): raise ValueError('source cache changed during lineage capture')
                events = []; self.append(stage, events, 'proposed', 'proposed', dict(inputs_sha256=hashlib.sha256(encoded(inputs)).hexdigest()))
                cases = contained(self.root, 'cases'); cases.mkdir(mode=0o700, exist_ok=True)
                stage.rename(self.case(case_id)); sync_directory(cases); sync_directory(self.root)
            return self.summary(case_id, inputs, events)

    def opened(self, case_id):
        case = self.case(case_id); events = self.history(case); inputs, identity = record(case/'inputs.json', None)
        if set(inputs) != set('schema_version project_sha256 scope_sha256 cache_origin cache_sha256 snapshot claim_sha256'.split()):
            raise ValueError('invalid lineage input manifest')
        if (type(inputs['schema_version']) is not int or inputs['schema_version'] != 1
                or (inputs['project_sha256'], inputs['scope_sha256']) != (self.project, self.scope)
                or events[0][0]['payload'].get('inputs_sha256') != identity):
            raise ValueError('lineage case scope/manifest changed')
        value, claim_hash = record(case/'claim.json', None); claim = LineageClaim.parse(value)
        if claim_hash != inputs['claim_sha256'] or self.case_identity(claim, inputs['cache_sha256']) != case_id:
            raise ValueError('lineage claim changed')
        fingerprint(inputs['cache_sha256'])
        if inputs['snapshot'] != 'snapshots/'+inputs['cache_sha256']+'.json': raise ValueError('invalid lineage snapshot locator')
        if regular_sha256(contained(self.root, inputs['snapshot']), None) != inputs['cache_sha256']:
            raise ValueError('lineage snapshot changed')
        for event, _ in events[1:]:
            if set(event['payload']) != {'review'}: raise ValueError('invalid persisted lineage decision')
            review = LineageReview.parse(event['payload']['review'])
            if (review.case_id != case_id or review.cache_sha256 != inputs['cache_sha256']
                    or review.expected_event_sha256 != event['previous_sha256'] or review.decision != event['kind']
                    or review.decision == 'reviewed' and claim.author.casefold() == review.reviewer.casefold()):
                raise ValueError('persisted lineage review binding changed')
        return case, inputs, claim, events

    def summary(self, case_id, inputs, events):
        return dict(schema_version=1, case_id=case_id, state=events[-1][0]['state'], event_sha256=events[-1][1],
            cache_sha256=inputs['cache_sha256'], project_sha256=self.project, scope_sha256=self.scope,
            review_authentication='operator_attested' if events[-1][0]['state'] == 'reviewed' else 'unverified',
            current_applicability='not_checked', native_freshness='unverified', independent_roots=None,
            task_outcomes_verified=False, execution_authorized=False, publication_allowed=False)

    def show(self, case_id):
        with self.locked():
            _, inputs, _, events = self.opened(case_id)
            return self.summary(case_id, inputs, events)

    def review(self, case_id, cache_path, review_path):
        value, _ = record(review_path, None); review = LineageReview.parse(value)
        if review.case_id != case_id: raise ValueError('lineage review case mismatch')
        with self.locked():
            case, inputs, claim, events = self.opened(case_id)
            if review.expected_event_sha256 != events[-1][1] or review.cache_sha256 != inputs['cache_sha256']:
                raise ValueError('stale lineage review identity')
            selected = Path(cache_path).absolute()
            if '..' in selected.parts or str(selected) != inputs['cache_origin']:
                raise ValueError('lineage review requires its explicitly selected cache locator')
            if review.decision == 'reviewed':
                origin, state, _, identity = self.cache(cache_path)
                if str(origin) != inputs['cache_origin'] or identity != inputs['cache_sha256']:
                    raise ValueError('selected source cache changed; lineage review is stale')
                members(state, claim, review.evidence)
                if claim.author.casefold() == review.reviewer.casefold(): raise ValueError('lineage review requires a separate operator identity')
                if self.cache(origin)[3] != identity: raise ValueError('source cache changed during lineage review')
            self.append(case, events, review.decision, review.decision, dict(review=review.to_dict()))
            return self.summary(case_id, inputs, events)

    def query(self, cache_path):
        origin, state, _, cache_hash = self.cache(cache_path)
        links = []; assertions = []; stale = []; heads = []; nodes = {}; parent = {}
        with self.locked():
            for case_id in self.cases():
                _, inputs, claim, events = self.opened(case_id); heads.append((case_id, events[-1][1]))
                if inputs['cache_sha256'] != cache_hash or inputs['cache_origin'] != str(origin):
                    stale.append(case_id); continue
                if events[-1][0]['state'] != 'reviewed': continue
                reviewed = LineageReview.parse(events[-1][0]['payload']['review'])
                try: members(state, claim, reviewed.evidence)
                except ValueError:
                    stale.append(case_id); continue
                row = dict(case_id=case_id, relation=claim.relation, left=claim.left.to_dict(), right=claim.right.to_dict(),
                    review_sha256=events[-1][1], evidence_class='reviewed_link', authentication='operator_attested')
                if claim.relation == 'independent_case': assertions.append(row); continue
                links.append(row)
                for ref in (claim.left, claim.right): nodes[ref.key] = ref
                left, right = representative(parent, claim.left.key), representative(parent, claim.right.key); parent[right] = left
            groups = {}
            for key in sorted(nodes): groups.setdefault(representative(parent, key), []).append(nodes[key].to_dict())
            components = sorted((dict(component_sha256=digest(value), members=value) for value in groups.values()), key=lambda row:row['component_sha256'])
            conflicts = []
            for row in assertions:
                left, right = RecordRef.parse(row['left']).key, RecordRef.parse(row['right']).key
                if left in parent and right in parent and representative(parent, left) == representative(parent, right):
                    conflicts.append(dict(case_id=row['case_id'], reason='independence_assertion_within_correlated_component'))
            aliases = {}
            for row in links:
                if row['relation'] == 'same_observation':
                    left = representative(aliases, RecordRef.parse(row['left']).key)
                    right = representative(aliases, RecordRef.parse(row['right']).key); aliases[right] = left
            derived = [row for row in links if row['relation'] == 'derived_from']
            adjacency = {}
            for row in derived:
                left = representative(aliases, RecordRef.parse(row['left']).key)
                right = representative(aliases, RecordRef.parse(row['right']).key)
                adjacency.setdefault(left, []).append(right)
            for row in derived:
                target = representative(aliases, RecordRef.parse(row['left']).key)
                pending = [representative(aliases, RecordRef.parse(row['right']).key)]; seen = set()
                while pending:
                    key = pending.pop()
                    if key == target:
                        conflicts.append(dict(case_id=row['case_id'], reason='derivation_cycle')); break
                    if key not in seen: seen.add(key); pending.extend(adjacency.get(key, ()))
            if self.cache(origin)[3] != cache_hash: raise ValueError('source cache changed during lineage query')
        revision = digest(dict(project=self.project, scope=self.scope, cache=cache_hash, heads=heads))
        result = dict(schema_version=1, grouping_revision_sha256=revision, cache_sha256=cache_hash,
            applicability='selected_frozen_cache_only', native_freshness='unverified', links=links,
            components=components, independence_assertions=assertions, conflicts=conflicts, stale_cases=stale,
            independent_roots=None, task_outcomes_verified=False, execution_authorized=False, publication_allowed=False)
        from .outcomes import correlate
        outcomes = correlate(state, links)
        if outcomes['records'] or outcomes['ambiguous_sources']: result['outcome_correlation'] = outcomes
        return result


    def coverage(self, cache_path):
        """Bounded evidence-coverage report: what the cache holds, what is missing, which joins were tried and what stays unproven."""
        result = self.query(cache_path)
        origin, state, _, cache_hash = self.cache(cache_path)
        inventory = SourceStore(origin, project_sha256=self.project, scope_sha256=self.scope).inventory()
        if cache_hash != result['cache_sha256'] or self.cache(origin)[3] != cache_hash: raise ValueError('source cache changed during coverage report')
        superseded = {tuple(edge[:3]) for edge in state['supersessions']}
        types = Counter(e['record']['record_type'] for e in state['records']
            if e['record']['state'] == 'present' and (e['record']['source_sha256'], e['record']['native_id_sha256'], e['record']['revision_sha256']) not in superseded)
        joins = Counter()
        for s in inventory['sources']:
            if s['evidence_graph'] is not None: joins.update(s['evidence_graph']['statuses'])
        missing = [dict(source_sha256=s['source_sha256'], reasons=[reason for reason, absent in (
            ('no_active_snapshot', not s['active_snapshot']), ('no_execution_metadata', s['execution_metadata'] is None),
            ('no_evidence_graph', s['evidence_graph'] is None), ('tombstoned_records', s['tombstones'] > 0),
            ('capture_truncated_or_limited', bool(s['declared_coverage'] and (s['declared_coverage']['truncated'] or s['declared_coverage']['limit_reached']
                or s['declared_coverage']['gaps'])))) if absent]) for s in inventory['sources']]
        rows = result.get('outcome_correlation', {}).get('records', [])
        return dict(schema_version=1, profile='evidence-coverage-v1', project_sha256=self.project, scope_sha256=self.scope,
            cache_sha256=cache_hash, grouping_revision_sha256=result['grouping_revision_sha256'],
            applicability=result['applicability'], native_freshness='unverified',
            source_kinds=sorted({s['kind'] for s in inventory['sources']}), source_count=inventory['source_count'],
            adapter_versions_sha256=sorted({v for s in inventory['sources'] for v in s['adapter_versions_sha256']}),
            record_types=dict(sorted(types.items())), missing_data=[m for m in missing if m['reasons']],
            attempted_joins=dict(graph_claims={k: joins.get(k, 0) for k in STATUSES},
                reviewed_links=dict(sorted(Counter(l['relation'] for l in result['links']).items()))),
            claims=dict(contradicted_reports=sum(r['contradiction'] for r in rows), unverified_outcomes=sum(r['outcome'] == 'unverified' for r in rows),
                ambiguous_outcome_sources=len(result.get('outcome_correlation', {}).get('ambiguous_sources', ())),
                conflicts=result['conflicts'], stale_cases=result['stale_cases']),
            correlated_components=len(result['components']), component_members=sum(len(c['members']) for c in result['components']),
            independent_roots=None, task_outcomes_verified=False, execution_authorized=False, publication_allowed=False,
            source_read=False, network_used=False)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog='ahl sources lineage', description=__doc__,
        epilog='Claims and reviews bind exact source-cache revisions and operator identities; they never authorize execution or prove independent roots. '
        'All commands print private JSON metadata. Exit 0: completed; 2: rejected input/binding; '
        '3: busy store, or query/coverage with stale cases/conflicts. '
        'Example: ahl sources lineage --store /private/lineage --project-sha256 PROJECT_HASH --scope-sha256 SCOPE_HASH '
        'propose --cache /private/sources.json --claim claim.json. Then inspect the returned case/event/cache hashes before preparing a review.')
    parser.add_argument('--store', type=Path, required=True,help='Private lineage journal directory, separate from the source cache')
    parser.add_argument('--project-sha256', required=True,help='Expected 64-hex project identity of the selected source cache')
    parser.add_argument('--scope-sha256', required=True,help='Expected 64-hex selection/scope identity of the selected source cache')
    commands = parser.add_subparsers(dest='action', required=True,help='Choose one explicit claim, review or read-only query operation')
    for name in ('propose', 'review', 'show', 'query', 'coverage'):
        purpose={'propose':'Record an unsigned claim and freeze its exact source-cache snapshot',
            'review':'Append an operator-attested decision bound to the case, event and cache hashes',
            'show':'Read a recorded case summary without checking current cache applicability',
            'query':'Read reviewed links and structured outcome contradictions in the selected cache',
            'coverage':'Report source kinds, missing data, attempted joins, contradicted claims and unproven independence for the selected cache'}
        action = commands.add_parser(name,help=purpose[name],description=purpose[name],
            epilog='Output is private JSON metadata, not native execution proof. Use the parent --help for scope identities and exit states.')
        if name in ('review', 'show'): action.add_argument('--case', required=True,help='Exact case ID returned by propose; not a file path')
        if name != 'show': action.add_argument('--cache', type=Path, required=True,help='Explicit private SourceStore journal file with matching project/scope identities')
        if name == 'propose': action.add_argument('--claim', type=Path, required=True,
            help='LineageClaim schema-1 JSON: left/right RecordRefs, relation, author and nonempty evidence references')
        if name == 'review': action.add_argument('--record', type=Path, required=True,
            help='LineageReview schema-1 JSON binding case/event/cache hashes, decision, reviewer, provenance, basis, evidence and reason')
    args = parser.parse_args(argv)
    try:
        store = LineageStore(args.store, project_sha256=args.project_sha256, scope_sha256=args.scope_sha256)
        if args.action == 'propose': result = store.propose(args.cache, args.claim)
        elif args.action == 'review': result = store.review(args.case, args.cache, args.record)
        elif args.action == 'show': result = store.show(args.case)
        elif args.action == 'coverage': result = store.coverage(args.cache)
        else: result = store.query(args.cache)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        found = result['claims'] if args.action == 'coverage' else result
        return 3 if args.action in ('query', 'coverage') and (found['conflicts'] or found['stale_cases']) else 0
    except BlockingIOError:
        print(json.dumps(dict(status='busy', execution_authorized=False, publication_allowed=False))); return 3
    except (ValueError, OSError, TypeError, KeyError, RecursionError):
        parser.exit(2, 'lineage operation rejected; inspect exact scope/cache/review bindings; private history retained\n')
