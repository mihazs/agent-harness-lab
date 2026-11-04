"""Synthetic stateless ai-memory wire; no actual memories, keys or live server."""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab.sources import memory_capture as capture
from agent_harness_lab.sources.memory_capture import MCPPageCapture
from agent_harness_lab.sources.registry import Capability, Registry
from agent_harness_lab.sources.store import SourceStore
from agent_harness_lab.sources.transport import TransportError
if __package__:
    from . import test_source_mcp as fixtures
else:
    import test_source_mcp as fixtures


class MemoryCaptureTests(unittest.TestCase):
    def setUp(self):
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected and not Path(capture.__file__).resolve().is_relative_to(Path(expected).resolve()):
            raise AssertionError('MCP capture imported wrong package layout')
        self.f=fixtures.MCPReaderTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.scope=dict(project_sha256=self.f.source.project_sha256,scope_sha256=self.f.source.scope_sha256)
        self.store=SourceStore(Path(self.temp.name)/'private/cache.json',**self.scope)
        self.pages={p:dict(path=p,body='SYNTHETIC_PRIVATE_BODY',page_id='synthetic-native-version',
            frontmatter=dict(session_id='synthetic-session',review_status='reviewed'),truncated=False) for p in ('notes/a.md','notes/b.md')}
        self.connector=MCPPageCapture(self.f.client,list(reversed(self.pages)),authorization='Bearer SYNTHETIC_SECRET')
        self.registry=Registry();self.registry.register(Capability('api','ai-memory-mcp','2.0.0',('contextual_assertion',)),self.connector)
        self.calls=[];self.after=None
        self.f.wire.side_effect=self.exchange

    def exchange(self,origin,target,headers,**kwargs):
        request=json.loads(kwargs['body']);self.calls.append(request)
        if request['method']=='initialize': result=self.f.initialized
        elif request['method']=='tools/list': result=dict(tools=self.f.tools)
        else:
            self.assertEqual(request['params']['name'],'memory_read_page')
            args=request['params']['arguments']
            self.assertEqual((args['workspace'],args['project']),('synthetic-workspace','synthetic-project'))
            self.assertEqual(args['max_chars'],self.f.schema['properties']['max_chars'].get('maximum',64000))
            result=dict(content=[dict(type='text',text=json.dumps(self.pages[args['path']]))])
        if self.after:self.after(request)
        return dict(jsonrpc='2.0',id=request['id'],result=deepcopy(result)),{'Content-Type':'application/json'},100

    def ingest(self):return self.registry.ingest(self.f.source,self.store,**self.scope)

    def test_actual_registry_preview_import_status_and_reimport_hash_only(self):
        probe=self.registry.probe(self.f.source,**self.scope);self.assertFalse(probe['network_used']);self.assertEqual(self.calls,[])
        snapshot=self.registry.preview(self.f.source,**self.scope)
        self.assertEqual(len(snapshot.records),2);self.assertEqual(snapshot.metadata.coverage.reported_completeness,'partial')
        self.assertTrue(all(r.record_type=='retrieved_memory_page' and r.independence=='unknown' for r in snapshot.records))
        self.assertEqual(self.connector.summary['visibility'],'retrieved')
        self.assertEqual(self.connector.summary['injected_context'],'unknown')
        self.assertEqual(snapshot.metadata.identities,())
        first=self.ingest();self.assertEqual(first.imported,2)
        before=len(self.calls);self.registry.status(self.f.source,self.store,**self.scope);self.assertEqual(len(self.calls),before)
        second=self.ingest();self.assertEqual(second.skipped,2)
        raw=self.store.path.read_text()
        for secret in ('SYNTHETIC_PRIVATE_BODY','SYNTHETIC_SECRET','synthetic-session','notes/a.md','synthetic-project','memory.example.invalid'):
            self.assertNotIn(secret,raw)
        self.assertEqual(self.store.path.stat().st_mode&0o777,0o600)

    def test_changed_native_revision_keeps_history_without_execution_claims(self):
        self.ingest();self.pages['notes/a.md']['page_id']='synthetic-version-two'
        result=self.ingest();self.assertEqual((result.imported,result.skipped),(1,1))
        values=json.loads(self.store.path.read_text())
        self.assertEqual(len(values['records']),3)
        self.assertEqual(self.connector.summary['native_session_binding'],'unknown')

    def test_foreign_disabled_missing_auth_and_version_never_connect(self):
        for source in (replace(self.f.source,project_sha256='f'*64),replace(self.f.source,enabled=False),replace(self.f.source,version='unsupported')):
            with self.assertRaises((ValueError,TransportError)):self.connector.read(source)
        denied=MCPPageCapture(self.f.client,['notes/a.md'],authorization=None)
        with self.assertRaises(TransportError):denied.read(self.f.source)
        self.assertEqual(self.calls,[])

    def test_blank_or_padded_scope_cannot_become_server_active_scope(self):
        for workspace,project in ((' ',' '),('synthetic-workspace ','synthetic-project'),('synthetic-workspace',' synthetic-project')):
            with self.subTest(workspace=workspace,project=project),self.assertRaises(TransportError):
                capture.AiMemoryMCP(self.f.url,workspace=workspace,project=project,
                    server_version='2.0.0',tool_schemas=self.f.client.schemas)
        self.assertEqual(self.calls,[])

    def test_shared_bytes_exact_cap_and_exhaustion_do_not_reset_per_page(self):
        self.f.client.transport.max_bytes=1200
        self.assertEqual(len(self.connector.read(self.f.source).records),2)
        self.assertEqual(len(self.calls),12)
        self.calls=[];self.f.client.transport.max_bytes=1100
        with self.assertRaises(TransportError):self.connector.read(self.f.source)
        self.assertEqual(len(self.calls),11)

    def test_changed_readback_or_later_failure_preserves_checkpoint_bytes(self):
        self.ingest();before=self.store.path.read_bytes();count=0
        def mutate(request):
            nonlocal count
            if request['method']=='tools/call':
                count+=1
                if count==3:self.pages['notes/a.md']['body']='SYNTHETIC_CHANGED'
        self.after=mutate
        with self.assertRaises(TransportError):self.ingest()
        self.assertEqual(self.store.path.read_bytes(),before)
        self.after=None;self.pages['notes/a.md']['truncated']=True
        with self.assertRaises(TransportError):self.ingest()
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_config_mutation_uses_frozen_scope_and_refuses_commit(self):
        self.ingest();before=self.store.path.read_bytes()
        def mutate(request):
            self.f.client.workspace='foreign'
        self.after=mutate
        with self.assertRaises(TransportError):self.ingest()
        self.assertEqual(self.store.path.read_bytes(),before)
        self.assertTrue(all(r['params']['arguments']['workspace']=='synthetic-workspace' for r in self.calls if r['method']=='tools/call'))

    def test_shared_deadline_and_late_normalization_stop_before_commit(self):
        self.ingest();before=self.store.path.read_bytes();clock=[0.0]
        self.f.client.transport.seconds=1
        self.after=lambda request:clock.__setitem__(0,clock[0]+.2)
        with patch('agent_harness_lab.sources.mcp.time.monotonic',side_effect=lambda:clock[0]):
            with self.assertRaises(TransportError):self.ingest()
        self.assertEqual(self.store.path.read_bytes(),before)
        self.after=None;clock[0]=0;original=capture.Snapshot
        def slow(*args,**kwargs):
            value=original(*args,**kwargs);clock[0]=2;return value
        with patch('agent_harness_lab.sources.mcp.time.monotonic',side_effect=lambda:clock[0]),patch.object(capture,'Snapshot',side_effect=slow):
            with self.assertRaises(TransportError):self.ingest()
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_selection_bounds_and_unavailable_completeness_are_not_absence(self):
        for paths in ([],['notes/a.md']*2,['../private'],['\ud800']):
            with self.assertRaises(ValueError):MCPPageCapture(self.f.client,paths,authorization='Bearer token')
        del self.pages['notes/a.md']['truncated']
        with self.assertRaises(TransportError):self.connector.read(self.f.source)

    def test_selected_pages_requests_and_aggregate_wire_bytes_have_no_implicit_ceiling(self):
        self.f.schema['properties']['max_chars'] = {'maximum': 100000}
        self.f.client.schemas = {name:capture.digest(self.f.schema) for name in fixtures.READ_TOOLS}
        self.pages = {f'notes/{i}.md':dict(path=f'notes/{i}.md',body='x'*80000,
            page_id='synthetic-'+str(i),truncated=False,total_chars=80000) for i in range(60)}
        self.connector = MCPPageCapture(self.f.client,list(self.pages),authorization='Bearer SYNTHETIC_SECRET')
        def exchange(*args,**kwargs):
            value,headers,_ = self.exchange(*args,**kwargs)
            return value,headers,len(json.dumps(value).encode())
        self.f.wire.side_effect = exchange
        snapshot = self.connector.read(self.f.source)
        self.assertEqual(len(snapshot.records),60)
        self.assertEqual(self.connector.summary['wire_requests'],360)
        bounds = {bound.dimension:bound for bound in snapshot.metadata.coverage.scan_bounds}
        self.assertIsNone(bounds['bytes'].limit)
        self.assertGreater(bounds['bytes'].observed,8*1024*1024)
        self.assertIsNone(bounds['pages'].limit)
        self.assertEqual(bounds['records'].limit,60)
        self.assertEqual(snapshot.metadata.coverage.reported_completeness,'partial')

    def test_protocol_errors_redact_and_never_commit_raw_provider_claims(self):
        self.f.initialized['serverInfo']['version']='unsupported'
        with self.assertRaises(TransportError) as error:self.ingest()
        self.assertNotIn('SYNTHETIC',str(error.exception));self.assertFalse(self.store.path.exists())
        self.assertFalse(any(r['method']=='tools/call' for r in self.calls))
