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
from .test_results import PROVIDER as TEST_RESULTS, SOURCE_VERSION as TEST_RESULTS_VERSION, TestResultsExport
from .execution import capture_snapshot
from .github_config import GitHubConfigured
from .gitea import PROVIDER as GITEA, GiteaConfigured
from .remote_config import ConfiguredAPI
from .native import NativeCapture
from .claude import ClaudeCapture, SOURCE_VERSION as CLAUDE_VERSION, ROOT_VERSION as CLAUDE_ROOT_VERSION
from .codex import CodexCapture, SOURCE_VERSION as CODEX_VERSION, ROOT_VERSION as CODEX_ROOT_VERSION
from agent_harness_lab.config import read_json


def main(argv):
    if argv and argv[0] == 'lineage':
        from .lineage import main as lineage_main
        return lineage_main(argv[1:])
    parser = argparse.ArgumentParser(prog='ahl sources',
        description='Inspect explicitly configured sources and maintain a private metadata cache; no source is enabled by default.',
        epilog='Without --manifest, only the default probe is valid and reports zero enabled sources. '
        'For a manifest, supply both expected SHA-256 scope identities. Probe checks the declaration; '
        'preview reads selected data without importing it; import writes a cache checkpoint; status reads cache metadata; '
        'recheck compares the selected source with its cache. Preview/import/recheck can contact a provider when the manifest selects an API source. '
        'All operations print JSON metadata. Exit 0: completed; 2: invalid/rejected input; 3: unsupported or blocked capability/access. '
        'Example: ahl sources preview --manifest export.json --project-sha256 PROJECT_HASH --scope-sha256 SCOPE_HASH. '
        'For reviewed cache relationships, use ahl sources lineage --help.')
    parser.add_argument('operation', nargs='?', default='probe', choices=('probe', 'preview', 'import', 'status', 'recheck', 'lineage'),
        help='Requested source operation (default: %(default)s); lineage has its own subcommands and help')
    parser.add_argument('--manifest',help='Schema-1 JSON: source/data local declaration, or source/endpoint/selection/limits GitHub or Gitea configuration; no automatic discovery')
    parser.add_argument('--project-sha256',help='Expected 64-hex project identity matching the manifest; required with --manifest')
    parser.add_argument('--scope-sha256',help='Expected 64-hex selection/scope identity matching the manifest; required with --manifest')
    parser.add_argument('--cache',help='Private SourceStore journal file; required for import, status and recheck')
    parser.add_argument('--authorization-file',help='Private ASCII HTTP Authorization header file for an API source using configured authentication; never read by probe')
    parser.add_argument('--authorization-env',metavar='NAME',help='Name of one environment variable holding the complete Authorization header value for a configured Gitea source; '
        'only this named variable is read, never by probe; excludes --authorization-file')
    args = parser.parse_args(argv)
    try:
        if args.manifest is None:
            if args.operation != 'probe' or any((args.project_sha256, args.scope_sha256, args.cache, args.authorization_file, args.authorization_env)):
                raise ValueError()
            result = dict(schema_version=1, enabled_sources=0, source_read=False, network_used=False)
        else:
            scope = dict(project_sha256=args.project_sha256, scope_sha256=args.scope_sha256)
            declaration = read_json(args.manifest)
            remote = isinstance(declaration, dict) and isinstance(declaration.get('source'), dict) and declaration['source'].get('kind') == 'api'
            if args.authorization_file is not None and not remote: raise ValueError('authorization file requires an API configuration')
            gitea = remote and declaration['source'].get('provider') == GITEA
            if args.authorization_env is not None and not gitea: raise ValueError('authorization variable requires a Gitea configuration')
            connector = (GiteaConfigured(args.manifest, authorization_file=args.authorization_file, authorization_env=args.authorization_env) if gitea
                else GitHubConfigured(args.manifest, authorization_file=args.authorization_file) if remote else LocalExport(args.manifest))
            source, _ = connector.declaration(**scope)
            if source.provider == 'ai-memory-okf':
                connector = OKFExport(args.manifest)
            elif source.provider == 'git':
                connector = GitExport(args.manifest)
            elif source.provider == TEST_RESULTS:
                connector = TestResultsExport(args.manifest)
            elif source.provider == 'claude-jsonl':
                connector = ClaudeCapture(args.manifest)
            elif source.provider == 'codex-rollout':
                connector = CodexCapture(args.manifest)
            registry = Registry()
            registry.register(Capability('local_file', 'ahl-metadata', 'v1', PLANES), connector)
            registry.register(Capability('local_file', 'ahl-metadata', 'v2', PLANES), connector)
            registry.register(Capability('local_file', 'ahl-metadata', 'v3', PLANES), connector)
            registry.register(Capability('local_file', 'ai-memory-okf', '0.2', ('contextual_assertion',)), connector)
            registry.register(Capability('local_git', 'git', 'v1', ('development_artifact',)), connector)
            registry.register(Capability('local_git', 'git', 'v2', ('development_artifact',)), connector)
            registry.register(Capability('local_file', TEST_RESULTS, TEST_RESULTS_VERSION, ('development_artifact',)), connector)
            for version in (CODEX_VERSION, CODEX_ROOT_VERSION):
                registry.register(Capability('local_file', 'codex-rollout', version, ('behavioral_event',)), connector)
            for version in (CLAUDE_VERSION, CLAUDE_ROOT_VERSION):
                registry.register(Capability('local_file', 'claude-jsonl', version, ('behavioral_event',)), connector)
            if isinstance(connector, ConfiguredAPI):
                registry.register(Capability('api', connector.provider, source.version, ('contextual_assertion', 'development_artifact')), connector)
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
                elif isinstance(connector, TestResultsExport):
                    result['test_results_summary'] = connector.summary
                elif isinstance(connector, GiteaConfigured):
                    result['gitea_summary'] = connector.summary
                    result['checkpoint_ready'] = snapshot.checkpoint_ready
                elif isinstance(connector, NativeCapture):
                    result['native_summary'] = connector.summary
                    result['checkpoint_ready'] = snapshot.checkpoint_ready
            elif args.operation in ('import', 'status', 'recheck'):
                if not args.cache:
                    raise ValueError()
                store = SourceStore(args.cache, **scope)
                if args.operation == 'recheck': result = registry.recheck(source, store, **scope)
                elif args.operation == 'import':
                    try:
                        result = registry.ingest(source, store, **scope).to_dict()
                    except ValueError:
                        # A denied, rate-limited or incomplete Gitea read is blocked access, never a checkpoint.
                        if not isinstance(connector, GiteaConfigured) or getattr(connector, 'summary', {}).get('checkpoint_ready') is not False: raise
                        print(json.dumps(dict(schema_version=1, status='blocked', checkpoint_committed=False, source_read=True,
                            network_used=True, gitea_summary=connector.summary), sort_keys=True))
                        return 3
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
