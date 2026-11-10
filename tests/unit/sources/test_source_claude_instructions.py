"""Synthetic Claude Code memory inventory; declared imports are never loading proof."""
from dataclasses import asdict
import hashlib
import json
import os
import time
from unittest.mock import patch

import pytest

from agent_harness_lab.sources.claude_instructions import imports
from agent_harness_lab.sources.git_view import Folder
from agent_harness_lab.sources.instructions import capture, validate
from agent_harness_lab.sources.schema import digest


class TestClaudeInstructionInventory:
    @pytest.fixture(autouse=True)
    def _inventory(self, tmp_path):
        self.root = tmp_path
        (self.root/'project/src/task').mkdir(parents=True)
        self.profile = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='src/task')
        self.restored = []
        yield
        # Unreadable fixtures are restored LIFO before pytest removes tmp_path.
        for path, mode in reversed(self.restored): path.chmod(mode)

    def write(self, name, text):
        path = self.root/'project'/name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode())
        return path

    def read(self, selected=()):
        with Folder(self.root, time.monotonic()+30, limit_total_bytes=False, limit_tree=False) as folder:
            value = capture(self.profile, folder, 'project', selected)
            folder.verify()
        dumped = json.dumps(asdict(value))
        assert str(self.root) not in dumped; assert 'SYNTHETIC_' not in dumped
        assert all(n.observed_loading == 'unknown' for n in value.nodes)
        assert (value.ambient_state, value.observed_loading) == ('unknown', 'unknown')
        return value

    def nodes(self, receipt): return {node.path_sha256: node for node in receipt.nodes}

    def edges(self, receipt): return {(e.kind, e.source_sha256, e.target_sha256) for e in receipt.edges}

    def state(self, receipt, path):
        node = self.nodes(receipt)[digest(path)]
        return node.applicability, node.reason

    def unresolved(self, receipt, importer, reason):
        return self.nodes(receipt)[digest(['unresolved_imports', importer, reason])]

    def test_root_nested_dot_claude_files_are_concatenated_and_local_files_stay_private(self):
        for name in ('CLAUDE.md', '.claude/CLAUDE.md', 'src/CLAUDE.local.md', 'src/task/CLAUDE.md'):
            self.write(name, 'SYNTHETIC_MEMORY')
        receipt = self.read()
        active = {n.path_sha256 for n in receipt.nodes if n.applicability == 'declared_applicable'}
        assert receipt.resolution_status == 'declared'
        assert active == {digest(p) for p in ('CLAUDE.md', '.claude/CLAUDE.md', 'src/task/CLAUDE.md')}
        assert self.state(receipt, 'src/CLAUDE.local.md') == ('private_excluded', 'private_excluded')
        assert ('parent', digest('.claude/CLAUDE.md'), digest('src/task/CLAUDE.md')) in self.edges(receipt)
        assert not any(digest('src/CLAUDE.local.md') in (s, t) for _, s, t in self.edges(receipt))
        assert not any(e.kind == 'shadows' for e in receipt.edges)
        assert self.state(receipt, 'src/CLAUDE.md') == ('unknown', 'missing')

    def test_imports_resolve_relative_to_the_importing_file_within_four_hops(self):
        self.profile['task_cwd'] = '.'
        self.write('CLAUDE.md', 'SYNTHETIC_ROOT @docs/one.md')
        self.write('docs/one.md', '@two.md'); self.write('docs/two.md', '@nested/three.md')
        self.write('docs/nested/three.md', '@../four.md'); self.write('docs/four.md', '@five.md')
        self.write('docs/five.md', '@six.md'); self.write('docs/six.md', 'SYNTHETIC_UNREACHED')
        self.write('two.md', 'SYNTHETIC_WRONG_SCOPE')
        receipt = self.read()
        for path in ('docs/one.md', 'docs/two.md', 'docs/nested/three.md', 'docs/four.md'):
            assert self.state(receipt, path) == ('declared_applicable', 'import')
        assert self.state(receipt, 'docs/five.md') == ('unknown', 'import_depth_limit')
        assert digest('docs/six.md') not in self.nodes(receipt)
        assert digest('two.md') not in self.nodes(receipt)
        assert ('imports', digest('docs/nested/three.md'), digest('docs/four.md')) in self.edges(receipt)

    def test_missing_self_and_mutual_imports_terminate_with_explicit_states(self):
        self.profile['task_cwd'] = '.'
        self.write('CLAUDE.md', '@a.md @absent.md @CLAUDE.md')
        self.write('a.md', '@b.md'); self.write('b.md', '@a.md')
        receipt = self.read(); edges = self.edges(receipt)
        absent = self.unresolved(receipt, 'CLAUDE.md', 'import_missing')
        assert (absent.presence, absent.applicability, absent.reason) == ('missing', 'unknown', 'import_missing')
        assert ('import_cycle', digest('CLAUDE.md'), digest('CLAUDE.md')) in edges
        assert ('import_cycle', digest('b.md'), digest('a.md')) in edges
        assert ('import_cycle', digest('a.md'), digest('b.md')) in edges
        assert ('imports', digest('CLAUDE.md'), digest('a.md')) in edges
        assert self.state(receipt, 'b.md') == ('declared_applicable', 'import')

    def test_import_syntax_skips_code_quotes_and_mentions_and_flags_comments(self):
        text = '\n'.join(['see @real.md and `@span.md` or ``@double.md``', 'mail user@example.com @"quoted.md"',
                          '```', '@fenced.md', '```', '~~~~', '@tilde.md', '~~~', '@still-fenced.md', '~~~~',
                          r'@Design\ Docs/api.md', '<!-- @hidden.md', '@hidden-too.md -->@after.md'])
        comment = 'import_inside_html_comment'
        assert (imports(text) == [('real.md', None), ('Design Docs/api.md', None), ('hidden.md', comment),
                                         ('hidden-too.md', comment)])
        assert imports('<!-- a --> @after.md <!-- @in.md') == [('after.md', None), ('in.md', comment)]
        self.profile['task_cwd'] = '.'
        self.write('CLAUDE.md', '<!-- @note.md -->'); self.write('note.md', 'SYNTHETIC_NOTE')
        assert self.state(self.read(), 'note.md') == ('unknown', 'import_inside_html_comment')

    def test_external_and_out_of_cwd_imports_are_never_expanded_or_read_outside_the_project(self):
        self.write('../outside.md', 'SYNTHETIC_OUTSIDE')
        self.write('src/task/CLAUDE.md', '@../../../outside.md @/etc/hostname @~/.claude/x.md @../../shared.md')
        self.write('shared.md', '@deeper.md'); self.write('deeper.md', 'SYNTHETIC_DEEPER')
        original = Folder.open  # read() and the streaming hash both open through it
        def guarded(folder, name, *args, **options):
            assert name.startswith('project/'), name
            return original(folder, name, *args, **options)
        with patch.object(Folder, 'open', guarded): receipt = self.read()
        external = [n for n in receipt.nodes if n.origin == 'external_reference']
        assert len(external) == 1
        assert all((n.presence, n.content_sha256, n.applicability) == ('unknown', None, 'unknown') for n in external)
        assert self.state(receipt, 'shared.md') == ('unknown', 'external_import_requires_approval')
        assert digest('deeper.md') not in self.nodes(receipt)

    def test_agents_files_follow_the_documented_default_only_as_a_declaration(self):
        self.write('AGENTS.md', 'SYNTHETIC_AGENTS')
        assert self.state(self.read(), 'AGENTS.md') == ('unknown', 'instruction_files_setting_unknown')
        self.write('src/CLAUDE.md', 'SYNTHETIC_CLAUDE')
        receipt = self.read()
        assert self.state(receipt, 'AGENTS.md') == ('shadowed', 'assumed_default_instruction_files_setting')
        assert ('shadows', digest('src/CLAUDE.md'), digest('AGENTS.md')) in self.edges(receipt)
        self.write('src/CLAUDE.md', '@../AGENTS.md')
        self.profile['task_cwd'] = 'src'
        receipt = self.read()
        assert self.state(receipt, 'AGENTS.md') == ('shadowed', 'assumed_default_instruction_files_setting')
        assert ('imports', digest('src/CLAUDE.md'), digest('AGENTS.md')) in self.edges(receipt)
        self.profile['task_cwd'] = '.'; self.write('CLAUDE.md', '@AGENTS.md')
        receipt = self.read()
        assert self.state(receipt, 'AGENTS.md') == ('declared_applicable', 'import')
        assert not any(e.kind == 'shadows' for e in receipt.edges)

    def test_selected_rules_subdirectory_and_foreign_files_stay_unknown_or_unsupported(self):
        self.write('AGENTS.override.md', 'SYNTHETIC_OVERRIDE')
        self.write('.claude/rules/testing.md', '---\npaths:\n  - "src/**"\n---\nSYNTHETIC_RULE')
        self.write('src/task/deep/CLAUDE.md', 'SYNTHETIC_ON_DEMAND')
        self.write('other/CLAUDE.md', 'SYNTHETIC_OTHER_SCOPE')
        receipt = self.read(['AGENTS.override.md', '.claude/rules/testing.md', 'src/task/deep/CLAUDE.md', 'other/CLAUDE.md'])
        assert self.state(receipt, 'AGENTS.override.md') == ('unsupported', 'unrecognized_filename')
        assert self.state(receipt, '.claude/rules/testing.md') == ('unknown', 'rule_conditional_paths')
        assert self.state(receipt, 'src/task/deep/CLAUDE.md') == ('unknown', 'on_demand_subdirectory')
        assert self.state(receipt, 'other/CLAUDE.md') == ('out_of_scope', 'outside_task_scope')

    def test_whole_tree_classifies_nested_unrelated_and_shadowed_files_without_selection(self):
        self.write('CLAUDE.md', 'SYNTHETIC_ROOT'); self.write('AGENTS.md', 'SYNTHETIC_ROOT_AGENTS')
        self.write('src/task/deep/CLAUDE.md', '@notes.md @absent.md @loop.md')
        self.write('src/task/deep/notes.md', 'SYNTHETIC_NOTES'); self.write('src/task/deep/loop.md', '@CLAUDE.md')
        self.write('src/task/deep/.claude/CLAUDE.md', 'SYNTHETIC_DOT'); self.write('src/task/deep/AGENTS.md', 'SYNTHETIC_SHADOW')
        self.write('src/task/lone/AGENTS.md', 'SYNTHETIC_LONE')
        self.write('other/CLAUDE.md', 'SYNTHETIC_OTHER'); self.write('other/AGENTS.md', 'SYNTHETIC_OTHER_AGENTS')
        self.write('vendor/x/CLAUDE.md', 'SYNTHETIC_VENDOR'); (self.root/'project/vendor/x/.git').mkdir()
        (self.root/'project/src/task/linked').mkdir(); (self.root/'project/src/task/linked/CLAUDE.md').symlink_to('../deep/CLAUDE.md')
        receipt = self.read(); edges = self.edges(receipt)
        assert self.state(receipt, 'AGENTS.md') == ('shadowed', 'assumed_default_instruction_files_setting')
        for path in ('src/task/deep/CLAUDE.md', 'src/task/deep/.claude/CLAUDE.md', 'src/task/lone/AGENTS.md'):
            assert self.state(receipt, path) == ('unknown', 'on_demand_subdirectory')
        assert self.state(receipt, 'src/task/deep/AGENTS.md') == ('shadowed', 'assumed_default_instruction_files_setting')
        assert ('shadows', digest('src/task/deep/CLAUDE.md'), digest('src/task/deep/AGENTS.md')) in edges
        assert self.state(receipt, 'other/CLAUDE.md') == ('out_of_scope', 'outside_task_scope')
        assert self.state(receipt, 'other/AGENTS.md') == ('out_of_scope', 'outside_task_scope')
        assert self.state(receipt, 'src/task/deep/notes.md') == ('unknown', 'on_demand_import')
        assert self.unresolved(receipt, 'src/task/deep/CLAUDE.md', 'import_missing').presence == 'missing'
        assert ('import_cycle', digest('src/task/deep/loop.md'), digest('src/task/deep/CLAUDE.md')) in edges
        assert self.state(receipt, 'src/task/linked/CLAUDE.md') == ('unsupported', 'instruction_link_not_followed')
        assert digest('vendor/x/CLAUDE.md') not in self.nodes(receipt)
        assert not any(n.applicability == 'declared_applicable' for n in receipt.nodes if n.reason != 'directory_concatenation')
        self.profile['task_cwd'] = 'src/task/deep'
        moved = self.read()
        assert self.state(moved, 'src/task/deep/CLAUDE.md') == ('declared_applicable', 'directory_concatenation')
        assert self.state(moved, 'src/task/deep/notes.md') == ('declared_applicable', 'import')
        assert self.state(moved, 'src/task/lone/AGENTS.md') == ('out_of_scope', 'outside_task_scope')
        assert moved.sha256 != receipt.sha256

    def test_local_files_are_digested_never_admitted_expanded_or_output(self):
        self.profile['task_cwd'] = '.'
        for name in ('CLAUDE.local.md', 'sub/CLAUDE.local.md', 'sub/.claude/CLAUDE.local.md'):
            self.write(name, 'SYNTHETIC_PRIVATE @private-import.md')
        self.write('private-import.md', 'SYNTHETIC_PRIVATE_IMPORT'); self.write('AGENTS.md', 'SYNTHETIC_AGENTS')
        self.write('CLAUDE.md', '@CLAUDE.local.md @sub/CLAUDE.local.md')
        (self.root/'project/other').mkdir(); (self.root/'project/other/CLAUDE.local.md').symlink_to('../CLAUDE.local.md')
        receipt = self.read()
        local = [n for n in receipt.nodes if n.path_sha256 in {digest(p) for p in (
            'CLAUDE.local.md', 'sub/CLAUDE.local.md', 'sub/.claude/CLAUDE.local.md', 'other/CLAUDE.local.md')}]
        assert len(local) == 4
        assert {n.applicability for n in local} == {'private_excluded'}
        assert self.nodes(receipt)[digest('CLAUDE.local.md')].content_sha256 == hashlib.sha256(b'SYNTHETIC_PRIVATE @private-import.md').hexdigest()
        assert digest('private-import.md') not in self.nodes(receipt)
        assert self.state(receipt, 'AGENTS.md') == ('shadowed', 'assumed_default_instruction_files_setting')
        for mode in ('claude-md-and-agents-md', 'surprise-mode'):
            self.profile['instruction_files'] = mode
            assert {n.applicability for n in self.read().nodes if n.path_sha256 in {x.path_sha256 for x in local}} == {'private_excluded'}

    def test_declared_instruction_file_modes_and_unknown_modes(self):
        self.profile['task_cwd'] = '.'
        self.write('AGENTS.md', '@agents-note.md'); self.write('agents-note.md', 'SYNTHETIC_NOTE')
        self.write('sub/AGENTS.md', 'SYNTHETIC_SUB_AGENTS'); self.write('sub/CLAUDE.md', 'SYNTHETIC_SUB')
        self.profile['instruction_files'] = 'claude-md-or-agents-md'
        receipt = self.read()
        assert self.state(receipt, 'AGENTS.md') == ('declared_applicable', 'declared_instruction_files_setting')
        assert self.state(receipt, 'agents-note.md') == ('declared_applicable', 'import')
        assert self.state(receipt, 'sub/AGENTS.md') == ('shadowed', 'declared_instruction_files_setting')
        self.write('CLAUDE.md', 'SYNTHETIC_ROOT')
        assert self.state(self.read(), 'AGENTS.md') == ('shadowed', 'declared_instruction_files_setting')
        self.profile['instruction_files'] = 'claude-md-and-agents-md'
        both = self.read()
        assert self.state(both, 'AGENTS.md') == ('declared_applicable', 'declared_instruction_files_setting')
        assert self.state(both, 'sub/AGENTS.md') == ('unknown', 'on_demand_subdirectory')
        self.profile['instruction_files'] = 'claude-md'
        assert self.state(self.read(), 'AGENTS.md') == ('out_of_scope', 'excluded_by_declared_instruction_files')
        self.profile['instruction_files'] = 'managed-only'
        managed = self.read()
        assert self.state(managed, 'CLAUDE.md') == ('out_of_scope', 'excluded_by_declared_instruction_files')
        assert self.state(managed, 'sub/CLAUDE.md') == ('unknown', 'on_demand_subdirectory')
        self.profile['instruction_files'] = 'surprise-mode'
        unknown = self.read()
        for path in ('CLAUDE.md', 'AGENTS.md', 'sub/CLAUDE.md', 'sub/AGENTS.md'):
            assert self.state(unknown, path) == ('unknown', 'instruction_files_setting_unknown')
        assert not any(n.applicability in ('declared_applicable', 'shadowed') for n in unknown.nodes)
        self.profile.pop('instruction_files'); self.profile['claude_md_excludes'] = ['**/sub/CLAUDE.md', 'CLAUDE.md']
        excluded = self.read()
        assert self.state(excluded, 'sub/CLAUDE.md') == ('unknown', 'claude_md_excludes_match_unverified')
        assert self.state(excluded, 'CLAUDE.md') == ('unknown', 'claude_md_excludes_match_unverified')

    @pytest.mark.parametrize('change', [
        pytest.param(dict(instruction_files='bad mode'), id='bad_mode'),
        pytest.param(dict(claude_md_excludes='x'), id='excludes_not_a_list'),
        pytest.param(dict(claude_md_excludes=['']), id='empty_exclude'),
        pytest.param(dict(project_trust='declared_trusted'), id='project_trust'),
    ])
    def test_invalid_instruction_file_declarations_are_rejected(self, change):
        with pytest.raises(ValueError): validate(dict(self.profile, **change))

    @pytest.mark.parametrize(('name', 'bound'), [pytest.param('TREE_ENTRIES', 3, id='entries'), pytest.param('TREE_DEPTH', 1, id='depth')])
    def test_tree_enumeration_bounds_block_instead_of_truncating(self, name, bound):
        from agent_harness_lab.sources import claude_instructions as module
        self.write('CLAUDE.md', 'SYNTHETIC_ROOT'); self.write('a/b/c/CLAUDE.md', 'SYNTHETIC_DEEP')
        with patch.object(module, name, bound):
            receipt = self.read()
            assert receipt.resolution_status == 'blocked'
            assert 'tree_enumeration_bound_exceeded' in {n.reason for n in receipt.nodes}

    def test_unsafe_targets_fail_closed_without_reading_them(self):
        self.profile['task_cwd'] = '.'
        (self.root/'project/.git').mkdir(); self.write('.git/config', 'SYNTHETIC_GIT_CONFIG')
        self.write('docs/x.md', 'SYNTHETIC_DOC'); self.write('binary.md', b'\xff\xfe')
        self.write('CLAUDE.md', '@.git/config @docs @binary.md')
        receipt = self.read(); nodes = self.nodes(receipt)
        unsafe = [n for n in receipt.nodes if n.reason == 'unsafe_import_target']
        assert [(n.presence, n.applicability) for n in unsafe] == [('unknown', 'unsupported')]
        directory = self.unresolved(receipt, 'CLAUDE.md', 'import_target_not_regular_file')
        assert (directory.presence, directory.applicability) == ('unknown', 'unsupported')
        assert nodes[digest('binary.md')].content_sha256 == hashlib.sha256(b'\xff\xfe').hexdigest()
        assert self.state(receipt, 'binary.md') == ('unknown', 'non_utf8_import')
        (self.root/'project/link.md').symlink_to(self.root/'project/docs/x.md')
        self.write('CLAUDE.md', '@link.md')
        with pytest.raises(ValueError): self.read()
        (self.root/'project/vendor').mkdir(); (self.root/'project/vendor/.git').write_text('gitdir: elsewhere')
        self.write('vendor/x.md', 'SYNTHETIC_VENDOR'); self.write('CLAUDE.md', '@vendor/x.md')
        with pytest.raises(ValueError): self.read()

    def linked_imports(self):
        self.profile['task_cwd'] = '.'
        self.write('docs/x.md', 'SYNTHETIC_DOC'); self.write('../elsewhere/x.md', 'SYNTHETIC_OUTSIDE')
        (self.root/'project/inside').symlink_to(self.root/'project/docs', target_is_directory=True)
        (self.root/'project/outside').symlink_to(self.root/'elsewhere', target_is_directory=True)

    @pytest.mark.parametrize('text', [
        pytest.param('@inside/x.md', id='inside_link'), pytest.param('@outside/x.md', id='outside_link'),
        pytest.param('@docs/x.md/child', id='file_component'), pytest.param('@inside/absent.md', id='link_to_absent'),
    ])
    def test_import_through_a_link_or_file_component_fails_closed(self, text):
        self.linked_imports()
        self.write('CLAUDE.md', text)
        with pytest.raises(ValueError): self.read()

    def test_import_below_an_absent_directory_is_missing_not_followed(self):
        self.linked_imports()
        self.write('CLAUDE.md', '@absent/x.md')
        assert self.unresolved(self.read(), 'CLAUDE.md', 'import_missing').presence == 'missing'

    def test_on_demand_imports_through_links_or_unreadable_paths_stay_unresolved(self):
        self.write('CLAUDE.md', 'SYNTHETIC_ROOT'); self.write('real/x.md', 'SYNTHETIC_REAL')
        self.write('src/task/note.md', 'SYNTHETIC_NOTE')
        (self.root/'project/src/task/linkdir').symlink_to('../../real', target_is_directory=True)
        (self.root/'project/src/task/linked.md').symlink_to('note.md')
        self.write('src/task/sub/CLAUDE.md', '@../linkdir/x.md @../linked.md @../note.md')
        receipt = self.read()
        assert receipt.resolution_status == 'declared'
        for reason in ('import_crosses_link_or_repository', 'import_target_link_not_followed'):
            node = self.unresolved(receipt, 'src/task/sub/CLAUDE.md', reason)
            assert (node.presence, node.applicability) == ('unknown', 'unsupported')
        assert self.state(receipt, 'src/task/note.md') == ('unknown', 'on_demand_import')
        self.write('src/task/CLAUDE.md', '@linkdir/x.md')
        with pytest.raises(ValueError): self.read()

    @pytest.mark.parametrize('kind', ['symlink', 'hardlink'])
    def test_launch_imports_of_linked_instruction_named_files_found_by_the_walk_fail_closed(self, kind):
        self.profile['task_cwd'] = '.'
        (self.root/'outside.md').write_text('SYNTHETIC_OUTSIDE'); self.write('CLAUDE.md', '@sub/CLAUDE.md')
        (self.root/'project/sub').mkdir()
        target = self.root/'project/sub/CLAUDE.md'
        if kind == 'symlink': target.symlink_to('../../outside.md')
        else: os.link(self.root/'outside.md', target)
        with pytest.raises(ValueError): self.read()

    @pytest.mark.parametrize('mode', ['claude-md-or-agents-md', 'claude-md-and-agents-md'])
    def test_empty_launch_agents_file_is_never_admitted_under_a_declared_mode(self, mode):
        self.profile['task_cwd'] = '.'
        self.write('AGENTS.md', '   \n'); self.write('agents-note.md', 'SYNTHETIC_NOTE')
        self.profile['instruction_files'] = mode
        if mode == 'claude-md-and-agents-md': self.write('CLAUDE.md', 'SYNTHETIC_ROOT')
        receipt = self.read()
        assert self.state(receipt, 'AGENTS.md') == ('unknown', 'empty')
        assert not any(e.target_sha256 == digest('AGENTS.md') for e in receipt.edges)

    def test_empty_local_files_are_private_not_missing(self):
        self.profile['task_cwd'] = '.'
        for name in ('CLAUDE.local.md', 'deep/CLAUDE.local.md'): self.write(name, '')
        receipt = self.read()
        for name in ('CLAUDE.local.md', 'deep/CLAUDE.local.md'):
            node = self.nodes(receipt)[digest(name)]
            assert (node.presence, node.applicability, node.reason) == ('present', 'private_excluded', 'private_excluded')

    def unreadable(self, path, mode):
        path.chmod(mode); self.restored.append((path, 0o755 if path.is_dir() else 0o644))

    @pytest.mark.skipif(os.geteuid() == 0, reason='permissions do not bind root')
    def test_unreadable_import_targets_stay_unresolved_on_demand_and_fail_the_launch_chain(self):
        self.write('src/task/sub/locked/x.md', 'SYNTHETIC_LOCKED'); self.write('src/task/sub/CLAUDE.md', '@locked/x.md')
        self.unreadable(self.root/'project/src/task/sub/locked', 0)
        node = self.unresolved(self.read(), 'src/task/sub/CLAUDE.md', 'import_target_unreadable')
        assert (node.presence, node.applicability, node.reason) == ('unknown', 'unsupported', 'import_target_unreadable')
        self.write('src/task/CLAUDE.md', '@sub/locked/x.md')
        with pytest.raises(PermissionError): self.read()
        (self.root/'project/src/task/CLAUDE.md').unlink()
        self.profile['task_cwd'] = 'src'
        self.write('src/CLAUDE.md', '@../other/CLAUDE.md'); self.write('other/CLAUDE.md', 'SYNTHETIC_HIDDEN')
        self.unreadable(self.root/'project/other/CLAUDE.md', 0)
        with pytest.raises(PermissionError): self.read()

    @pytest.mark.skipif(os.geteuid() == 0, reason='permissions do not bind root')
    def test_unreadable_entries_are_reported_and_block_only_where_they_can_hide_instructions(self):
        self.write('CLAUDE.md', 'SYNTHETIC_ROOT'); self.write('cache/CLAUDE.md', 'SYNTHETIC_HIDDEN')
        self.write('listed/CLAUDE.md', 'SYNTHETIC_HIDDEN'); self.write('other/CLAUDE.md', 'SYNTHETIC_HIDDEN')
        self.unreadable(self.root/'project/cache', 0); self.unreadable(self.root/'project/listed', 0o444)
        self.unreadable(self.root/'project/other/CLAUDE.md', 0)
        receipt = self.read()
        assert receipt.resolution_status == 'declared'
        for path in ('cache', 'listed'):
            node = self.nodes(receipt)[digest(['unreadable_directory', path])]
            assert (node.presence, node.applicability, node.reason) == ('unknown', 'unknown', 'instruction_directory_unreadable')
        node = self.nodes(receipt)[digest('other/CLAUDE.md')]
        assert (node.presence, node.applicability, node.reason) == ('unknown', 'out_of_scope', 'instruction_file_unreadable')
        assert self.state(receipt, 'CLAUDE.md') == ('declared_applicable', 'directory_concatenation')
        self.unreadable(self.write('src/task/file/CLAUDE.md', 'SYNTHETIC_HIDDEN'), 0)
        hidden = self.read()
        assert hidden.resolution_status == 'blocked'
        node = self.nodes(hidden)[digest('src/task/file/CLAUDE.md')]
        assert (node.presence, node.applicability, node.reason) == ('unknown', 'unknown', 'instruction_file_unreadable')
        self.write('src/task/deep/CLAUDE.md', 'SYNTHETIC_HIDDEN'); self.unreadable(self.root/'project/src/task/deep', 0)
        blocked = self.read()
        assert blocked.resolution_status == 'blocked'
        assert digest(['unreadable_directory', 'src/task/deep']) in self.nodes(blocked)

    def test_files_that_are_never_expanded_are_hashed_without_being_kept(self):
        import tracemalloc
        from agent_harness_lab.sources import claude_instructions as module
        size = 48*1024*1024
        self.write('CLAUDE.md', 'SYNTHETIC_ROOT')
        for name in ('unrelated/CLAUDE.local.md', 'unrelated/CLAUDE.md', 'src/task/deep/CLAUDE.local.md'):
            path = self.write(name, b'')
            with path.open('r+b') as handle: handle.truncate(size)
        reads, original = [], Folder.read
        def recorded(folder, name, maximum=65536, **options):
            reads.append((name, maximum)); return original(folder, name, maximum, **options)
        tracemalloc.start()
        try:
            with patch.object(Folder, 'read', recorded): receipt = self.read()
            peak = tracemalloc.get_traced_memory()[1]
        finally: tracemalloc.stop()
        assert peak < 16*1024*1024
        assert {maximum for _, maximum in reads} == {module.SKIPPED_BYTES}
        assert [name for name, _ in reads] == ['project/CLAUDE.md']
        sparse = hashlib.sha256(bytes(size)).hexdigest()
        assert self.nodes(receipt)[digest('unrelated/CLAUDE.md')].content_sha256 == sparse
        assert self.state(receipt, 'unrelated/CLAUDE.md') == ('out_of_scope', 'outside_task_scope')
        for path in ('unrelated/CLAUDE.local.md', 'src/task/deep/CLAUDE.local.md'):
            assert self.state(receipt, path) == ('private_excluded', 'private_excluded')
            assert self.nodes(receipt)[digest(path)].content_sha256 == sparse

    def test_oversized_on_demand_files_are_truncated_not_skipped_and_say_so(self):
        self.write('src/task/sub/note.md', 'SYNTHETIC_NOTE'); self.write('src/task/sub/late.md', 'SYNTHETIC_LATE')
        filler = 'x'*40000  # the smallest documented on-demand limit, in characters
        self.write('src/task/sub/CLAUDE.md', '@note.md\n'+filler+'\n@late.md\n')
        receipt = self.read()
        assert self.state(receipt, 'src/task/sub/CLAUDE.md') == ('unknown', 'on_demand_truncation_unverified')
        assert self.state(receipt, 'src/task/sub/note.md') == ('unknown', 'on_demand_import')
        assert self.state(receipt, 'src/task/sub/late.md') == ('unknown', 'import_beyond_on_demand_truncation_unverified')
        assert ('imports', digest('src/task/sub/CLAUDE.md'), digest('src/task/sub/late.md')) in self.edges(receipt)
        self.write('src/task/sub/CLAUDE.md', b'@note.md\n'+b'x'*(4*1024*1024+10))
        large = self.read()
        assert self.state(large, 'src/task/sub/CLAUDE.md') == ('unknown', 'on_demand_truncation_unverified')
        assert self.state(large, 'src/task/sub/note.md') == ('unknown', 'on_demand_import')
        assert ('imports', digest('src/task/sub/CLAUDE.md'), digest('src/task/sub/note.md')) in self.edges(large)
        assert imports('@a.md\r\n@b.md', 8) == [('a.md', None), ('b.md', 'import_beyond_on_demand_truncation_unverified')]
        self.profile['task_cwd'] = 'src/task/sub'
        assert self.state(self.read(), 'src/task/sub/CLAUDE.md') == ('unknown', 'runtime_size_limit_unverified')

    def test_cycle_detection_stays_near_linear_for_repeated_imports(self):
        self.profile['task_cwd'] = '.'
        count = 8000
        self.write('CLAUDE.md', '@hub.md @back.md')
        self.write('hub.md', ' '.join(f'@f{i}.md' for i in range(count)))
        for i in range(count): self.write(f'f{i}.md', '')
        self.write('back.md', ' '.join(['@hub.md']*count+['@CLAUDE.md']))
        from agent_harness_lab.sources import claude_instructions as module
        with patch.object(module, 'cyclic', wraps=module.cyclic) as classify: receipt = self.read()
        # Work is bounded by distinct edges, not repeated tokens: one pass over deduplicated pairs.
        assert classify.call_count == 1
        pairs = classify.call_args.args[0]
        assert len(pairs) == len(set(pairs)); assert len(pairs) == count+4
        edges = self.edges(receipt)
        assert ('import_cycle', digest('back.md'), digest('CLAUDE.md')) in edges
        assert ('import_cycle', digest('CLAUDE.md'), digest('back.md')) in edges
        assert ('imports', digest('back.md'), digest('hub.md')) in edges
        assert ('imports', digest('hub.md'), digest('f0.md')) in edges

    def test_cycle_classification_is_iterative_on_deep_chains(self):
        from agent_harness_lab.sources.claude_instructions import cyclic
        chain = [(i, i+1) for i in range(200000)]
        assert cyclic(chain) == set()
        assert len(cyclic(chain+[(200000, 0)])) == 200001

    def test_unresolved_references_are_aggregated_per_importing_file(self):
        self.profile['task_cwd'] = '.'
        tokens = [f'@missing-{i}.md @/abs/{i}.md @~/home-{i}.md @bad\\{i}.md' for i in range(1000)]
        self.write('CLAUDE.md', ' '.join(tokens))
        receipt = self.read()
        imported = [n for n in receipt.nodes if n.reason != 'missing' and n.path_sha256 != digest('CLAUDE.md')]
        assert sorted(n.reason for n in imported) == ['external_import_not_captured', 'import_missing', 'invalid_import_path']

    def test_uncertain_markdown_regions_never_yield_declared_imports(self):
        uncertain = 'import_markdown_context_uncertain'
        text = '\n'.join(['start `span', '@span-inside.md', 'end` @after-span.md', '', '> ```', '> @quoted-fence.md',
                          '> ```', '- ```', '  @list-fence.md', '  ```', '', '    @indented.md', '\t@tabbed.md', '@plain.md'])
        found = dict(imports(text))
        assert found['span-inside.md'] == uncertain
        assert found['after-span.md'] == None
        assert 'quoted-fence.md' not in found; assert 'list-fence.md' not in found
        assert (found['indented.md'], found['tabbed.md'], found['plain.md']) == (uncertain, uncertain, None)
        assert imports('```@inline.md``` text\n@next.md') == [('next.md', None)]
        self.profile['task_cwd'] = '.'
        self.write('CLAUDE.md', 'a `b\n@target.md c` d'); self.write('target.md', 'SYNTHETIC_TARGET')
        assert self.state(self.read(), 'target.md') == ('unknown', uncertain)

    def test_empty_launch_file_is_visible_but_not_applicable(self):
        self.write('CLAUDE.md', ' \n\t'); self.write('AGENTS.md', 'SYNTHETIC_AGENTS')
        receipt = self.read()
        assert self.state(receipt, 'CLAUDE.md') == ('unknown', 'empty')
        assert self.state(receipt, 'AGENTS.md') == ('shadowed', 'assumed_default_instruction_files_setting')

    def test_content_import_and_cwd_changes_invalidate_the_receipt(self):
        self.write('CLAUDE.md', '@notes.md'); self.write('src/task/CLAUDE.md', 'SYNTHETIC_LEAF')
        notes = self.write('notes.md', 'SYNTHETIC_ONE')
        first = self.read().sha256
        notes.write_text('SYNTHETIC_TWO'); second = self.read().sha256
        self.profile['task_cwd'] = '.'; third = self.read().sha256
        assert len({first, second, third}) == 3

    def test_versions_and_shapes_are_exact(self):
        self.write('CLAUDE.md', 'SYNTHETIC_PRESENT')
        self.profile['version'] = '2.1.316'
        assert (self.read().resolution_status, self.read().nodes) == ('unsupported', ())
        claude = dict(self.profile, version='2.1.317')
        validate(dict(claude, version='9.9.9', fallback_filenames=[]))

    @pytest.mark.parametrize('change', [
        pytest.param(dict(fallback_filenames=[]), id='fallback_filenames'),
        pytest.param(dict(project_doc_max_bytes=1), id='project_doc_max_bytes'),
        pytest.param(dict(runtime='codex', version='0.166.0'), id='codex_runtime'),
    ])
    def test_shapes_of_other_runtimes_are_rejected(self, change):
        with pytest.raises(ValueError): validate(dict(self.profile, **change))
