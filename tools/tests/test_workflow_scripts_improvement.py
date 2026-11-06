"""workflow_contract_v1 stages Claude workflow scripts under the executable contract; synthetic only, nothing runs."""
import json
from pathlib import Path
import tempfile
import unittest

from agent_harness_lab import surfaces
from agent_harness_lab.harness_change_plan import HarnessChangePlan, REFERENCE_NAMES
from agent_harness_lab.hooks_contract import (ARGS_BOUND, CAP_VARIABLE, MAX_AGENTS, OUTCOMES, PROFILE, WORKFLOW_PROFILE,
                                              contracts_for, evaluate, outcome, stage, stage_workflows, trial_request)
from agent_harness_lab.one_call import deferred_surfaces
from agent_harness_lab.promotion.journal import Journal, snapshot_record
from test_hooks_contract import hooks, no_execution

NAME = '.claude/workflows/review.js'
BEFORE = b"export const meta = { name: 'review', description: 'Synthetic review' }\nawait agent('scan')\n"
CLEAN = (b"export const meta = { name: 'review', description: 'Synthetic review', phases: ['scan'] }\n"
         b"phase('scan')\nawait agent('scan the fixture')\n")
META_LATE = b"const limit = 3\n" + CLEAN
FORBIDDEN = {'forbidden_math_random': CLEAN + b'const seed = Math.random()\n',
             'forbidden_date_now': CLEAN + b'const at = Date.now()\n',
             'forbidden_dynamic_import': CLEAN + b"const fs = await import('node:fs')\n",
             'forbidden_new_date_no_args': CLEAN + b'const day = new Date()\n'}


def codes(contract):
    return {row[1] for row in contract.findings}


class WorkflowContractTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.project = self.root / 'project'
        (self.project / '.claude/workflows').mkdir(parents=True)
        self.script = self.project / NAME; self.script.write_bytes(BEFORE); self.script.chmod(0o644)
        self.journal = Journal(self.project, self.root / 'private/journal')

    def unit(self, content=CLEAN, mode=0o644, name=NAME):
        before, after = stage_workflows(self.project, {name: (content, mode)})
        return before, after, evaluate(self.project, before, after)

    def trial(self, label, content):
        """An I13-style trial copy outside the project: trial/workspace holding the staged bytes at their path."""
        workspace = self.root / 'trials' / label / 'workspace'
        (workspace / NAME).parent.mkdir(parents=True)
        (workspace / NAME).write_bytes(content); (workspace / NAME).chmod(0o644)
        return workspace.parent

    def test_meta_not_first_is_blocked_before_any_run(self):
        with no_execution():
            before, after, contract = self.unit(META_LATE)
            self.assertEqual((contract.profile, contract.status), (WORKFLOW_PROFILE, 'blocked'))
            self.assertIn(('blocked', 'meta_not_first_literal', NAME), contract.findings)
            with self.assertRaisesRegex(ValueError, 'did not pass'):
                self.journal.prepare(before, after, hook_contract=contract)
            # No run request can be made for a blocked unit, so nothing reaches a trial workspace.
            with self.assertRaisesRegex(ValueError, 'only a passed workflow contract'):
                trial_request(contract, 'candidate', self.project, self.trial('late', META_LATE), {})
            result = outcome(contract, before, after)
        self.assertEqual((result['status'], result['reason']), ('blocked', 'static_contract_blocked'))
        self.assertEqual(self.script.read_bytes(), BEFORE)

    def test_forbidden_tokens_inconclusive_lint_and_unsafe_modes_are_hard_gates(self):
        cases = dict(FORBIDDEN, workflow_lint_inconclusive=CLEAN + b'const text = `unterminated ${x}\n')
        for code, content in cases.items():
            with self.subTest(code=code):
                _, _, contract = self.unit(content)
                self.assertEqual(contract.status, 'blocked')
                self.assertIn(code, codes(contract))
        self.assertIn('script_group_or_world_writable', codes(self.unit(mode=0o664)[2]))
        before, _, _ = self.unit()
        after = {NAME: None}
        self.assertIn('workflow_removal_outside_contract', codes(evaluate(self.project, before, after)))
        (self.project / '.claude/workflows/nested').mkdir()
        with self.assertRaisesRegex(ValueError, 'not a Claude workflow'):
            stage_workflows(self.project, {'.claude/workflows/nested/deep.js': (CLEAN, 0o644)})
        nested = self.project / 'pkg'; (nested / '.git').mkdir(parents=True)
        self.assertIn('script_outside_checkout', codes(self.unit(name='pkg/.claude/workflows/review.js')[2]))

    def test_passed_unit_is_admitted_as_one_deployment_and_rolls_back(self):
        with no_execution():
            before, after, contract = self.unit()
            self.assertEqual((contract.status, contract.findings, contract.behavioral), ('passed', (), 'inconclusive'))
            document = contract.document()
            self.assertEqual((document['executed'], document['lint'], document['codex']),
                             (False, 'static_lint_not_runtime_validation', 'no_workflow_loader_in_pinned_source'))
            record = self.journal.prepare(before, after, {'source': 'synthetic'}, hook_contract=contract)
            self.assertEqual(record['metadata']['hook_contract']['profile'], WORKFLOW_PROFILE)
            self.journal.execute(record['deployment'])
            self.assertEqual(self.script.read_bytes(), CLEAN)
            self.journal.rollback(record['deployment'])
        self.assertEqual(self.script.read_bytes(), BEFORE)
        # Without a passed contract the journal still refuses a workflow write, as before this contract existed.
        with self.assertRaisesRegex(ValueError, 'owned harness surface'):
            self.journal.prepare(before, after, {'source': 'synthetic'})
        # Admission binds the bytes: a forged after snapshot is refused before anything is written.
        record = self.journal.prepare(before, after, hook_contract=contract)
        path = self.journal.folder / (record['deployment'] + '.json')
        stored = json.loads(path.read_text())
        stored['after'][NAME] = snapshot_record(FORBIDDEN['forbidden_math_random'], 0o644)
        path.write_text(json.dumps(stored))
        with self.assertRaisesRegex(ValueError, 'does not bind|did not pass|not authenticated'):
            self.journal.execute(record['deployment'])
        self.assertEqual(self.script.read_bytes(), BEFORE)

    def test_analysis_never_executes_a_workflow(self):
        sentinel = self.root / 'executed'
        # Were this script ever run by node or the runtime, it would leave the sentinel behind.
        live = CLEAN + f"require('fs').writeFileSync({json.dumps(str(sentinel))}, 'ran')\n".encode()
        with no_execution():
            before, after, contract = self.unit(live)
            self.assertEqual(contract.status, 'passed')
            (plan,) = contracts_for(self.project, before, after)
            self.assertEqual(plan, contract)
            request = trial_request(contract, 'candidate', self.project, self.trial('live', live), {'target': 'x'})
            result = outcome(contract, before, after)
        self.assertFalse(sentinel.exists())
        self.assertIs(request['executed'], False)
        self.assertEqual(request['invocation'], 'caller_runner_unverified')
        self.assertFalse(result['executed_by_analysis'])

    def test_trial_request_runs_only_inside_a_bounded_trial_workspace(self):
        before, after, contract = self.unit()
        trial = self.trial('candidate', CLEAN)
        request = trial_request(contract, 'candidate', self.project, trial, {'files': ['a.py']})
        self.assertEqual(request['environment'], {CAP_VARIABLE: str(MAX_AGENTS)})
        self.assertEqual(request['cwd'], str((trial / 'workspace').resolve()))
        self.assertEqual(request['cap_capability']['version_gate'], '>=2.1.269')
        self.assertEqual(trial_request(contract, 'candidate', self.project, trial, {}, max_agents=1)['environment'],
                         {CAP_VARIABLE: '1'})
        for cap in (0, MAX_AGENTS + 1, True):
            with self.assertRaisesRegex(ValueError, 'agent cap'):
                trial_request(contract, 'candidate', self.project, trial, {}, max_agents=cap)
        with self.assertRaisesRegex(ValueError, 'args fixture'):
            trial_request(contract, 'candidate', self.project, trial, {'blob': 'x' * ARGS_BOUND})
        # The project itself, or a trial nested in it, would load the project's own .claude/workflows/.
        (self.project / 'workspace').mkdir()
        inside = self.project / 'artifacts/trial'; (inside / 'workspace' / NAME).parent.mkdir(parents=True)
        (inside / 'workspace' / NAME).write_bytes(CLEAN)
        for place in (self.project, inside):
            with self.assertRaisesRegex(ValueError, 'outside the project'):
                trial_request(contract, 'candidate', self.project, place, {})
        with self.assertRaisesRegex(ValueError, 'unit bytes'):
            trial_request(contract, 'baseline', self.project, trial, {})
        with self.assertRaisesRegex(ValueError, 'trial workspace'):
            trial_request(contract, 'candidate', self.project, self.root / 'missing', {})

    def test_without_a_native_run_the_result_is_never_improved(self):
        before, after, contract = self.unit()
        result = outcome(contract, before, after)
        self.assertEqual((result['status'], result['reason']), ('recommendation_only', 'no_native_trial_run'))
        self.assertIn('-await agent(\'scan\')\n', result['patch'])
        self.assertTrue(result['patch'].startswith('--- a/' + NAME))
        baseline = trial_request(contract, 'baseline', self.project, self.trial('baseline', BEFORE), {'n': 1})
        candidate = trial_request(contract, 'candidate', self.project, self.trial('candidate', CLEAN), {'n': 1})
        forged = dict(candidate, environment={CAP_VARIABLE: '64'}, max_concurrent_agents=64)
        other_args = trial_request(contract, 'candidate', self.project, self.trial('other', CLEAN), {'n': 2})
        malformed = {'ungraded': {'baseline': (baseline, 'failed'), 'candidate': (candidate, None)},
                     'one side': {'candidate': (candidate, 'passed')},
                     'sides swapped': {'baseline': (candidate, 'failed'), 'candidate': (baseline, 'passed')},
                     'uncapped': {'baseline': (baseline, 'failed'), 'candidate': (forged, 'passed')},
                     'different fixtures': {'baseline': (baseline, 'failed'), 'candidate': (other_args, 'passed')},
                     'not a request': {'baseline': ({}, 'failed'), 'candidate': ({}, 'passed')}}
        for label, runs in malformed.items():
            with self.subTest(runs=label):
                self.assertEqual(outcome(contract, before, after, runs)['status'], 'inconclusive')
        _, _, other = self.unit(CLEAN + b'// another candidate\n')
        stale = {'baseline': (baseline, 'failed'), 'candidate': (candidate, 'passed')}
        self.assertEqual(outcome(other, *stage_workflows(self.project, {NAME: (CLEAN + b'// another candidate\n', 0o644)}),
                                 stale)['status'], 'inconclusive')
        self.assertEqual(outcome(contract, before, after, stale)['status'], 'improved')
        self.assertEqual(outcome(contract, before, after, {'baseline': (baseline, 'passed'),
                                                           'candidate': (candidate, 'passed')})['status'], 'not_improved')
        self.assertEqual(set(OUTCOMES), {'blocked', 'recommendation_only', 'inconclusive', 'not_improved', 'improved'})

    def test_plan_resolves_workflow_operations_only_through_a_passed_contract(self):
        _, after, passed = self.unit()
        _, _, blocked = self.unit(META_LATE)
        states = {name: (row['sha256'], row['mode']) for name, row in after.items()}
        options = dict(references={name: None for name in REFERENCE_NAMES}, diagnosis=[], falsifiers=[], dependencies=[],
                       controls=[])
        bare = HarnessChangePlan.create({}, states, deferred=deferred_surfaces(()), **options)
        self.assertEqual(bare.unresolved_operations, (NAME,))
        held = HarnessChangePlan.create({}, states, deferred=deferred_surfaces((blocked,)), contracts=(blocked,), **options)
        self.assertEqual(held.unresolved_operations, (NAME,))
        resolved = HarnessChangePlan.create({}, states, deferred=deferred_surfaces((passed,)), contracts=(passed,), **options)
        self.assertEqual(resolved.unresolved_operations, ())
        self.assertEqual(HarnessChangePlan.parse(resolved.document()), resolved)
        # A workflow unit is not a hook unit, so it never lifts the deferred 'hooks' surface.
        self.assertIn('hooks', resolved.deferred_surfaces)

    def test_mixed_hook_and_workflow_changes_are_separate_units(self):
        settings = self.project / '.claude/settings.json'; settings.write_bytes(b'{}\n')
        hook_script = '.claude/hooks/check.sh'
        hook_before, hook_after = stage(self.project, '.claude/settings.json', hooks(), {hook_script: (b'#!/bin/sh\n', 0o755)})
        before, after = stage_workflows(self.project, {NAME: (CLEAN, 0o644)})
        before, after = dict(before, **hook_before), dict(after, **hook_after)
        units = contracts_for(self.project, before, after)
        self.assertEqual([(unit.profile, unit.members) for unit in units],
                         [(PROFILE, ('.claude/hooks/check.sh', '.claude/settings.json')), (WORKFLOW_PROFILE, (NAME,))])
        self.assertEqual([unit.status for unit in units], ['passed', 'passed'])
        # One transaction that mixes both is no single unit and stays blocked.
        self.assertEqual(evaluate(self.project, before, after).status, 'blocked')
        self.assertEqual(evaluate(self.project, before, after).profile, PROFILE)

    def test_codex_has_no_workflow_equivalent(self):
        self.assertEqual(surfaces.classify(NAME, 'codex').writability, 'unsupported')
        row = next(row for row in surfaces.CAPABILITIES if (row.surface, row.runtime) == ('workflows', 'codex'))
        self.assertEqual(row.item, 'no workflow script loader in the pinned source')
        _, _, contract = self.unit()
        self.assertEqual(contract.runtime, 'claude-code')
        self.assertEqual(contract.document()['codex'], 'no_workflow_loader_in_pinned_source')

    def test_hook_contract_documents_keep_their_identity(self):
        settings = self.project / '.claude/settings.json'; settings.write_bytes(b'{}\n')
        before, after = stage(self.project, '.claude/settings.json', hooks(), {'.claude/hooks/check.sh': (b'#!/bin/sh\n', 0o755)})
        document = evaluate(self.project, before, after).document()
        self.assertEqual(document['profile'], PROFILE)
        self.assertNotIn('lint', document)
        self.assertNotIn('codex', document)


if __name__ == '__main__':
    unittest.main()
