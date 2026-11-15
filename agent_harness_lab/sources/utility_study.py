"""Preregistered offline study of cross-source diagnostic utility against a session-only baseline.

The case set, truth labels and verdict rule are frozen under one hash before any arm is scored. A result
describes that fixture only: it is not production precision, agent success or independent-root evidence.
"""
from .link_study import _rate
from .schema import digest

PROFILE = 'cross-source-utility-study-v1'
RULE = 'utility-verdict-v1'
ARMS = ('session_only', 'cross_source')
KINDS = ('join', 'contradiction')
TRUTH = ('positive', 'negative', 'ambiguous')
CASE_KEYS = {'case_id', 'kind', 'truth', 'session_visible', 'sources_needed'}
OBSERVATION_KEYS = {'case_id', 'reported', 'sources_seen', 'records_read'}


def _labels(value, *, empty=False):
    if not isinstance(value, list) or not (value or empty) or not all(isinstance(v, str) and v for v in value) or len(set(value)) != len(value):
        raise ValueError('source labels must be a list of distinct strings, nonempty for sources_needed')
    return value


def register(cases, *, false_finding_ceiling=0.1):
    """Freeze ``[{case_id, kind, truth, session_visible, sources_needed}]`` and the verdict rule under one hash.

    ``truth`` is ``positive`` (a real join or contradiction), ``negative`` or a deliberately ``ambiguous`` control;
    ``session_visible`` says the session alone could show the signal; ``sources_needed`` names the source kinds it needs.
    """
    if not isinstance(false_finding_ceiling, float) or not 0 < false_finding_ceiling < 1: raise ValueError('ceiling must be a float in (0, 1)')
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != CASE_KEYS: raise ValueError('case needs exactly '+', '.join(sorted(CASE_KEYS)))
        if not isinstance(case['case_id'], str) or not case['case_id'] or case['case_id'] in seen: raise ValueError('case ids must be distinct nonempty strings')
        seen.add(case['case_id'])
        if case['kind'] not in KINDS or case['truth'] not in TRUTH or type(case['session_visible']) is not bool: raise ValueError('unsupported kind, truth or visibility')
        _labels(case['sources_needed'])
    body = dict(schema_version=1, profile=PROFILE, cases=sorted(cases, key=lambda c: c['case_id']),
        rule=dict(id=RULE, false_finding_upper_ceiling=false_finding_ceiling, arms=list(ARMS)))
    return dict(body, preregistration_sha256=digest(body))


def _observations(cases, rows):
    """One validated observation per preregistered case: a dropped, extra or repeated case is a rejected study."""
    found = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != OBSERVATION_KEYS: raise ValueError('observation needs exactly '+', '.join(sorted(OBSERVATION_KEYS)))
        if row['reported'] not in ('positive', 'not_positive') or type(row['records_read']) is not int or row['records_read'] < 0:
            raise ValueError('unsupported reported value or records_read')
        _labels(row['sources_seen'], empty=True)
        if row['case_id'] in found: raise ValueError('observation case ids must be distinct')
        found[row['case_id']] = row
    if set(found) != {c['case_id'] for c in cases}: raise ValueError('an arm must observe exactly the preregistered cases')
    return found


def _summary(rows):
    counts = dict(tp=0, fp=0, fn=0, tn=0, ambiguous_promoted=0)
    for case, obs in rows:
        positive = obs['reported'] == 'positive'
        if case['truth'] == 'positive': counts['tp' if positive else 'fn'] += 1
        else:
            counts['fp' if positive else 'tn'] += 1
            counts['ambiguous_promoted'] += positive and case['truth'] == 'ambiguous'
    return dict(cases=len(rows), counts=counts, precision=_rate(counts['tp'], counts['tp']+counts['fp']),
        recall=_rate(counts['tp'], counts['tp']+counts['fn']), false_finding_rate=_rate(counts['fp'], counts['fp']+counts['tn']),
        actionable_findings=counts['tp'], actionable_yield=counts['tp']/len(rows) if rows else None)


def _arm(cases, found):
    rows = [(c, found[c['case_id']]) for c in cases]
    needed = sum(len(c['sources_needed']) for c in cases)
    hit = sum(len(set(o['sources_seen']) & set(c['sources_needed'])) for c, o in rows)
    read, tp = sum(o['records_read'] for _, o in rows), sum(c['truth'] == 'positive' and o['reported'] == 'positive' for c, o in rows)
    return dict(all=_summary(rows), **{kind: _summary([r for r in rows if r[0]['kind'] == kind]) for kind in KINDS},
        needs_cross_source=_summary([r for r in rows if not r[0]['session_visible']]),
        session_visible=_summary([r for r in rows if r[0]['session_visible']]),
        source_coverage=dict(hits=hit, total=needed, value=hit/needed if needed else None),
        overhead=dict(records_read=read, per_case=read/len(rows) if rows else None, per_actionable_finding=read/tp if tp else None))


def _gain(cross, base, part):
    a, b = cross[part]['recall']['value'], base[part]['recall']['value']
    return None if a is None or b is None else round(a-b, 6)


def evaluate(prereg, arms, *, preregistered_sha256):
    """Score both arms on the frozen cases and apply the preregistered verdict rule.

    ``arms`` maps ``session_only`` and ``cross_source`` to ``[{case_id, reported, sources_seen, records_read}]``.
    ``preregistered_sha256`` is the hash recorded at registration, so edited cases or rules are rejected.
    The verdict is ``not_supported`` (a false finding, a regression on session-visible cases or no recall gain on
    cases needing other sources), ``inconclusive`` (no comparable cases, or too few negative controls for the false-finding
    ceiling) or ``supported_on_fixture``.
    """
    body = {k: v for k, v in prereg.items() if k != 'preregistration_sha256'}
    if not (preregistered_sha256 == prereg.get('preregistration_sha256') == digest(body)): raise ValueError('preregistration differs from its recorded hash')
    if body['profile'] != PROFILE or body['rule']['id'] != RULE or not isinstance(arms, dict) or set(arms) != set(ARMS): raise ValueError('unsupported study or arms')
    cases = register(body['cases'], false_finding_ceiling=body['rule']['false_finding_upper_ceiling'])['cases']
    base, cross = (_arm(cases, _observations(cases, arms[name])) for name in ARMS)
    needs, visible = _gain(cross, base, 'needs_cross_source'), _gain(cross, base, 'session_visible')
    upper = cross['all']['false_finding_rate']['wilson95']
    reasons = [r for r, bad in (('false_finding', cross['all']['counts']['fp']), ('session_visible_regression', visible is not None and visible < 0),
        ('no_recall_gain', needs is not None and needs <= 0)) if bad]
    if reasons: verdict = 'not_supported'
    elif needs is None: verdict, reasons = 'inconclusive', ['no_comparable_cases']
    elif upper is None or upper[1] > body['rule']['false_finding_upper_ceiling']: verdict, reasons = 'inconclusive', ['too_few_negative_controls_for_ceiling']
    else: verdict = 'supported_on_fixture'
    return dict(schema_version=1, profile=PROFILE, preregistration_sha256=preregistered_sha256, cases=len(cases), arms=dict(zip(ARMS, (base, cross))),
        incremental=dict(recall_gain_needs_cross_source=needs, recall_gain_session_visible=visible,
            overhead_records_read=cross['overhead']['records_read']-base['overhead']['records_read']),
        verdict=verdict, reasons=reasons, scope='preregistered_fixture_only', diagnostic_utility_claimed=False, agent_success_claimed=False,
        independent_roots=None, publication_allowed=False)
