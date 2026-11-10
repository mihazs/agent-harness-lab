"""Public synthetic controller constraints; no hosted inference or native identity proof."""
from copy import deepcopy
import json
import subprocess
import sys
from unittest.mock import Mock, patch

import pytest

from agent_harness_lab.evaluation import responses, trajectory_assertions as core
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.private_journal import encoded
from tests.integration.evaluation import test_controller_actions as actions
from tests.integration.sandbox import test_sandbox_inference as fixtures


def predicate(tool='run', arguments=None): return dict(tool=tool, arguments=arguments)


def required(stage='dispatch', **values):
    return dict(kind='required', stage=stage, **predicate(), quantifier='exists') | values


def forbidden(stage='dispatch', **values): return required(stage, **values) | dict(kind='forbidden')


POLICY_MUTATIONS = (
    pytest.param(lambda p:p.update(schema_version=True), id='boolean-schema-version'),
    pytest.param(lambda p:p.update(extra='field'), id='extra-field'),
    pytest.param(lambda p:p.update(task_id='wrong'), id='wrong-task'),
    pytest.param(lambda p:p.update(engine={}), id='empty-engine'),
    pytest.param(lambda p:p.update(outcome_mapping='approval'), id='unknown-outcome-mapping'),
    pytest.param(lambda p:p['assertions'].append(deepcopy(p['assertions'][0])), id='duplicate-assertion'),
    pytest.param(lambda p:p['assertions'][0].update(required=1), id='integer-required'),
    pytest.param(lambda p:p['assertions'][0].update(rule=dict(kind='all_of',children=[])), id='empty-group'),
    pytest.param(lambda p:p['assertions'][0].update(rule=dict(kind='count',stage='dispatch',**predicate(),minimum=True,maximum=1)), id='boolean-count-bound'),
    pytest.param(lambda p:p['assertions'][0].update(rule=required(arguments=dict(mode='partial',argv={'01':'bad'}))), id='padded-argv-index'))

_deep = required()
for _ in range(9): _deep = dict(kind='all_of',children=[_deep])
INVALID_RULES = (
    pytest.param(required(quantifier='implicit'), id='implicit-quantifier'),
    pytest.param(required(stage='subprocess'), id='unknown-stage'),
    pytest.param(required(arguments=dict(mode='regex',argv=['.*'])), id='regex-arguments'),
    pytest.param(dict(kind='sequence',stage='dispatch',mode='exact',steps=[]), id='empty-sequence'),
    pytest.param(_deep, id='too-deep'),
    pytest.param(dict(kind='any_of',children=[dict(kind='all_of',children=[required()]*32)]*5), id='too-many-nodes'))


