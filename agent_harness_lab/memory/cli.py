"""ahl memory: manage an opt-in private evidence store. Output is always JSON on stdout."""
import argparse
import json
import os
import sys

from .history_index import RoleDenied, list_histories
from .store import IndexUnavailable, MemoryStore, MemoryStoreError, ReadOnlyStore, StoreBusy


def main(argv):
    parser = argparse.ArgumentParser(prog='ahl memory', description='Manage an opt-in, private, local evidence store: '
        'immutable hash-addressed objects plus a derived, rebuildable SQLite index. No daemon, no network, no model call. '
        'The store holds only what later slices publish into it; it never modifies an existing HistoryStore (history only reads selected ones).',
        epilog='Exit: 0 completed; 2 invalid input or refused path; 3 blocked, read-only, busy, or verification found problems.')
    parser.add_argument('command', choices=('init', 'status', 'verify', 'reindex', 'gc', 'history'),
                        help='init creates a new store; status is cheap; verify re-hashes everything; reindex rebuilds the index from manifests; gc plans (and with --apply removes) unreferenced objects; history lists existing evaluation histories read-only, needs --history-store and no memory store')
    parser.add_argument('--store', default=os.environ.get('AHL_MEMORY_STORE'),
                        help='Absolute store directory outside any checkout (default: $AHL_MEMORY_STORE); no default location exists')
    parser.add_argument('--exclude', action='append', default=[],
                        help='Directory the store must not overlap (project, bank, source, workspace, image, credential or protected store); repeatable')
    parser.add_argument('--history-store', action='append', default=[],
                        help='history: absolute existing HistoryStore to list, never scanned for or modified; repeatable')
    parser.add_argument('--dry-run', action='store_true', help='reindex: report what would be rebuilt without changing anything; gc: plan only (the default)')
    parser.add_argument('--apply', action='store_true', help='gc: remove the planned objects (default is a plan only)')
    parser.add_argument('--json', action='store_true', help='Accepted for uniformity with other commands; output is always JSON')
    args = parser.parse_args(argv)
    if args.command == 'history':
        if not args.history_store or args.dry_run or args.apply:
            parser.exit(2, 'ahl memory: history needs at least one --history-store and takes no other mode flag\n')
        try:
            result = list_histories(args.history_store)
        except RoleDenied as error:
            print(f'ahl memory: blocked: {error}', file=sys.stderr)
            return 3
        except BlockingIOError:
            print('ahl memory: blocked: a history store is busy', file=sys.stderr)
            return 3
        except (ValueError, OSError) as error:
            parser.exit(2, f'ahl memory: {error}\n')
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return 0
    if args.history_store:
        parser.exit(2, 'ahl memory: --history-store applies to history only\n')
    if not args.store:
        parser.exit(2, 'ahl memory: --store (or AHL_MEMORY_STORE) is required; there is no default location\n')
    if args.dry_run and args.command not in ('reindex', 'gc') or args.apply and args.command != 'gc' or args.dry_run and args.apply:
        parser.exit(2, 'ahl memory: --dry-run applies to reindex and gc, --apply to gc only, and not together\n')
    try:
        store = (MemoryStore.init if args.command == 'init' else MemoryStore.open)(args.store, excluded=args.exclude)
        if args.command == 'verify':
            result = store.verify()
            ok = result['ok']
        else:
            result = {'init': store.status, 'status': store.status, 'gc': lambda: store.gc(apply=args.apply),
                      'reindex': lambda: store.reindex(dry_run=args.dry_run)}[args.command]()
            ok = True
    except (ReadOnlyStore, StoreBusy, IndexUnavailable) as error:
        print(f'ahl memory: blocked: {error}', file=sys.stderr)
        return 3
    except (MemoryStoreError, OSError) as error:
        parser.exit(2, f'ahl memory: {error}\n')
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if ok else 3
