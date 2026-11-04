"""Synthetic Claude Code memory inventory; declared imports are never loading proof."""
from dataclasses import asdict
import hashlib
import json
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

    def test_root_nested_dot_claude_and_local_files_are_concatenated_not_shadowed(self):
        for name in ('CLAUDE.md', '.claude/CLAUDE.md', 'src/CLAUDE.local.md', 'src/task/CLAUDE.md'):
            self.write(name, 'SYNTHETIC_MEMORY')
        receipt = self.read()
        active = {n.path_sha256 for n in receipt.nodes if n.applicability == 'declared_applicable'}
        self.assertEqual(receipt.resolution_status, 'declared')
        self.assertEqual(active, {digest(p) for p in ('CLAUDE.md', '.claude/CLAUDE.md', 'src/CLAUDE.local.md', 'src/task/CLAUDE.md')})
        self.assertIn(('parent', digest('.claude/CLAUDE.md'), digest('src/CLAUDE.local.md')), self.edges(receipt))
        self.assertIn(('parent', digest('src/CLAUDE.local.md'), digest('src/task/CLAUDE.md')), self.edges(receipt))
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
        original = Folder.read
        def guarded(folder, name, *args):
            self.assertTrue(name.startswith('project/'), name)
            return original(folder, name, *args)
        with patch.object(Folder, 'read', guarded): receipt = self.read()
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
        self.assertEqual(self.state(receipt, '.claude/rules/testing.md'), ('unknown', 'rules_not_modeled'))
        self.assertEqual(self.state(receipt, 'src/task/deep/CLAUDE.md'), ('unknown', 'on_demand_subdirectory'))
        self.assertEqual(self.state(receipt, 'other/CLAUDE.md'), ('out_of_scope', 'outside_task_scope'))

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
