"""Versioned prediction envelope and comparability check for diagnostic backends on held-out roots (#14); validation role only.

Every backend answer (majority, rules, TF-IDF, embeddings, NLI, a local encoder or a recorded response) is
recorded in one envelope: backend identity, taxonomy, score meaning, fit roles, model inputs, the evidence
window, the outcome (predicted, abstained or failed, never a bare omission) and a resource receipt. The
check scores nothing and ranks nothing: it reports which backends answered which frozen roots, rejects
envelopes that leak held-out material or label a score as a probability without support, and keeps
unmeasured resources, truncation and incomplete results visible. The best status is ``insufficient_evidence``.
"""
import hashlib
import json
import sys

FROZEN_PROFILE = 'diagnostic-backend-frozen-v1'
ENVELOPE_PROFILE = 'diagnostic-prediction-envelope-v1'
REPORT_PROFILE = 'diagnostic-backend-envelope-report-v1'
KINDS = ('majority', 'rules', 'tfidf', 'embeddings', 'nli', 'local_encoder', 'recorded_response')
SCORE_SEMANTICS = ('none', 'rank_score', 'similarity', 'entailment', 'self_reported_confidence',
                   'calibrated_probability')
PROBABILITY_KINDS = ('tfidf', 'embeddings', 'local_encoder')  # similarity, entailment and stated confidence never qualify
SPLITS = ('train', 'calibration', 'development', 'heldout')
STATUSES = ('predicted', 'abstained', 'failed')
PHASES = ('cold', 'warm')
RESOURCES = ('latency_seconds', 'peak_memory_bytes', 'cost')
FORBIDDEN_INPUTS = frozenset(('label', 'labels', 'reward', 'rewards', 'ground_truth', 'outcome', 'grade',
                              'grader_result', 'expected', 'answer'))
LIMITATIONS = ('label_authority_unverified', 'root_independence_unverified', 'frozen_declaration_preregistration_unverified')
_FROZEN = frozenset(('schema_version', 'profile', 'taxonomy_revision', 'classes', 'roots'))
_ENVELOPE = frozenset(('schema_version', 'profile', 'root_case_id', 'split', 'backend', 'fit', 'input', 'outcome', 'resources'))
_SECTIONS = dict(
    backend=frozenset(('kind', 'ref', 'revision', 'preprocessing_sha256', 'taxonomy_revision', 'score_semantics', 'calibration_ref')),
    fit=frozenset(('roles', 'demonstration_roots')), input=frozenset(('fields', 'max_chars', 'truncated')),
    outcome=frozenset(('status', 'label', 'score', 'reason')), resources=frozenset(('phase',) + RESOURCES))
_ID_CHARS, _ROOT_CHARS, _LIST = 128, 256, 4096


def _ident(value, limit=_ID_CHARS):
    return isinstance(value, str) and 0 < len(value) <= limit


def _list(value, check, limit=_LIST):
    return isinstance(value, list) and len(value) <= limit and all(check(v) for v in value)


def _number(value):
    return type(value) in (int, float) and -sys.float_info.max <= value <= sys.float_info.max  # false for nan, inf


def _amount(value):
    return value is None or _number(value) and value >= 0  # None is an unmeasured resource, never zero


