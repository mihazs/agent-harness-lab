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
from .claude_mem import PROVIDER as CLAUDE_MEM, SOURCE_VERSION as CLAUDE_MEM_VERSION, ClaudeMemExport
from .mempalace_search import PROVIDER as MEMPALACE_SEARCH, SOURCE_VERSION as MEMPALACE_SEARCH_VERSION, MemPalaceSearch
from .mempalace_list import PROVIDER as MEMPALACE_LIST, SOURCE_VERSION as MEMPALACE_LIST_VERSION, MemPalaceList
from .mempalace import PROVIDER as MEMPALACE, SOURCE_VERSION as MEMPALACE_VERSION, MemPalaceDrawer
from .mem0 import PROVIDER as MEM0, SOURCE_VERSION as MEM0_VERSION, Mem0Export
from .hindsight_model_refs import PROVIDER as HINDSIGHT_REFS, SOURCE_VERSION as HINDSIGHT_REFS_VERSION, HindsightModelReferences
from .hindsight_models import PROVIDER as HINDSIGHT_MODELS, SOURCE_VERSION as HINDSIGHT_MODELS_VERSION, HindsightModels
from .hindsight_knowledge import PROVIDER as HINDSIGHT_KNOWLEDGE, SOURCE_VERSION as HINDSIGHT_KNOWLEDGE_VERSION, HindsightKnowledge
from .hindsight_zip import PROVIDER as HINDSIGHT_ZIP, SOURCE_VERSION as HINDSIGHT_ZIP_VERSION, HindsightZipArchive
from .hindsight import PROVIDER as HINDSIGHT, SOURCE_VERSION as HINDSIGHT_VERSION, HindsightArchive
from .cognee import PROVIDER as COGNEE, SOURCE_VERSION as COGNEE_VERSION, CogneeExport
from .opencode import PROVIDER as OPENCODE, SOURCE_VERSION as OPENCODE_VERSION, OpenCodeExport
from .opencode_backup import PROVIDER as OPENCODE_BACKUP, SOURCE_VERSION as OPENCODE_BACKUP_VERSION, OpenCodeBackup
from .execution import capture_snapshot
from .github_config import GitHubConfigured
from .gitea import PROVIDER as GITEA, GiteaConfigured
from .gitlab import PROVIDER as GITLAB, GitLabConfigured
from .remote_config import ConfiguredAPI
from .native import NativeCapture
from .claude import ClaudeCapture, SOURCE_VERSION as CLAUDE_VERSION, ROOT_VERSION as CLAUDE_ROOT_VERSION
from .codex import CodexCapture, SOURCE_VERSION as CODEX_VERSION, ROOT_VERSION as CODEX_ROOT_VERSION
from agent_harness_lab.config import read_json

# Selected-resource forge readers: connector and the preview summary key each prints.
FORGES = {GITEA: (GiteaConfigured, 'gitea_summary'), GITLAB: (GitLabConfigured, 'gitlab_summary')}


