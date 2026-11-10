"""Private outcome correlations over reviewed, exact source revisions."""
from .execution import RecordRef


def correlate(state, links):
    records = {(r['record']['source_sha256'], r['record']['native_id_sha256'], r['record']['revision_sha256']): r['record']
               for r in state['records']}
    superseded = {tuple(edge[:3]) for edge in state['supersessions']}
    leaves = {}
    for key in records:
        if key not in superseded: leaves.setdefault(key[:2], []).append(key)
    ambiguous = [RecordRef(*key).to_dict() for key, value in sorted(records.items())
        if value.get('outcome') is not None and value['state'] == 'present' and key not in superseded and len(leaves[key[:2]]) != 1]
    claims = {key: value for key, value in records.items() if value.get('outcome') is not None
              and value['state'] == 'present' and key not in superseded and len(leaves[key[:2]]) == 1}
    connected = {}
    for link in links:
        if link['relation'] != 'same_execution': continue
        left, right = RecordRef.parse(link['left']).key, RecordRef.parse(link['right']).key
        if left in claims and right in claims and claims[left]['outcome']['kind'] != claims[right]['outcome']['kind']:
            reported, observed = (left, right) if claims[left]['outcome']['kind'] == 'reported' else (right, left)
            connected.setdefault(reported, {}).setdefault(observed, set()).add(link['case_id'])
    rows, used = [], set()
    def public(key):
        return dict(source=RecordRef(*key).to_dict(), **claims[key]['outcome'])
    for key in sorted(claims):
        if claims[key]['outcome']['kind'] != 'reported': continue
        pair = sorted(connected.get(key, {}).items())
        observed_values = {claims[other]['outcome']['value'] for other, _ in pair if claims[other]['outcome']['value'] != 'unknown'}
        outcome = next(iter(observed_values)) if len(observed_values) == 1 else 'unverified'
        report = claims[key]['outcome']['value']
        contradictory = len(observed_values) > 1 or bool(observed_values and report != 'unknown' and report not in observed_values)
        rows.append(dict(reported_outcome=public(key), observed_outcome=[public(other) for other, _ in pair],
            outcome=outcome, contradiction=contradictory, reviewed_case_ids=sorted({case for _, cases in pair for case in cases})))
        used.update(other for other, _ in pair)
    for key in sorted(claims):
        if claims[key]['outcome']['kind'] == 'observed' and key not in used:
            value = claims[key]['outcome']['value']
            rows.append(dict(reported_outcome=None, observed_outcome=[public(key)],
                outcome=value if value != 'unknown' else 'unverified', contradiction=False, reviewed_case_ids=[]))
    return dict(profile='cross-source-outcomes-v1', records=rows, ambiguous_sources=ambiguous, task_outcomes_verified=False,
        native_freshness_verified=False, execution_authorized=False, publication_allowed=False)
