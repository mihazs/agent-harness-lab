"""Descriptive as-of view over declared validity of imported knowledge records; never authority.

A TemporalFacet carries what a knowledge source (OKF page, LLM wiki page, Markdown knowledge
graph note, ADR) declares about one exact imported record revision. `resolve` reports which of
those records were in force at one instant. A historical replay sees only records first
recorded at or before that instant; `as_known_now` is an explicit retrospective view. Missing,
unparseable or timezone-less times, supersession cycles, ambiguous revisions and competing
successors stay unknown with reasons. Declared validity is an author claim, not verified truth.
"""
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from .execution import RecordRef, frozen, objects, unpack
from .schema import Contract, digest, fingerprint, timestamp

PROFILE = 'knowledge-temporal-v1'
STATUSES = ('in_force', 'superseded', 'not_yet_valid', 'expired', 'not_adopted', 'unknown')
DECISIONS = ('unspecified', 'proposed', 'accepted', 'rejected', 'deprecated', 'superseded')
KNOWLEDGE = ('historical', 'as_known_now')
TIMES = ('recorded_at', 'valid_from', 'valid_until')


def declared_time(value):
    """Return (instant, None) or (None, reason); absence, naive and unparseable text stay unknown."""
    if value is None: return None, 'missing'
    try: parsed = datetime.fromisoformat(value)
    except ValueError: return None, 'unparseable'
    if parsed.tzinfo is None or parsed.utcoffset() is None: return None, 'timezone_missing'
    return parsed, None


@dataclass(frozen=True)
class TemporalFacet(Contract):
    """Declared knowledge time, effective interval, decision status and supersession of one revision."""
    record: RecordRef
    recorded_at: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    open_ended: bool = False
    decision_status: str = 'unspecified'
    supersedes: tuple = ()
    superseded_by: tuple = ()
    schema_version: int = 1

    def __post_init__(self):
        self.validate((), {'decision_status': DECISIONS})
        if not isinstance(self.record, RecordRef) or type(self.open_ended) is not bool:
            raise ValueError('temporal facet requires an exact record reference')
        for name in TIMES:
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value or len(value) > 64
                                      or any(ord(c) < 32 for c in value)):
                raise ValueError('declared times must be absent or bounded single-line text')
        for name in ('supersedes', 'superseded_by'):
            frozen(getattr(self, name), RecordRef)
            if len({r.key for r in getattr(self, name)}) != len(getattr(self, name)):
                raise ValueError('duplicate supersession declaration')

    @classmethod
    def parse(cls, value):
        value = unpack(cls, value); value['record'] = RecordRef.parse(value['record'])
        for name in ('supersedes', 'superseded_by'): value[name] = objects(value[name], RecordRef)
        return cls(**value)


def ref(key):
    return RecordRef(*key).to_dict()


def cyclic(adjacency):
    """Members of supersession cycles (including self-loops), found without recursion."""
    index, low, stack, on, counter = {}, {}, [], set(), 0
    for root in sorted(adjacency):
        if root in index: continue
        work = [(root, iter(sorted(adjacency.get(root, ()))))]
        index[root] = low[root] = counter; counter += 1; stack.append(root); on.add(root)
        while work:
            node, children = work[-1]
            child = next(children, None)
            if child is not None:
                if child not in index:
                    index[child] = low[child] = counter; counter += 1; stack.append(child); on.add(child)
                    work.append((child, iter(sorted(adjacency.get(child, ())))))
                elif child in on: low[node] = min(low[node], index[child])
                continue
            work.pop()
            if work: low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop(); on.discard(member); component.append(member)
                    if member == node: break
                if len(component) > 1 or node in adjacency.get(node, ()): yield sorted(component)


def reaches(adjacency, start, goal):
    pending, seen = deque([start]), {start}
    while pending:
        node = pending.popleft()
        for child in adjacency.get(node, ()):
            if child == goal: return True
            if child not in seen: seen.add(child); pending.append(child)
    return False


def own(facet, instant, recorded):
    """Status from the record's own declarations once no successor has taken effect."""
    if facet.decision_status in ('proposed', 'rejected'):
        return 'not_adopted', ['decision_'+facet.decision_status]
    start, problem = declared_time(facet.valid_from)
    if problem: return 'unknown', ['valid_from_'+problem]
    end = None
    if facet.valid_until is None and not facet.open_ended: return 'unknown', ['valid_until_missing']
    if facet.valid_until is not None:
        if facet.open_ended: return 'unknown', ['conflicting_end_declaration']
        end, problem = declared_time(facet.valid_until)
        if problem: return 'unknown', ['valid_until_'+problem]
        if end <= start: return 'unknown', ['empty_or_inverted_interval']
    if instant < start: return 'not_yet_valid', ['before_declared_valid_from']
    if end is not None and end <= instant: return 'expired', ['at_or_after_declared_valid_until']
    if facet.decision_status in ('deprecated', 'superseded'):
        # A self-declared lifecycle change has no effective time beyond the revision's knowledge time.
        if recorded is not None and recorded <= instant:
            return ('expired' if facet.decision_status == 'deprecated' else 'superseded'), \
                ['declared_'+facet.decision_status+'_by_revision_recorded_by_instant']
        return 'unknown', ['lifecycle_effective_time_unknown']
    return 'in_force', ['open_ended_by_declaration' if end is None else 'within_declared_interval']


def effective(facet, instant):
    """Whether an adopted successor's declared start has been reached; None when it is unknown."""
    if facet.decision_status in ('proposed', 'rejected'): return False
    start, problem = declared_time(facet.valid_from)
    return None if problem else start <= instant


