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
    if args.session is not None and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', args.session):
        raise ValueError('--session must be an exact native Codex thread ID or Claude session UUID')
    if args.since not in (None, 'first'):
        try:
            moment = datetime.fromisoformat(args.since)
        except ValueError:
            raise ValueError("--since must be 'first' or an ISO 8601 date such as 2025-01-31") from None
        # shortcut: a date without an offset is read as UTC; add a timezone option if that proves ambiguous.
        moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
        args._history_since_ns = int(moment.timestamp() * 1_000_000_000)


def summary(args, data):
    """What was captured and what stays unknown; aggregate counts only, no paths or bodies."""
    analysis = data['analysis']
    return dict(profile='ahl-history-evidence-v1', scope='project', source=args.history_source or 'native',
        since=args.since or 'first', since_basis='capture file modification time; unbounded for first',
        read_only=True, sessions_captured=sum(1 for row in data['sources'] if row.get('format') in ('codex', 'claude')),
        session_selected=args.session is not None, events=analysis['events'], cases=analysis['cases'],
        capture_reasons=data.get('capture_reasons', []), pin_compatibility=dict(unverified=sum(1 for row in data['sources'] if row.get('pin_compatibility') == 'unverified')),
        unknown=['capture completeness', 'recurrence outside the captured sessions', 'sessions outside project scope or before --since'])
