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
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources import harness_inventory as inventory
from agent_harness_lab.sources.schema import digest
if __package__:
    from . import harness_surface_fixture as fixture
else:
    import harness_surface_fixture as fixture

CODEX = dict(schema_version=1, runtime='codex', version='0.166.0', task_cwd='src/task')
SURFACES = {'AGENTS.md': 'instruction', 'CLAUDE.md': 'instruction', '.agents/skills/beta/SKILL.md': 'skill',
            '.claude/skills/alpha/SKILL.md': 'skill', '.claude/hooks/check.sh': 'hook', 'src/task/AGENTS.md': 'instruction'}
PARSED = {'AGENTS.md': 'parsed', 'src/task/AGENTS.md': 'parsed', 'CLAUDE.md': 'unsupported'}  # Codex reads no CLAUDE.md
REFUSED = {'.git': 'vcs_metadata', **fixture.HOSTILE}
AUDIT = []


def _audit(event, args):
    # A listed descriptor is recorded by inode while it is still open; a path is recorded as given.
    if AUDIT and event in ('open', 'os.scandir', 'os.listdir'):
        AUDIT[-1].append(_identity(os.fstat(args[0])) if isinstance(args[0], int) else args[0])


sys.addaudithook(_audit)


def denied(*args, **kwargs): raise AssertionError('inventory must not spawn processes or use the network')


