"""Synthetic Claude Code memory inventory; declared imports are never loading proof."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_harness_lab.sources.claude_instructions import imports
from agent_harness_lab.sources.git_view import Folder
from agent_harness_lab.sources.instructions import capture, validate
from agent_harness_lab.sources.schema import digest


class ClaudeInstructionInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root/'project/src/task').mkdir(parents=True)
        self.profile = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='src/task')

    def write(self, name, text):
        path = self.root/'project'/name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode())
        return path

    def read(self, selected=()):
        with Folder(self.root, time.monotonic()+30, limit_total_bytes=False, limit_tree=False) as folder:
            value = capture(self.profile, folder, 'project', selected)
            folder.verify()
        dumped = json.dumps(asdict(value))
        self.assertNotIn(str(self.root), dumped); self.assertNotIn('SYNTHETIC_', dumped)
        self.assertTrue(all(n.observed_loading == 'unknown' for n in value.nodes))
        self.assertEqual((value.ambient_state, value.observed_loading), ('unknown', 'unknown'))
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
        self.assertEqual(receipt.resolution_status, 'declared')
        self.assertEqual(active, {digest(p) for p in ('CLAUDE.md', '.claude/CLAUDE.md', 'src/task/CLAUDE.md')})
        self.assertEqual(self.state(receipt, 'src/CLAUDE.local.md'), ('private_excluded', 'private_excluded'))
        self.assertIn(('parent', digest('.claude/CLAUDE.md'), digest('src/task/CLAUDE.md')), self.edges(receipt))
        self.assertFalse(any(digest('src/CLAUDE.local.md') in (s, t) for _, s, t in self.edges(receipt)))
        self.assertFalse(any(e.kind == 'shadows' for e in receipt.edges))
        self.assertEqual(self.state(receipt, 'src/CLAUDE.md'), ('unknown', 'missing'))

    def test_imports_resolve_relative_to_the_importing_file_within_four_hops(self):
        self.profile['task_cwd'] = '.'
        self.write('CLAUDE.md', 'SYNTHETIC_ROOT @docs/one.md')
        self.write('docs/one.md', '@two.md'); self.write('docs/two.md', '@nested/three.md')
        self.write('docs/nested/three.md', '@../four.md'); self.write('docs/four.md', '@five.md')
        self.write('docs/five.md', '@six.md'); self.write('docs/six.md', 'SYNTHETIC_UNREACHED')
        self.write('two.md', 'SYNTHETIC_WRONG_SCOPE')
        receipt = self.read()
        for path in ('docs/one.md', 'docs/two.md', 'docs/nested/three.md', 'docs/four.md'):
            self.assertEqual(self.state(receipt, path), ('declared_applicable', 'import'))
        self.assertEqual(self.state(receipt, 'docs/five.md'), ('unknown', 'import_depth_limit'))
        self.assertNotIn(digest('docs/six.md'), self.nodes(receipt))
        self.assertNotIn(digest('two.md'), self.nodes(receipt))
        self.assertIn(('imports', digest('docs/nested/three.md'), digest('docs/four.md')), self.edges(receipt))

    def test_missing_self_and_mutual_imports_terminate_with_explicit_states(self):
        self.profile['task_cwd'] = '.'
        self.write('CLAUDE.md', '@a.md @absent.md @CLAUDE.md')
        self.write('a.md', '@b.md'); self.write('b.md', '@a.md')
        receipt = self.read(); edges = self.edges(receipt)
        absent = self.unresolved(receipt, 'CLAUDE.md', 'import_missing')
        self.assertEqual((absent.presence, absent.applicability, absent.reason), ('missing', 'unknown', 'import_missing'))
        self.assertIn(('import_cycle', digest('CLAUDE.md'), digest('CLAUDE.md')), edges)
        self.assertIn(('import_cycle', digest('b.md'), digest('a.md')), edges)
        self.assertIn(('import_cycle', digest('a.md'), digest('b.md')), edges)
        self.assertIn(('imports', digest('CLAUDE.md'), digest('a.md')), edges)
        self.assertEqual(self.state(receipt, 'b.md'), ('declared_applicable', 'import'))

    def test_import_syntax_skips_code_quotes_and_mentions_and_flags_comments(self):
        text = '\n'.join(['see @real.md and `@span.md` or ``@double.md``', 'mail user@example.com @"quoted.md"',
                          '```', '@fenced.md', '```', '~~~~', '@tilde.md', '~~~', '@still-fenced.md', '~~~~',
                          r'@Design\ Docs/api.md', '<!-- @hidden.md', '@hidden-too.md -->@after.md'])
        comment = 'import_inside_html_comment'
        self.assertEqual(imports(text), [('real.md', None), ('Design Docs/api.md', None), ('hidden.md', comment),
                                         ('hidden-too.md', comment)])
        self.assertEqual(imports('<!-- a --> @after.md <!-- @in.md'), [('after.md', None), ('in.md', comment)])
        self.profile['task_cwd'] = '.'
        self.write('CLAUDE.md', '<!-- @note.md -->'); self.write('note.md', 'SYNTHETIC_NOTE')
        self.assertEqual(self.state(self.read(), 'note.md'), ('unknown', 'import_inside_html_comment'))

    def test_external_and_out_of_cwd_imports_are_never_expanded_or_read_outside_the_project(self):
        self.write('../outside.md', 'SYNTHETIC_OUTSIDE')
        self.write('src/task/CLAUDE.md', '@../../../outside.md @/etc/hostname @~/.claude/x.md @../../shared.md')
        self.write('shared.md', '@deeper.md'); self.write('deeper.md', 'SYNTHETIC_DEEPER')
        original = Folder.open  # read() and the streaming hash both open through it
        def guarded(folder, name, *args, **options):
            self.assertTrue(name.startswith('project/'), name)
            return original(folder, name, *args, **options)
        with patch.object(Folder, 'open', guarded): receipt = self.read()
        external = [n for n in receipt.nodes if n.origin == 'external_reference']
        self.assertEqual(len(external), 1)
        self.assertTrue(all((n.presence, n.content_sha256, n.applicability) == ('unknown', None, 'unknown') for n in external))
        self.assertEqual(self.state(receipt, 'shared.md'), ('unknown', 'external_import_requires_approval'))
        self.assertNotIn(digest('deeper.md'), self.nodes(receipt))

    def test_agents_files_follow_the_documented_default_only_as_a_declaration(self):
        self.write('AGENTS.md', 'SYNTHETIC_AGENTS')
        self.assertEqual(self.state(self.read(), 'AGENTS.md'), ('unknown', 'instruction_files_setting_unknown'))
        self.write('src/CLAUDE.md', 'SYNTHETIC_CLAUDE')
        receipt = self.read()
        self.assertEqual(self.state(receipt, 'AGENTS.md'), ('shadowed', 'assumed_default_instruction_files_setting'))
        self.assertIn(('shadows', digest('src/CLAUDE.md'), digest('AGENTS.md')), self.edges(receipt))
        self.write('src/CLAUDE.md', '@../AGENTS.md')
        self.profile['task_cwd'] = 'src'
        receipt = self.read()
        self.assertEqual(self.state(receipt, 'AGENTS.md'), ('shadowed', 'assumed_default_instruction_files_setting'))
        self.assertIn(('imports', digest('src/CLAUDE.md'), digest('AGENTS.md')), self.edges(receipt))
        self.profile['task_cwd'] = '.'; self.write('CLAUDE.md', '@AGENTS.md')
        receipt = self.read()
        self.assertEqual(self.state(receipt, 'AGENTS.md'), ('declared_applicable', 'import'))
        self.assertFalse(any(e.kind == 'shadows' for e in receipt.edges))

    def test_selected_rules_subdirectory_and_foreign_files_stay_unknown_or_unsupported(self):
        self.write('AGENTS.override.md', 'SYNTHETIC_OVERRIDE')
        self.write('.claude/rules/testing.md', '---\npaths:\n  - "src/**"\n---\nSYNTHETIC_RULE')
        self.write('src/task/deep/CLAUDE.md', 'SYNTHETIC_ON_DEMAND')
        self.write('other/CLAUDE.md', 'SYNTHETIC_OTHER_SCOPE')
        receipt = self.read(['AGENTS.override.md', '.claude/rules/testing.md', 'src/task/deep/CLAUDE.md', 'other/CLAUDE.md'])
        self.assertEqual(self.state(receipt, 'AGENTS.override.md'), ('unsupported', 'unrecognized_filename'))
        self.assertEqual(self.state(receipt, '.claude/rules/testing.md'), ('unknown', 'rule_conditional_paths'))
        self.assertEqual(self.state(receipt, 'src/task/deep/CLAUDE.md'), ('unknown', 'on_demand_subdirectory'))
        self.assertEqual(self.state(receipt, 'other/CLAUDE.md'), ('out_of_scope', 'outside_task_scope'))

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
        self.assertEqual(self.state(receipt, 'AGENTS.md'), ('shadowed', 'assumed_default_instruction_files_setting'))
        for path in ('src/task/deep/CLAUDE.md', 'src/task/deep/.claude/CLAUDE.md', 'src/task/lone/AGENTS.md'):
            self.assertEqual(self.state(receipt, path), ('unknown', 'on_demand_subdirectory'))
        self.assertEqual(self.state(receipt, 'src/task/deep/AGENTS.md'), ('shadowed', 'assumed_default_instruction_files_setting'))
        self.assertIn(('shadows', digest('src/task/deep/CLAUDE.md'), digest('src/task/deep/AGENTS.md')), edges)
        self.assertEqual(self.state(receipt, 'other/CLAUDE.md'), ('out_of_scope', 'outside_task_scope'))
        self.assertEqual(self.state(receipt, 'other/AGENTS.md'), ('out_of_scope', 'outside_task_scope'))
        self.assertEqual(self.state(receipt, 'src/task/deep/notes.md'), ('unknown', 'on_demand_import'))
        self.assertEqual(self.unresolved(receipt, 'src/task/deep/CLAUDE.md', 'import_missing').presence, 'missing')
        self.assertIn(('import_cycle', digest('src/task/deep/loop.md'), digest('src/task/deep/CLAUDE.md')), edges)
        self.assertEqual(self.state(receipt, 'src/task/linked/CLAUDE.md'), ('unsupported', 'instruction_link_not_followed'))
        self.assertNotIn(digest('vendor/x/CLAUDE.md'), self.nodes(receipt))
        self.assertFalse(any(n.applicability == 'declared_applicable' for n in receipt.nodes if n.reason != 'directory_concatenation'))
        self.profile['task_cwd'] = 'src/task/deep'
        moved = self.read()
        self.assertEqual(self.state(moved, 'src/task/deep/CLAUDE.md'), ('declared_applicable', 'directory_concatenation'))
        self.assertEqual(self.state(moved, 'src/task/deep/notes.md'), ('declared_applicable', 'import'))
        self.assertEqual(self.state(moved, 'src/task/lone/AGENTS.md'), ('out_of_scope', 'outside_task_scope'))
        self.assertNotEqual(moved.sha256, receipt.sha256)

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
        self.assertEqual(len(local), 4)
        self.assertEqual({n.applicability for n in local}, {'private_excluded'})
        self.assertEqual(self.nodes(receipt)[digest('CLAUDE.local.md')].content_sha256,
                         hashlib.sha256(b'SYNTHETIC_PRIVATE @private-import.md').hexdigest())
        self.assertNotIn(digest('private-import.md'), self.nodes(receipt))
        self.assertEqual(self.state(receipt, 'AGENTS.md'), ('shadowed', 'assumed_default_instruction_files_setting'))
        for mode in ('claude-md-and-agents-md', 'surprise-mode'):
            self.profile['instruction_files'] = mode
            self.assertEqual({n.applicability for n in self.read().nodes if n.path_sha256 in {x.path_sha256 for x in local}},
                             {'private_excluded'})

    def test_declared_instruction_file_modes_and_unknown_modes(self):
        self.profile['task_cwd'] = '.'
        self.write('AGENTS.md', '@agents-note.md'); self.write('agents-note.md', 'SYNTHETIC_NOTE')
        self.write('sub/AGENTS.md', 'SYNTHETIC_SUB_AGENTS'); self.write('sub/CLAUDE.md', 'SYNTHETIC_SUB')
        self.profile['instruction_files'] = 'claude-md-or-agents-md'
        receipt = self.read()
        self.assertEqual(self.state(receipt, 'AGENTS.md'), ('declared_applicable', 'declared_instruction_files_setting'))
        self.assertEqual(self.state(receipt, 'agents-note.md'), ('declared_applicable', 'import'))
        self.assertEqual(self.state(receipt, 'sub/AGENTS.md'), ('shadowed', 'declared_instruction_files_setting'))
        self.write('CLAUDE.md', 'SYNTHETIC_ROOT')
        self.assertEqual(self.state(self.read(), 'AGENTS.md'), ('shadowed', 'declared_instruction_files_setting'))
        self.profile['instruction_files'] = 'claude-md-and-agents-md'
        both = self.read()
        self.assertEqual(self.state(both, 'AGENTS.md'), ('declared_applicable', 'declared_instruction_files_setting'))
        self.assertEqual(self.state(both, 'sub/AGENTS.md'), ('unknown', 'on_demand_subdirectory'))
        self.profile['instruction_files'] = 'claude-md'
        self.assertEqual(self.state(self.read(), 'AGENTS.md'), ('out_of_scope', 'excluded_by_declared_instruction_files'))
        self.profile['instruction_files'] = 'managed-only'
        managed = self.read()
        self.assertEqual(self.state(managed, 'CLAUDE.md'), ('out_of_scope', 'excluded_by_declared_instruction_files'))
        self.assertEqual(self.state(managed, 'sub/CLAUDE.md'), ('unknown', 'on_demand_subdirectory'))
        self.profile['instruction_files'] = 'surprise-mode'
        unknown = self.read()
        for path in ('CLAUDE.md', 'AGENTS.md', 'sub/CLAUDE.md', 'sub/AGENTS.md'):
            self.assertEqual(self.state(unknown, path), ('unknown', 'instruction_files_setting_unknown'))
        self.assertFalse(any(n.applicability in ('declared_applicable', 'shadowed') for n in unknown.nodes))
        self.profile.pop('instruction_files'); self.profile['claude_md_excludes'] = ['**/sub/CLAUDE.md', 'CLAUDE.md']
        excluded = self.read()
        self.assertEqual(self.state(excluded, 'sub/CLAUDE.md'), ('unknown', 'claude_md_excludes_match_unverified'))
        self.assertEqual(self.state(excluded, 'CLAUDE.md'), ('unknown', 'claude_md_excludes_match_unverified'))
        for invalid in (dict(self.profile, instruction_files='bad mode'), dict(self.profile, claude_md_excludes='x'),
                        dict(self.profile, claude_md_excludes=['']), dict(self.profile, project_trust='declared_trusted')):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError): validate(invalid)

    def test_tree_enumeration_bounds_block_instead_of_truncating(self):
        from agent_harness_lab.sources import claude_instructions as module
        self.write('CLAUDE.md', 'SYNTHETIC_ROOT'); self.write('a/b/c/CLAUDE.md', 'SYNTHETIC_DEEP')
        for name, bound in (('TREE_ENTRIES', 3), ('TREE_DEPTH', 1)):
            with self.subTest(bound=name), patch.object(module, name, bound):
                receipt = self.read()
                self.assertEqual(receipt.resolution_status, 'blocked')
                self.assertIn('tree_enumeration_bound_exceeded', {n.reason for n in receipt.nodes})

    def test_unsafe_targets_fail_closed_without_reading_them(self):
        self.profile['task_cwd'] = '.'
        (self.root/'project/.git').mkdir(); self.write('.git/config', 'SYNTHETIC_GIT_CONFIG')
        self.write('docs/x.md', 'SYNTHETIC_DOC'); self.write('binary.md', b'\xff\xfe')
        self.write('CLAUDE.md', '@.git/config @docs @binary.md')
        receipt = self.read(); nodes = self.nodes(receipt)
        unsafe = [n for n in receipt.nodes if n.reason == 'unsafe_import_target']
        self.assertEqual([(n.presence, n.applicability) for n in unsafe], [('unknown', 'unsupported')])
        directory = self.unresolved(receipt, 'CLAUDE.md', 'import_target_not_regular_file')
        self.assertEqual((directory.presence, directory.applicability), ('unknown', 'unsupported'))
        self.assertEqual(nodes[digest('binary.md')].content_sha256, hashlib.sha256(b'\xff\xfe').hexdigest())
        self.assertEqual(self.state(receipt, 'binary.md'), ('unknown', 'non_utf8_import'))
        (self.root/'project/link.md').symlink_to(self.root/'project/docs/x.md')
        self.write('CLAUDE.md', '@link.md')
        with self.assertRaises(ValueError): self.read()
        (self.root/'project/vendor').mkdir(); (self.root/'project/vendor/.git').write_text('gitdir: elsewhere')
        self.write('vendor/x.md', 'SYNTHETIC_VENDOR'); self.write('CLAUDE.md', '@vendor/x.md')
        with self.assertRaises(ValueError): self.read()

    def test_import_through_a_link_or_file_component_fails_closed(self):
        self.profile['task_cwd'] = '.'
        self.write('docs/x.md', 'SYNTHETIC_DOC'); self.write('../elsewhere/x.md', 'SYNTHETIC_OUTSIDE')
        (self.root/'project/inside').symlink_to(self.root/'project/docs', target_is_directory=True)
        (self.root/'project/outside').symlink_to(self.root/'elsewhere', target_is_directory=True)
        for text in ('@inside/x.md', '@outside/x.md', '@docs/x.md/child', '@inside/absent.md'):
            with self.subTest(text=text):
                self.write('CLAUDE.md', text)
                with self.assertRaises(ValueError): self.read()
        self.write('CLAUDE.md', '@absent/x.md')
        self.assertEqual(self.unresolved(self.read(), 'CLAUDE.md', 'import_missing').presence, 'missing')

    def test_on_demand_imports_through_links_or_unreadable_paths_stay_unresolved(self):
        self.write('CLAUDE.md', 'SYNTHETIC_ROOT'); self.write('real/x.md', 'SYNTHETIC_REAL')
        self.write('src/task/note.md', 'SYNTHETIC_NOTE')
        (self.root/'project/src/task/linkdir').symlink_to('../../real', target_is_directory=True)
        (self.root/'project/src/task/linked.md').symlink_to('note.md')
        self.write('src/task/sub/CLAUDE.md', '@../linkdir/x.md @../linked.md @../note.md')
        receipt = self.read()
        self.assertEqual(receipt.resolution_status, 'declared')
        for reason in ('import_crosses_link_or_repository', 'import_target_link_not_followed'):
            node = self.unresolved(receipt, 'src/task/sub/CLAUDE.md', reason)
            self.assertEqual((node.presence, node.applicability), ('unknown', 'unsupported'))
        self.assertEqual(self.state(receipt, 'src/task/note.md'), ('unknown', 'on_demand_import'))
        self.write('src/task/CLAUDE.md', '@linkdir/x.md')
        with self.assertRaises(ValueError): self.read()

    def test_launch_imports_of_linked_instruction_named_files_found_by_the_walk_fail_closed(self):
        self.profile['task_cwd'] = '.'
        (self.root/'outside.md').write_text('SYNTHETIC_OUTSIDE'); self.write('CLAUDE.md', '@sub/CLAUDE.md')
        (self.root/'project/sub').mkdir()
        for link in (lambda path: path.symlink_to('../../outside.md'), lambda path: os.link(self.root/'outside.md', path)):
            target = self.root/'project/sub/CLAUDE.md'; target.unlink(missing_ok=True); link(target)
            with self.subTest(link=link), self.assertRaises(ValueError): self.read()

    def test_empty_launch_agents_file_is_never_admitted_under_a_declared_mode(self):
        self.profile['task_cwd'] = '.'
        self.write('AGENTS.md', '   \n'); self.write('agents-note.md', 'SYNTHETIC_NOTE')
        for mode in ('claude-md-or-agents-md', 'claude-md-and-agents-md'):
            self.profile['instruction_files'] = mode
            if mode == 'claude-md-and-agents-md': self.write('CLAUDE.md', 'SYNTHETIC_ROOT')
            with self.subTest(mode=mode):
                receipt = self.read()
                self.assertEqual(self.state(receipt, 'AGENTS.md'), ('unknown', 'empty'))
                self.assertFalse(any(e.target_sha256 == digest('AGENTS.md') for e in receipt.edges))

    def test_empty_local_files_are_private_not_missing(self):
        self.profile['task_cwd'] = '.'
        for name in ('CLAUDE.local.md', 'deep/CLAUDE.local.md'): self.write(name, '')
        receipt = self.read()
        for name in ('CLAUDE.local.md', 'deep/CLAUDE.local.md'):
            node = self.nodes(receipt)[digest(name)]
            self.assertEqual((node.presence, node.applicability, node.reason), ('present', 'private_excluded', 'private_excluded'))

    def unreadable(self, path, mode):
        path.chmod(mode); self.addCleanup(path.chmod, 0o755 if path.is_dir() else 0o644)

    @unittest.skipIf(os.geteuid() == 0, 'permissions do not bind root')
    def test_unreadable_import_targets_stay_unresolved_on_demand_and_fail_the_launch_chain(self):
        self.write('src/task/sub/locked/x.md', 'SYNTHETIC_LOCKED'); self.write('src/task/sub/CLAUDE.md', '@locked/x.md')
        self.unreadable(self.root/'project/src/task/sub/locked', 0)
        node = self.unresolved(self.read(), 'src/task/sub/CLAUDE.md', 'import_target_unreadable')
        self.assertEqual((node.presence, node.applicability, node.reason), ('unknown', 'unsupported', 'import_target_unreadable'))
        self.write('src/task/CLAUDE.md', '@sub/locked/x.md')
        with self.assertRaises(PermissionError): self.read()
        (self.root/'project/src/task/CLAUDE.md').unlink()
        self.profile['task_cwd'] = 'src'
        self.write('src/CLAUDE.md', '@../other/CLAUDE.md'); self.write('other/CLAUDE.md', 'SYNTHETIC_HIDDEN')
        self.unreadable(self.root/'project/other/CLAUDE.md', 0)
        with self.assertRaises(PermissionError): self.read()

    @unittest.skipIf(os.geteuid() == 0, 'permissions do not bind root')
    def test_unreadable_entries_are_reported_and_block_only_where_they_can_hide_instructions(self):
        self.write('CLAUDE.md', 'SYNTHETIC_ROOT'); self.write('cache/CLAUDE.md', 'SYNTHETIC_HIDDEN')
        self.write('listed/CLAUDE.md', 'SYNTHETIC_HIDDEN'); self.write('other/CLAUDE.md', 'SYNTHETIC_HIDDEN')
        self.unreadable(self.root/'project/cache', 0); self.unreadable(self.root/'project/listed', 0o444)
        self.unreadable(self.root/'project/other/CLAUDE.md', 0)
        receipt = self.read()
        self.assertEqual(receipt.resolution_status, 'declared')
        for path in ('cache', 'listed'):
            node = self.nodes(receipt)[digest(['unreadable_directory', path])]
            self.assertEqual((node.presence, node.applicability, node.reason), ('unknown', 'unknown', 'instruction_directory_unreadable'))
        node = self.nodes(receipt)[digest('other/CLAUDE.md')]
        self.assertEqual((node.presence, node.applicability, node.reason), ('unknown', 'out_of_scope', 'instruction_file_unreadable'))
        self.assertEqual(self.state(receipt, 'CLAUDE.md'), ('declared_applicable', 'directory_concatenation'))
        self.unreadable(self.write('src/task/file/CLAUDE.md', 'SYNTHETIC_HIDDEN'), 0)
        hidden = self.read()
        self.assertEqual(hidden.resolution_status, 'blocked')
        node = self.nodes(hidden)[digest('src/task/file/CLAUDE.md')]
        self.assertEqual((node.presence, node.applicability, node.reason), ('unknown', 'unknown', 'instruction_file_unreadable'))
        self.write('src/task/deep/CLAUDE.md', 'SYNTHETIC_HIDDEN'); self.unreadable(self.root/'project/src/task/deep', 0)
        blocked = self.read()
        self.assertEqual(blocked.resolution_status, 'blocked')
        self.assertIn(digest(['unreadable_directory', 'src/task/deep']), self.nodes(blocked))

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
        self.assertLess(peak, 16*1024*1024)
        self.assertEqual({maximum for _, maximum in reads}, {module.SKIPPED_BYTES})
        self.assertEqual([name for name, _ in reads], ['project/CLAUDE.md'])
        sparse = hashlib.sha256(bytes(size)).hexdigest()
        self.assertEqual(self.nodes(receipt)[digest('unrelated/CLAUDE.md')].content_sha256, sparse)
        self.assertEqual(self.state(receipt, 'unrelated/CLAUDE.md'), ('out_of_scope', 'outside_task_scope'))
        for path in ('unrelated/CLAUDE.local.md', 'src/task/deep/CLAUDE.local.md'):
            self.assertEqual(self.state(receipt, path), ('private_excluded', 'private_excluded'))
            self.assertEqual(self.nodes(receipt)[digest(path)].content_sha256, sparse)

    def test_oversized_on_demand_files_are_truncated_not_skipped_and_say_so(self):
        self.write('src/task/sub/note.md', 'SYNTHETIC_NOTE'); self.write('src/task/sub/late.md', 'SYNTHETIC_LATE')
        filler = 'x'*40000  # the smallest documented on-demand limit, in characters
        self.write('src/task/sub/CLAUDE.md', '@note.md\n'+filler+'\n@late.md\n')
        receipt = self.read()
        self.assertEqual(self.state(receipt, 'src/task/sub/CLAUDE.md'), ('unknown', 'on_demand_truncation_unverified'))
        self.assertEqual(self.state(receipt, 'src/task/sub/note.md'), ('unknown', 'on_demand_import'))
        self.assertEqual(self.state(receipt, 'src/task/sub/late.md'), ('unknown', 'import_beyond_on_demand_truncation_unverified'))
        self.assertIn(('imports', digest('src/task/sub/CLAUDE.md'), digest('src/task/sub/late.md')), self.edges(receipt))
        self.write('src/task/sub/CLAUDE.md', b'@note.md\n'+b'x'*(4*1024*1024+10))
        large = self.read()
        self.assertEqual(self.state(large, 'src/task/sub/CLAUDE.md'), ('unknown', 'on_demand_truncation_unverified'))
        self.assertEqual(self.state(large, 'src/task/sub/note.md'), ('unknown', 'on_demand_import'))
        self.assertIn(('imports', digest('src/task/sub/CLAUDE.md'), digest('src/task/sub/note.md')), self.edges(large))
        self.assertEqual(imports('@a.md\r\n@b.md', 8), [('a.md', None), ('b.md', 'import_beyond_on_demand_truncation_unverified')])
        self.profile['task_cwd'] = 'src/task/sub'
        self.assertEqual(self.state(self.read(), 'src/task/sub/CLAUDE.md'), ('unknown', 'runtime_size_limit_unverified'))

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
        self.assertEqual(classify.call_count, 1)
        pairs = classify.call_args.args[0]
        self.assertEqual(len(pairs), len(set(pairs))); self.assertEqual(len(pairs), count+4)
        edges = self.edges(receipt)
        self.assertIn(('import_cycle', digest('back.md'), digest('CLAUDE.md')), edges)
        self.assertIn(('import_cycle', digest('CLAUDE.md'), digest('back.md')), edges)
        self.assertIn(('imports', digest('back.md'), digest('hub.md')), edges)
        self.assertIn(('imports', digest('hub.md'), digest('f0.md')), edges)

    def test_cycle_classification_is_iterative_on_deep_chains(self):
        from agent_harness_lab.sources.claude_instructions import cyclic
        chain = [(i, i+1) for i in range(200000)]
        self.assertEqual(cyclic(chain), set())
        self.assertEqual(len(cyclic(chain+[(200000, 0)])), 200001)

    def test_unresolved_references_are_aggregated_per_importing_file(self):
        self.profile['task_cwd'] = '.'
        tokens = [f'@missing-{i}.md @/abs/{i}.md @~/home-{i}.md @bad\\{i}.md' for i in range(1000)]
        self.write('CLAUDE.md', ' '.join(tokens))
        receipt = self.read()
        imported = [n for n in receipt.nodes if n.reason != 'missing' and n.path_sha256 != digest('CLAUDE.md')]
        self.assertEqual(sorted(n.reason for n in imported),
                         ['external_import_not_captured', 'import_missing', 'invalid_import_path'])

    def test_uncertain_markdown_regions_never_yield_declared_imports(self):
        uncertain = 'import_markdown_context_uncertain'
        text = '\n'.join(['start `span', '@span-inside.md', 'end` @after-span.md', '', '> ```', '> @quoted-fence.md',
                          '> ```', '- ```', '  @list-fence.md', '  ```', '', '    @indented.md', '\t@tabbed.md', '@plain.md'])
        found = dict(imports(text))
        self.assertEqual(found['span-inside.md'], uncertain)
        self.assertEqual(found['after-span.md'], None)
        self.assertNotIn('quoted-fence.md', found); self.assertNotIn('list-fence.md', found)
        self.assertEqual((found['indented.md'], found['tabbed.md'], found['plain.md']), (uncertain, uncertain, None))
        self.assertEqual(imports('```@inline.md``` text\n@next.md'), [('next.md', None)])
        self.profile['task_cwd'] = '.'
        self.write('CLAUDE.md', 'a `b\n@target.md c` d'); self.write('target.md', 'SYNTHETIC_TARGET')
        self.assertEqual(self.state(self.read(), 'target.md'), ('unknown', uncertain))

    def test_empty_launch_file_is_visible_but_not_applicable(self):
        self.write('CLAUDE.md', ' \n\t'); self.write('AGENTS.md', 'SYNTHETIC_AGENTS')
        receipt = self.read()
        self.assertEqual(self.state(receipt, 'CLAUDE.md'), ('unknown', 'empty'))
        self.assertEqual(self.state(receipt, 'AGENTS.md'), ('shadowed', 'assumed_default_instruction_files_setting'))

    def test_content_import_and_cwd_changes_invalidate_the_receipt(self):
        self.write('CLAUDE.md', '@notes.md'); self.write('src/task/CLAUDE.md', 'SYNTHETIC_LEAF')
        notes = self.write('notes.md', 'SYNTHETIC_ONE')
        first = self.read().sha256
        notes.write_text('SYNTHETIC_TWO'); second = self.read().sha256
        self.profile['task_cwd'] = '.'; third = self.read().sha256
        self.assertEqual(len({first, second, third}), 3)

    def test_versions_and_shapes_are_exact(self):
        self.write('CLAUDE.md', 'SYNTHETIC_PRESENT')
        self.profile['version'] = '2.1.316'
        self.assertEqual((self.read().resolution_status, self.read().nodes), ('unsupported', ()))
        claude = dict(self.profile, version='2.1.317')
        for invalid in (dict(claude, fallback_filenames=[]), dict(claude, project_doc_max_bytes=1),
                        dict(claude, runtime='codex', version='0.166.0')):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError): validate(invalid)
        validate(dict(claude, version='9.9.9', fallback_filenames=[]))


if __name__ == '__main__':
    unittest.main()
