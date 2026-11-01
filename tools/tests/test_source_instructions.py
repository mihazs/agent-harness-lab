"""Synthetic instruction resolution; declared applicability is never loading proof."""
from dataclasses import asdict, FrozenInstanceError
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest

from agent_harness_lab.sources.git_view import Folder
from agent_harness_lab.sources.instructions import capture
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

    def read(self, selected=()):
        with Folder(self.root, time.monotonic()+2) as folder:
            value = capture(self.profile, folder, 'project', selected)
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

    def test_profile_validation_rejects_unknown_fields_types_and_escaped_cwd(self):
        invalid = [('task_cwd', '../outside'), ('task_cwd', 'src//task'),
                   ('project_doc_max_bytes', True), ('project_doc_max_bytes', 1048577),
                   ('fallback_filenames', 'TEAM.md'), ('schema_version', True), ('arbitrary', 'value')]
        original = dict(self.profile)
        for key, value in invalid:
            with self.subTest(key=key, value=value):
                self.profile = dict(original, **{key: value})
                with self.assertRaises(ValueError): self.read()