def resolve(facets, at, *, revisions=(), knowledge='historical'):
    """Resolve declared validity at instant `at`.

    `revisions` are store revision edges `[source, native_id, before, after]`; a later revision
    supersedes an earlier one with the same rules as a declared successor. Ties between
    revisions or successors are resolved only by explicit declarations, never by arrival order.
    """
    timestamp(at); instant = datetime.fromisoformat(at)
    if knowledge not in KNOWLEDGE: raise ValueError('unsupported knowledge view')
    facets, revisions = tuple(facets), tuple(revisions); frozen(facets, TemporalFacet)
    index = {}
    for facet in facets:
        if facet.record.key in index: raise ValueError('duplicate temporal facet')
        index[facet.record.key] = facet
    edges = []
    for edge in revisions:
        if not isinstance(edge, (list, tuple)) or len(edge) != 4: raise ValueError('invalid revision edge')
        for value in edge: fingerprint(value)
        source, native, before, after = edge
        edges.append(((source, native, before), (source, native, after), (source, native, after), 'revision'))
    for key, facet in index.items():
        edges.extend((r.key, key, key, 'declared') for r in facet.supersedes)
        edges.extend((key, r.key, key, 'declared') for r in facet.superseded_by)
    recorded, future, structural = {}, set(), {}
    for key, facet in index.items():
        when, problem = declared_time(facet.recorded_at); recorded[key] = when
        if knowledge == 'historical':
            if problem: structural.setdefault(key, set()).add('recorded_at_'+problem)
            elif when > instant: future.add(key)
    adjacency, blocked, dangling = {}, {}, set()
    for before, after, declarer, kind in edges:
        if declarer in future or before in future: continue
        if before not in index:
            if kind == 'declared': dangling.add((before, after, 'missing_predecessor'))
            continue
        if after not in index:
            if kind == 'declared': dangling.add((before, after, 'missing_successor'))
            blocked.setdefault(before, set()).add('successor_without_temporal_facet'); continue
        if after in future:
            blocked.setdefault(before, set()).add('successor_not_known_at_instant'); continue
        adjacency.setdefault(before, set()).add(after)
    conflicts = []
    for members in cyclic(adjacency):
        conflicts.append(dict(kind='supersession_cycle', members=[ref(k) for k in members]))
        for key in members: structural.setdefault(key, set()).add('supersession_cycle')
    groups = {}
    for key in sorted(index.keys()-future): groups.setdefault(key[:2], []).append(key)
    for _, members in sorted(groups.items()):
        # A revision is a leaf of its record unless another revision of the same record is reachable from it;
        # edges to other records do not order revisions of this record.
        leaves = [k for k in members if not any(o != k and reaches(adjacency, k, o) for o in members)]
        if len(leaves) > 1:
            conflicts.append(dict(kind='ambiguous_revisions', members=[ref(k) for k in leaves]))
            for key in leaves: structural.setdefault(key, set()).add('ambiguous_revisions')
    competing = set()
    for before in sorted(adjacency):
        successors = sorted(adjacency[before])
        rivals = sorted({s for s in successors for o in successors if s != o
                         and not reaches(adjacency, s, o) and not reaches(adjacency, o, s)})
        if rivals:
            competing.update(rivals)
            conflicts.append(dict(kind='competing_successors', predecessor=ref(before), members=[ref(k) for k in rivals]))
    conflicts.sort(key=lambda row: json.dumps(row, sort_keys=True))
    rows = []
    for key in sorted(index.keys()-future):
        successors = sorted(adjacency.get(key, ()))
        if key in structural: status, reasons = 'unknown', sorted(structural[key])
        elif key in competing: status, reasons = 'unknown', ['competing_successors']
        elif key in blocked: status, reasons = 'unknown', sorted(blocked[key])
        elif any(s in structural for s in successors): status, reasons = 'unknown', ['successor_status_unknown']
        else:
            effects = [effective(index[s], instant) for s in successors]
            if True in effects: status, reasons = 'superseded', ['superseded_by_effective_successor']
            elif None in effects: status, reasons = 'unknown', ['successor_effective_time_unknown']
            else:
                status, reasons = own(index[key], instant, recorded[key])
                if successors: reasons.append('successor_not_adopted_or_not_yet_effective')
        rows.append(dict(record=ref(key), status=status, reasons=reasons, successors=[ref(s) for s in successors]))
    inputs = dict(at=at, knowledge=knowledge, facets=sorted((f.to_dict() for f in facets), key=digest),
                  revisions=sorted(list(e) for e in revisions))
    return dict(schema_version=1, profile=PROFILE, as_of=at, knowledge=knowledge,
        retrospective=knowledge == 'as_known_now', inputs_sha256=digest(inputs), records=rows,
        not_known_at_instant=[ref(k) for k in sorted(future)],
        dangling=[dict(predecessor=ref(b), successor=ref(a), reason=r) for b, a, r in sorted(dangling)],
        conflicts=conflicts, counts={s: n for s, n in sorted(Counter(r['status'] for r in rows).items())},
        validity_source='author_declared', truth_established=False, authority='none',
        task_outcomes_verified=False, execution_authorized=False, publication_allowed=False)


def resolve_store(store, facets, at, *, knowledge='historical'):
    """Resolve facets bound to present revisions of one validated SourceStore journal snapshot."""
    state, raw = store.read_snapshot()
    present = {(r['source_sha256'], r['native_id_sha256'], r['revision_sha256']): r['state']
               for r in (entry['record'] for entry in state['records'])}
    facets = tuple(facets); frozen(facets, TemporalFacet)
    if any(present.get(f.record.key) != 'present' for f in facets):
        raise ValueError('temporal facets must describe present revisions in the selected journal')
    result = resolve(facets, at, revisions=state['supersessions'], knowledge=knowledge)
    result['cache_sha256'] = hashlib.sha256(raw).hexdigest()
    return result
