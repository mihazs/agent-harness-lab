"""Native loading evidence joined to a synthetic inventory; invented checkout and transcript, no real session."""
import json
from pathlib import Path
import tempfile
import unittest
from agent_harness_lab.sources import harness_inventory as inventory
from agent_harness_lab.sources.schema import digest
from agent_harness_lab.telemetry import claude_native
from agent_harness_lab.telemetry.schema import verified_outcome
from tools.tests import harness_surface_fixture as fixture

CLAUDE = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='.')
RULE = '---\npaths: ["src/**"]\n---\nSYNTHETIC_RULE_TEXT\n'
AGENT = '---\nname: reviewer\ndescription: invented reviewer\n---\nSYNTHETIC_AGENT_TEXT\n'


class LoadingJoinTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='ahl-join-'); self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.checkout = fixture.build(self.base / 'checkout', {'.claude/rules/python.md': RULE,
            '.claude/agents/reviewer.md': AGENT}, hostile=False)
        self.transcript = self.base / 'session.jsonl'
        self.rows = []

    def row(self, ident, **extra):
        return dict(uuid=ident, parentUuid=None, timestamp='2026-01-01T00:00:00Z', sessionId='synthetic-session',
            cwd=str(self.checkout), version='2.1.316', isSidechain=False, **extra)

    def attach(self, ident, relative, text=None, kind='nested_memory'):
        path = str(self.checkout / relative) if not relative.startswith('/') else relative
        attachment = dict(type=kind, path=path)
        if text is not None: attachment['content'] = dict(path=path, type='Project', content=text)
        self.rows.append(self.row(ident, type='attachment', attachment=attachment))

    def call(self, ident, name, arguments):
        self.rows.append(self.row(ident, type='assistant', message=dict(role='assistant', content=[
            dict(type='tool_use', id='call-' + ident, name=name, input=arguments)])))

    def join(self, profile=CLAUDE, events=True):
        captured = inventory.capture(self.checkout, profile)
        if events is not True: return captured, inventory.join_loading(captured, events)
        self.transcript.write_text(''.join(json.dumps(row) + '\n' for row in self.rows))
        rows, _, _ = claude_native.read(self.transcript, self.checkout)
        return captured, inventory.join_loading(captured, rows)

    def node(self, captured, joined, path):
        index = next(i for i, node in enumerate(captured.nodes) if node.path == path)
        return joined['nodes'][index]

    def test_memory_and_rule_attachments_join_their_nodes_and_absent_paths_stay_unattributed(self):
        self.attach('m', 'CLAUDE.md', fixture.FILES['CLAUDE.md'])
        self.attach('r', '.claude/rules/python.md', RULE)
        self.attach('x', 'missing/CLAUDE.md', 'SYNTHETIC_ABSENT')
        self.attach('u', '/elsewhere/CLAUDE.md', 'SYNTHETIC_OUTSIDE')
        captured, joined = self.join()
        self.assertEqual(joined['status'], 'joined')
        for path, kind in (('CLAUDE.md', 'instruction'), ('.claude/rules/python.md', 'path_rule')):
            row = self.node(captured, joined, path)
            self.assertEqual((row['kind'], row['observed_loading'], row['effective'], row['exact_attachments']),
                (kind, 'observed_pattern', 'unknown', 1))
        self.assertEqual(len(joined['nodes']), len(captured.nodes))
        self.assertEqual(joined['unattributed']['attachment'], 2)
        self.assertEqual(sum(row['observed_loading'] == 'observed_pattern' for row in joined['nodes']), 2)
        self.assertEqual((joined['outcome'], joined['independence'], joined['effective']), ('unknown',) * 3)
        self.assertEqual(joined['version_alignment'], 'unverified')
        self.assertTrue(all(node.observed_loading == 'unknown' for node in captured.nodes))
        text = json.dumps(joined)
        for secret in ('SYNTHETIC', str(self.checkout), 'CLAUDE.md'): self.assertNotIn(secret, text)

    def test_path_without_matching_content_is_path_only_never_observed(self):
        self.attach('m', 'CLAUDE.md', 'SYNTHETIC_EDITED_TEXT')
        self.attach('n', 'CLAUDE.md')
        captured, joined = self.join()
        row = self.node(captured, joined, 'CLAUDE.md')
        self.assertEqual((row['observed_loading'], row['path_only_attachments'], row['exact_attachments']), ('unknown', 2, 0))

    def test_agent_hook_and_mcp_signals_never_set_observed_loading(self):
        self.call('a', 'Agent', dict(subagent_type='reviewer', prompt='SYNTHETIC'))
        self.call('t', 'Task', dict(subagent_type='not-declared'))
        self.call('m', 'mcp__synthetic-server__lookup', dict(query='SYNTHETIC'))
        self.rows.append(self.row('h', type='attachment', attachment=dict(type='hook_success', hookName='SYNTHETIC')))
        captured, joined = self.join()
        agent = self.node(captured, joined, '.claude/agents/reviewer.md')
        self.assertEqual((agent['declared_invocations'], agent['observed_loading']), (1, 'unknown'))
        self.assertEqual(joined['unattributed'], dict(attachment=0, hook_output=1, agent=1, mcp=1))
        self.assertFalse(any(row['observed_loading'] != 'unknown' for row in joined['nodes']))

    def test_missing_capture_codex_and_foreign_events_leave_every_node_unknown(self):
        captured, joined = self.join(events=None)
        self.assertEqual(joined['status'], 'no_capture')
        self.assertTrue(all(row['observed_loading'] == row['effective'] == 'unknown' for row in joined['nodes']))
        codex = dict(CLAUDE, runtime='codex', version='0.166.0')
        _, joined = self.join(codex, events=[])
        self.assertEqual((joined['status'], joined['capture_version']), ('no_native_loading_signal', None))
        forged = dict(case_id='c', event_id='e', activity='context', source_version='claude-jsonl-v1',
            loading_signal='observed_attachment', loading_path_sha256=digest('CLAUDE.md'),
            loading_content_sha256=captured.nodes[0].content_sha256, outcome='success', outcome_provenance='native_field')
        _, joined = self.join(events=[forged])
        self.assertEqual(joined['ignored_records'], 1)
        self.assertTrue(all(row['observed_loading'] == 'unknown' for row in joined['nodes']))
        self.assertIsNone(verified_outcome(forged))
        _, joined = self.join(dict(CLAUDE, task_cwd='src/task'), events=[])
        self.assertEqual(joined['status'], 'task_cwd_not_checkout_root')
        with self.assertRaises(ValueError): inventory.join_loading(dict(nodes=()), [])


if __name__ == '__main__': unittest.main()
