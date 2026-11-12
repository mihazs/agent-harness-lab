"""ahl memory: manage an opt-in private evidence store. Output is always JSON on stdout."""
import argparse
import json
import os
import sys

from .store import IndexUnavailable, MemoryStore, MemoryStoreError, ReadOnlyStore, StoreBusy


def main(argv):
    parser = argparse.ArgumentParser(prog='ahl memory', description='Manage an opt-in, private, local evidence store: '
        'immutable hash-addressed objects plus a derived, rebuildable SQLite index. No daemon, no network, no model call. '
        'The store holds only what later slices publish into it; it never reads or modifies an existing HistoryStore.',
        epilog='Exit: 0 completed; 2 invalid input or refused path; 3 blocked, read-only, busy, or verification found problems.')
    parser.add_argument('command', choices=('init', 'status', 'verify', 'reindex', 'gc'),
                        help='init creates a new store; status is cheap; verify re-hashes everything; reindex rebuilds the index from manifests; gc plans (and with --apply removes) unreferenced objects')
    parser.add_argument('--store', default=os.environ.get('AHL_MEMORY_STORE'),
                        help='Absolute store directory outside any checkout (default: $AHL_MEMORY_STORE); no default location exists')
    parser.add_argument('--exclude', action='append', default=[],
                        help='Directory the store must not overlap (project, bank, source, workspace, image, credential or protected store); repeatable')
    parser.add_argument('--dry-run', action='store_true', help='reindex: report what would be rebuilt without changing anything')
    parser.add_argument('--apply', action='store_true', help='gc: remove the planned objects (default is a plan only)')
    args = parser.parse_args(argv)
    if not args.store:
        parser.exit(2, 'ahl memory: --store (or AHL_MEMORY_STORE) is required; there is no default location\n')
    if args.dry_run and args.command != 'reindex' or args.apply and args.command != 'gc':
        parser.exit(2, 'ahl memory: --dry-run applies to reindex and --apply to gc only\n')
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