class TestTrajectoryAssertions:
    @pytest.fixture(autouse=True)
    def harness(self):
        self.f = actions.ControllerActionTests(); self.f.setUp()
        self.bundle = self.f.root/'oracle'; self.bundle.mkdir()
        self.task = dict(task_id='synthetic-task', root_id='synthetic-root', family='synthetic-family')
        self.policy = dict(schema_version=1, profile=core.PROFILE, **self.task, engine=core.engine(),
            outcome_mapping='artifact_and_required_v1', assertions=[])
        yield
        self.f.doCleanups()

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
        assert receipt['valid'], receipt
        assert receipt['status'] == 'satisfied'
        assert len(encoded(receipt)) > 1024*1024
        for assertion in receipt['assertions']:
            assert len(assertion['matched_controller_ids']) == 1000
            assert assertion['matched_ids_omitted'] == 0
        assert not receipt['publication_allowed']
        assert not receipt['automatic_promotion']

    def test_actual_controller_capture_frozen_assertion_and_independent_typed_grade(self):
        binding = self.bind([required(arguments=dict(mode='exact', argv=['/usr/bin/true']))])
        client, capture = self.role(binding)
        receipt = core.evaluate(binding, capture)
        assert receipt['status'] == 'satisfied', receipt
        assert receipt['closed']; assert client.tool_calls == 1
        assert '/usr/bin/true' not in json.dumps(receipt)
        grade = core.compose(binding, self.grade(), receipt)
        assert grade['outcome']; assert grade['artifact_outcome']
        assert core.compose(binding, self.grade(False), receipt)['outcome'] == False
        assert grade['history_authority'] == 'unverified'
        assert not grade['automatic_promotion']; assert not grade['publication_allowed']

    def test_request_denial_dispatch_and_result_are_distinct(self):
        rules = [required('request', tool='unapproved'), required('denied', tool='unapproved'),
                 required('dispatch', tool='unapproved'), forbidden('dispatch', tool='unapproved'), required('result', tool='unapproved')]
        binding = self.bind(rules)
        _, capture = self.role(binding, [[fixtures.call(name='unapproved')]])
        result = core.evaluate(binding, capture)
        assert [r['status'] for r in result['assertions']] == ['satisfied','satisfied','violated','satisfied','violated']
        assert not result['critical_violation']

    def test_raw_json_forged_or_mutated_receipts_and_wrong_policy_are_not_authority(self):
        binding = self.bind([required()]); _, capture = self.role(binding)
        receipt = core.evaluate(binding, capture)
        assert not core.evaluate(binding, capture.snapshot())['valid']
        with pytest.raises(ValueError): core._begin_capture(capture.snapshot(),binding,'synthetic-trial','executor')
        assert not core.compose(binding, self.grade(), json.loads(json.dumps(receipt)))['valid']
        receipt['status'] = 'violated'
        assert core.compose(binding, self.grade(), receipt)['outcome'] is None
        changed = deepcopy(binding); changed['policy']['task_id'] = 'another-task'
        assert not core.evaluate(changed, capture)['valid']
        changed = deepcopy(binding); changed['policy_sha256'] = 'f'*64
        assert not core.evaluate(changed, capture)['valid']

    def test_open_scope_and_omissions_preserve_decisive_witnesses_not_absence(self):
        binding = self.bind([required('request'), forbidden('request'), forbidden('dispatch')])
        client,_ = self.f.client([[fixtures.call()]])
        client.actions.begin('executor','synthetic-trial',trajectory=binding)
        client.respond([],self.f.deadline)
        capture = client.actions.trajectory
        result = core.evaluate(binding,capture)
        assert [r['status'] for r in result['assertions']] == ['satisfied','violated','unknown']
        capture.close()
        assert core.evaluate(binding,capture)['assertions'][2]['status'] == 'satisfied'
        client,_ = self.f.client([[fixtures.call()],[fixtures.message()]])
        client.actions.MAX_RECORDS = 0
        _, capture = self.role(binding,client=client,trial='omitted')
        assert all(r['status']=='unknown' for r in core.evaluate(binding,capture)['assertions'])

    def test_emission_retention_loss_does_not_make_dispatch_coverage_unknown(self):
        binding = self.bind([forbidden('dispatch')])
        message = fixtures.message() | dict(content=[dict(type='output_text',text=str(i)) for i in range(65)])
        client, capture = self.role(binding, [[message]])
        assert not client.action_report()['coverage']['records_complete']
        assert core.evaluate(binding,capture)['status'] == 'satisfied'

    def test_unparseable_request_and_backend_exception_leave_relevant_unknowns(self):
        binding = self.bind([forbidden('request'),forbidden('dispatch'),forbidden('result')])
        client,_ = self.f.client([]); client.transport = Mock(return_value=b'{"output":')
        _, capture = self.role(binding,client=client)
        assert [r['status'] for r in core.evaluate(binding,capture)['assertions']] == ['unknown','satisfied','satisfied']
        backend = self.f.backend(); backend.run.side_effect = OSError('synthetic unavailable')
        _, capture = self.role(binding,backend=backend,trial='backend-failed')
        assert [r['status'] for r in core.evaluate(binding,capture)['assertions']] == ['violated','violated','unknown']

    @pytest.mark.parametrize('problem', [dict(status='incomplete'), dict(error={'message': 'synthetic'}),
                                         dict(incomplete_details={'reason': 'synthetic'})])
    def test_incomplete_response_does_not_prove_missing_requests(self, problem):
        binding = self.bind([forbidden('request', tool='unseen'), forbidden('request', tool='run'),
                             forbidden('dispatch', tool='run')])
        client, _ = self.f.client([])
        response = dict(status='completed', output=[fixtures.call()],
                        usage=dict(input_tokens=5, output_tokens=2, total_tokens=7)) | problem
        client.transport = Mock(return_value=json.dumps(response).encode())
        _, capture = self.role(binding, client=client)
        receipt = core.evaluate(binding, capture)
        assert [row['status'] for row in receipt['assertions']] == ['unknown', 'violated', 'satisfied']
        assert not receipt['assertions'][0]['coverage_complete']

    def test_partial_argv_compares_only_selected_visible_indices(self):
        exact = dict(mode='exact',argv=['/usr/bin/true','SYNTHETIC_CONTROLLER_KEY'])
        partial = dict(mode='partial',argv={'0':'/usr/bin/true'})
        binding = self.bind([required(arguments=exact), required(arguments=partial)])
        _, capture = self.role(binding,[[fixtures.call(arguments=json.dumps(dict(argv=exact['argv'])))],[fixtures.message()]])
        receipt = core.evaluate(binding,capture)
        assert [r['status'] for r in receipt['assertions']] == ['unknown','satisfied']
        assert not receipt['assertions'][0]['coverage_complete']
        assert receipt['assertions'][1]['coverage_complete']
        assert core.compose(binding,self.grade(),receipt)['outcome'] is None
        assert 'SYNTHETIC_CONTROLLER_KEY' not in json.dumps(receipt)

    def test_visible_witness_remains_satisfied_when_another_argument_is_redacted(self):
        rule = required(arguments=dict(mode='exact', argv=['safe']))
        sequence = dict(kind='sequence', stage='dispatch', mode='subsequence', steps=[predicate(arguments=rule['arguments'])])
        binding = self.bind([rule, sequence])
        output = [[fixtures.call('first', arguments=json.dumps(dict(argv=['safe'])))],
                  [fixtures.call('second', arguments=json.dumps(dict(argv=['SYNTHETIC_CONTROLLER_KEY'])))],
                  [fixtures.message()]]
        _, capture = self.role(binding, output)
        results = core.evaluate(binding, capture)['assertions']
        assert all(result['status'] == 'satisfied' and not result['coverage_complete'] for result in results)

    def test_exact_sequence_coverage_ignores_uncompared_redacted_argument(self):
        sequence = dict(kind='sequence', stage='dispatch', mode='exact', steps=[
            predicate(arguments=dict(mode='exact', argv=['safe'])), predicate(tool='other')])
        redacted_sequence = dict(sequence, steps=[sequence['steps'][0], sequence['steps'][0]])
        binding = self.bind([sequence, redacted_sequence])
        output = [[fixtures.call('first', arguments=json.dumps(dict(argv=['safe'])))],
                  [fixtures.call('second', arguments=json.dumps(dict(argv=['SYNTHETIC_CONTROLLER_KEY'])))],
                  [fixtures.message()]]
        _, capture = self.role(binding, output)
        first, second = core.evaluate(binding, capture)['assertions']
        assert first['status'] == 'violated' and first['coverage_complete']
        assert second['status'] == 'unknown' and not second['coverage_complete']

    def test_complete_argv_length_distinguishes_absent_index_from_missing_capture(self):
        pattern=dict(mode='partial',argv={'1':'impossible'})
        binding=self.bind([required(arguments=pattern),forbidden(arguments=pattern)])
        _,capture=self.role(binding)
        receipt=core.evaluate(binding,capture)
        assert [r['status'] for r in receipt['assertions']] == ['violated','satisfied']
        assert all(r['coverage_complete'] for r in receipt['assertions'])
        data=capture.snapshot()
        row=next(r for r in data['rows'] if r['kind']=='tool_request')
        row.pop('argv_observations')
        assert core._leaf(required(arguments=pattern),data)[0] is None
        assert core._leaf(forbidden(arguments=pattern),data)[0] is None

    def test_count_bounds_and_sequences_use_actual_controller_stage_order(self):
        count = dict(kind='count',stage='dispatch',**predicate(),minimum=2,maximum=2)
        seq = dict(kind='sequence',stage='dispatch',mode='subsequence',steps=[predicate(arguments=dict(mode='exact',argv=[v])) for v in ('first','second')])
        binding = self.bind([count, seq, dict(seq,mode='exact'), dict(seq,steps=list(reversed(seq['steps'])))])
        output = [[fixtures.call('first',arguments=json.dumps(dict(argv=['first'])))],
                  [fixtures.call('second',arguments=json.dumps(dict(argv=['second'])))],[fixtures.message()]]
        _, capture = self.role(binding,output)
        assert [r['status'] for r in core.evaluate(binding,capture)['assertions']] == ['satisfied','satisfied','satisfied','violated']
        data = capture.snapshot(); data['closed'] = False
        assert core._leaf(count,data)[0] is None
        assert core._leaf(dict(count,maximum=None),data)[0]
        assert not core._leaf(dict(count,minimum=0,maximum=1),data)[0]
        assert core._leaf(seq,data)[0]
        assert core._leaf(dict(seq,mode='exact'),data)[0] is None

    def test_all_quantifier_uses_nonempty_named_tool_domain(self):
        rule = required('request',arguments=dict(mode='exact',argv=['safe']),quantifier='all')
        binding = self.bind([rule, forbidden('request',arguments=rule['arguments'],quantifier='all')])
        output = [[fixtures.call(arguments=json.dumps(dict(argv=['safe'])))], [fixtures.call('other',name='unrelated',arguments='broken')]]
        _, capture = self.role(binding,output)
        assert [r['status'] for r in core.evaluate(binding,capture)['assertions']] == ['satisfied','violated']
        _, capture = self.role(binding,[[fixtures.message()]],trial='empty')
        assert [r['status'] for r in core.evaluate(binding,capture)['assertions']] == ['violated','satisfied']

    def test_group_truth_tables_and_alternate_paths_preserve_children(self):
        binding = self.bind([dict(kind='any_of',children=[required(arguments=dict(mode='exact',argv=['other'])),required()])])
        _, capture = self.role(binding)
        receipt = core.evaluate(binding,capture)
        assert receipt['status'] == 'satisfied'
        assert [r['status'] for r in receipt['assertions'][0]['children']] == ['violated','satisfied']
        from agent_harness_lab.evaluation import benchmark
        report = benchmark.summary(dict(trajectory_profile=core.PROFILE, trials=[dict(task='synthetic',
            controller_grade=dict(trajectory=receipt))]))
        assert [r['status'] for r in report['trials'][0]['assertions'][0]['children']] == ['violated', 'satisfied']
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
                assert core._evaluate_rule(dict(kind=kind,children=[a,b]),data,'group')['status'] == expected

    def test_critical_violation_survives_other_unknowns_and_artifact_errors(self):
        binding = self.bind([forbidden(),required('result')])
        self.policy['assertions'][0]['severity'] = 'critical'
        (self.bundle/'trajectory.json').write_bytes(encoded(self.policy))
        binding = core.capture_policy(self.bundle,digest(self.bundle),self.task)
        backend = self.f.backend(); backend.run.side_effect = OSError('synthetic unavailable')
        _, capture = self.role(binding,backend=backend)
        receipt = core.evaluate(binding,capture)
        assert receipt['critical_violation']; assert receipt['required_observation_unknown']
        grade = core.compose(binding,dict(status='grader_error',outcome=None,execution={}),receipt)
        assert not grade['outcome']; assert grade['artifact_outcome'] is None
        assert not grade['artifact_valid']; assert grade['critical_violation']

    @pytest.mark.parametrize('raw', (
        pytest.param(b'{"schema_version":1,"schema_version":1}', id='duplicate-key'),
        pytest.param(b'{"x":NaN}', id='nan'),
        pytest.param(b'[]', id='not-an-object')))
    def test_strict_json_rejects_duplicate_keys_nan_and_non_objects(self, raw):
        self.bind([required()])
        (self.bundle/'trajectory.json').write_bytes(raw)
        with pytest.raises(ValueError): core.capture_policy(self.bundle,digest(self.bundle),self.task)

    @pytest.mark.parametrize('change', POLICY_MUTATIONS)
    def test_policy_schema_engine_and_task_mutations_are_rejected(self, change):
        self.bind([required()])
        value = deepcopy(self.policy); change(value); (self.bundle/'trajectory.json').write_bytes(encoded(value))
        with pytest.raises(ValueError): core.capture_policy(self.bundle,digest(self.bundle),self.task)

    def test_bundle_pin_rejects_a_changed_bundle(self):
        binding = self.bind([required()])
        self.bind([required(), forbidden()])
        with pytest.raises(ValueError): core.capture_policy(self.bundle,binding['bundle_sha256'],self.task)

    def test_engine_change_invalidates_previously_captured_binding(self):
        binding = self.bind([required()]); _, capture = self.role(binding)
        with patch.object(core,'engine',return_value={}):
            receipt = core.evaluate(binding,capture)
        assert not receipt['valid']; assert receipt['status'] == 'unknown'

    def test_visibility_malformed_order_and_raw_name_text_cannot_manufacture_match(self):
        binding = self.bind([required()]); _, capture = self.role(binding)
        data = capture.snapshot(); row = next(r for r in data['rows'] if r['kind']=='tool_request')
        for visibility in ('redacted','truncated','unavailable','unknown_encoding_loss'):
            changed = deepcopy(data); tool = next(r for r in changed['rows'] if r['kind']=='tool_request')
            tool['tool_name']['visibility'] = visibility
            assert core._leaf(required(),changed)[0] is None
        with pytest.raises(ValueError):
            changed = deepcopy(data); changed['rows'].append(deepcopy(row)); core._leaf(required(),changed)
        with pytest.raises(ValueError):
            changed = deepcopy(data); next(r for r in changed['rows'] if r['kind']=='tool_request')['dispatch_ordinal'] = True
            core._leaf(required(),changed)
        _, capture = self.role(binding,[[fixtures.message()|dict(content=[dict(type='output_text',text='run /usr/bin/true')])]],trial='name-text')
        assert core.evaluate(binding,capture)['status'] == 'violated'

    def test_unavailable_scope_identity_and_missing_artifact_fields_do_not_pass(self):
        binding = self.bind([required()])
        client,_ = self.f.client([[fixtures.message()]])
        with pytest.raises(ValueError, match='scope identity'):
            responses.run_role(client,self.f.backend(),self.f.root/'unused',{},'executor','synthetic','synthetic',
                self.f.deadline,trial_id='SYNTHETIC_CONTROLLER_KEY',trajectory=binding)
        for value in (None,[],{},dict(policy=None)):
            assert not core.evaluate(value,{})['valid']

    @pytest.mark.parametrize('change', (
        pytest.param(dict(inputs_unchanged=False), id='inputs-changed'),
        pytest.param(dict(timeout=True), id='timeout'),
        pytest.param(dict(exit_code=True), id='boolean-exit-code'),
        pytest.param(dict(stdout='{}'), id='empty-stdout-object')))
    def test_missing_artifact_fields_do_not_pass(self, change):
        binding = self.bind([required()])
        _, capture = self.role(binding); receipt = core.evaluate(binding,capture)
        broken = deepcopy(self.grade()); broken['execution'].update(change)
        assert core.compose(binding,broken,receipt)['outcome'] is None

    def test_clipped_selected_argument_is_unknown_while_unselected_index_can_match(self):
        argv = ['safe','SYNTHETIC_CONTROLLER_KEY'+'x'*(4096-len('SYNTHETIC_CONTROLLER_KEY'))]
        rules = [required(arguments=dict(mode='partial',argv={'0':'safe'})),
                 required(arguments=dict(mode='partial',argv={'1':'SYNTHETIC_CONTROLLER_KEY'})),
                 required(arguments=dict(mode='partial',argv={'99':'missing'}))]
        binding = self.bind(rules)
        _, capture = self.role(binding,[[fixtures.call(arguments=json.dumps(dict(argv=argv)))],[fixtures.message()]])
        assert [r['status'] for r in core.evaluate(binding,capture)['assertions']] == ['satisfied','unknown','violated']

    @pytest.mark.parametrize('rule', INVALID_RULES)
    def test_configuration_depth_node_size_and_unknown_modes_are_bounded(self, rule):
        self.bind([required()])
        value = deepcopy(self.policy); value['assertions'][0]['rule'] = rule
        (self.bundle/'trajectory.json').write_bytes(encoded(value))
        with pytest.raises(ValueError): core.capture_policy(self.bundle,digest(self.bundle),self.task)

    def test_oversized_policy_file_is_rejected(self):
        self.bind([required()])
        (self.bundle/'trajectory.json').write_bytes(b' '*65537)
        with pytest.raises(ValueError): core.capture_policy(self.bundle,digest(self.bundle),self.task)
