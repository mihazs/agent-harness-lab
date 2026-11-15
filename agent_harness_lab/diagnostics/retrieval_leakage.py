"""Retrieval-side leakage check for held-out probes against a local lexical index (#14); validation role only.

A held-out probe must not find its own root, or a copy of its own text, in the index it retrieves from.
Declared roots are compared exactly and summaries by the corpus word-3-gram near-duplicate rule, so a
paraphrase, an undeclared project and a shared time window are not detected. A probe whose root is
unknown (``None``) is counted and can never clear the index. The best status is ``insufficient_evidence``:
no result here establishes root independence, label authority or a retrieval quality claim.
Reports carry root hashes and incident IDs only, never raw roots, probe text or summaries.
"""
import hashlib

from agent_harness_lab.diagnostics.corpus import NEAR_PERMILLE, NEAR_RULE, _jaccard, _shingles
from agent_harness_lab.diagnostics.retrieval import LexicalIndex

REPORT_PROFILE = 'diagnostic-retrieval-leakage-v1'
_ROOT_CHARS = 256
LIMITATIONS = ('root_independence_unverified', 'paraphrase_leakage_not_detected',
               'project_and_time_lineage_not_checked')


def _sha256(value):
    return None if value is None else hashlib.sha256(value.encode()).hexdigest()


def evaluate(index, probes, top_k=None):
    """Report held-out probes whose own root or copied text sits in ``index``.

    Each probe row has ``root_case_id`` (a declared root, or ``None`` when unknown; the key is required),
    ``split: heldout`` and a nonempty ``text`` (the query). ``top_k`` is the retrieval depth used to mark
    which leaked incidents the probe would actually retrieve; ``None`` means every lexical match.
    Any leaked incident rejects the index, retrieved or not, because the index holds held-out material.
    """
    if not isinstance(index, LexicalIndex) or (top_k is not None and (type(top_k) is not int or top_k < 1)):
        raise ValueError('a LexicalIndex and a positive explicit retrieval depth are required')
    findings, probe_roots, count, unknown = [], set(), 0, 0
    for row in probes:
        if not isinstance(row, dict) or row.get('split') != 'heldout' or 'root_case_id' not in row:
            raise ValueError('retrieval leakage check requires heldout rows with a declared root key')
        root, text = row['root_case_id'], row.get('text')
        if root is not None and (not isinstance(root, str) or not root or len(root) > _ROOT_CHARS):
            raise ValueError('probe root must be a nonempty declared root or None for unknown')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('probe text must be a nonempty string')
        count += 1
        unknown += root is None
        probe_roots.add(root)
        retrieved = {hit['incident_id'] for hit in index.query(text, top_k)['results']}
        shingles = _shingles(text)
        for incident in sorted(index.incidents, key=lambda i: i.incident_id):
            similarity = _jaccard(shingles, _shingles(incident.summary))
            reasons = (['same_root'] if root is not None and incident.root_id == root else []) \
                + (['near_duplicate_text'] if similarity >= NEAR_PERMILLE else [])
            if reasons:
                findings.append(dict(probe_root_sha256=_sha256(root), incident_id=incident.incident_id, reasons=reasons,
                                     similarity_permille=similarity, retrieved=incident.incident_id in retrieved))
    if not count:
        raise ValueError('empty retrieval leakage input')
    leaks = dict(same_root=sum('same_root' in f['reasons'] for f in findings),
                 near_duplicate_text=sum('near_duplicate_text' in f['reasons'] for f in findings),
                 retrieved=sum(f['retrieved'] for f in findings))
    reasons = [name for flag, name in ((leaks['same_root'], 'same_root_incident_in_index'),
                                       (leaks['near_duplicate_text'], 'near_duplicate_text_in_index'),
                                       (unknown, 'unknown_root_probes'),
                                       (not index.incidents, 'empty_index')) if flag]
    return dict(
        schema_version=1, profile=REPORT_PROFILE, role='validate',
        status='rejected' if leaks['same_root'] or leaks['near_duplicate_text'] else 'insufficient_evidence',
        reasons=reasons + list(LIMITATIONS), index_revision=index.revision, index_records=len(index.incidents),
        index_roots=len({i.root_id for i in index.incidents}), probes=count, probe_roots=len(probe_roots - {None}),
        unknown_root_probes=unknown, top_k=top_k, near_duplicate_rule=NEAR_RULE, near_duplicate_permille=NEAR_PERMILLE,
        leaks=leaks, findings=findings, private=any(i.source_kind == 'private' for i in index.incidents),
        root_independence='unverified', promotion_authority=False, publication_allowed=False)
