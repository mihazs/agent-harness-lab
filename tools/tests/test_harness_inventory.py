"""Read-only harness surface inventory over synthetic checkouts; no real configuration, process or network."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import main
from agent_harness_lab.sources import harness_inventory as inventory
from agent_harness_lab.sources.schema import digest
from tools.tests import harness_surface_fixture as fixture

CODEX = dict(schema_version=1, runtime='codex', version='0.166.0', task_cwd='src/task')
SURFACES = {'AGENTS.md': 'instruction', 'CLAUDE.md': 'instruction', '.agents/skills/beta/SKILL.md': 'skill',
            '.claude/skills/alpha/SKILL.md': 'skill', '.claude/hooks/check.sh': 'hook'}
REFUSED = {'.git': 'vcs_metadata', **fixture.HOSTILE}
AUDIT = []


def _audit(event, args):
    # A listed descriptor is recorded by inode while it is still open; a path is recorded as given.
    if AUDIT and event in ('open', 'os.scandir', 'os.listdir'):
        AUDIT[-1].append(_identity(os.fstat(args[0])) if isinstance(args[0], int) else args[0])


sys.addaudithook(_audit)


def denied(*args, **kwargs): raise AssertionError('inventory must not spawn processes or use the network')


class HarnessInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ahl-inventory-'); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.checkout = fixture.build(self.base / 'checkout')
        self.outside = fixture.outside(self.base)

    def capture(self, profile=CODEX, **options):
        return inventory.capture(self.checkout, profile, **options)

    def test_regular_surface_files_become_nodes_and_hostile_entries_are_refused(self):
        value = self.capture()
        self.assertEqual(value.status, 'declared')
        self.assertEqual({n.path: n.kind for n in value.nodes}, SURFACES)
        self.assertEqual({r.path: r.reason for r in value.refusals}, REFUSED)
        hook = next(n for n in value.nodes if n.kind == 'hook')
        self.assertEqual((hook.mode, hook.scope, hook.origin, hook.root), (0o755, '.claude/hooks', 'checkout', 'checkout'))
        for node in value.nodes:
            self.assertEqual(node.path_sha256, digest(node.path))  # the #46 HarnessFile path identity
            self.assertEqual((node.declared_state, node.parse_status, node.trust), ('present', 'not_parsed', 'unknown'))
            self.assertEqual(node.content_sha256, hashlib.sha256((self.checkout / node.path).read_bytes()).hexdigest())
        self.assertEqual({n.kind: n.capability_ref for n in value.nodes},
                         {'instruction': 'instructions:codex', 'skill': 'commands_skills:codex', 'hook': 'hooks:codex'})
        self.assertNotIn(fixture.SECRET, json.dumps(value.document()))

    def test_no_node_or_inventory_can_claim_observed_loading_or_effect(self):
        value = self.capture()
        self.assertTrue(value.nodes)
        for node in value.nodes:
            self.assertEqual((node.observed_loading, node.effective), ('unknown', 'unknown'))
            for field in ('observed_loading', 'effective'):
                with self.subTest(field=field), self.assertRaises(ValueError): replace(node, **{field: 'observed'})
        for field in ('observed_loading', 'effective'):
            with self.assertRaises(ValueError): replace(value, **{field: 'observed'})
        with self.assertRaises(ValueError): replace(value.nodes[0], trust='trusted')

    def test_inventory_leaves_the_tree_unchanged_and_spawns_no_process_or_network(self):
        before = fixture.tree_state(self.checkout)
        with patch('subprocess.Popen', denied), patch('os.system', denied), patch('os.posix_spawn', denied), \
                patch('os.fork', denied), patch('socket.socket', denied), patch('socket.create_connection', denied):
            value = self.capture()
        self.assertEqual(value.status, 'declared')
        self.assertEqual(fixture.tree_state(self.checkout), before)

    def test_bound_overflow_is_blocked_never_a_truncated_success(self):
        for name, bound, reason in (('MAX_FILES', 2, 'file_bound_exceeded'), ('MAX_BYTES', 40, 'byte_bound_exceeded'),
                                    ('MAX_DEPTH', 1, 'depth_bound_exceeded'), ('MAX_ENTRIES', 5, 'entry_bound_exceeded')):
            with self.subTest(bound=name), patch.object(inventory, name, bound):
                value = self.capture()
                self.assertEqual((value.status, value.blocked_reason, value.nodes, value.refusals), ('blocked', reason, (), ()))
        with self.assertRaises(ValueError):
            inventory.HarnessInventory(**{**self.identity(), 'status': 'blocked', 'blocked_reason': 'file_bound_exceeded',
                                          'nodes': self.capture().nodes, 'refusals': ()})

    def identity(self):
        value = self.capture()
        return dict(runtime=value.runtime, version=value.version, profile_sha256=value.profile_sha256,
                    task_cwd_sha256=value.task_cwd_sha256)

    def test_entry_bound_stops_reading_a_large_directory_before_holding_it(self):
        for index in range(200): (self.checkout / f'bulk-{index:03}.txt').write_text('')
        real, yielded = os.scandir, []

        class Counting:
            def __init__(self, path): self.inner = real(path)
            def __enter__(self): return self
            def __exit__(self, *exc): self.inner.close()
            def __iter__(self):
                for entry in self.inner: yielded.append(entry.name); yield entry

        with patch.object(inventory, 'MAX_ENTRIES', 10), patch('os.scandir', Counting):
            value = self.capture()
        self.assertEqual((value.status, value.blocked_reason), ('blocked', 'entry_bound_exceeded'))
        self.assertEqual(len(yielded), 11)  # stops at the first entry past the bound, not after the whole directory

    def test_nothing_outside_the_checkout_is_opened_without_declared_roots(self):
        self.capture()  # warm imports so the audit sees only the inventory's own opens
        real, opened = os.open, []

        def recording(path, flags, mode=0o777, *, dir_fd=None):
            descriptor = real(path, flags, mode, dir_fd=dir_fd)
            opened.append((_identity(os.fstat(descriptor)), path)); return descriptor

        home = self.outside
        with patch.dict(os.environ, HOME=str(home), CODEX_HOME=str(home), CLAUDE_CONFIG_DIR=str(home)), \
                patch('os.open', recording):
            AUDIT.append([])
            try: value = self.capture()
            finally: audited = AUDIT.pop()
        self.assertEqual(value.external_roots, ())
        self.assertEqual({(n.origin, n.root) for n in value.nodes}, {('checkout', 'checkout')})
        # Opens by name beneath a dir_fd carry no absolute path, so every descriptor's inode must be the
        # checkout, an entry beneath it, or one of its ancestors; a listed descriptor must be one of those too.
        allowed = {_identity(os.stat(p)) for p in (self.checkout, *self.checkout.parents)}
        for folder, directories, names in os.walk(self.checkout, followlinks=False):
            allowed.update(_identity(os.lstat(os.path.join(folder, n))) for n in directories + names)
        listed = [p for p in audited if isinstance(p, tuple)]
        self.assertTrue(opened and listed)
        for identity, path in opened: self.assertTrue(identity in allowed, f'opened outside the checkout: {path}')
        self.assertTrue(set(listed) <= allowed, 'listed a directory outside the checkout')
        root = str(self.checkout)
        for path in (p for p in audited if isinstance(p, str) and p.startswith('/')):
            self.assertTrue(path == root or path.startswith(root + '/') or root.startswith(path.rstrip('/') + '/'), path)

    def test_declared_external_roots_are_read_only_labelled_and_never_trusted(self):
        value = self.capture(dict(CODEX, project_trust='declared_trusted'), external_roots={'codex_home': str(self.outside)})
        external = [n for n in value.nodes if n.origin == 'caller_declared_external']
        self.assertEqual({(n.root, n.path, n.kind) for n in external}, {('codex_home', 'AGENTS.md', 'instruction'),
                                                                         ('codex_home', 'skills/gamma/SKILL.md', 'skill')})
        self.assertEqual({n.trust for n in external}, {'unknown'})
        self.assertEqual({n.trust for n in value.nodes if n.origin == 'checkout'}, {'declared_trusted'})
        self.assertEqual(value.external_roots, ('codex_home',))
        for roots in ({'checkout': str(self.outside)}, {'inner': str(self.checkout / 'src')}, {'parent': str(self.base)},
                      {'relative': 'outside'}, ['codex_home']):
            with self.subTest(roots=roots), self.assertRaises(ValueError): self.capture(external_roots=roots)

    def test_project_trust_is_a_declaration_that_defaults_to_unknown(self):
        # A synthetic config claiming trust is never read; an undeclared project stays unknown.
        with patch.dict(os.environ, CODEX_HOME=str(self.outside)):
            self.assertEqual({n.trust for n in self.capture().nodes}, {'unknown'})
        untrusted = self.capture(dict(CODEX, project_trust='declared_untrusted'))
        self.assertEqual({n.trust for n in untrusted.nodes}, {'declared_untrusted'})
        with self.assertRaises(ValueError): self.capture(dict(CODEX, project_trust='trusted'))

    def test_parser_registry_is_sorted_single_line_entries_with_stub_parsers(self):
        kinds = [row[0] for row in inventory.PARSERS]
        self.assertEqual(kinds, sorted(set(kinds)))
        for kind, surface, reference in inventory.PARSERS:
            self.assertEqual(inventory._resolve(reference)('AGENTS.md', b'', CODEX), ('not_parsed', {}))
        source = Path(inventory.__file__).read_text()
        for row in inventory.PARSERS:
            self.assertEqual(sum(f"('{row[0]}', " in line for line in source.splitlines()), 1)
        with patch.object(inventory, 'PARSERS', inventory.PARSERS[::-1]), self.assertRaises(ValueError): self.capture()
        registered = (('hook', 'hooks', __name__ + ':_failing'), ('instruction', 'instructions', __name__ + ':_facts'))
        with patch.object(inventory, 'PARSERS', registered):
            nodes = {n.path: n for n in self.capture().nodes}
        self.assertEqual((nodes['AGENTS.md'].parse_status, nodes['AGENTS.md'].facts), ('parsed', (('bytes', len(fixture.FILES['AGENTS.md'])),)))
        self.assertEqual(nodes['.claude/hooks/check.sh'].parse_status, 'parse_failed')
        self.assertEqual((nodes['.claude/skills/alpha/SKILL.md'].parse_status, nodes['.claude/skills/alpha/SKILL.md'].capability_ref),
                         ('no_parser', None))

    def test_unsupported_versions_invalid_profiles_and_task_directories(self):
        unpinned = self.capture(dict(CODEX, version='0.165.0'))
        self.assertEqual((unpinned.status, unpinned.nodes), ('unsupported', ()))
        for profile in (dict(CODEX, runtime='other'), dict(CODEX, task_cwd='../up'), dict(CODEX, task_cwd='.git'),
                        dict(CODEX, extra=1), dict(CODEX, task_cwd='vendor/nested'), dict(CODEX, task_cwd='missing')):
            with self.subTest(profile=profile), self.assertRaises(ValueError): self.capture(profile)
        claude = self.capture(dict(CODEX, runtime='claude-code', version='2.1.317', task_cwd='.'))
        self.assertEqual({n.capability_ref for n in claude.nodes if n.kind == 'instruction'}, {'instructions:claude-code'})

    def test_cli_writes_private_unpublishable_output(self):
        state = self.base / 'state'
        out, error = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, XDG_STATE_HOME=str(state)), redirect_stdout(out), redirect_stderr(error):
            code = main(['sources', 'inventory', str(self.checkout), '--runtime', 'codex', '--version', '0.166.0',
                         '--task-cwd', 'src/task', '--external-root', f'codex_home={self.outside}'])
        self.assertEqual((code, error.getvalue()), (0, ''))
        result = json.loads(out.getvalue())
        self.assertEqual((result['publication_allowed'], result['status'], result['nodes'], result['network_used']),
                         (False, 'declared', 7, False))
        self.assertNotIn('AGENTS.md', out.getvalue())
        output = Path(result['output'])
        self.assertTrue(output.is_relative_to(state))
        self.assertEqual(stat.S_IMODE(output.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        document = json.loads(output.read_text())
        self.assertEqual((document['publication_allowed'], document['loading_evidence'], document['profile']),
                         (False, False, inventory.PROFILE))
        self.assertNotIn(fixture.SECRET, output.read_text())
        with patch.dict(os.environ, XDG_STATE_HOME=str(state)), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(['sources', 'inventory', str(self.checkout / 'linked.md'), '--runtime', 'codex',
                                   '--version', '0.166.0']), 2)
            with patch.object(inventory, 'MAX_FILES', 1):
                self.assertEqual(main(['sources', 'inventory', str(self.checkout), '--runtime', 'codex', '--version', '0.166.0']), 3)


def _identity(info): return info.st_dev, info.st_ino


def _facts(path, raw, profile): return 'parsed', {'bytes': len(raw)}


def _failing(path, raw, profile): raise ValueError('synthetic parser refusal')


if __name__ == '__main__': unittest.main()
