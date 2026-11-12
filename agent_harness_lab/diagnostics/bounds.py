"""Size and time bounds for the private analysis artifact and event store.

Analysis always runs over every event; only the detail written to disk is capped. Whatever is cut is
described by counts alone (what, total, kept) and never receives a success or independence label.
"""
import json
import time
from collections import Counter

PROFILE = 'ahl-artifact-bounds-v1'
# Documented in docs/analysis.md; tests pass smaller values through args._artifact_limits.
DEFAULTS = dict(analysis_bytes=64 << 20, detail_rows=1000, evidence_refs=50, store_bytes=64 << 20, store_seconds=300)
NOTE = 'Counts only: omitted rows carry no success, failure or independence label; unknown stays unknown.'


def limits(args):
    return dict(DEFAULTS, **getattr(args, '_artifact_limits', {}))


class _Counter:
    size = 0

    def write(self, text):
        self.size += len(text)  # json.dump emits ASCII, so characters are bytes
        return len(text)


def size(value):
    """Rendered byte size of a value as the private writer emits it, without holding the text."""
    sink = _Counter()
    json.dump(value, sink, indent=2, allow_nan=False)
    return sink.size + 1


def _refs(row, refs, cut, name):
    evidence = row.get('evidence')
    if isinstance(evidence, list) and len(evidence) > refs:
        cut[name + '.evidence_refs'] = cut.get(name + '.evidence_refs', 0) + len(evidence) - refs
        return dict(row, evidence=evidence[:refs], evidence_total=len(evidence))
    return row


def _list(rows, limit, refs, name, cut):
    kept = [_refs(row, refs, cut, name) for row in rows[:limit]]
    if len(rows) > limit:
        cut[name] = dict(total=len(rows), kept=limit)
    return kept


def trim(analysis, rows, refs):
    """Return (analysis view, cut) with each detail list capped; the input is never mutated."""
    cut, view = {}, dict(analysis)
    seen, findings = Counter(), []
    for finding in analysis.get('findings', []):
        kind = finding.get('kind', 'unknown') if isinstance(finding, dict) else 'unknown'
        seen[kind] += 1
        if seen[kind] <= rows:
            findings.append(_refs(finding, refs, cut, 'findings.' + kind) if isinstance(finding, dict) else finding)
    for kind, total in seen.items():
        if total > rows:
            cut['findings.' + kind] = dict(total=total, kept=rows)
    view['findings'] = findings
    view['recurring_sequences'] = _list(analysis.get('recurring_sequences', []), rows, refs, 'recurring_sequences', cut)
    if isinstance(analysis.get('process_mining'), dict):
        mining = dict(analysis['process_mining'])
        for name in ('raw_edges', 'projected_run_collapsed_edges'):
            mining[name] = _list(mining.get(name, []), rows, refs, 'process_mining.' + name, cut)
        view['process_mining'] = mining
    if cut:  # report.md prints these instead of the kept lengths
        view['detail_totals'] = dict(findings=len(analysis.get('findings', [])), recurring_sequences=len(analysis.get('recurring_sequences', [])),
                                     raw_edges=len(analysis.get('process_mining', {}).get('raw_edges', [])),
                                     projected_run_collapsed_edges=len(analysis.get('process_mining', {}).get('projected_run_collapsed_edges', [])))
    return view, cut


def analysis_view(data, args, store=None):
    """The analysis.json content: every section whole under --full-detail, otherwise capped to the byte bound."""
    cap = limits(args)
    full = bool(getattr(args, 'full_detail', False))
    rows, cut = cap['detail_rows'], {}
    while True:
        view, cut = (data['analysis'], {}) if full else trim(data['analysis'], rows, cap['evidence_refs'])
        provenance = dict(profile=PROFILE, full_detail=full, note=NOTE, byte_limit=cap['analysis_bytes'],
                          analysis_computed_from='all_events', row_limit=None if full else rows,
                          truncated=dict(applied=bool(cut), sections=cut), event_store=store)
        out = dict(data, analysis=view, artifact_bounds=provenance)
        if full or rows == 0 or size(out) <= cap['analysis_bytes']:
            break
        rows //= 2
    if not full and size(out) > cap['analysis_bytes']:
        provenance['exceeds_byte_limit'] = True  # non-list sections alone are over the bound; reported, not hidden
    return out


def store_rows(lines, args, report):
    """Pass event lines through until a store bound is reached; later lines are counted, never written.

    `report` receives the provenance after the stream is exhausted (counts only).
    """
    cap = limits(args)
    full = bool(getattr(args, 'full_detail', False))
    started, written, omitted, used, reason = time.monotonic(), 0, 0, 0, None
    for line in lines:
        if not full and reason is None:
            used += len(line.encode()) + 1
            if used > cap['store_bytes']:
                reason = 'byte_limit'
            elif time.monotonic() - started > cap['store_seconds']:
                reason = 'time_limit'
        if reason is None:
            written += 1
            yield json.loads(line)
        else:
            omitted += 1
    report.update(profile=PROFILE, full_detail=full, byte_limit=cap['store_bytes'], seconds_limit=cap['store_seconds'],
                  events_written=written, truncated=dict(applied=reason is not None, reason=reason, events_omitted=omitted))