def main(argv):
    if argv and argv[0] == 'lineage':
        from .lineage import main as lineage_main
        return lineage_main(argv[1:])
    if argv and argv[0] == 'inventory':
        from .harness_inventory import main as inventory_main
        return inventory_main(argv[1:])
    if argv and argv[0] == 'github':
        from .github_quota import main as quota_main
        return quota_main(argv[1:])
    parser = argparse.ArgumentParser(prog='ahl sources',
        description='Inspect explicitly configured sources and maintain a private metadata cache; no source is enabled by default.',
        epilog='Without --manifest, only the default probe is valid and reports zero enabled sources. '
        'For a manifest, supply both expected SHA-256 scope identities. Probe checks the declaration; '
        'preview reads selected data without importing it; import writes a cache checkpoint; status reads cache metadata; '
        'recheck compares the selected source with its cache. Preview/import/recheck can contact a provider when the manifest selects an API source. '
        'All operations print JSON metadata. Exit 0: completed; 2: invalid/rejected input; 3: unsupported or blocked capability/access. '
        'Local providers include ahl-metadata, ai-memory-okf, git, test-results, claude-jsonl, codex-rollout, '
        'claude-mem-export (one selected Claude-Mem v15.9.0 JSON export; no worker, database or network), '
        'mem0-oss-export (one selected Mem0 OSS v2.2.1 get_all/history JSON export; no server, Platform or network), '
        'hindsight-bank-archive (one extracted Hindsight v1.1.0 bank transfer archive; no recall, reflect, service or network) and '
        'cognee-export (one selected Cognee v1.6.5 graph JSON export of one dataset; no recall, inference or store). '
        'opencode-export (one selected pinned-shape JSON session export; metadata only). '
        'opencode-sqlite-backup (one selected caller-supplied backup; metadata only). '
        'Rate limits: forge reads share a reserve, budget and quota state; see ahl sources github quota --help. '
        'API configurations select github, gitea or gitlab (api/gitlab/v4: named issue and merge request IIDs of one '
        'project on an explicit HTTPS instance, GET only, no enumeration). '
        'Example: ahl sources preview --manifest export.json --project-sha256 PROJECT_HASH --scope-sha256 SCOPE_HASH. '
        'For reviewed cache relationships, use ahl sources lineage --help; for a read-only harness surface '
        'inventory of one checkout, use ahl sources inventory --help.')
    parser.add_argument('operation', nargs='?', default='probe', choices=('probe', 'preview', 'import', 'status', 'recheck', 'lineage', 'inventory'),
        help='Requested source operation (default: %(default)s); lineage and inventory have their own options and help')
    parser.add_argument('--manifest',help='Schema-1 JSON: source/data local declaration, or source/endpoint/selection/limits GitHub, Gitea or GitLab configuration; no automatic discovery')
    parser.add_argument('--project-sha256',help='Expected 64-hex project identity matching the manifest; required with --manifest')
    parser.add_argument('--scope-sha256',help='Expected 64-hex selection/scope identity matching the manifest; required with --manifest')
    parser.add_argument('--cache',help='Private SourceStore journal file; required for import, status and recheck')
    parser.add_argument('--authorization-file',help='Private ASCII HTTP Authorization header file for an API source using configured authentication; never read by probe')
    parser.add_argument('--authorization-env',metavar='NAME',help='Name of one environment variable holding the complete Authorization header value for a configured Gitea or GitLab source; '
        'only this named variable is read, never by probe; excludes --authorization-file')
    args = parser.parse_args(argv)
    connector = None
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
            forge = FORGES.get(declaration['source'].get('provider')) if remote else None
            if args.authorization_env is not None and forge is None: raise ValueError('authorization variable requires a Gitea or GitLab configuration')
            connector = (forge[0](args.manifest, authorization_file=args.authorization_file, authorization_env=args.authorization_env) if forge
                else GitHubConfigured(args.manifest, authorization_file=args.authorization_file) if remote else LocalExport(args.manifest))
            source, _ = connector.declaration(**scope)
            if source.provider == 'ai-memory-okf':
                connector = OKFExport(args.manifest)
            elif source.provider == 'git':
                connector = GitExport(args.manifest)
            elif source.provider == TEST_RESULTS:
                connector = TestResultsExport(args.manifest)
            elif source.provider == CLAUDE_MEM:
                connector = ClaudeMemExport(args.manifest)
            elif source.provider == MEMPALACE_SEARCH:
                connector = MemPalaceSearch(args.manifest)
            elif source.provider == MEMPALACE_LIST:
                connector = MemPalaceList(args.manifest)
            elif source.provider == MEMPALACE:
                connector = MemPalaceDrawer(args.manifest)
            elif source.provider == MEM0:
                connector = Mem0Export(args.manifest)
            elif source.provider == HINDSIGHT_REFS:
                connector = HindsightModelReferences(args.manifest)
            elif source.provider == HINDSIGHT_MODELS:
                connector = HindsightModels(args.manifest)
            elif source.provider == HINDSIGHT_KNOWLEDGE:
                connector = HindsightKnowledge(args.manifest)
            elif source.provider == HINDSIGHT_ZIP:
                connector = HindsightZipArchive(args.manifest)
            elif source.provider == HINDSIGHT:
                connector = HindsightArchive(args.manifest)
            elif source.provider == COGNEE:
                connector = CogneeExport(args.manifest)
            elif source.provider == OPENCODE:
                connector = OpenCodeExport(args.manifest)
            elif source.provider == OPENCODE_BACKUP:
                connector = OpenCodeBackup(args.manifest)
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
            registry.register(Capability('local_file', CLAUDE_MEM, CLAUDE_MEM_VERSION, ('contextual_assertion',)), connector)
            registry.register(Capability('local_file', MEMPALACE_SEARCH, MEMPALACE_SEARCH_VERSION, ('contextual_assertion',)), connector)
            registry.register(Capability('local_file', MEMPALACE_LIST, MEMPALACE_LIST_VERSION, ('contextual_assertion',)), connector)
            registry.register(Capability('local_file', MEMPALACE, MEMPALACE_VERSION, ('contextual_assertion',)), connector)
            registry.register(Capability('local_file', MEM0, MEM0_VERSION, ('contextual_assertion',)), connector)
            registry.register(Capability('local_file', HINDSIGHT_REFS, HINDSIGHT_REFS_VERSION, ('contextual_assertion',)), connector)
            registry.register(Capability('local_file', HINDSIGHT_MODELS, HINDSIGHT_MODELS_VERSION, ('contextual_assertion',)), connector)
            registry.register(Capability('local_file', HINDSIGHT_KNOWLEDGE, HINDSIGHT_KNOWLEDGE_VERSION, ('contextual_assertion',)), connector)
            registry.register(Capability('local_file', HINDSIGHT_ZIP, HINDSIGHT_ZIP_VERSION, ('contextual_assertion',)), connector)
            registry.register(Capability('local_file', HINDSIGHT, HINDSIGHT_VERSION, ('contextual_assertion',)), connector)
            registry.register(Capability('local_file', COGNEE, COGNEE_VERSION, ('contextual_assertion',)), connector)
            registry.register(Capability('local_file', OPENCODE, OPENCODE_VERSION, ('contextual_assertion',)), connector)
            registry.register(Capability('local_file', OPENCODE, 'v2', ('contextual_assertion',)), connector)
            registry.register(Capability('local_file', OPENCODE, 'v3', ('contextual_assertion',)), connector)
            registry.register(Capability('local_file', OPENCODE_BACKUP, OPENCODE_BACKUP_VERSION, ('contextual_assertion',)), connector)
            registry.register(Capability('local_file', OPENCODE_BACKUP, 'v2', ('contextual_assertion',)), connector)
            registry.register(Capability('local_file', OPENCODE_BACKUP, 'v3', ('contextual_assertion',)), connector)
            registry.register(Capability('local_file', OPENCODE_BACKUP, 'v4', ('contextual_assertion',)), connector)
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
                elif isinstance(connector, ClaudeMemExport):
                    result['claude_mem_summary'] = connector.summary
                elif isinstance(connector, MemPalaceSearch):
                    result['mempalace_search_summary'] = connector.summary
                elif isinstance(connector, MemPalaceList):
                    result['mempalace_list_summary'] = connector.summary
                elif isinstance(connector, MemPalaceDrawer):
                    result['mempalace_summary'] = connector.summary
                elif isinstance(connector, Mem0Export):
                    result['mem0_summary'] = connector.summary
                elif isinstance(connector, HindsightModelReferences):
                    result['hindsight_refs_summary'] = connector.summary
                elif isinstance(connector, HindsightModels):
                    result['hindsight_models_summary'] = connector.summary
                elif isinstance(connector, HindsightKnowledge):
                    result['hindsight_knowledge_summary'] = connector.summary
                elif isinstance(connector, HindsightZipArchive):
                    result['hindsight_zip_summary'] = connector.summary
                elif isinstance(connector, HindsightArchive):
                    result['hindsight_summary'] = connector.summary
                elif isinstance(connector, CogneeExport):
                    result['cognee_summary'] = connector.summary
                elif isinstance(connector, OpenCodeExport):
                    result['opencode_summary'] = connector.summary
                elif isinstance(connector, OpenCodeBackup):
                    result['opencode_backup_summary'] = connector.summary
                elif forge is not None:
                    result[forge[1]] = connector.summary
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
                        # A denied, rate-limited or incomplete forge read is blocked access, never a checkpoint.
                        if forge is None or getattr(connector, 'summary', {}).get('checkpoint_ready') is not False: raise
                        print(json.dumps({'schema_version': 1, 'status': 'blocked', 'checkpoint_committed': False, 'source_read': True,
                            'network_used': True, forge[1]: connector.summary}, sort_keys=True))
                        return 3
                    if remote: result.update(source_read=True, network_used=True)
                else:
                    result = registry.status(source, store, **scope)
                    if remote: result['network_used'] = False
        if remote and getattr(connector, 'limiter', None) is not None: result['rate_limit'] = connector.limiter.receipt()
        print(json.dumps(result, sort_keys=True))
        return 0
    except MissingOKFDependency:
        print('OKF parser unavailable; install agent-harness-lab[okf] explicitly', file=sys.stderr)
        return 3
    except (NotImplementedError, PermissionError):
        print('source operation blocked by unavailable filesystem capabilities/access', file=sys.stderr)
        return 3
    except (ValueError, OSError, TypeError, KeyError, RecursionError, subprocess.SubprocessError):
        limiter = getattr(connector, 'limiter', None)
        if limiter is not None and limiter.run['halt'] is not None and args.operation in ('preview', 'import'):
            # A rate-limit halt is blocked access with its reset time, never a permission problem or a checkpoint.
            print(json.dumps({'schema_version': 1, 'status': 'blocked', 'checkpoint_committed': False, 'source_read': True,
                'network_used': True, 'rate_limit': limiter.receipt()}, sort_keys=True))
            return 3
        print('source operation rejected; inspect scope, declaration and metadata; no automatic retry', file=sys.stderr)
        return 2