def _sha256(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def check_frozen(frozen):
    if (not isinstance(frozen, dict) or set(frozen) != _FROZEN or type(frozen['schema_version']) is not int
            or frozen['schema_version'] != 1 or frozen['profile'] != FROZEN_PROFILE):
        raise ValueError('invalid frozen backend declaration schema')
    classes, roots = frozen['classes'], frozen['roots']
    if not _ident(frozen['taxonomy_revision']):
        raise ValueError('frozen declaration requires a taxonomy revision')
    if not _list(classes, _ident) or len(classes) < 2 or len(set(classes)) != len(classes):
        raise ValueError('frozen declaration requires at least two unique classes')
    if not _list(roots, lambda v: _ident(v, _ROOT_CHARS), 10 * _LIST) or not roots or len(set(roots)) != len(roots):
        raise ValueError('frozen declaration requires unique eligible roots')


def _check(record, frozen):
    """Validate one envelope's shape; leakage and unsupported claims are findings, malformed input is refused."""
    if (not isinstance(record, dict) or set(record) != _ENVELOPE or type(record['schema_version']) is not int
            or record['schema_version'] != 1 or record['profile'] != ENVELOPE_PROFILE or record['split'] != 'heldout'):
        raise ValueError('invalid prediction envelope schema or non-heldout row')
    if record['root_case_id'] not in frozen['roots']:
        raise ValueError('envelope root is not one of the frozen eligible roots')
    for name, fields in _SECTIONS.items():
        if not isinstance(record[name], dict) or set(record[name]) != fields:
            raise ValueError(f'envelope {name} must state exactly {sorted(fields)}; omission is not unknown-safe')
    backend, fit, window, outcome, resources = (record[name] for name in _SECTIONS)
    if (backend['kind'] not in KINDS or backend['score_semantics'] not in SCORE_SEMANTICS
            or not all(_ident(backend[k]) for k in ('ref', 'revision')) or not _sha256(backend['preprocessing_sha256'])
            or backend['taxonomy_revision'] != frozen['taxonomy_revision']
            or not (backend['calibration_ref'] is None or _ident(backend['calibration_ref']))):
        raise ValueError('invalid or foreign backend identity')
    if (not _list(fit['roles'], lambda v: v in SPLITS) or not _list(fit['demonstration_roots'], lambda v: _ident(v, _ROOT_CHARS))):
        raise ValueError('fit roles must be declared splits and demonstrations declared roots')
    if (not _list(window['fields'], _ident, 64) or not window['fields'] or type(window['max_chars']) is not int
            or window['max_chars'] < 1 or window['truncated'] not in (True, False, None)):
        raise ValueError('invalid model input declaration or evidence window')
    status, label, reason = outcome['status'], outcome['label'], outcome['reason']
    if (status not in STATUSES or not (outcome['score'] is None or _number(outcome['score']))
            or (status == 'predicted') != (label in frozen['classes'])
            or (status == 'predicted') == _ident(reason) or not (reason is None or _ident(reason))
            or (status != 'predicted' and outcome['score'] is not None)):
        raise ValueError('outcome must be a declared-class prediction, or an abstention or failure with a reason')
    if resources['phase'] not in PHASES or not all(_amount(resources[k]) for k in RESOURCES):
        raise ValueError('invalid resource receipt')
    return record


def _identity(record):
    backend, fit, window = record['backend'], record['fit'], record['input']
    parts = dict(backend, roles=sorted(fit['roles']), demonstrations=sorted(fit['demonstration_roots']),
                 fields=sorted(window['fields']), max_chars=window['max_chars'])
    return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _receipt(rows):
    phases = {}
    for phase in PHASES:
        mine = [r['resources'] for r in rows if r['resources']['phase'] == phase]
        if not mine:
            continue
        unmeasured = [k for k in RESOURCES if any(m[k] is None for m in mine)]
        values = {k: None if k in unmeasured else (max if k == 'peak_memory_bytes' else sum)(m[k] for m in mine)
                  for k in RESOURCES}
        phases[phase] = dict(records=len(mine), **values, unmeasured=unmeasured)
    return phases


def evaluate(frozen, records):
    """Describe each backend's recorded answers over the frozen roots; nothing is scored or selected.

    ``frozen`` declares the taxonomy revision, classes and eligible held-out roots before any comparison.
    Each record is one envelope (see ``ENVELOPE_PROFILE``) for one backend and one root; a repeated
    backend and root, a root outside the frozen set or a foreign taxonomy revision is refused.
    """
    check_frozen(frozen)
    roots = frozen['roots']
    evaluated = set(roots)
    by_backend, seen = {}, set()
    for record in records:
        _check(record, frozen)
        key = (record['backend']['ref'], record['root_case_id'])
        if key in seen:
            raise ValueError('one envelope per backend and root')
        seen.add(key)
        by_backend.setdefault(key[0], []).append(record)
    if not seen:
        raise ValueError('empty backend envelope input')
    found, gaps = set(), set()
    report, answered = {}, []
    for ref, rows in sorted(by_backend.items()):
        identities = {_identity(r) for r in rows}
        counts = {s: sum(r['outcome']['status'] == s for r in rows) for s in STATUSES}
        truncated = dict(yes=0, unknown=0, no=0)
        for r in rows:
            truncated[{True: 'yes', None: 'unknown', False: 'no'}[r['input']['truncated']]] += 1
        for r in rows:
            fit, window, backend = r['fit'], r['input'], r['backend']
            for flag, hit in (('heldout_role_used_in_fit', 'heldout' in fit['roles']),
                              ('demonstration_root_is_evaluated', bool(set(fit['demonstration_roots']) & evaluated)),
                              ('label_or_reward_in_model_input', any(f.strip().lower() in FORBIDDEN_INPUTS for f in window['fields'])),
                              ('unsupported_probability_claim', backend['score_semantics'] == 'calibrated_probability'
                               and (backend['kind'] not in PROBABILITY_KINDS or backend['calibration_ref'] is None))):
                if hit:
                    found.add(flag)
            if backend['score_semantics'] == 'calibrated_probability':
                gaps.add('calibration_unverified')
        if len(identities) > 1:
            found.add('backend_identity_changed')
        coverage = counts['predicted'] / len(roots)
        gaps.update(name for name, hit in (('incomplete_backend_results', len(rows) < len(roots)),
                                           ('zero_coverage_backend', not counts['predicted']),
                                           ('truncation_unknown', truncated['unknown'] > 0)) if hit)
        answered.append({r['root_case_id'] for r in rows})
        report[ref] = dict(kind=rows[0]['backend']['kind'], identity_sha256=identities.pop() if len(identities) == 1 else None,
                           score_semantics=rows[0]['backend']['score_semantics'], records=len(rows), **counts,
                           missing_roots=len(roots) - len(rows), coverage=coverage, truncated=truncated,
                           resources=_receipt(rows))
    inputs = {(tuple(sorted(r['input']['fields'])), r['input']['max_chars']) for rows in by_backend.values() for r in rows}
    if len(inputs) > 1:
        gaps.add('evidence_windows_differ')
    return dict(
        schema_version=1, profile=REPORT_PROFILE, role='validate',
        status='rejected' if found else 'insufficient_evidence', reasons=sorted(found) + sorted(gaps) + list(LIMITATIONS),
        frozen_sha256=hashlib.sha256(json.dumps(frozen, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        taxonomy_revision=frozen['taxonomy_revision'], unit='declared_root', roots=len(roots),
        comparable_roots=len(set.intersection(*answered)), backends=report,
        quality='not_computed', selection='not_issued', beneficial_claim='not_issued', qualification='descriptive_only',
        certification='not_issued', automatic_promotion=False, promotion_authority=False, publication_allowed=False,
        label_authority='unverified', root_independence='unverified')
