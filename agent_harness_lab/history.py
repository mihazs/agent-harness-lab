"""Read-only historical-session evidence request for improve; it reuses the existing collection and adds no adapter."""
from datetime import datetime, timezone
import re


def prepare(args):
    """Validate the request and bind its lower bound; never reads a session store."""
    requested = args.since is not None or args.history_source is not None
    if requested and not args.history:
        raise ValueError('--since and --source require --history')
    args._history_since_ns = None
    if not args.history:
        return
    if args.events is not None or args.source_root is not None:
        raise ValueError('--history selects the project session store; drop --events/--source-root, or drop --history')
    if args.session is not None and not re.fullmatch(r'[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', args.session):
        raise ValueError('--session must be an exact native Codex thread or Claude session UUID')
    if args.since not in (None, 'first'):
        try:
            moment = datetime.fromisoformat(args.since)
        except ValueError:
            raise ValueError("--since must be 'first' or an ISO 8601 date such as 2025-01-31") from None
        # shortcut: a date without an offset is read as UTC; add a timezone option if that proves ambiguous.
        moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
        args._history_since_ns = int(moment.timestamp() * 1_000_000_000)


PIN_CHECK = 'ahl sources claude pin-check --events <newest-capture.jsonl>'


def runtime_lag(sources):
    """Once, when captures were recorded by a Claude Code release newer than the newest fixture-verified one; counts only."""
    from .telemetry.claude_native import NEWEST_VERIFIED, release
    newer = {}
    for row in sources:
        if row.get('format') == 'claude':
            for version in row.get('recorded_versions', ()):
                if release(version) is not None and release(version) > release(NEWEST_VERIFIED): newer[version] = newer.get(version, 0) + 1
    if not newer: return None
    return dict(status='recorded_runtime_newer_than_newest_verified', newest_verified=NEWEST_VERIFIED,
        newest_recorded=max(newer, key=release), captures_by_version=dict(sorted(newer.items(), key=lambda item: release(item[0]))),
        next_step='Run `%s` to compare its structure with the verified contract; when it reports compatible, add that release\'s contract fixture '
            'and extend VERIFIED_RANGES. Until then these captures are read by the descriptive, unverified adapter.' % PIN_CHECK)


def summary(args, data):
    from .telemetry.claude_cwd import from_sources
    """What was captured and what stays unknown; aggregate counts only, no paths or bodies."""
    analysis, lag = data['analysis'], runtime_lag(data['sources'])
    return dict(profile='ahl-history-evidence-v1', scope='project', source=args.history_source or 'native',
        since=args.since or 'first', since_basis='capture file modification time; unbounded for first',
        read_only=True, sessions_captured=sum(1 for row in data['sources'] if row.get('format') in ('codex', 'claude')),
        session_selected=args.session is not None, events=analysis['events'], cases=analysis['cases'],
        capture_reasons=data.get('capture_reasons', []), cwd_drift=from_sources(data['sources']), pin_compatibility={kind: sum(1 for row in data['sources'] if row.get('pin_compatibility') == kind) for kind in ('declared_range', 'unverified')},
        **({} if lag is None else dict(runtime_lag=lag)),
        unknown=['capture completeness', 'recurrence outside the captured sessions', 'sessions outside project scope or before --since'])
