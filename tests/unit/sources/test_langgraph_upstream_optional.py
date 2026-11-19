"""Optional compatibility probe against the pinned upstream Item serializer."""
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import json

import pytest

from agent_harness_lab.sources.langgraph_items import LangGraphItems
from agent_harness_lab.sources.schema import SourceSpec, digest


def test_pinned_upstream_item_dict_is_accepted(tmp_path):
    try:
        installed = version('langgraph-checkpoint')
    except PackageNotFoundError:
        pytest.skip('optional upstream serializer is not installed')
    if installed != '4.3.2': pytest.skip('optional upstream serializer is not at the pinned version')
    base = pytest.importorskip('langgraph.store.base')
    namespace = ('memories', 'synthetic-user')
    item = base.Item(namespace=namespace, key='one', value={'content': 'synthetic'},
                     created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                     updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc))
    source = SourceSpec('local_file', 'langgraph-store-items', 'v1.2.22', digest(str(tmp_path.resolve())),
                        digest(('langgraph.project', 'project')), digest(('langgraph.namespace', namespace)),
                        identity_provenance='source_declared_id', enabled=True)
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(dict(schema_version=1, source=source.to_dict(), data='items.json')))
    (tmp_path / 'items.json').write_text(json.dumps([item.dict()]))
    captured = LangGraphItems(manifest, project='project', namespace=namespace).read(source)
    assert len(captured.records) == 1 and captured.records[0].plane == 'contextual_assertion'
    assert captured.metadata.coverage.reported_completeness == 'partial'
