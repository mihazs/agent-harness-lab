"""Explicit offline source operations; installing AHL enables no collectors."""
import argparse
import json
import sys
from .registry import Capability, Registry
from .schema import PLANES
from .snapshot import LocalExport
from .store import SourceStore


def main(argv):
    parser = argparse.ArgumentParser(prog='ahl sources')
    parser.add_argument('operation', nargs='?', default='probe', choices=('probe', 'preview', 'import', 'status'))
    parser.add_argument('--manifest')
    parser.add_argument('--project-sha256')
    parser.add_argument('--scope-sha256')
    parser.add_argument('--cache')
    args = parser.parse_args(argv)
    try:
        if args.manifest is None:
            if args.operation != 'probe' or any((args.project_sha256, args.scope_sha256, args.cache)):
                raise ValueError()
            result = dict(schema_version=1, enabled_sources=0, source_read=False, network_used=False)
        else:
            scope = dict(project_sha256=args.project_sha256, scope_sha256=args.scope_sha256)
            connector = LocalExport(args.manifest)
            source, _ = connector.declaration(**scope)
            registry = Registry()
            registry.register(Capability('local_file', 'ahl-metadata', 'v1', PLANES), connector)
            result = registry.probe(source, **scope)
            if result['status'] != 'recognized':
                print(json.dumps(result)); return 3
            if args.operation == 'preview':
                snapshot = registry.preview(source, **scope)
                result.update(snapshot_sha256=snapshot.sha256, records=len(snapshot.records), source_read=True,
                              capture_completeness='unknown')
            elif args.operation in ('import', 'status'):
                if not args.cache:
                    raise ValueError()
                store = SourceStore(args.cache, **scope)
                result = (registry.ingest(source, store, **scope).to_dict() if args.operation == 'import'
                          else registry.status(source, store, **scope))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (NotImplementedError, PermissionError):
        print('source operation blocked by unavailable filesystem capabilities/access', file=sys.stderr)
        return 3
    except (ValueError, OSError, TypeError, KeyError, RecursionError):
        print('source operation rejected; inspect scope, declaration and metadata; no automatic retry', file=sys.stderr)
        return 2
