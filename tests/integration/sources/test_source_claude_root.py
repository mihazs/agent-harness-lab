"""Synthetic Claude project-directory journals; no real session stores are read."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest

from tests.integration.sources import test_source_claude as fixtures
from tests.support.unittest_cleanups import run_cleanups
from agent_harness_lab.sources.schema import digest
from agent_harness_lab.telemetry.claude_discovery import directory_names, simple_hash

SESSION = '00000000-0000-4000-8000-000000000001'
OTHER = '00000000-0000-4000-8000-000000000002'


@pytest.fixture
def claude_journal():
    journal = fixtures.ClaudeSourceTests(); journal.setUp()
    yield journal
    run_cleanups(journal)


class TestClaudeRoot:
    @pytest.fixture(autouse=True)
    def _journal(self, claude_journal):
        self.f = claude_journal
        for row in self.f.rows:
            if 'sessionId' in row: row['sessionId'] = SESSION
        self.projects = self.f.root/'claude/projects'; self.projects.mkdir(parents=True)
        self.archive = self.f.root/'archive/projects'; self.archive.mkdir(parents=True)
        self.name = directory_names(self.f.project)[0]
        self.f.selected = dict(schema_version=1, project='project', source_roots=['claude/projects'], session=None,
            max_files=100, max_scan_files=2000, max_records=10000)
        self.select(); self.primary = self.write(SESSION+'.jsonl')

    def select(self, **changes):
        f = self.f; f.selected.update(changes); f.declare(); f.source = replace(f.source, version='2.1.316-v2')
        f.manifest.write_text(json.dumps(dict(schema_version=1, source=f.source.to_dict(), data='selection.json')))
        f.scope = dict(project_sha256=f.source.project_sha256, scope_sha256=f.source.scope_sha256)

    def write(self, name, rows=None, root=None, directory=None):
        path = (root or self.projects)/(directory or self.name)/name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(row)+'\n' for row in (self.f.rows if rows is None else rows)))
        return path

    def invoke(self, operation):
        code, result = self.f.invoke(operation)
        for private in (SESSION, OTHER, self.name): assert private not in json.dumps(result)
        return code, result

    def preview(self):
        code, result = self.invoke('preview'); assert code == 0; return result

    def reasons(self, preview): return sorted((r['status'], r['reason']) for r in preview['native_summary']['receipts'])

    def sidechain(self, agent='a1'):
        rows = deepcopy(self.f.rows[1:6])
        for row in rows: row.update(isSidechain=True, agentId=agent)
        return rows

    def test_upstream_directory_naming_and_unestablished_paths(self):
        assert directory_names('/synthetic/a_b.c')[0] == '-synthetic-a-b-c'
        assert simple_hash('') == '0'; assert simple_hash('/synthetic/'+'p'*300) == '2tz7g9'
        exact, prefix = directory_names('/synthetic/'+'p'*300)
        assert len(prefix) == 201; assert exact == prefix+'2tz7g9'
        with pytest.raises(ValueError): directory_names('/synthetic/\U0001F600')

    def test_root_cli_preview_import_recheck_and_status_use_scoped_directory_only(self):
        f = self.f; preview = self.preview(); discovery = preview['native_summary']['discovery']
        assert preview['checkpoint_ready']; assert preview['records'] == 12
        assert (discovery['selected_directories'], discovery['accepted_files']) == (1, 1)
        assert self.reasons(preview) == [('accepted', 'scoped_native_records')]
        assert self.invoke('import')[1]['imported'] == 12
        before = f.cache.read_bytes(); assert self.invoke('recheck')[1]['statuses']['verified'] == 9
        assert f.cache.read_bytes() == before
        self.primary.unlink(); assert self.invoke('status')[1]['revisions'] == 12

    def test_v1_profile_and_record_identities_are_unchanged_by_the_root_profile(self):
        f = self.f; root = f.reader().read(f.source)
        f.selected = dict(schema_version=1, project='project', capture='session.jsonl', max_records=10000); f.write(); f.declare()
        single = f.reader().read(f.source)
        assert [(r.native_id_sha256, r.revision_sha256) for r in single.records] == [(r.native_id_sha256, r.revision_sha256) for r in root.records]
        assert single.metadata.coverage.parser_version == 'claude-native-journal-v1'
        assert root.metadata.coverage.parser_version == 'claude-native-root-journal-v1'

    def test_foreign_directories_are_never_read_and_collisions_are_rejected_by_record_scope(self):
        foreign = self.projects/'-synthetic-foreign'/(OTHER+'.jsonl'); foreign.parent.mkdir()
        foreign.write_bytes(b'{}\n'); foreign.open('ab').truncate(64*1024*1024)
        collision = deepcopy(self.f.rows)
        for row in collision:
            if 'cwd' in row: row['cwd'] = str(self.f.root/'foreign')
        self.write(OTHER+'.jsonl', collision)
        preview = self.preview(); discovery = preview['native_summary']['discovery']
        assert preview['checkpoint_ready']; assert discovery['foreign_files'] == 1
        assert discovery['bytes_read'] < 1024*1024; assert preview['records'] == 12
        assert ('rejected', 'foreign_project') in self.reasons(preview)

    def test_archive_roots_and_prefix_copies_merge_by_native_uuid_and_import_idempotently(self):
        f = self.f; copy = self.write(SESSION+'.jsonl', f.rows[:4], root=self.archive)
        self.select(source_roots=['claude/projects', 'archive/projects'])
        reader = f.reader(); snapshot = reader.read(f.source)
        assert len(snapshot.records) == 12; assert len(snapshot.graph.joins) == 9
        assert reader.summary['discovery']['accepted_files'] == 2; assert reader.summary['duplicates_skipped'] == 3
        assert f.invoke('import')[1]['imported'] == 12; before = f.cache.read_bytes()
        assert f.invoke('import')[1]['imported'] == 0; assert f.cache.read_bytes() == before
        self.primary.unlink(); copy.write_text(''.join(json.dumps(row)+'\n' for row in f.rows))
        result = f.invoke('import')[1]; assert (result['imported'], result['skipped']) == (0, 12)
        assert self.invoke('recheck')[1]['statuses']['verified'] == 9

    def test_raw_body_conflict_across_copies_rejects_the_whole_batch(self):
        f = self.f; self.select(source_roots=['claude/projects', 'archive/projects'])
        assert f.invoke('import')[0] == 0; before = f.cache.read_bytes()
        changed = deepcopy(f.rows); changed[2]['message']['content'][0]['input']['command'] = 'SYNTHETIC_CHANGED_BODY'
        self.write(SESSION+'.jsonl', changed, root=self.archive)
        for operation in ('preview', 'import', 'recheck'):
            assert f.invoke(operation)[0] == 2; assert f.cache.read_bytes() == before

    def test_subagent_transcripts_keep_their_own_writer_and_unknown_parent_session(self):
        self.write(SESSION+'/subagents/agent-a1.jsonl', self.sidechain())
        other = deepcopy(self.f.rows)
        for row in other:
            if 'sessionId' in row: row['sessionId'] = OTHER
        self.write(OTHER+'.jsonl', other)
        reader = self.f.reader(); snapshot = reader.read(self.f.source); summary = snapshot.metadata.summary()
        assert reader.summary['discovery']['accepted_files'] == 3; assert len(snapshot.records) == 12+7+12
        assert len({r.native_id_sha256 for r in snapshot.records}) == len(snapshot.records)
        assert summary['fields']['parent_session']['unknown'] == len(snapshot.metadata.identities)
        assert snapshot.graph.summary()['independent_roots'] is None
        sessions = {field.value_sha256 for i in snapshot.metadata.identities for field in i.fields if field.name == 'session'}
        assert len(sessions) == 2
        self.select(session=SESSION); selected = self.preview()
        assert selected['native_summary']['discovery']['accepted_files'] == 2; assert selected['checkpoint_ready']

    def test_session_named_file_with_other_records_is_not_attributed(self):
        other = deepcopy(self.f.rows)
        for row in other:
            if 'sessionId' in row: row['sessionId'] = OTHER
        self.primary.write_text(''.join(json.dumps(row)+'\n' for row in other)); self.select(session=SESSION)
        preview = self.preview(); assert preview['records'] == 0; assert not preview['checkpoint_ready']
        assert self.reasons(preview) == [('unknown', 'native_location_mismatch')]

    def test_file_location_must_match_native_session_and_writer(self):
        main = deepcopy(self.f.rows[1:6])
        for name, rows in ((OTHER+'.jsonl', self.f.rows), (OTHER+'.jsonl', self.sidechain()),
                (SESSION+'/subagents/agent-a1.jsonl', main), (SESSION+'/subagents/agent-zz.jsonl', self.sidechain()),
                (OTHER+'/subagents/agent-a1.jsonl', self.sidechain())):
            path = self.write(name, rows); preview = self.preview()
            assert not preview['checkpoint_ready'], name; assert preview['records'] == 12
            assert ('unknown', 'native_location_mismatch') in self.reasons(preview); path.unlink()
        self.write(SESSION+'/subagents/nested/agent-a1.jsonl', self.sidechain()); assert self.preview()['checkpoint_ready']

    def test_subagent_tree_depth_and_entry_bounds_yield_labelled_limits(self):
        from agent_harness_lab.telemetry import claude_discovery
        deep = self.primary.parent/SESSION/'subagents'
        for _ in range(claude_discovery.MAX_SUBAGENT_DEPTH+1): deep = deep/'d'
        deep.mkdir(parents=True); (deep/'agent-a1.jsonl').write_text('')
        preview = self.preview(); discovery = preview['native_summary']['discovery']
        assert not preview['checkpoint_ready']; assert 'subagent_tree' in discovery['reached_limits']
        assert ('unknown', 'subagent_tree_limit') in self.reasons(preview); assert preview['records'] == 12
        assert self.f.invoke('import')[0] == 2; assert not self.f.cache.exists()
        shutil.rmtree(self.primary.parent/SESSION); wide = self.primary.parent/SESSION/'subagents'; wide.mkdir(parents=True)
        for index in range(6): (wide/f'note-{index}.txt').write_text('')
        with patch.object(claude_discovery, 'MAX_SUBAGENT_ENTRIES', 5): preview = self.preview()
        assert not preview['checkpoint_ready']; assert ('unknown', 'subagent_tree_limit') in self.reasons(preview)
        with patch.object(claude_discovery, 'MAX_SUBAGENT_ENTRIES', 6): assert self.preview()['checkpoint_ready']

    def test_unknown_unsafe_partial_and_limited_scans_stay_visible_without_committing(self):
        f = self.f
        metadata = self.write(OTHER+'.jsonl', [f.rows[0], f.rows[-1]])
        preview = self.preview(); assert not preview['checkpoint_ready']
        assert ('unknown', 'native_scope_unavailable') in self.reasons(preview); assert f.invoke('import')[0] == 2
        metadata.unlink(); self.write('notes.jsonl')
        preview = self.preview(); assert preview['checkpoint_ready']
        assert ('unknown', 'unrecognized_native_layout') in self.reasons(preview)
        linked = self.primary.parent/(OTHER+'.jsonl'); linked.symlink_to(self.primary)
        assert not self.preview()['checkpoint_ready']; linked.unlink()
        branch = self.primary.parent/SESSION; branch.symlink_to(self.f.root/'archive', target_is_directory=True)
        preview = self.preview(); assert not preview['checkpoint_ready']
        assert preview['native_summary']['discovery']['unsafe_entries_skipped'] == 1; branch.unlink()
        full = self.primary.read_bytes(); self.primary.write_bytes(full.rstrip(b'\n'))
        preview = self.preview(); assert not preview['checkpoint_ready']
        assert ('accepted', 'partial_native_tail') in self.reasons(preview); assert f.invoke('import')[0] == 2
        self.primary.write_bytes(full); self.write(OTHER+'.jsonl', [dict(row, sessionId=OTHER) if 'sessionId' in row else row for row in f.rows])
        for limit in (dict(max_files=1), dict(max_scan_files=1)):
            self.select(**limit); preview = self.preview(); assert not preview['checkpoint_ready']
            assert f.invoke('import')[0] == 2; self.select(max_files=100, max_scan_files=2000)
        self.select(max_files=1); assert ('unknown', 'not_read_after_file_limit') in self.reasons(self.preview())
        assert not f.cache.exists()

    def test_empty_or_missing_project_directory_is_not_a_capture(self):
        self.primary.unlink(); self.primary.parent.rmdir(); preview = self.preview()
        assert preview['records'] == 0; assert not preview['checkpoint_ready']
        assert preview['native_summary']['discovery']['missing_project_directories'] == 1
        assert self.f.invoke('import')[0] == 2; assert not self.f.cache.exists()

    def test_truncated_project_names_tolerate_hash_variants_and_still_require_record_scope(self):
        f = self.f; project = f.root/('p'*120)/('q'*120); project.mkdir(parents=True)
        for row in f.rows:
            if 'cwd' in row: row['cwd'] = str(project)
        exact, prefix = directory_names(project); assert len(exact) > 200
        self.select(project=str(project.relative_to(f.root))); self.primary.unlink()
        self.write(SESSION+'.jsonl', directory=prefix+'bunhash')
        preview = self.preview(); discovery = preview['native_summary']['discovery']
        assert preview['checkpoint_ready']; assert preview['records'] == 12
        assert discovery['selected_directories'] == 2; assert discovery['missing_project_directories'] == 1

    def test_invalid_root_selection_is_rejected_before_scanning(self):
        f = self.f; original = dict(f.selected); (self.f.root/'claude/projects/nested').mkdir()
        outside = self.f.root/'linked'; outside.symlink_to(self.projects, target_is_directory=True)
        with patch('agent_harness_lab.telemetry.claude_discovery.capture', side_effect=AssertionError('no capture read')):
            for change in (dict(source_roots='claude/projects'), dict(source_roots=[]), dict(source_roots=['../outside']),
                    dict(source_roots=['missing']), dict(source_roots=['linked']), dict(max_files=True), dict(max_scan_files=-1)):
                f.selected = {**original, **change}; self.select(); assert f.invoke('preview')[0] == 2
        for change in (dict(session='not-a-uuid'), dict(session=12), dict(source_roots=['claude/projects', 'claude/projects/nested']),
                dict(source_roots=['claude/projects', 'claude/projects'])):
            f.selected = {**original, **change}; self.select(); assert f.invoke('preview')[0] == 2
        f.selected = original; self.select()
        assert f.invoke('preview', '--project-sha256', digest('foreign'))[0] == 2
        assert not f.cache.exists()

    def test_selected_input_mutation_after_conversion_cannot_commit(self):
        from agent_harness_lab.sources.native import relationships
        f = self.f; assert f.invoke('import')[0] == 0; before = f.cache.read_bytes()
        def mutate(*args, **kwargs):
            result = relationships(*args, **kwargs)
            with self.primary.open('ab') as handle: handle.write(b'{}\n')
            return result
        with patch('agent_harness_lab.sources.native.relationships', mutate): assert f.invoke('import')[0] == 2
        assert f.cache.read_bytes() == before
        self.write(SESSION+'.jsonl'); self.select(max_records=5)
        assert f.invoke('preview')[0] == 2; assert f.cache.read_bytes() == before
        assert Path(self.primary).exists()
