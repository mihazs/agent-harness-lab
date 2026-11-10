"""Native loading evidence joined to a synthetic inventory; invented checkout and transcript, no real session."""
import json

import pytest

from agent_harness_lab.sources import harness_inventory as inventory
from agent_harness_lab.sources.schema import digest
from agent_harness_lab.telemetry import claude_native
from agent_harness_lab.telemetry.schema import SOURCE_ORDER, verified_outcome

from tests.support import harness_surface_fixture as fixture

CLAUDE = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='.')
RULE = '---\npaths: ["src/**"]\n---\nSYNTHETIC_RULE_TEXT\n'
AGENT = '---\nname: reviewer\ndescription: invented reviewer\n---\nSYNTHETIC_AGENT_TEXT\n'


SKIPPED_AGENT = '---\nname: reviewer\n---\nSYNTHETIC_NO_DESCRIPTION\n'
MAIN_AGENT = '.claude/agents/reviewer.md'


class TestLoadingJoin:
    @pytest.fixture(autouse=True)
    def _checkout(self, tmp_path):
        self.base = tmp_path.resolve()
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

    def read(self):
        self.transcript.write_text(''.join(json.dumps(row) + '\n' for row in self.rows))
        rows, _, _ = claude_native.read(self.transcript, self.checkout)
        return rows

    def join(self, profile=CLAUDE, events=True):
        captured = inventory.capture(self.checkout, profile)
        return captured, inventory.join_loading(captured, self.read() if events is True else events)

    def node(self, captured, joined, path):
        index = next(i for i, node in enumerate(captured.nodes) if node.path == path)
        return joined['nodes'][index]

    def test_memory_and_rule_attachments_join_their_nodes_and_absent_paths_stay_unattributed(self):
        self.attach('m', 'CLAUDE.md', fixture.FILES['CLAUDE.md'])
        self.attach('r', '.claude/rules/python.md', RULE)
        self.attach('x', 'missing/CLAUDE.md', 'SYNTHETIC_ABSENT')
        self.attach('u', '/elsewhere/CLAUDE.md', 'SYNTHETIC_OUTSIDE')
        captured, joined = self.join()
        assert joined['status'] == 'joined'
        for path, kind in (('CLAUDE.md', 'instruction'), ('.claude/rules/python.md', 'path_rule')):
            row = self.node(captured, joined, path)
            assert (row['kind'], row['observed_loading'], row['effective'], row['exact_attachments']) == (kind, 'observed_pattern', 'unknown', 1)
        assert len(joined['nodes']) == len(captured.nodes)
        assert joined['unattributed']['attachment'] == 2
        assert sum(row['observed_loading'] == 'observed_pattern' for row in joined['nodes']) == 2
        assert (joined['outcome'], joined['independence'], joined['effective']) == ('unknown',) * 3
        assert joined['version_alignment'] == 'unverified'
        assert (joined['loading_evidence'], joined['attachment_shapes']) == ('observed_pattern', 'unverified_fixture_only')
        assert (joined['duplicate_events'], joined['warnings']) == (0, [])
        assert all(node.observed_loading == 'unknown' for node in captured.nodes)
        text = json.dumps(joined)
        for secret in ('SYNTHETIC', str(self.checkout), 'CLAUDE.md'): assert secret not in text

    def test_path_without_matching_content_is_path_only_never_observed(self):
        self.attach('m', 'CLAUDE.md', 'SYNTHETIC_EDITED_TEXT')
        self.attach('n', 'CLAUDE.md')
        captured, joined = self.join()
        row = self.node(captured, joined, 'CLAUDE.md')
        assert (row['observed_loading'], row['path_only_attachments'], row['exact_attachments']) == ('unknown', 2, 0)

    def test_join_without_matched_evidence_reports_no_observed_pattern(self):
        _, joined = self.join(events=[])
        assert joined['status'] == 'joined'
        assert (joined['loading_evidence'], joined['attachment_shapes'], joined['duplicate_events']) == ('none', 'none', 0)
        self.attach('n', 'CLAUDE.md', 'SYNTHETIC_EDITED_TEXT')
        self.call('a', 'Agent', dict(subagent_type='reviewer'))
        _, joined = self.join()
        assert (joined['loading_evidence'], joined['attachment_shapes']) == ('none', 'unverified_fixture_only')
        _, joined = self.join(events=None)
        assert (joined['status'], joined['loading_evidence'], joined['attachment_shapes']) == ('no_capture', 'none', 'none')

    def test_a_capture_read_twice_counts_each_event_once_and_warns(self):
        self.attach('m', 'CLAUDE.md', fixture.FILES['CLAUDE.md'])
        self.attach('x', 'missing/CLAUDE.md', 'SYNTHETIC_ABSENT')
        self.call('a', 'Agent', dict(subagent_type='reviewer'))
        captured, once = self.join()
        rows = self.read()
        _, twice = self.join(events=rows + rows)
        assert (twice['nodes'], twice['unattributed']) == (once['nodes'], once['unattributed'])
        assert self.node(captured, twice, 'CLAUDE.md')['exact_attachments'] == 1
        loading = sum('loading_signal' in row for row in rows)
        assert loading == 3
        assert (twice['duplicate_events'], twice['warnings']) == (loading, ['duplicate_event_identity'])
        first = next(row for row in rows if row.get('loading_path_sha256') == digest('CLAUDE.md'))
        forged = dict(first, loading_content_sha256=digest('SYNTHETIC_OTHER'))
        results = [self.join(events=order)[1] for order in ([first, forged], [forged, first], [first, forged, first])]
        assert len({json.dumps(result, sort_keys=True, default=list) for result in results[:2]}) == 1
        for joined in results:
            row = self.node(captured, joined, 'CLAUDE.md')
            assert (row['observed_loading'], row['exact_attachments'], row['path_only_attachments']) == ('unknown', 0, 0)
            assert (joined['loading_evidence'], joined['attachment_shapes'], joined['conflicting_events']) == ('none', 'none', 1)
            assert joined['warnings'] == ['duplicate_event_conflict', 'duplicate_event_identity']
        assert [joined['duplicate_events'] for joined in results] == [1, 1, 2]
        assert once['conflicting_events'] == 0
        foreign = {key: value for key, value in first.items() if key not in ('capture_order', *SOURCE_ORDER)}
        foreign['source_version'] = 'claude-jsonl-v1'
        _, joined = self.join(events=[foreign, first])
        assert self.node(captured, joined, 'CLAUDE.md')['exact_attachments'] == 1
        assert ((joined['ignored_records'], joined['loading_evidence'], joined['duplicate_events'],
            joined['warnings']) == (1, 'observed_pattern', 0, []))

    def test_one_attachment_at_different_lines_of_two_reads_is_one_exact_attachment(self):
        self.attach('m', 'CLAUDE.md', fixture.FILES['CLAUDE.md'])
        first = self.read()
        self.rows.insert(0, self.row('u0', type='user', message=dict(role='user', content='SYNTHETIC_PROMPT')))
        second = self.read()
        pick = lambda rows: next(row for row in rows if 'loading_signal' in row)
        assert sorted(key for key in pick(first) if pick(first)[key] != pick(second).get(key)) == ['capture_order']
        captured, joined = self.join(events=first + second)
        assert self.node(captured, joined, 'CLAUDE.md')['exact_attachments'] == 1
        assert ((joined['loading_evidence'], joined['conflicting_events'], joined['duplicate_events'],
            joined['warnings']) == ('observed_pattern', 0, 1, ['duplicate_event_identity']))

    def test_distinct_events_of_one_file_are_each_counted(self):
        self.attach('m1', 'CLAUDE.md', fixture.FILES['CLAUDE.md'])
        self.attach('m2', 'CLAUDE.md', fixture.FILES['CLAUDE.md'])
        captured, joined = self.join()
        assert self.node(captured, joined, 'CLAUDE.md')['exact_attachments'] == 2
        assert (joined['duplicate_events'], joined['warnings']) == (0, [])

    def test_agent_hook_and_mcp_signals_never_set_observed_loading(self):
        self.call('a', 'Agent', dict(subagent_type='reviewer', prompt='SYNTHETIC'))
        self.call('t', 'Task', dict(subagent_type='not-declared'))
        self.call('m', 'mcp__synthetic-server__lookup', dict(query='SYNTHETIC'))
        self.rows.append(self.row('h', type='attachment', attachment=dict(type='hook_success', hookName='SYNTHETIC')))
        captured, joined = self.join()
        agent = self.node(captured, joined, '.claude/agents/reviewer.md')
        assert (agent['declared_invocations'], agent['observed_loading']) == (1, 'unknown')
        assert joined['unattributed'] == dict(attachment=0, hook_output=1, agent=1, mcp=1)
        assert not any(row['observed_loading'] != 'unknown' for row in joined['nodes'])
        assert (joined['loading_evidence'], joined['attachment_shapes']) == ('none', 'unverified_fixture_only')

    def test_an_attachment_for_an_absent_path_alone_is_an_unverified_shape(self):
        self.attach('x', 'missing/CLAUDE.md', 'SYNTHETIC_ABSENT')
        _, joined = self.join()
        assert joined['unattributed']['attachment'] == 1
        assert (joined['loading_evidence'], joined['attachment_shapes']) == ('none', 'unverified_fixture_only')

    def test_contentless_attachment_of_an_unread_private_node_is_path_only(self):
        self.checkout = fixture.build(self.base / 'private', {'CLAUDE.local.md': 'SYNTHETIC_PRIVATE\n'}, hostile=False)
        self.attach('p', 'CLAUDE.local.md')
        captured, joined = self.join()
        assert next(node for node in captured.nodes if node.path == 'CLAUDE.local.md').content_sha256 is None
        row = self.node(captured, joined, 'CLAUDE.local.md')
        assert (row['observed_loading'], row['exact_attachments'], row['path_only_attachments']) == ('unknown', 0, 1)

    def agent_case(self, case, files):
        self.checkout = fixture.build(self.base / f'agents-{case}', files, hostile=False)
        self.call('a', 'Agent', dict(subagent_type='reviewer', prompt='SYNTHETIC'))
        captured, joined = self.join()
        self.rows.clear()
        counts = {node.path: joined['nodes'][i]['declared_invocations']
                  for i, node in enumerate(captured.nodes) if node.kind == 'agent'}
        return counts, joined['unattributed']['agent']

    @pytest.mark.parametrize('case,files,expected,unattributed', [  # invocations per agent path, unattributed agent count
        pytest.param(0, {MAIN_AGENT: SKIPPED_AGENT}, {MAIN_AGENT: 0}, 1, id='no-description'),
        pytest.param(1, {MAIN_AGENT: AGENT, 'pkg/.claude/agents/reviewer.md': AGENT},
                     {MAIN_AGENT: 1, 'pkg/.claude/agents/reviewer.md': 0}, 0, id='nested-package-copy'),
        pytest.param(2, {MAIN_AGENT: AGENT, '.claude/agents/old.md': SKIPPED_AGENT},
                     {MAIN_AGENT: 1, '.claude/agents/old.md': 0}, 0, id='same-name-without-description'),
        pytest.param(3, {MAIN_AGENT: AGENT, '.claude/agents/copy.md': AGENT},
                     {MAIN_AGENT: 0, '.claude/agents/copy.md': 0}, 1, id='ambiguous-duplicate'),
    ])
    def test_agent_invocation_counts_only_on_the_definition_precedence_selects(self, case, files, expected, unattributed):
        assert self.agent_case(case, files) == (expected, unattributed)

    def test_missing_capture_codex_and_foreign_events_leave_every_node_unknown(self):
        captured, joined = self.join(events=None)
        assert joined['status'] == 'no_capture'
        assert all(row['observed_loading'] == row['effective'] == 'unknown' for row in joined['nodes'])
        codex = dict(CLAUDE, runtime='codex', version='0.166.0')
        _, joined = self.join(codex, events=[])
        assert (joined['status'], joined['capture_version']) == ('no_native_loading_signal', None)
        forged = dict(case_id='c', event_id='e', activity='context', source_version='claude-jsonl-v1',
            loading_signal='observed_attachment', loading_path_sha256=digest('CLAUDE.md'),
            loading_content_sha256=captured.nodes[0].content_sha256, outcome='success', outcome_provenance='native_field')
        _, joined = self.join(events=[forged])
        assert joined['ignored_records'] == 1
        assert all(row['observed_loading'] == 'unknown' for row in joined['nodes'])
        assert verified_outcome(forged) is None
        _, joined = self.join(dict(CLAUDE, task_cwd='src/task'), events=[])
        assert joined['status'] == 'task_cwd_not_checkout_root'

    def test_join_requires_a_captured_inventory(self):
        with pytest.raises(ValueError): inventory.join_loading(dict(nodes=()), [])

