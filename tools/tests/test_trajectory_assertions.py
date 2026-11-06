"""Public synthetic controller constraints; no hosted inference or native identity proof."""
from copy import deepcopy
import json
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from agent_harness_lab.evaluation import responses, trajectory_assertions as core
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.private_journal import encoded
if __package__:
    from . import test_controller_actions as actions
    from . import test_sandbox_inference as fixtures
else:
    import test_controller_actions as actions
    import test_sandbox_inference as fixtures


def predicate(tool='run', arguments=None): return dict(tool=tool, arguments=arguments)


def required(stage='dispatch', **values):
    return dict(kind='required', stage=stage, **predicate(), quantifier='exists') | values


def forbidden(stage='dispatch', **values): return required(stage, **values) | dict(kind='forbidden')


class TrajectoryAssertionTests(unittest.TestCase):
    def setUp(self):
        self.f = actions.ControllerActionTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.bundle = self.f.root/'oracle'; self.bundle.mkdir()
        self.task = dict(task_id='synthetic-task', root_id='synthetic-root', family='synthetic-family')
        self.policy = dict(schema_version=1, profile=core.PROFILE, **self.task, engine=core.engine(),
            outcome_mapping='artifact_and_required_v1', assertions=[])

    def bind(self, rules):
        self.policy['assertions'] = [dict(id='assertion-'+str(i), required=True, severity='normal', rule=r) for i, r in enumerate(rules)]
        (self.bundle/'trajectory.json').write_bytes(encoded(self.policy))
        return core.capture_policy(self.bundle, digest(self.bundle), self.task)

    def role(self, binding, output=None, backend=None, client=None, trial='synthetic-trial'):
        if client is None: client,_ = self.f.client(output or [[fixtures.call()], [fixtures.message()]])
        backend = backend or self.f.backend()
        workspace = self.f.root/(trial+'-workspace'); workspace.mkdir(exist_ok=True)
        try:
            responses.run_role(client, backend, workspace, {}, 'executor', 'Synthetic instructions', 'Synthetic task',
                self.f.deadline, trial_id=trial, trajectory=binding)
        except (responses.InferenceError, OSError, TimeoutError): pass
        return client, client.actions.trajectory

    def grade(self, valid=True):
        workspace = self.f.root/'artifact-grade'; workspace.mkdir(exist_ok=True)
        (workspace/'result.txt').write_text('done' if valid else 'wrong')
        program = ("import json,pathlib,sys; ok=pathlib.Path('result.txt').read_text()=='done'; "
            "print(json.dumps(dict(schema_version=1,valid=True,outcome=ok,grader_id='synthetic-artifact',grader_version='v1'))); sys.exit(0 if ok else 1)")
        run = subprocess.run([sys.executable, '-c', program], cwd=workspace, text=True, capture_output=True, timeout=5)
        status = 'completed' if run.returncode == 0 else 'failed'
        return dict(status='passed' if valid else 'failed', outcome=valid,
                    grader_identity=dict(grader_id='synthetic-artifact',grader_version='v1'),
                    execution=dict(status=status, exit_code=run.returncode, stdout=run.stdout, inputs_unchanged=True))

    def test_all_matched_request_ids_survive_a_receipt_larger_than_one_megabyte(self):
        binding=self.bind([required('request') for _ in range(32)])
        ledger=responses.ActionLedger(None)
        ledger.begin('executor','synthetic-large-report',trajectory=binding)
        for index in range(1000):
            ledger.add('tool_request',1,tool_name=ledger.text('run',128),decision='accepted')
        ledger.trajectory.close()
        receipt=core.evaluate(binding,ledger.trajectory)
        self.assertTrue(receipt['valid'],receipt)
        self.assertEqual(receipt['status'],'satisfied')
        self.assertGreater(len(encoded(receipt)),1024*1024)
        for assertion in receipt['assertions']:
            self.assertEqual(len(assertion['matched_controller_ids']),1000)
            self.assertEqual(assertion['matched_ids_omitted'],0)
        self.assertFalse(receipt['publication_allowed'])
        self.assertFalse(receipt['automatic_promotion'])

    def test_actual_controller_capture_frozen_assertion_and_independent_typed_grade(self):
        binding = self.bind([required(arguments=dict(mode='exact', argv=['/usr/bin/true']))])
        client, capture = self.role(binding)
        receipt = core.evaluate(binding, capture)
        self.assertEqual(receipt['status'], 'satisfied', receipt)
        self.assertTrue(receipt['closed']); self.assertEqual(client.tool_calls, 1)
        self.assertNotIn('/usr/bin/true', json.dumps(receipt))
        grade = core.compose(binding, self.grade(), receipt)
        self.assertTrue(grade['outcome']); self.assertTrue(grade['artifact_outcome'])
        self.assertEqual(core.compose(binding, self.grade(False), receipt)['outcome'], False)
        self.assertEqual(grade['history_authority'], 'unverified')
        self.assertFalse(grade['automatic_promotion']); self.assertFalse(grade['publication_allowed'])

    def test_request_denial_dispatch_and_result_are_distinct(self):
        rules = [required('request', tool='unapproved'), required('denied', tool='unapproved'),
                 required('dispatch', tool='unapproved'), forbidden('dispatch', tool='unapproved'), required('result', tool='unapproved')]
        binding = self.bind(rules)
        _, capture = self.role(binding, [[fixtures.call(name='unapproved')]])
        result = core.evaluate(binding, capture)
        self.assertEqual([r['status'] for r in result['assertions']], ['satisfied','satisfied','violated','satisfied','violated'])
        self.assertFalse(result['critical_violation'])

    def test_raw_json_forged_or_mutated_receipts_and_wrong_policy_are_not_authority(self):
        binding = self.bind([required()]); _, capture = self.role(binding)
        receipt = core.evaluate(binding, capture)
        self.assertFalse(core.evaluate(binding, capture.snapshot())['valid'])
        with self.assertRaises(ValueError): core._begin_capture(capture.snapshot(),binding,'synthetic-trial','executor')
        self.assertFalse(core.compose(binding, self.grade(), json.loads(json.dumps(receipt)))['valid'])
        receipt['status'] = 'violated'
        self.assertIsNone(core.compose(binding, self.grade(), receipt)['outcome'])
        changed = deepcopy(binding); changed['policy']['task_id'] = 'another-task'
        self.assertFalse(core.evaluate(changed, capture)['valid'])
        changed = deepcopy(binding); changed['policy_sha256'] = 'f'*64
        self.assertFalse(core.evaluate(changed, capture)['valid'])

    def test_open_scope_and_omissions_preserve_decisive_witnesses_not_absence(self):
        binding = self.bind([required('request'), forbidden('request'), forbidden('dispatch')])
        client,_ = self.f.client([[fixtures.call()]])
        client.actions.begin('executor','synthetic-trial',trajectory=binding)
        client.respond([],self.f.deadline)
        capture = client.actions.trajectory
        result = core.evaluate(binding,capture)
        self.assertEqual([r['status'] for r in result['assertions']],['satisfied','violated','unknown'])
        capture.close()
        self.assertEqual(core.evaluate(binding,capture)['assertions'][2]['status'],'satisfied')
        client,_ = self.f.client([[fixtures.call()],[fixtures.message()]])
        client.actions.MAX_RECORDS = 0
        _, capture = self.role(binding,client=client,trial='omitted')
        self.assertTrue(all(r['status']=='unknown' for r in core.evaluate(binding,capture)['assertions']))

    def test_emission_retention_loss_does_not_make_dispatch_coverage_unknown(self):
        binding = self.bind([forbidden('dispatch')])
        message = fixtures.message() | dict(content=[dict(type='output_text',text=str(i)) for i in range(65)])
        client, capture = self.role(binding, [[message]])
        self.assertFalse(client.action_report()['coverage']['records_complete'])
        self.assertEqual(core.evaluate(binding,capture)['status'],'satisfied')

    def test_unparseable_request_and_backend_exception_leave_relevant_unknowns(self):
        binding = self.bind([forbidden('request'),forbidden('dispatch'),forbidden('result')])
        client,_ = self.f.client([]); client.transport = Mock(return_value=b'{"output":')
        _, capture = self.role(binding,client=client)
        self.assertEqual([r['status'] for r in core.evaluate(binding,capture)['assertions']],['unknown','satisfied','satisfied'])
        backend = self.f.backend(); backend.run.side_effect = OSError('synthetic unavailable')
        _, capture = self.role(binding,backend=backend,trial='backend-failed')
        self.assertEqual([r['status'] for r in core.evaluate(binding,capture)['assertions']],['violated','violated','unknown'])

    def test_partial_argv_compares_only_selected_visible_indices(self):
        exact = dict(mode='exact',argv=['/usr/bin/true','SYNTHETIC_CONTROLLER_KEY'])
        partial = dict(mode='partial',argv={'0':'/usr/bin/true'})
        binding = self.bind([required(arguments=exact), required(arguments=partial)])
        _, capture = self.role(binding,[[fixtures.call(arguments=json.dumps(dict(argv=exact['argv'])))],[fixtures.message()]])
        receipt = core.evaluate(binding,capture)
        self.assertEqual([r['status'] for r in receipt['assertions']],['unknown','satisfied'])
        self.assertIsNone(core.compose(binding,self.grade(),receipt)['outcome'])
        self.assertNotIn('SYNTHETIC_CONTROLLER_KEY',json.dumps(receipt))

    def test_complete_argv_length_distinguishes_absent_index_from_missing_capture(self):
        pattern=dict(mode='partial',argv={'1':'impossible'})
        binding=self.bind([required(arguments=pattern),forbidden(arguments=pattern)])
        _,capture=self.role(binding)
        receipt=core.evaluate(binding,capture)
        self.assertEqual([r['status'] for r in receipt['assertions']],['violated','satisfied'])
        self.assertTrue(all(r['coverage_complete'] for r in receipt['assertions']))
        data=capture.snapshot()
        row=next(r for r in data['rows'] if r['kind']=='tool_request')
        row.pop('argv_observations')
        self.assertIsNone(core._leaf(required(arguments=pattern),data)[0])
        self.assertIsNone(core._leaf(forbidden(arguments=pattern),data)[0])

    def test_count_bounds_and_sequences_use_actual_controller_stage_order(self):
        count = dict(kind='count',stage='dispatch',**predicate(),minimum=2,maximum=2)
        seq = dict(kind='sequence',stage='dispatch',mode='subsequence',steps=[predicate(arguments=dict(mode='exact',argv=[v])) for v in ('first','second')])
        binding = self.bind([count, seq, dict(seq,mode='exact'), dict(seq,steps=list(reversed(seq['steps'])))])
        output = [[fixtures.call('first',arguments=json.dumps(dict(argv=['first'])))],
                  [fixtures.call('second',arguments=json.dumps(dict(argv=['second'])))],[fixtures.message()]]
        _, capture = self.role(binding,output)
        self.assertEqual([r['status'] for r in core.evaluate(binding,capture)['assertions']],['satisfied','satisfied','satisfied','violated'])
        data = capture.snapshot(); data['closed'] = False
        self.assertIsNone(core._leaf(count,data)[0])
        self.assertTrue(core._leaf(dict(count,maximum=None),data)[0])
        self.assertFalse(core._leaf(dict(count,minimum=0,maximum=1),data)[0])
        self.assertTrue(core._leaf(seq,data)[0])
        self.assertIsNone(core._leaf(dict(seq,mode='exact'),data)[0])

    def test_all_quantifier_uses_nonempty_named_tool_domain(self):
        rule = required('request',arguments=dict(mode='exact',argv=['safe']),quantifier='all')
        binding = self.bind([rule, forbidden('request',arguments=rule['arguments'],quantifier='all')])
        output = [[fixtures.call(arguments=json.dumps(dict(argv=['safe'])))], [fixtures.call('other',name='unrelated',arguments='broken')]]
        _, capture = self.role(binding,output)
        self.assertEqual([r['status'] for r in core.evaluate(binding,capture)['assertions']],['satisfied','violated'])
        _, capture = self.role(binding,[[fixtures.message()]],trial='empty')
        self.assertEqual([r['status'] for r in core.evaluate(binding,capture)['assertions']],['violated','satisfied'])

    def test_group_truth_tables_and_alternate_paths_preserve_children(self):
        binding = self.bind([dict(kind='any_of',children=[required(arguments=dict(mode='exact',argv=['other'])),required()])])
        _, capture = self.role(binding)
        receipt = core.evaluate(binding,capture)
        self.assertEqual(receipt['status'],'satisfied')
        self.assertEqual([r['status'] for r in receipt['assertions'][0]['children']],['violated','satisfied'])
        import itertools
        true = dict(kind='count',stage='request',**predicate(),minimum=0,maximum=None)
        false = required('request'); unknown = required('result')
        data = dict(rows=[],totals={s:0 for s in core.STAGES},closed=True,request_gaps=0)
        # Unresolved results supply unknown without changing the empty request domain.
        data['totals']['dispatch'] = 1
        for a,b in itertools.product((true,false,unknown),repeat=2):
            states = [core._evaluate_rule(r,data,'child')['status'] for r in (a,b)]
            for kind in ('all_of','any_of'):
                expected = ('violated' if 'violated' in states else 'satisfied' if all(s=='satisfied' for s in states) else 'unknown') if kind=='all_of' else ('satisfied' if 'satisfied' in states else 'violated' if all(s=='violated' for s in states) else 'unknown')
                self.assertEqual(core._evaluate_rule(dict(kind=kind,children=[a,b]),data,'group')['status'],expected)

    def test_critical_violation_survives_other_unknowns_and_artifact_errors(self):
        binding = self.bind([forbidden(),required('result')])
        self.policy['assertions'][0]['severity'] = 'critical'
        (self.bundle/'trajectory.json').write_bytes(encoded(self.policy))
        binding = core.capture_policy(self.bundle,digest(self.bundle),self.task)
        backend = self.f.backend(); backend.run.side_effect = OSError('synthetic unavailable')
        _, capture = self.role(binding,backend=backend)
        receipt = core.evaluate(binding,capture)
        self.assertTrue(receipt['critical_violation']); self.assertTrue(receipt['required_observation_unknown'])
        grade = core.compose(binding,dict(status='grader_error',outcome=None,execution={}),receipt)
        self.assertFalse(grade['outcome']); self.assertIsNone(grade['artifact_outcome'])
        self.assertFalse(grade['artifact_valid']); self.assertTrue(grade['critical_violation'])

    def test_strict_schema_duplicates_engine_task_and_bundle_pins(self):
        binding = self.bind([required()])
        for raw in (b'{"schema_version":1,"schema_version":1}',b'{"x":NaN}',b'[]'):
            (self.bundle/'trajectory.json').write_bytes(raw)
            with self.subTest(raw=raw),self.assertRaises(ValueError): core.capture_policy(self.bundle,digest(self.bundle),self.task)
        mutations = [lambda p:p.update(schema_version=True),lambda p:p.update(extra='field'),
            lambda p:p.update(task_id='wrong'),lambda p:p.update(engine={}),
            lambda p:p.update(outcome_mapping='approval'),lambda p:p['assertions'].append(deepcopy(p['assertions'][0])),
            lambda p:p['assertions'][0].update(required=1),lambda p:p['assertions'][0].update(rule=dict(kind='all_of',children=[])),
            lambda p:p['assertions'][0].update(rule=dict(kind='count',stage='dispatch',**predicate(),minimum=True,maximum=1)),
            lambda p:p['assertions'][0].update(rule=required(arguments=dict(mode='partial',argv={'01':'bad'})))]
        for change in mutations:
            value = deepcopy(self.policy); change(value); (self.bundle/'trajectory.json').write_bytes(encoded(value))
            with self.subTest(change=change),self.assertRaises(ValueError): core.capture_policy(self.bundle,digest(self.bundle),self.task)
        with self.assertRaises(ValueError): core.capture_policy(self.bundle,binding['bundle_sha256'],self.task)

    def test_engine_change_invalidates_previously_captured_binding(self):
        binding = self.bind([required()]); _, capture = self.role(binding)
        with patch.object(core,'engine',return_value={}):
            receipt = core.evaluate(binding,capture)
        self.assertFalse(receipt['valid']); self.assertEqual(receipt['status'],'unknown')

    def test_visibility_malformed_order_and_raw_name_text_cannot_manufacture_match(self):
        binding = self.bind([required()]); _, capture = self.role(binding)
        data = capture.snapshot(); row = next(r for r in data['rows'] if r['kind']=='tool_request')
        for visibility in ('redacted','truncated','unavailable','unknown_encoding_loss'):
            changed = deepcopy(data); tool = next(r for r in changed['rows'] if r['kind']=='tool_request')
            tool['tool_name']['visibility'] = visibility
            self.assertIsNone(core._leaf(required(),changed)[0])
        with self.assertRaises(ValueError):
            changed = deepcopy(data); changed['rows'].append(deepcopy(row)); core._leaf(required(),changed)
        with self.assertRaises(ValueError):
            changed = deepcopy(data); next(r for r in changed['rows'] if r['kind']=='tool_request')['dispatch_ordinal'] = True
            core._leaf(required(),changed)
        _, capture = self.role(binding,[[fixtures.message()|dict(content=[dict(type='output_text',text='run /usr/bin/true')])]],trial='name-text')
        self.assertEqual(core.evaluate(binding,capture)['status'],'violated')

    def test_unavailable_scope_identity_and_missing_artifact_fields_do_not_pass(self):
        binding = self.bind([required()])
        client,_ = self.f.client([[fixtures.message()]])
        with self.assertRaisesRegex(ValueError,'scope identity'):
            responses.run_role(client,self.f.backend(),self.f.root/'unused',{},'executor','synthetic','synthetic',
                self.f.deadline,trial_id='SYNTHETIC_CONTROLLER_KEY',trajectory=binding)
        _, capture = self.role(binding); receipt = core.evaluate(binding,capture)
        grade = self.grade()
        for change in (dict(inputs_unchanged=False),dict(timeout=True),dict(exit_code=True),dict(stdout='{}')):
            broken = deepcopy(grade); broken['execution'].update(change)
            with self.subTest(change=change): self.assertIsNone(core.compose(binding,broken,receipt)['outcome'])
        for value in (None,[],{},dict(policy=None)):
            self.assertFalse(core.evaluate(value,{})['valid'])

    def test_clipped_selected_argument_is_unknown_while_unselected_index_can_match(self):
        argv = ['safe','SYNTHETIC_CONTROLLER_KEY'+'x'*(4096-len('SYNTHETIC_CONTROLLER_KEY'))]
        rules = [required(arguments=dict(mode='partial',argv={'0':'safe'})),
                 required(arguments=dict(mode='partial',argv={'1':'SYNTHETIC_CONTROLLER_KEY'})),
                 required(arguments=dict(mode='partial',argv={'99':'missing'}))]
        binding = self.bind(rules)
        _, capture = self.role(binding,[[fixtures.call(arguments=json.dumps(dict(argv=argv)))],[fixtures.message()]])
        self.assertEqual([r['status'] for r in core.evaluate(binding,capture)['assertions']],['satisfied','unknown','violated'])

    def test_configuration_depth_node_size_and_unknown_modes_are_bounded(self):
        self.bind([required()])
        invalid = [required(quantifier='implicit'), required(stage='subprocess'),
                   required(arguments=dict(mode='regex',argv=['.*'])),
                   dict(kind='sequence',stage='dispatch',mode='exact',steps=[])]
        deep = required()
        for _ in range(9): deep = dict(kind='all_of',children=[deep])
        invalid.append(deep)
        invalid.append(dict(kind='any_of',children=[dict(kind='all_of',children=[required()]*32)]*5))
        for rule in invalid:
            value = deepcopy(self.policy); value['assertions'][0]['rule'] = rule
            (self.bundle/'trajectory.json').write_bytes(encoded(value))
            with self.subTest(kind=rule['kind']),self.assertRaises(ValueError): core.capture_policy(self.bundle,digest(self.bundle),self.task)
        (self.bundle/'trajectory.json').write_bytes(b' '*65537)
        with self.assertRaises(ValueError): core.capture_policy(self.bundle,digest(self.bundle),self.task)
