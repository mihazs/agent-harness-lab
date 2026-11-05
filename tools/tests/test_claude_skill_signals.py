"""Declared skill signals in synthetic Claude 2.1.316 captures; no real sessions, CLI or skills."""
import json
from pathlib import Path
import tempfile
import unittest
if __package__:
    from . import claude_skill_fixture as fixture
else:
    import claude_skill_fixture as fixture
from agent_harness_lab.evaluation.skills import activation
from agent_harness_lab.evidence import collect
from agent_harness_lab.telemetry.adapters import CAPABILITIES
from agent_harness_lab.telemetry.claude_discovery import capture, directory_names
from agent_harness_lab.telemetry.claude_native import CAPABILITY, VERSION, read, skill_fingerprint
from agent_harness_lab.telemetry.schema import verified_outcome

NAME = 'SYNTHETIC_SKILL_NAME'
SKILL_FIELDS = ('skill_signal', 'skill_id', 'skill_hash', 'skill_signal_confidence')
SESSION = '00000000-0000-4000-8000-0000000000aa'


class ClaudeSkillSignalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.path = self.root/'session.jsonl'

    def capture(self, rows, project=fixture.PROJECT):
        fixture.write(self.path, rows); return read(self.path, project)

    def skill_rows(self, project=fixture.PROJECT):
        bundle = project + '/{}/' + NAME + '/SKILL.md'
        calls = [('Skill', dict(skill=NAME, args='SYNTHETIC_SECRET')), ('Skill', dict(skill='plugin:'+NAME)),
            ('Read', dict(file_path=bundle.format('.claude/skills'))), ('Read', dict(file_path=bundle.format('.agents/skills'), offset=1, limit=2)),
            ('Read', dict(file_path=bundle.format('skills'), pages='1')), ('Skill', dict(skill='')), ('Skill', 'SYNTHETIC_SECRET')]
        rows, parent = fixture.rows(project), 'odd'
        for index, (tool, arguments) in enumerate(calls):
            rows.append(fixture.call(f's{index}', parent, tool, arguments, f'skill-call-{index}', project))
            rows.append(fixture.result(f'sr{index}', f's{index}', f'skill-call-{index}', error=index == 2, project=project))
            parent = f'sr{index}'
        return rows

    def test_captures_without_skill_evidence_keep_the_pre_change_projection(self):
        events, summary, _ = self.capture(fixture.rows())
        self.assertEqual(fixture.projection_sha256(events), fixture.BASELINE_SHA256)
        self.assertFalse(any(key in event for event in events for key in SKILL_FIELDS))
        # The capability is a module contract; receipts and pinned summaries gain no keys.
        self.assertFalse(any(key.startswith('skill') for key in summary))

    def test_declared_invocations_and_exact_bundle_reads_are_content_free_signals(self):
        events, _, _ = self.capture(self.skill_rows())
        signals = [(e['tool_name'], e['skill_signal'], e['skill_id'], e['skill_signal_confidence']) for e in events if 'skill_signal' in e]
        name, plugin = skill_fingerprint(NAME), skill_fingerprint('plugin:'+NAME)
        self.assertEqual(signals, [('Skill', 'declared_invocation', name, 'declared_name'), ('Skill', 'declared_invocation', plugin, 'declared_name'),
            ('Read', 'file_read', name, 'full_read_request'), ('Read', 'file_read', name, 'partial_read_request'),
            ('Read', 'file_read', name, 'partial_read_request')])
        self.assertTrue(all(e['activity'] == 'tool_call' and 'skill_hash' not in e for e in events if 'skill_signal' in e))
        self.assertFalse(any('skill_signal' in e for e in events if e['activity'] != 'tool_call'))
        # A malformed or unnamed Skill call stays a plain call: the skill it refers to is unknown.
        unnamed = [e for e in events if e['activity'] == 'tool_call' and e.get('tool_name') == 'Skill' and 'skill_signal' not in e]
        self.assertEqual(len(unnamed), 2)
        text = json.dumps(events)
        for private in (NAME, fixture.PROJECT, 'SYNTHETIC_SECRET', 'SKILL.md', 'skills'): self.assertNotIn(private, text)

    def test_skill_evidence_never_changes_identities_or_becomes_activation_or_outcome(self):
        with_skill, summary, _ = self.capture(self.skill_rows())
        without, _, _ = self.capture(self.strip(self.skill_rows()))
        self.assertEqual([(e['case_id'], e['event_id'], e.get('call_id')) for e in with_skill],
                         [(e['case_id'], e['event_id'], e.get('call_id')) for e in without])
        self.assertEqual([{k: v for k, v in e.items() if k not in SKILL_FIELDS} for e in with_skill], list(without))
        for event in with_skill:
            self.assertIsNone(verified_outcome(event)); self.assertEqual(event['outcome'], 'unknown')
            if 'skill_signal' in event:
                state = activation(signal=event['skill_signal'], provenance='native_field', evidence_ref=event['event_id'])
                self.assertIsNone(state['activated']); self.assertEqual(state['missing_reason'], 'runtime_activation_unobserved')
        self.assertFalse(any(key.startswith('skill') for key in summary))
        self.assertEqual((CAPABILITY['skill_activation'], CAPABILITY['skill_signal']), (False, 'observed_declared'))
        self.assertEqual([CAPABILITIES[name]['skill_activation'] for name in ('codex', 'claude')], [False, False])
        self.assertNotIn('skill_signal', CAPABILITIES['codex'])

    def test_bundle_reads_outside_the_selected_project_or_forged_labels_are_not_signals(self):
        rows = self.skill_rows('/synthetic-ahl/elsewhere')
        events, _, _ = self.capture(rows, '/synthetic-ahl/elsewhere')
        self.assertEqual(sum(e.get('skill_signal') == 'file_read' for e in events), 3)
        moved = [dict(r, cwd=fixture.PROJECT) for r in rows]
        events, _, _ = self.capture(moved)
        self.assertEqual({e['skill_signal'] for e in events if 'skill_signal' in e}, {'declared_invocation'})
        forged = fixture.rows()
        forged[1].update(skill_signal='activated', skill_id='SYNTHETIC_SECRET', skill_signal_confidence='verified')
        forged[1]['message']['content'][0].update(skill_signal='activated')
        events, _, _ = self.capture(forged)
        self.assertEqual(fixture.projection_sha256(events), fixture.BASELINE_SHA256)

    def strip(self, rows):
        for row in rows:
            for block in row.get('message', {}).get('content', ()):
                if isinstance(block, dict) and block.get('type') == 'tool_use': block['input'] = {}
        return rows

    def test_explicit_collection_keeps_its_fingerprint_and_the_root_profile_maps_signals(self):
        project = self.root/'project'; project.mkdir(); label = str(project.resolve())
        def collected(rows):
            fixture.write(self.path, rows)
            return collect(project, events=self.path, source_format='claude', source_version=VERSION)
        plain = collected(fixture.rows(label))
        self.assertEqual(plain['fingerprint'], collected(self.strip(fixture.rows(label)))['fingerprint'])
        signalled = collected(self.skill_rows(label))
        self.assertNotEqual(signalled['fingerprint'], plain['fingerprint'])
        self.assertEqual(signalled['evaluation_outcomes'], []); self.assertNotIn(NAME, json.dumps(signalled))
        projects = self.root/'claude/projects'; folder = projects/directory_names(project.resolve())[0]; folder.mkdir(parents=True)
        fixture.write(folder/(SESSION+'.jsonl'), [dict(r, sessionId=SESSION) for r in self.skill_rows(label)])
        with capture([projects], project.resolve()) as (events, summary, _sha256, _folder, _sessions):
            self.assertEqual(sum('skill_signal' in e for e in events), 5)
            self.assertFalse(any(key.startswith('skill') for key in summary))

if __name__ == '__main__':
    unittest.main()
