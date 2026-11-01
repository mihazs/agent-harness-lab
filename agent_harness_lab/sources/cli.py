"""Explicit offline source operations; installing AHL enables no collectors."""
import argparse
import json
import sys
import subprocess
from .registry import Capability, Registry
from .schema import PLANES
from .snapshot import LocalExport
from .okf import MissingOKFDependency, OKFExport
from .store import SourceStore
from .git import GitExport
from .execution import capture_snapshot


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
            if source.provider == 'ai-memory-okf':
                connector = OKFExport(args.manifest)
            elif source.provider == 'git':
                connector = GitExport(args.manifest)
            registry = Registry()
            registry.register(Capability('local_file', 'ahl-metadata', 'v1', PLANES), connector)
            registry.register(Capability('local_file', 'ahl-metadata', 'v2', PLANES), connector)
            registry.register(Capability('local_file', 'ai-memory-okf', '0.2', ('contextual_assertion',)), connector)
            registry.register(Capability('local_git', 'git', 'v1', ('development_artifact',)), connector)
            result = registry.probe(source, **scope)
            if result['status'] != 'recognized':
                print(json.dumps(result)); return 3
            if args.operation == 'preview':
                snapshot = registry.preview(source, **scope)
                result.update(snapshot_sha256=snapshot.sha256, records=len(snapshot.records), source_read=True,
                              capture_completeness='unknown')
                if snapshot.metadata is not None:
                    result.update(execution_metadata=snapshot.metadata.summary(),
                        checkpoint_snapshot_sha256=capture_snapshot(source, snapshot.sha256, snapshot.metadata))
                if isinstance(connector, OKFExport):
                    result['context_summary'] = connector.summary
                elif isinstance(connector, GitExport):
                    result['git_summary'] = connector.summary
            elif args.operation in ('import', 'status'):
                if not args.cache:
                    raise ValueError()
                store = SourceStore(args.cache, **scope)
                result = (registry.ingest(source, store, **scope).to_dict() if args.operation == 'import'
                          else registry.status(source, store, **scope))
        print(json.dumps(result, sort_keys=True))
        return 0
    except MissingOKFDependency:
        print('OKF parser unavailable; install agent-harness-lab[okf] explicitly', file=sys.stderr)
        return 3
    except (NotImplementedError, PermissionError):
        print('source operation blocked by unavailable filesystem capabilities/access', file=sys.stderr)
        return 3
    except (ValueError, OSError, TypeError, KeyError, RecursionError, subprocess.SubprocessError):
        print('source operation rejected; inspect scope, declaration and metadata; no automatic retry', file=sys.stderr)
        return 2