class TestHarnessInventory:
    @pytest.fixture(autouse=True)
    def _checkout(self, tmp_path):
        self.base = tmp_path.resolve()
        self.checkout = fixture.build(self.base / 'checkout')
        self.outside = fixture.outside(self.base)

    def capture(self, profile=CODEX, **options):
        return inventory.capture(self.checkout, profile, **options)

    def test_regular_surface_files_become_nodes_and_hostile_entries_are_refused(self):
        value = self.capture()
        assert value.status == 'declared'
        assert {n.path: n.kind for n in value.nodes} == SURFACES
        assert {r.path: r.reason for r in value.refusals} == REFUSED
        hook = next(n for n in value.nodes if n.kind == 'hook')
        assert (hook.mode, hook.scope, hook.origin, hook.root) == (0o755, '.claude/hooks', 'checkout', 'checkout')
        for node in value.nodes:
            assert node.path_sha256 == digest(node.path)  # the #46 HarnessFile path identity
            assert (node.declared_state, node.parse_status, node.trust) == ('present', PARSED.get(node.path, 'not_parsed'), 'unknown')
            assert node.content_sha256 == hashlib.sha256((self.checkout / node.path).read_bytes()).hexdigest()
        assert {n.kind: n.capability_ref for n in value.nodes} == {'instruction': 'instructions:codex', 'skill': 'commands_skills:codex', 'hook': 'hooks:codex'}
        assert fixture.SECRET not in json.dumps(value.document())

    @pytest.mark.parametrize('path,field', [pytest.param(path, field, id=f'{path}-{field}')
                                            for path in SURFACES for field in ('observed_loading', 'effective')])
    def test_node_cannot_claim_observed_loading_or_effect(self, path, field):
        node = next(n for n in self.capture().nodes if n.path == path)
        with pytest.raises(ValueError): replace(node, **{field: 'observed'})

    def test_no_node_or_inventory_can_claim_observed_loading_or_effect(self):
        value = self.capture()
        assert value.nodes
        for node in value.nodes:
            assert (node.observed_loading, node.effective) == ('unknown', 'unknown')
        for field in ('observed_loading', 'effective'):
            with pytest.raises(ValueError): replace(value, **{field: 'observed'})
        with pytest.raises(ValueError): replace(value.nodes[0], trust='trusted')

    def test_inventory_leaves_the_tree_unchanged_and_spawns_no_process_or_network(self):
        before = fixture.tree_state(self.checkout)
        with patch('subprocess.Popen', denied), patch('os.system', denied), patch('os.posix_spawn', denied), \
                patch('os.fork', denied), patch('socket.socket', denied), patch('socket.create_connection', denied):
            value = self.capture()
        assert value.status == 'declared'
        assert fixture.tree_state(self.checkout) == before

    @pytest.mark.parametrize('name,bound,reason', [
        pytest.param('MAX_FILES', 2, 'file_bound_exceeded', id='MAX_FILES'),
        pytest.param('MAX_BYTES', 40, 'byte_bound_exceeded', id='MAX_BYTES'),
        pytest.param('MAX_DEPTH', 1, 'depth_bound_exceeded', id='MAX_DEPTH'),
        pytest.param('MAX_ENTRIES', 5, 'entry_bound_exceeded', id='MAX_ENTRIES'),
    ])
    def test_bound_overflow_is_blocked_never_a_truncated_success(self, name, bound, reason):
        with patch.object(inventory, name, bound):
            value = self.capture()
            assert (value.status, value.blocked_reason, value.nodes, value.refusals) == ('blocked', reason, (), ())

    def test_blocked_inventory_cannot_carry_nodes(self):
        with pytest.raises(ValueError):
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
        assert (value.status, value.blocked_reason) == ('blocked', 'entry_bound_exceeded')
        assert len(yielded) == 11  # stops at the first entry past the bound, not after the whole directory

    def capture_opens(self, **options):
        """Capture while recording (inode, path) for every os.open and every audited open or listing."""
        self.capture(**options)  # warm imports so the audit sees only the inventory's own opens
        real, opened = os.open, []

        def recording(path, flags, mode=0o777, *, dir_fd=None):
            descriptor = real(path, flags, mode, dir_fd=dir_fd)
            opened.append((_identity(os.fstat(descriptor)), path)); return descriptor

        with patch('os.open', recording):
            AUDIT.append([])
            try: value = self.capture(**options)
            finally: audited = AUDIT.pop()
        return value, opened, audited

    def test_hard_linked_surface_file_is_refused_and_its_inode_never_opened(self):
        shared = self.checkout / '.claude/skills/delta/SKILL.md'; shared.parent.mkdir(parents=True)
        os.link(self.outside / 'skills/gamma/SKILL.md', shared)  # a checkout name for an outside file
        value, opened, _ = self.capture_opens()
        assert inventory.Refusal('.claude/skills/delta/SKILL.md', 'checkout', 'hardlink') in value.refusals
        assert '.claude/skills/delta/SKILL.md' not in {n.path for n in value.nodes}
        assert _identity(shared.stat()) not in {identity for identity, _ in opened}

    def test_private_excluded_surface_file_is_recorded_but_never_read_or_hashed(self):
        cache = self.checkout / '.claude/hooks/__pycache__/check.cpython.pyc'; cache.parent.mkdir()
        cache.write_bytes(fixture.SECRET.encode())
        value, opened, _ = self.capture_opens()
        node = next(n for n in value.nodes if n.path == '.claude/hooks/__pycache__/check.cpython.pyc')
        assert (node.declared_state, node.parse_status, node.content_sha256, node.facts) == ('present_content_not_read', 'not_read', None, ())
        assert _identity(cache.stat()) not in {identity for identity, _ in opened}

    def test_nothing_outside_the_checkout_is_opened_without_declared_roots(self):
        home = self.outside
        with patch.dict(os.environ, HOME=str(home), CODEX_HOME=str(home), CLAUDE_CONFIG_DIR=str(home)):
            value, opened, audited = self.capture_opens()
        assert value.external_roots == ()
        assert {(n.origin, n.root) for n in value.nodes} == {('checkout', 'checkout')}
        # Opens by name beneath a dir_fd carry no absolute path, so every descriptor's inode must be the
        # checkout, an entry beneath it, or one of its ancestors; a listed descriptor must be one of those too.
        allowed = {_identity(os.stat(p)) for p in (self.checkout, *self.checkout.parents)}
        for folder, directories, names in os.walk(self.checkout, followlinks=False):
            allowed.update(_identity(os.lstat(os.path.join(folder, n))) for n in directories + names)
        listed = [p for p in audited if isinstance(p, tuple)]
        assert opened and listed
        for identity, path in opened: assert identity in allowed, f'opened outside the checkout: {path}'
        assert set(listed) <= allowed, 'listed a directory outside the checkout'
        root = str(self.checkout)
        for path in (p for p in audited if isinstance(p, str) and p.startswith('/')):
            assert path == root or path.startswith(root + '/') or root.startswith(path.rstrip('/') + '/'), path

    def test_declared_external_roots_are_read_only_labelled_and_never_trusted(self):
        value = self.capture(dict(CODEX, project_trust='declared_trusted'), external_roots={'codex_home': str(self.outside)})
        external = [n for n in value.nodes if n.origin == 'caller_declared_external']
        assert ({(n.root, n.path, n.kind) for n in external} == {('codex_home', 'AGENTS.md', 'instruction'),
                                                                         ('codex_home', 'skills/gamma/SKILL.md', 'skill')})
        assert {n.trust for n in external} == {'unknown'}
        assert {n.trust for n in value.nodes if n.origin == 'checkout'} == {'declared_trusted'}
        assert value.external_roots == ('codex_home',)

    @pytest.mark.parametrize('roots', [
        pytest.param(lambda t: {'checkout': str(t.outside)}, id='reserved-checkout-label'),
        pytest.param(lambda t: {'inner': str(t.checkout / 'src')}, id='inside-checkout'),
        pytest.param(lambda t: {'parent': str(t.base)}, id='checkout-ancestor'),
        pytest.param(lambda t: {'relative': 'outside'}, id='relative-path'),
        pytest.param(lambda t: ['codex_home'], id='not-a-mapping'),
    ])
    def test_invalid_external_roots_are_rejected(self, roots):
        with pytest.raises(ValueError): self.capture(external_roots=roots(self))

    def test_project_trust_is_a_declaration_that_defaults_to_unknown(self):
        # A synthetic config claiming trust is never read; an undeclared project stays unknown.
        with patch.dict(os.environ, CODEX_HOME=str(self.outside)):
            assert {n.trust for n in self.capture().nodes} == {'unknown'}
        untrusted = self.capture(dict(CODEX, project_trust='declared_untrusted'))
        assert {n.trust for n in untrusted.nodes} == {'declared_untrusted'}
        with pytest.raises(ValueError): self.capture(dict(CODEX, project_trust='trusted'))

    def test_parser_registry_is_sorted_single_line_entries_with_stub_parsers(self):
        kinds = [row[0] for row in inventory.PARSERS]
        assert kinds == sorted(set(kinds))
        for kind, surface, reference in (row for row in inventory.PARSERS if row[2] == inventory.STUB):
            assert inventory._resolve(reference)('AGENTS.md', b'', CODEX) == ('not_parsed', {})
        source = Path(inventory.__file__).read_text()
        for row in inventory.PARSERS:
            assert sum(f"('{row[0]}', " in line for line in source.splitlines()) == 1
        with patch.object(inventory, 'PARSERS', inventory.PARSERS[::-1]), pytest.raises(ValueError): self.capture()
        registered = (('hook', 'hooks', __name__ + ':_failing'), ('instruction', 'instructions', __name__ + ':_facts'))
        with patch.object(inventory, 'PARSERS', registered):
            nodes = {n.path: n for n in self.capture().nodes}
        assert (nodes['AGENTS.md'].parse_status, nodes['AGENTS.md'].facts) == ('parsed', (('bytes', len(fixture.FILES['AGENTS.md'])),))
        assert nodes['.claude/hooks/check.sh'].parse_status == 'parse_failed'
        assert (nodes['.claude/skills/alpha/SKILL.md'].parse_status, nodes['.claude/skills/alpha/SKILL.md'].capability_ref) == ('no_parser', None)

    def test_unsupported_versions_invalid_profiles_and_task_directories(self):
        unpinned = self.capture(dict(CODEX, version='0.165.0'))
        assert (unpinned.status, unpinned.nodes) == ('unsupported', ())
        claude = self.capture(dict(CODEX, runtime='claude-code', version='2.1.317', task_cwd='.'))
        assert {n.capability_ref for n in claude.nodes if n.kind == 'instruction'} == {'instructions:claude-code'}

    @pytest.mark.parametrize('profile', [
        pytest.param(dict(CODEX, runtime='other'), id='unknown-runtime'),
        pytest.param(dict(CODEX, task_cwd='../up'), id='task-cwd-escapes'),
        pytest.param(dict(CODEX, task_cwd='.git'), id='task-cwd-vcs'),
        pytest.param(dict(CODEX, extra=1), id='extra-field'),
        pytest.param(dict(CODEX, task_cwd='vendor/nested'), id='task-cwd-nested'),
        pytest.param(dict(CODEX, task_cwd='missing'), id='task-cwd-missing'),
    ])
    def test_invalid_profiles_and_task_directories_are_rejected(self, profile):
        with pytest.raises(ValueError): self.capture(profile)

    def test_cli_writes_private_unpublishable_output(self):
        state = self.base / 'state'
        out, error = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, XDG_STATE_HOME=str(state)), redirect_stdout(out), redirect_stderr(error):
            code = main(['sources', 'inventory', str(self.checkout), '--runtime', 'codex', '--version', '0.166.0',
                         '--task-cwd', 'src/task', '--external-root', f'codex_home={self.outside}'])
        assert (code, error.getvalue()) == (0, '')
        result = json.loads(out.getvalue())
        assert (result['publication_allowed'], result['status'], result['nodes'], result['network_used']) == (False, 'declared', 8, False)
        assert 'AGENTS.md' not in out.getvalue()
        output = Path(result['output'])
        assert output.is_relative_to(state)
        assert stat.S_IMODE(output.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
        document = json.loads(output.read_text())
        assert (document['publication_allowed'], document['loading_evidence'], document['profile']) == (False, False, inventory.PROFILE)
        assert fixture.SECRET not in output.read_text()
        with patch.dict(os.environ, XDG_STATE_HOME=str(state)), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            assert (main(['sources', 'inventory', str(self.checkout / 'linked.md'), '--runtime', 'codex',
                                   '--version', '0.166.0']) == 2)
            with patch.object(inventory, 'MAX_FILES', 1):
                assert main(['sources', 'inventory', str(self.checkout), '--runtime', 'codex', '--version', '0.166.0']) == 3


def _identity(info): return info.st_dev, info.st_ino


def _facts(path, raw, profile): return 'parsed', {'bytes': len(raw)}


def _failing(path, raw, profile): raise ValueError('synthetic parser refusal')


def test_vendored_trees_are_listed_and_labelled_vendored(tmp_path):
    checkout = tmp_path
    for name in ('CLAUDE.md', 'node_modules/pkg/CLAUDE.md', 'node_modules/pkg/.claude/rules/x.md',
                 '.venv/lib/AGENTS.md'):
        (checkout / name).parent.mkdir(parents=True, exist_ok=True); (checkout / name).write_text('synthetic')
    profile = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='.')
    nodes = {node.path: node for node in inventory.capture(checkout, profile).nodes}
    assert ({path: dict(node.facts).get('vendored') for path, node in nodes.items()} == {'CLAUDE.md': None, 'node_modules/pkg/CLAUDE.md': True,
                          'node_modules/pkg/.claude/rules/x.md': True, '.venv/lib/AGENTS.md': True})
    assert nodes['node_modules/pkg/.claude/rules/x.md'].kind == 'path_rule'
