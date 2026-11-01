"""Explicit source operations; probes and installation enable no collectors."""
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
from .github_config import GitHubConfigured
from .codex import CodexCapture, SOURCE_VERSION as CODEX_VERSION
from agent_harness_lab.config import read_json


def main(argv):
    parser = argparse.ArgumentParser(prog='ahl sources')
    parser.add_argument('operation', nargs='?', default='probe', choices=('probe', 'preview', 'import', 'status', 'recheck'))
    parser.add_argument('--manifest')
    parser.add_argument('--project-sha256')
    parser.add_argument('--scope-sha256')
    parser.add_argument('--cache')
    parser.add_argument('--authorization-file')
    args = parser.parse_args(argv)
    try:
        if args.manifest is None:
            if args.operation != 'probe' or any((args.project_sha256, args.scope_sha256, args.cache, args.authorization_file)):
                raise ValueError()
            result = dict(schema_version=1, enabled_sources=0, source_read=False, network_used=False)
        else:
            scope = dict(project_sha256=args.project_sha256, scope_sha256=args.scope_sha256)
            declaration = read_json(args.manifest)
            remote = isinstance(declaration, dict) and isinstance(declaration.get('source'), dict) and declaration['source'].get('kind') == 'api'
            if args.authorization_file is not None and not remote: raise ValueError('authorization file requires an API configuration')
            connector = (GitHubConfigured(args.manifest, authorization_file=args.authorization_file) if remote else LocalExport(args.manifest))
            source, _ = connector.declaration(**scope)
            if source.provider == 'ai-memory-okf':
                connector = OKFExport(args.manifest)
            elif source.provider == 'git':
                connector = GitExport(args.manifest)
            elif source.provider == 'codex-rollout':
                connector = CodexCapture(args.manifest)
            registry = Registry()
            registry.register(Capability('local_file', 'ahl-metadata', 'v1', PLANES), connector)
            registry.register(Capability('local_file', 'ahl-metadata', 'v2', PLANES), connector)
            registry.register(Capability('local_file', 'ahl-metadata', 'v3', PLANES), connector)
            registry.register(Capability('local_file', 'ai-memory-okf', '0.2', ('contextual_assertion',)), connector)
            registry.register(Capability('local_git', 'git', 'v1', ('development_artifact',)), connector)
            registry.register(Capability('local_git', 'git', 'v2', ('development_artifact',)), connector)
            registry.register(Capability('local_file', 'codex-rollout', CODEX_VERSION, ('behavioral_event',)), connector)
            if isinstance(connector, GitHubConfigured):
                registry.register(Capability('api', 'github', source.version, ('contextual_assertion', 'development_artifact')), connector)
            result = registry.probe(source, **scope)
            if result['status'] != 'recognized':
                print(json.dumps(result)); return 3
            if args.operation == 'preview':
                snapshot = registry.preview(source, **scope)
                result.update(snapshot_sha256=snapshot.sha256, records=len(snapshot.records), source_read=True,
                              capture_completeness='unknown', network_used=remote)
                if snapshot.metadata is not None:
                    result['execution_metadata'] = snapshot.metadata.summary()
                if snapshot.graph is not None: result['evidence_graph'] = snapshot.graph.summary()
                if snapshot.metadata is not None or snapshot.graph is not None:
                    result['checkpoint_snapshot_sha256'] = capture_snapshot(source, snapshot.sha256, snapshot.metadata, snapshot.graph)
                if isinstance(connector, OKFExport):
                    result['context_summary'] = connector.summary
                elif isinstance(connector, GitExport):
                    result['git_summary'] = connector.summary
                elif isinstance(connector, CodexCapture):
                    result['native_summary'] = connector.summary
                    result['checkpoint_ready'] = snapshot.checkpoint_ready
            elif args.operation in ('import', 'status', 'recheck'):
                if not args.cache:
                    raise ValueError()
                store = SourceStore(args.cache, **scope)
                if args.operation == 'recheck': result = registry.recheck(source, store, **scope)
                elif args.operation == 'import':
                    result = registry.ingest(source, store, **scope).to_dict()
                    if remote: result.update(source_read=True, network_used=True)
                else:
                    result = registry.status(source, store, **scope)
                    if remote: result['network_used'] = False
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
