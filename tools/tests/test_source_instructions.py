"""Synthetic instruction resolution; declared applicability is never loading proof."""
from dataclasses import asdict, FrozenInstanceError
import hashlib
import json
from pathlib import Path
import tempfile
import os
import time
import unittest
from unittest.mock import patch

from agent_harness_lab.sources import harness_inventory
from agent_harness_lab.sources.git_view import Folder
from agent_harness_lab.sources.instructions import capture, validate
from agent_harness_lab.sources.schema import digest


class InstructionInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root/'project/src/task').mkdir(parents=True)
        self.profile = dict(schema_version=1, runtime='codex', version='0.166.0', task_cwd='src/task',
                            fallback_filenames=['TEAM.md'], project_doc_max_bytes=32768)

    def write(self, name, text):
        path = self.root/'project'/name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def read(self, selected=(), **options):
        with Folder(self.root, time.monotonic()+30, limit_total_bytes=False, limit_tree=False) as folder:
            value = capture(self.profile, folder, 'project', selected, **options)
            folder.verify()
        self.assertNotIn(str(self.root), json.dumps(asdict(value)))
        return value

    def nodes(self, receipt): return {node.path_sha256: node for node in receipt.nodes}

    def test_root_nested_override_fallback_and_parent_edges_remain_declared(self):
        self.write('AGENTS.md', 'SYNTHETIC_ROOT')
        self.write('src/AGENTS.md', 'SYNTHETIC_SHADOWED')
        self.write('src/AGENTS.override.md', 'SYNTHETIC_OVERRIDE')
        self.write('src/task/TEAM.md', 'SYNTHETIC_FALLBACK')
        receipt = self.read(); nodes = self.nodes(receipt)
        active = ['AGENTS.md', 'src/AGENTS.override.md', 'src/task/TEAM.md']
        self.assertEqual(receipt.resolution_status, 'declared')
        self.assertEqual({n.path_sha256 for n in receipt.nodes if n.applicability == 'declared_applicable'}, {digest(p) for p in active})
        self.assertEqual(nodes[digest('src/AGENTS.md')].applicability, 'shadowed')
        self.assertIn(('parent', digest(active[0]), digest(active[1])), {(e.kind, e.source_sha256, e.target_sha256) for e in receipt.edges})
        self.assertIn(('shadows', digest(active[1]), digest('src/AGENTS.md')), {(e.kind, e.source_sha256, e.target_sha256) for e in receipt.edges})
        self.assertTrue(all(n.observed_loading == 'unknown' for n in receipt.nodes))
        self.assertEqual(receipt.ambient_state, 'unknown')
        self.assertNotIn('SYNTHETIC_', json.dumps(asdict(receipt)))
        with self.assertRaises(FrozenInstanceError): receipt.resolution_status = 'observed'

    def test_empty_override_does_not_shadow_normal_guidance(self):
        self.write('AGENTS.override.md', ' \n\t')
        self.write('AGENTS.md', 'SYNTHETIC_NORMAL')
        nodes = self.nodes(self.read())
        self.assertEqual(nodes[digest('AGENTS.md')].applicability, 'declared_applicable')
        self.assertEqual(nodes[digest('AGENTS.override.md')].reason, 'empty')

    def test_same_filename_in_unrelated_scope_is_present_but_not_applicable(self):
        self.write('other/AGENTS.md', 'SYNTHETIC_OTHER_SCOPE')
        nodes = self.nodes(self.read(['other/AGENTS.md']))
        self.assertEqual(nodes[digest('other/AGENTS.md')].presence, 'present')
        self.assertEqual(nodes[digest('other/AGENTS.md')].applicability, 'out_of_scope')

    def test_unsupported_runtime_or_version_does_not_guess_precedence(self):
        self.write('AGENTS.md', 'SYNTHETIC_PRESENT')
        for runtime, version in [('claude-code', '2.0.0'), ('codex', '0.165.0')]:
            with self.subTest(runtime=runtime, version=version):
                self.profile.update(runtime=runtime, version=version)
                value = self.read()
                self.assertEqual(value.resolution_status, 'unsupported')
                self.assertEqual(value.nodes, ())

    def test_cwd_settings_content_and_mode_each_invalidate_the_receipt(self):
        path = self.write('AGENTS.md', 'SYNTHETIC_ORIGINAL')
        first = self.read().sha256
        path.write_text('SYNTHETIC_NEW'); second = self.read().sha256
        path.chmod(0o700); third = self.read().sha256
        self.profile['task_cwd'] = 'src'; fourth = self.read().sha256
        self.profile['fallback_filenames'] = []; fifth = self.read().sha256
        self.assertEqual(len({first, second, third, fourth, fifth}), 5)

    def test_cap_exhaustion_does_not_invent_native_truncated_content(self):
        self.write('AGENTS.md', 'SYNTHETIC_TOO_LARGE')
        self.profile['project_doc_max_bytes'] = 4
        value = self.read(); node = self.nodes(value)[digest('AGENTS.md')]
        self.assertEqual(value.resolution_status, 'blocked')
        self.assertEqual(node.applicability, 'unknown')
        self.assertEqual(node.reason, 'byte_limit_requires_native_resolution')
        self.assertEqual(node.content_sha256, hashlib.sha256(b'SYNTHETIC_TOO_LARGE').hexdigest())

    def test_large_instruction_is_fully_hashed_without_an_implicit_byte_limit(self):
        self.profile['task_cwd'] = '.'
        content = 'SYNTHETIC_LARGE\n' + 'x'*(1024*1024+1)
        self.write('AGENTS.md', content)
        for present, limit in ((False, None), (True, None), (True, 2*1024*1024)):
            with self.subTest(present=present, limit=limit):
                if present: self.profile['project_doc_max_bytes'] = limit
                else: self.profile.pop('project_doc_max_bytes', None)
                receipt = self.read(); node = self.nodes(receipt)[digest('AGENTS.md')]
                self.assertEqual(receipt.resolution_status, 'declared')
                self.assertEqual(node.applicability, 'declared_applicable')
                self.assertEqual(node.content_sha256, hashlib.sha256(content.encode()).hexdigest())
                self.assertEqual(node.observed_loading, 'unknown')

    def test_all_fallback_edges_and_selected_files_are_retained(self):
        self.profile.update(task_cwd='.', fallback_filenames=[f'TEAM-{i}.md' for i in range(501)])
        self.write('AGENTS.md', 'SYNTHETIC_ACTIVE')
        for name in self.profile['fallback_filenames']: self.write(name, 'SYNTHETIC_SHADOWED')
        selected = [f'other/selected-{i}.md' for i in range(101)]
        for name in selected: self.write(name, 'SYNTHETIC_OUTSIDE')
        receipt = self.read(selected); nodes = self.nodes(receipt)
        self.assertEqual(len(receipt.nodes), 604)
        self.assertEqual({edge.target_sha256 for edge in receipt.edges if edge.kind == 'shadows'},
                         {digest(name) for name in self.profile['fallback_filenames']})
        self.assertTrue(all(nodes[digest(name)].applicability == 'out_of_scope' for name in selected))

    def test_deep_task_directory_keeps_the_complete_parent_chain(self):
        self.profile['task_cwd'] = '/'.join(['nested']*40)
        (self.root/'project'/self.profile['task_cwd']).mkdir(parents=True)
        self.write('AGENTS.md', 'SYNTHETIC_ROOT')
        leaf = self.profile['task_cwd']+'/AGENTS.md'
        self.write(leaf, 'SYNTHETIC_LEAF')
        receipt = self.read(); nodes = self.nodes(receipt)
        self.assertEqual(nodes[digest(leaf)].applicability, 'declared_applicable')
        self.assertIn(('parent', digest('AGENTS.md'), digest(leaf)),
                      {(edge.kind, edge.source_sha256, edge.target_sha256) for edge in receipt.edges})

    def test_symlinks_invalid_text_and_unsafe_fallbacks_fail_closed(self):
        target = self.write('outside.txt', 'SYNTHETIC_OUTSIDE')
        (self.root/'project/AGENTS.md').symlink_to(target)
        with self.assertRaises(ValueError): self.read()
        (self.root/'project/AGENTS.md').unlink()
        (self.root/'project/AGENTS.md').write_bytes(b'\xff')
        with self.assertRaises(ValueError): self.read()
        (self.root/'project/AGENTS.md').unlink()
        for names in [['../outside.txt'], ['nested/name'], ['.git'], ['TEAM.md', 'TEAM.md']]:
            with self.subTest(names=names):
                self.profile['fallback_filenames'] = names
                with self.assertRaises(ValueError): self.read()

    def home(self, name, text):
        path = self.root/'home'/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text)

    def test_codex_home_is_read_only_when_declared_and_override_beats_agents(self):
        self.write('AGENTS.md', 'SYNTHETIC_PROJECT'); self.home('AGENTS.md', 'SYNTHETIC_GLOBAL')
        self.home('AGENTS.override.md', 'SYNTHETIC_OVERRIDE')
        original, names = Folder.read, []
        def recording(folder, name, *args, **kwargs):
            names.append(name); return original(folder, name, *args, **kwargs)
        with patch.object(Folder, 'read', recording), patch.dict(os.environ, CODEX_HOME=str(self.root/'home')):
            undeclared = self.read()
        self.assertTrue(names and all(name.startswith('project/') for name in names), names)
        self.assertFalse(any(n.origin == 'codex_home' for n in undeclared.nodes))
        receipt = self.read(codex_home='home'); nodes = self.nodes(receipt)
        override, agents = nodes[digest(['codex_home', 'AGENTS.override.md'])], nodes[digest(['codex_home', 'AGENTS.md'])]
        self.assertEqual((override.applicability, override.reason, override.origin),
                         ('declared_applicable', 'codex_home_first_non_empty', 'codex_home'))
        self.assertEqual((agents.applicability, agents.reason), ('shadowed', 'codex_home_precedence'))
        edges = {(e.kind, e.source_sha256, e.target_sha256) for e in receipt.edges}
        self.assertIn(('shadows', override.path_sha256, agents.path_sha256), edges)
        self.assertIn(('parent', override.path_sha256, digest('AGENTS.md')), edges)
        self.assertNotEqual(receipt.sha256, undeclared.sha256)
        self.home('AGENTS.override.md', ' \n')
        nodes = self.nodes(self.read(codex_home='home'))
        self.assertEqual(nodes[digest(['codex_home', 'AGENTS.override.md'])].reason, 'empty')
        self.assertEqual(nodes[digest(['codex_home', 'AGENTS.md'])].applicability, 'declared_applicable')
        self.profile['project_doc_max_bytes'] = len('SYNTHETIC_PROJECT')  # the global file is outside the project budget
        self.assertEqual(self.read(codex_home='home').resolution_status, 'declared')
        for home in ('project', 'project/src', '.', '../home'):
            with self.subTest(home=home), self.assertRaises(ValueError): self.read(codex_home=home)
        self.profile.update(runtime='codex', version='0.165.0')
        self.assertEqual((self.read(codex_home='home').resolution_status, self.read(codex_home='home').nodes), ('unsupported', ()))
        self.profile = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='.')
        with self.assertRaises(ValueError): self.read(codex_home='home')

    def test_project_root_markers_and_explicit_untrust_are_declared_inputs(self):
        self.write('AGENTS.md', 'SYNTHETIC_ROOT'); self.write('src/AGENTS.md', 'SYNTHETIC_MIDDLE')
        self.write('src/task/AGENTS.md', 'SYNTHETIC_LEAF'); self.home('AGENTS.md', 'SYNTHETIC_GLOBAL')
        state = lambda receipt, path: (self.nodes(receipt)[digest(path)].applicability, self.nodes(receipt)[digest(path)].reason)
        self.profile['project_root_markers'] = ['.hg']; (self.root/'project/src/.hg').mkdir()
        rooted = self.read()
        self.assertEqual(state(rooted, 'AGENTS.md'), ('out_of_scope', 'outside_project_root'))
        self.assertEqual(state(rooted, 'src/AGENTS.md'), ('declared_applicable', 'directory_precedence'))
        self.profile['project_root_markers'] = []
        alone = self.read()
        self.assertEqual(state(alone, 'src/AGENTS.md'), ('out_of_scope', 'outside_project_root'))
        self.assertEqual(state(alone, 'src/task/AGENTS.md'), ('declared_applicable', 'directory_precedence'))
        self.profile['project_root_markers'] = ['.absent-marker']
        missing = self.read()
        self.assertEqual(state(missing, 'AGENTS.md'), ('unknown', 'project_root_marker_not_found_in_checkout'))
        self.assertEqual(state(missing, 'src/task/AGENTS.md'), ('declared_applicable', 'directory_precedence'))
        self.profile.pop('project_root_markers')
        self.assertEqual(state(self.read(), 'AGENTS.md'), ('declared_applicable', 'directory_precedence'))
        self.profile['project_trust'] = 'declared_untrusted'
        untrusted = self.read(codex_home='home')
        self.assertEqual({state(untrusted, p) for p in ('AGENTS.md', 'src/AGENTS.md', 'src/task/AGENTS.md')},
                         {('out_of_scope', 'explicitly_untrusted')})
        self.assertEqual(self.nodes(untrusted)[digest(['codex_home', 'AGENTS.md'])].applicability, 'declared_applicable')
        for key, value in (('project_trust', 'trusted'), ('project_root_markers', ['a/b']), ('project_root_markers', '.git'),
                           ('project_root_markers', ['.git', '.git'])):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): validate(dict(self.profile, **{key: value}))

    def test_unknown_marker_ancestors_still_count_toward_the_declared_byte_limit(self):
        state = lambda receipt, path: (self.nodes(receipt)[digest(path)].applicability, self.nodes(receipt)[digest(path)].reason)
        self.write('AGENTS.md', 'SYNTHETIC'); self.write('TEAM.md', 'SYNTHETIC_SHADOWED_FALLBACK')
        self.write('src/task/AGENTS.md', 'LEAF!')
        self.profile.update(project_doc_max_bytes=15, project_root_markers=['.absent-marker'])
        over = self.read()
        self.assertEqual(state(over, 'AGENTS.md'), ('unknown', 'project_root_marker_not_found_in_checkout'))
        self.assertEqual(state(over, 'src/task/AGENTS.md'), ('unknown', 'byte_limit_requires_native_resolution'))
        self.assertEqual(over.resolution_status, 'blocked')
        self.profile['project_doc_max_bytes'] = 9+2+5  # the scope's first file, a separator and the leaf
        fits = self.read()
        self.assertEqual(state(fits, 'src/task/AGENTS.md'), ('declared_applicable', 'directory_precedence'))
        self.assertEqual(fits.resolution_status, 'declared')
        self.profile['project_root_markers'] = ['.hg']; (self.root/'project/src/.hg').mkdir()
        self.profile['project_doc_max_bytes'] = 5
        rooted = self.read()
        self.assertEqual(state(rooted, 'AGENTS.md'), ('out_of_scope', 'outside_project_root'))
        self.assertEqual(state(rooted, 'src/task/AGENTS.md'), ('declared_applicable', 'directory_precedence'))

    def test_inventory_parser_reports_relations_and_never_reads_local_files(self):
        root = self.root/'project'
        for name, text in (('CLAUDE.md', 'SYNTHETIC_ROOT @a.md'), ('src/task/deep/CLAUDE.md', 'SYNTHETIC_DEEP'),
                           ('src/task/deep/AGENTS.md', 'SYNTHETIC_DEEP_AGENTS'), ('other/CLAUDE.md', 'SYNTHETIC_OTHER'),
                           ('src/CLAUDE.local.md', 'SYNTHETIC_PRIVATE'), ('src/AGENTS.override.md', 'x'*(32*1024+1))):
            self.write(name, text)
        claude = harness_inventory.capture(root, dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='src/task'))
        nodes = {n.path: n for n in claude.nodes}
        facts = lambda node: dict(node.facts)
        self.assertEqual(facts(nodes['CLAUDE.md'])['checkout_relation'], 'launch_ancestor')
        self.assertEqual(facts(nodes['CLAUDE.md'])['import_tokens'], 1)
        deep = facts(nodes['src/task/deep/CLAUDE.md'])
        self.assertEqual((deep['checkout_relation'], deep['loading_trigger'], deep['subdirectory_size_limit']),
                         ('on_demand_subdirectory', 'read_write_edit_in_subtree', 'unverified'))
        self.assertEqual(facts(nodes['src/task/deep/AGENTS.md'])['instruction_files_setting'], 'instruction_files_setting_unknown')
        self.assertEqual(facts(nodes['other/CLAUDE.md'])['checkout_relation'], 'outside_task_scope')
        local = nodes['src/CLAUDE.local.md']
        self.assertEqual((local.declared_state, local.parse_status, local.content_sha256), ('present_content_not_read', 'not_read', None))
        self.assertNotIn('src/AGENTS.override.md', nodes)
        self.assertNotIn('SYNTHETIC_', json.dumps(claude.document()))
        codex_profile = dict(schema_version=1, runtime='codex', version='0.166.0', task_cwd='src/task')
        codex = {n.path: n for n in harness_inventory.capture(root, codex_profile).nodes}
        override = facts(codex['src/AGENTS.override.md'])
        self.assertEqual((override['checkout_relation'], override['exceeds_default_project_doc_max_bytes']), ('directory_chain', True))
        self.assertEqual(facts(codex['src/task/deep/AGENTS.md'])['checkout_relation'], 'below_task_cwd_not_loaded')
        self.assertNotIn('src/CLAUDE.local.md', codex)
        self.assertEqual(codex['CLAUDE.md'].parse_status, 'unsupported')

    def test_profile_validation_rejects_unknown_fields_types_and_escaped_cwd(self):
        invalid = [('task_cwd', '../outside'), ('task_cwd', 'src//task'),
                   ('project_doc_max_bytes', True), ('project_doc_max_bytes', -1),
                   ('fallback_filenames', 'TEAM.md'), ('schema_version', True), ('arbitrary', 'value')]
        original = dict(self.profile)
        for key, value in invalid:
            with self.subTest(key=key, value=value):
                self.profile = dict(original, **{key: value})
                with self.assertRaises(ValueError): self.read()
