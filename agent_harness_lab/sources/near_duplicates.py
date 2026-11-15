"""Offline lexical near-duplicate review queue over caller-held source text.

A queued pair is a place to look, never a link, a root merge or an independence claim: wording is copied
across unrelated records, and a paraphrase can share none. Text comes only from the caller, is never
fetched, and never enters the report. Pairs already covered by a reviewed lineage component or an
independent-case assertion stay listed, so repeated text exposure and statistical independence remain
separate properties.
"""
from collections import Counter, defaultdict
import re
import unicodedata
from .execution import RecordRef
from .grouping import text_fingerprint
from .schema import digest

PROFILE = 'cross-source-near-duplicate-queue-v1'
RULE = 'word-punctuation-3gram-jaccard-v1'
THRESHOLD_PERMILLE = 800
STATUSES = ('pending_review', 'covered_by_reviewed_link', 'adjudicated_independent_case', 'conflicting_review')
RELATIONS = ('exact', 'normalized', 'near')
TOKEN = re.compile(r'\w+|[^\w\s]')
IDENTIFIER = re.compile(r'\w*\d\w*')
# shortcut: English negation cues only; extend per language once a reviewed fixture shows a missed negation.
NEGATION = re.compile(r"\b(?:not|no|never|none|without|cannot|nor|neither)\b|n['’]t\b")


def _prepare(ref, text):
    """(ref, fingerprint, tokens, shingles, negations, identifiers) for one exact revision and its text."""
    if not isinstance(ref, RecordRef): raise ValueError('near-duplicate queue needs exact source revisions')
    fingerprint = text_fingerprint(text)
    folded = unicodedata.normalize('NFC', text).casefold()
    tokens = TOKEN.findall(folded)
    if not tokens: raise ValueError('near-duplicate queue needs text with at least one token')
    shingles = frozenset(zip(tokens, tokens[1:], tokens[2:])) or frozenset((tuple(tokens),))
    return ref, fingerprint, shingles, Counter(NEGATION.findall(folded)), frozenset(IDENTIFIER.findall(folded))


def _review(lineage):
    """(record -> reviewed component index, reviewed independent-case pairs) from a lineage query result."""
    if lineage is None: return {}, set()
    component = {RecordRef.parse(m).key: index for index, c in enumerate(lineage['components']) for m in c['members']}
    independent = {frozenset((RecordRef.parse(a['left']).key, RecordRef.parse(a['right']).key)) for a in lineage['independence_assertions']}
    return component, independent


def queue(items, *, lineage=None, threshold_permille=THRESHOLD_PERMILLE):
    """List distinct source records whose text is lexically near-identical, for human review.

    ``items`` is ``[(RecordRef, text)]`` of distinct exact revisions; ``lineage`` an optional ``LineageStore.query``
    result of the same frozen cache. Revisions of one native record are history, not duplicates, and are skipped.
    A pair is flagged when negation cues or identifier-like tokens differ, because overlap alone cannot tell
    ``fixed`` from ``not fixed`` or issue 12 from issue 13.
    """
    if type(threshold_permille) is not int or not 0 < threshold_permille <= 1000: raise ValueError('threshold must be a permille in (0, 1000]')
    rows = [_prepare(ref, text) for ref, text in items]
    if len({row[0].key for row in rows}) != len(rows): raise ValueError('near-duplicate queue needs distinct source revisions')
    component, independent = _review(lineage)
    index, pairs, skipped = defaultdict(list), [], 0
    for position, (ref, fingerprint, shingles, negations, identifiers) in enumerate(rows):
        candidates = {earlier for shingle in shingles for earlier in index[shingle]}
        for earlier in sorted(candidates):
            other, other_fingerprint, other_shingles, other_negations, other_identifiers = rows[earlier]
            score = len(shingles & other_shingles) * 1000 // len(shingles | other_shingles)
            if score < threshold_permille: continue
            if (ref.source_sha256, ref.native_id_sha256) == (other.source_sha256, other.native_id_sha256): skipped += 1; continue
            (left, right), asserted = sorted((ref, other), key=lambda r: r.key), frozenset((ref.key, other.key)) in independent
            linked = component.get(ref.key) is not None and component.get(ref.key) == component.get(other.key)
            status = ('conflicting_review' if linked and asserted else 'covered_by_reviewed_link' if linked
                else 'adjudicated_independent_case' if asserted else 'pending_review')
            relation = ('exact' if fingerprint.exact_sha256 == other_fingerprint.exact_sha256
                else 'normalized' if fingerprint.normalized_sha256 == other_fingerprint.normalized_sha256 else 'near')
            cautions = [name for name, differs in (('negation_differs', negations != other_negations),
                ('identifiers_differ', identifiers != other_identifiers)) if differs]
            pairs.append(dict(left=left.to_dict(), right=right.to_dict(), similarity_permille=score, content_relation=relation,
                cross_source=ref.source_sha256 != other.source_sha256, cautions=cautions, queue_status=status,
                evidence_class='heuristic_candidate'))
        for shingle in shingles: index[shingle].append(position)
    pairs.sort(key=lambda p: (p['left']['source_sha256'], p['left']['native_id_sha256'], p['left']['revision_sha256'],
        p['right']['source_sha256'], p['right']['native_id_sha256'], p['right']['revision_sha256']))
    exposed = {(side['source_sha256'], side['native_id_sha256'], side['revision_sha256']) for p in pairs for side in (p['left'], p['right'])}
    body = dict(schema_version=1, profile=PROFILE, rule=RULE, threshold_permille=threshold_permille, pairs=pairs,
        counts=dict(items=len(rows), candidate_pairs=len(pairs), exposed_items=len(exposed),
            cross_source_pairs=sum(p['cross_source'] for p in pairs), cautioned_pairs=sum(bool(p['cautions']) for p in pairs),
            same_record_revision_pairs_skipped=skipped,
            statuses={s: sum(p['queue_status'] == s for p in pairs) for s in STATUSES},
            relations={r: sum(p['content_relation'] == r for p in pairs) for r in RELATIONS}),
        lineage_grouping_revision_sha256=lineage['grouping_revision_sha256'] if lineage is not None else None,
        text_binding='caller_declared', text_in_report=False)
    return dict(body, queue_sha256=digest(body), independent_roots=None, task_outcomes_verified=False,
        execution_authorized=False, publication_allowed=False, source_read=False, network_used=False)
