"""Public unsigned packet mechanics; fixed bank preconditions issue no authority."""
from contextlib import ExitStack,redirect_stdout,redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_harness_lab import config,workflow
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import bank_requalification as renewal,bank_qualification as bankq,closed_inputs,review_authority as authority
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.evaluation.regression_review import ReviewStore
from test_conditional_application import settings


class ConfiguredDelegationPacketTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.deadline=time.monotonic()+10
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:
            import agent_harness_lab
            self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()))
        self.project=self.root/'project';self.project.mkdir();self.instruction=self.project/'AGENTS.md'
        self.instruction.write_text('SYNTHETIC_PRIVATE_BODY\n');self.instruction.chmod(0o640)
        self.store=ReviewStore(self.root/'review');locator=renewal.allocate_delegation(self.store)
        self.history=HistoryStore(self.root/'history')
        with self.history.locked(create=True):pass
        self.events=self.root/'events.jsonl';self.events.write_text('')
        self.image=self.root/'image';self.image.mkdir(mode=0o700)
        self.cgroup=self.root/'cgroup';self.cgroup.mkdir()
        self.key=self.root/'token';self.key.write_text('SYNTHETIC_NEVER_READ');self.key.chmod(0o600)
        self.inference=self.root/'settings.json';self.inference.write_text(json.dumps(dict(schema_version=1,model='synthetic',
            credential_file=str(self.key),max_requests=60,max_tool_calls=40,max_output_tokens=100,max_request_bytes=65536,
            max_response_bytes=32768,instruction_protocol='ahl-controlled-agents-v1')))
        value=settings();value['runtime'].update(inference_settings=str(self.inference),cgroup_root=str(self.cgroup),image_root=str(self.image))
        value['application'].update(review_store=str(self.store.root),delegation_request=locator['request_path'],delegation_signature=locator['signature_path'])
        config.create(self.project,config.prepare(value));self.case_id=value['application']['bank_case_id'];self.request=Path(locator['request_path'])
        case=self.store.case(self.case_id);case.mkdir(parents=True,mode=0o700)
        (case/'declaration.json').write_text(json.dumps(dict(author='synthetic-bank-author')))
        metadata=dict(history_store=str(self.history.root),image_sha256=value['runtime']['image_sha256'])
        self.patches=ExitStack();self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.object(renewal,'parent',return_value=(case,[({},'d'*64)],None,metadata)))
        self.patches.enter_context(patch.object(bankq,'exposure_check',return_value=('e'*64,[])))
        self.patches.enter_context(patch('agent_harness_lab.evaluation.cgroup.delegated',return_value=self.cgroup))
        self.patches.enter_context(patch('agent_harness_lab.evaluation.responses.read_credential',side_effect=AssertionError('no secret read')))
        self.patches.enter_context(patch('agent_harness_lab.evaluation.responses.request',side_effect=AssertionError('no inference')))
        self.patches.enter_context(patch('subprocess.Popen',side_effect=AssertionError('no worker or signing process')))
        self.patches.enter_context(patch.object(authority,'request',side_effect=self.request_value))

    def request_value(self,store,review,**kwargs):
        return dict(schema_version=1,profile=authority.PROFILE,purpose=renewal.PURPOSE,
            store_sha256=closed_inputs.pin(str(store.root)),policy_sha256='f'*64,review=review)

    def invoke(self,*args):
        output=io.StringIO()
        with redirect_stdout(output),redirect_stderr(io.StringIO()):
            code=main(['bank-review','--store',str(self.store.root),'prepare-delegation','--reviewer','synthetic-reviewer',*map(str,args)])
        return code,json.loads(output.getvalue())

    def test_plain_configured_command_materializes_unsigned_packet_and_no_baseline_copy(self):
        before={p:p.read_bytes() for p in (self.instruction,self.project/'ahl.json',self.key)}
        code,result=self.invoke('--project',self.project,'--events',self.events)
        self.assertEqual(code,0);self.assertEqual(result['request_path'],str(self.request))
        packet=json.loads(self.request.read_bytes());domain=packet['review']['domain']
        self.assertEqual(domain['sources'][0]['selection'],str(self.events))
        self.assertEqual(domain['sources'][0]['snapshot_sha256'],closed_inputs.source_pin(domain['sources'][0],self.deadline))
        with tempfile.TemporaryDirectory() as folder:
            baseline=Path(folder)/'baseline';workflow.snapshot(self.project,baseline,allowed_paths=['AGENTS.md'])
            self.assertEqual(domain['baseline_sha256'],closed_inputs.tree_pin(baseline,self.deadline))
        self.assertEqual(domain['history_identity_sha256'],closed_inputs.store_identity(self.history.root))
        self.assertEqual(packet['review']['runtime']['image_sha256'],'c'*64)
        self.assertEqual(packet['review']['runtime']['settings_sha256'],bankq.record(self.inference,65536)[1])
        self.assertNotIn('SYNTHETIC_PRIVATE_BODY',self.request.read_text());self.assertNotIn('SYNTHETIC_NEVER_READ',self.request.read_text())
        self.assertFalse(result['publication_allowed']);self.assertFalse(self.request.with_suffix('.json.sig').exists())
        self.assertEqual(list(self.store.root.glob('.delegation-baseline-*')),[])
        for path,raw in before.items():self.assertEqual(path.read_bytes(),raw)

    def test_mixed_missing_and_conflicting_inputs_do_not_publish(self):
        for extra in ([],['--domain',self.root/'manual.json'],['--history-store',self.root/'foreign'],['--case','0'*64],['--request',self.root/'foreign.json']):
            with self.subTest(extra=extra),self.assertRaises(SystemExit):
                self.invoke('--project',self.project,*([] if not extra else ['--events',self.events]),*extra)
            self.assertFalse(self.request.exists())

    def test_changed_source_or_configuration_before_publication_rejects(self):
        for path in (self.events,self.project/'ahl.json'):
            before=path.read_bytes()
            def changed(*args,**kwargs):
                result=self.request_value(*args,**kwargs);path.write_bytes(before+b' ');return result
            with self.subTest(path=path),patch.object(authority,'request',side_effect=changed),self.assertRaises(SystemExit):
                self.invoke('--project',self.project,'--events',self.events)
            self.assertFalse(self.request.exists());path.write_bytes(before)

    def test_unpinned_native_adapter_and_unsafe_source_reject_before_publication(self):
        linked=self.root/'linked';linked.symlink_to(self.events)
        for args in (['--source-root',self.root],['--events',linked],['--events',self.events,'--source-version','unsupported']):
            with self.subTest(args=args),self.assertRaises(SystemExit):self.invoke('--project',self.project,*args)
            self.assertFalse(self.request.exists())

    def test_explicit_pinned_native_source_preserves_adapter_and_session(self):
        from agent_harness_lab.evaluation.bank_requalification_domain import CODEX_VERSION
        source=self.root/'native-source';source.mkdir();(source/'synthetic.jsonl').write_text('')
        code,_=self.invoke('--project',self.project,'--source-root',source,'--format','codex',
            '--source-version',CODEX_VERSION,'--session','synthetic-session','--history-store',self.history.root)
        self.assertEqual(code,0)
        selected=json.loads(self.request.read_bytes())['review']['domain']['sources'][0]
        self.assertEqual((selected['kind'],selected['format'],selected['version'],selected['session']),
            ('native_source_root','codex',CODEX_VERSION,'synthetic-session'))
        self.assertEqual(selected['snapshot_sha256'],closed_inputs.tree_pin(source,self.deadline))

    def test_explicit_legacy_documents_use_the_original_prepare_contract(self):
        domain=self.root/'domain.json';runtime=self.root/'runtime.json'
        domain.write_text('{"synthetic":"domain"}');runtime.write_text('{"synthetic":"runtime"}')
        with patch.object(renewal,'prepare',return_value=dict(publication_allowed=False)) as prepare:
            self.assertEqual(self.invoke('--case',self.case_id,'--domain',domain,'--runtime',runtime)[0],0)
        self.assertEqual(prepare.call_args.args[3:],({'synthetic':'domain'},{'synthetic':'runtime'}))
        self.assertNotIn('_before_publish',prepare.call_args.kwargs)
