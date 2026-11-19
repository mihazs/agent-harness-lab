"""Child process for the opt-in real native/memory correlation regression."""
import hashlib
import json
import os
from pathlib import Path
import sys

from agent_harness_lab.sources.codex import CodexCapture
from agent_harness_lab.sources.identity_key import fingerprint
from agent_harness_lab.sources.okf import OKFExport
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.session_correlation import correlate_sessions
from agent_harness_lab.sources.store import SourceStore


def ingest(source, connector, store, record_type):
    registry = Registry()
    registry.register(Capability(source.kind, source.provider, source.version, (record_type,)), connector)
    registry.ingest(source, store, project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)


def main():
    root = Path(os.environ['REAL_DEV_ROOT'])
    project = root/sys.argv[1]
    project_id, scope_id = json.loads((root/'scope.json').read_text())
    selected = dict(schema_version=1, project=project.name, capture='capture.jsonl', max_records=10000)
    (root/'selection.json').write_text(json.dumps(selected))
    native = SourceSpec('local_file', 'codex-rollout', '0.166.0-v1', digest(str(root)),
                        digest(project_id), digest(selected), identity_provenance='source_declared_id', enabled=True)
    manifest = root/'native.json'
    manifest.write_text(json.dumps(dict(schema_version=1, source=native.to_dict(), data='selection.json')))
    native_store = SourceStore(root/'native-journal.json', project_sha256=native.project_sha256,
                               scope_sha256=native.scope_sha256)
    ingest(native, CodexCapture(manifest), native_store, 'behavioral_event')
    with (root/'capture.jsonl').open() as capture:
        session_id = json.loads(capture.readline())['payload']['id']
    raw_key = (root/'key').read_bytes()
    for version in (1, 2):
        identity = digest if version == 1 else lambda value: fingerprint(raw_key, value)
        fields = dict(kind='local_file', provider='ai-memory-okf', version='0.2',
                      root_sha256=identity(str(root/'memory')), project_sha256=identity(project_id),
                      scope_sha256=identity(scope_id), identity_provenance='source_declared_id', enabled=True)
        if version == 2:
            fields.update(schema_version=2, identity_scheme='hmac-sha256-v1',
                          identity_key_id=hashlib.sha256(raw_key).hexdigest())
        source = SourceSpec(**fields)
        manifest = root/'memory'/f'source-{version}.json'
        manifest.write_text(json.dumps(dict(schema_version=1, source=source.to_dict(), data='export')))
        key = raw_key if version == 2 else None
        connector = OKFExport(manifest, identity_key=key)
        store = SourceStore(root/'memory'/f'journal-{version}.json', project_sha256=source.project_sha256,
                            scope_sha256=source.scope_sha256, identity_key=key)
        ingest(source, connector, store, 'contextual_assertion')
        report = correlate_sessions(connector, source, store, [(native, native_store, str(project))])
        assert report['status'] == 'evaluated'
        assert report['counts']['native_session_records'] > 0
        pages = [p for p in connector.pages(source) if p.metadata.get('session_id') == session_id]
        assert len(pages) == 1 and pages[0].metadata.get('project') is None
        page_id = identity(pages[0].path)
        assert any(row['status'] == 'memory_only' and 'scope_undeclared' in row['reasons']
                   and any(ref['native_id_sha256'] == page_id for ref in row['memory_sessions'])
                   for row in report['clusters'])
        assert any(row['status'] == 'native_only' for row in report['clusters'])


if __name__ == '__main__':
    main()
