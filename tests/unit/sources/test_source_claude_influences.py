"""Synthetic managed and user Claude Code instruction influences; declared roots are never loading proof."""
from dataclasses import asdict
import json
import os
import time
from unittest.mock import patch

import pytest

from agent_harness_lab.sources.git_view import Folder
from agent_harness_lab.sources.instructions import InstructionNode, capture
from agent_harness_lab.sources.schema import digest

MANAGED, USER = digest(['claude_managed', 'CLAUDE.md']), digest(['claude_user', 'CLAUDE.md'])


class TestClaudeInfluences:
    @pytest.fixture(autouse=True)
    def _inventory(self, tmp_path):
        self.root = tmp_path
        (self.root/'project/src/task').mkdir(parents=True)
        self.profile = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='src/task')

    def write(self, name, text, folder='project'):
        path = self.root/folder/name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode())
        return path

    def read(self, **options):
        with Folder(self.root, time.monotonic()+30, limit_total_bytes=False, limit_tree=False) as folder:
            value = capture(self.profile, folder, 'project', (), **options)
            folder.verify()
        dumped = json.dumps(asdict(value))
        assert str(self.root) not in dumped; assert 'SYNTHETIC_' not in dumped
        assert all(n.observed_loading == 'unknown' for n in value.nodes)
        assert (value.ambient_state, value.observed_loading) == ('unknown', 'unknown')
        return value

    def both(self, **options): return self.read(claude_managed_root='managed', claude_user_home='user', **options)

    def nodes(self, receipt): return {node.path_sha256: node for node in receipt.nodes}

    def edges(self, receipt): return {(e.kind, e.source_sha256, e.target_sha256) for e in receipt.edges}

    def state(self, receipt, key):
        node = self.nodes(receipt)[key]
        return node.applicability, node.reason

    def test_roots_are_never_read_or_discovered_unless_the_caller_declares_them(self):
        self.write('CLAUDE.md', 'SYNTHETIC_PROJECT'); self.write('CLAUDE.md', 'SYNTHETIC_USER', 'user')
        self.write('CLAUDE.md', 'SYNTHETIC_MANAGED', 'managed')
        original, names = Folder.read, []
        def recording(folder, name, *args, **kwargs):
            names.append(name); return original(folder, name, *args, **kwargs)
        with patch.object(Folder, 'read', recording), patch.dict(os.environ, HOME=str(self.root/'user')):
            undeclared = self.read()
        assert names and all(name.startswith('project/') for name in names), names
        assert not any(n.origin in ('claude_user', 'claude_managed') for n in undeclared.nodes)

    def test_declared_managed_and_user_files_are_read_only_applicable_declarations(self):
        self.write('CLAUDE.md', 'SYNTHETIC_PROJECT'); self.write('CLAUDE.md', 'SYNTHETIC_USER', 'user')
        self.write('CLAUDE.md', 'SYNTHETIC_MANAGED', 'managed')
        receipt = self.both(); nodes = self.nodes(receipt)
        assert (nodes[MANAGED].origin, nodes[MANAGED].presence, self.state(receipt, MANAGED)) == (
            'claude_managed', 'present', ('declared_applicable', 'declared_managed_policy'))
        assert (nodes[USER].origin, nodes[USER].presence, self.state(receipt, USER)) == (
            'claude_user', 'present', ('declared_applicable', 'declared_user_instructions'))
        assert receipt.resolution_status == 'declared'
        assert receipt.sha256 != self.read().sha256

    def test_managed_precedes_user_which_precedes_the_first_project_launch_file(self):
        self.write('CLAUDE.md', 'SYNTHETIC_PROJECT'); self.write('CLAUDE.md', 'SYNTHETIC_USER', 'user')
        self.write('CLAUDE.md', 'SYNTHETIC_MANAGED', 'managed')
        edges = self.edges(self.both())
        assert ('parent', MANAGED, USER) in edges
        assert ('parent', USER, digest('CLAUDE.md')) in edges
        assert ('parent', MANAGED, digest('CLAUDE.md')) not in edges

    def test_a_single_applicable_influence_links_directly_to_the_project(self):
        self.write('CLAUDE.md', 'SYNTHETIC_PROJECT'); self.write('CLAUDE.md', 'SYNTHETIC_MANAGED', 'managed')
        edges = self.edges(self.read(claude_managed_root='managed'))
        assert edges == {('parent', MANAGED, digest('CLAUDE.md'))}

    @pytest.mark.parametrize('content, state', [
        pytest.param(None, ('unknown', 'missing'), id='missing'),
        pytest.param(' \n\t', ('unknown', 'empty'), id='empty'),
        pytest.param(b'\xff\xfe', ('unknown', 'non_utf8_instruction'), id='not_utf8'),
    ])
    def test_unusable_declared_files_stay_unknown_and_never_become_applicable(self, content, state):
        (self.root/'user').mkdir()
        if content is not None: self.write('CLAUDE.md', content, 'user')
        receipt = self.read(claude_user_home='user')
        assert self.state(receipt, USER) == state
        assert not any(USER in (s, t) for _, s, t in self.edges(receipt))

    def test_a_missing_declared_root_directory_is_a_missing_file_not_an_error(self):
        receipt = self.read(claude_managed_root='managed')
        assert (self.nodes(receipt)[MANAGED].presence, self.state(receipt, MANAGED)) == ('missing', ('unknown', 'missing'))

    def test_a_file_past_the_runtime_skip_size_stays_unknown(self):
        with patch('agent_harness_lab.sources.claude_instructions.SKIPPED_BYTES', 8):
            self.write('CLAUDE.md', 'SYNTHETIC_TOO_LARGE_FOR_THE_LIMIT', 'managed')
            receipt = self.read(claude_managed_root='managed')
        assert self.state(receipt, MANAGED) == ('unknown', 'runtime_size_limit_unverified')

    @pytest.mark.parametrize('root', ['project', 'project/src', '.', '../user'], ids=['checkout', 'inside-checkout', 'root', 'escaping'])
    def test_roots_inside_the_checkout_or_escaping_it_are_refused(self, root):
        self.write('CLAUDE.md', 'SYNTHETIC_USER', 'user')
        with pytest.raises(ValueError): self.read(claude_user_home=root)

    @pytest.mark.parametrize('roots', [
        pytest.param(dict(claude_managed_root='shared', claude_user_home='shared'), id='same'),
        pytest.param(dict(claude_managed_root='shared', claude_user_home='shared/user'), id='nested'),
        pytest.param(dict(claude_managed_root='x/.git', claude_user_home='user'), id='git-metadata'),
    ])
    def test_overlapping_or_git_roots_are_refused(self, roots):
        with pytest.raises(ValueError): self.read(**roots)

    def test_roots_are_refused_for_a_runtime_that_does_not_own_them(self):
        self.profile = dict(schema_version=1, runtime='codex', version='0.166.0', task_cwd='src/task', fallback_filenames=[])
        with pytest.raises(ValueError): self.read(claude_user_home='user')

    def test_an_unsupported_version_with_declared_roots_reads_nothing_and_does_not_guess(self):
        self.write('CLAUDE.md', 'SYNTHETIC_USER', 'user'); self.profile['version'] = '2.1.316'
        names = []
        with patch.object(Folder, 'open', lambda *a, **k: names.append(a[1])):
            receipt = self.read(claude_user_home='user')
        assert (receipt.resolution_status, receipt.nodes, names) == ('unsupported', (), [])

    def test_a_linked_declared_file_fails_closed_instead_of_being_followed(self):
        target = self.write('target.md', 'SYNTHETIC_SECRET', 'elsewhere'); (self.root/'user').mkdir()
        os.symlink(target, self.root/'user/CLAUDE.md')
        with pytest.raises(ValueError): self.read(claude_user_home='user')

    def test_a_hard_linked_declared_file_fails_closed(self):
        os.link(self.write('CLAUDE.md', 'SYNTHETIC_MANAGED', 'managed'), self.root/'managed/other.md')
        with pytest.raises(ValueError): self.read(claude_managed_root='managed')

    def test_a_linked_declared_root_directory_fails_closed(self):
        self.write('CLAUDE.md', 'SYNTHETIC_USER', 'real'); os.symlink(self.root/'real', self.root/'user')
        with pytest.raises(OSError): self.read(claude_user_home='user')

    def test_a_declared_root_that_is_a_file_fails_closed(self):
        self.write('user', 'SYNTHETIC_NOT_A_FOLDER', '.')
        with pytest.raises(OSError): self.read(claude_user_home='user')

    @pytest.mark.skipif(os.geteuid() == 0, reason='root ignores file permissions')
    def test_an_unreadable_declared_file_stays_unknown_and_blocks_the_receipt(self):
        path = self.write('CLAUDE.md', 'SYNTHETIC_USER', 'user'); path.chmod(0)
        try: receipt = self.read(claude_user_home='user')
        finally: path.chmod(0o600)
        assert (receipt.resolution_status, self.nodes(receipt)[USER].presence, self.state(receipt, USER)) == (
            'blocked', 'unknown', ('unknown', 'instruction_file_unreadable'))

    def test_imports_in_declared_files_are_never_followed_and_leave_an_unknown_marker(self):
        self.write('CLAUDE.md', 'SYNTHETIC_PROJECT')
        self.write('CLAUDE.md', '@notes.md @../project/CLAUDE.md @/etc/hostname @~/x.md', 'user')
        self.write('notes.md', 'SYNTHETIC_NOTES', 'user')
        original, opened = Folder.open, []
        def recording(folder, name, *args, **options):
            opened.append(name); return original(folder, name, *args, **options)
        with patch.object(Folder, 'open', recording): receipt = self.read(claude_user_home='user')
        assert 'user/notes.md' not in opened
        marker = self.nodes(receipt)[digest(['unresolved_imports', 'claude_user', 'influence_imports_not_followed'])]
        assert (marker.origin, marker.presence, marker.applicability, marker.content_sha256) == ('external_reference', 'unknown', 'unknown', None)
        assert ('imports', USER, marker.path_sha256) in self.edges(receipt)
        assert digest('user/notes.md') not in self.nodes(receipt) and digest('notes.md') not in self.nodes(receipt)
        assert self.state(receipt, USER) == ('declared_applicable', 'declared_user_instructions')

    def test_a_declared_file_without_import_tokens_gets_no_marker(self):
        self.write('CLAUDE.md', 'plain text mentioning user@example.com and `@span.md`', 'user')
        assert [n for n in self.read(claude_user_home='user').nodes if n.origin == 'external_reference'] == []

    @pytest.mark.parametrize('setting, user_state', [
        pytest.param('claude-md', ('declared_applicable', 'declared_user_instructions'), id='claude-md'),
        pytest.param('claude-md-and-agents-md', ('declared_applicable', 'declared_user_instructions'), id='both'),
        pytest.param('managed-only', ('out_of_scope', 'excluded_by_declared_instruction_files'), id='managed-only'),
        pytest.param('invented-mode', ('unknown', 'instruction_files_setting_unknown'), id='unknown-mode'),
    ])
    def test_the_declared_project_instructions_setting_decides_only_the_user_file(self, setting, user_state):
        self.write('CLAUDE.md', 'SYNTHETIC_USER', 'user'); self.write('CLAUDE.md', 'SYNTHETIC_MANAGED', 'managed')
        self.profile['instruction_files'] = setting
        receipt = self.both()
        assert self.state(receipt, USER) == user_state
        assert self.state(receipt, MANAGED) == ('declared_applicable', 'declared_managed_policy')

    def test_declared_excludes_leave_the_user_file_unverified_and_never_touch_managed(self):
        self.write('CLAUDE.md', 'SYNTHETIC_USER', 'user'); self.write('CLAUDE.md', 'SYNTHETIC_MANAGED', 'managed')
        self.profile['claude_md_excludes'] = ['**/other-team/CLAUDE.md']
        receipt = self.both()
        assert self.state(receipt, USER) == ('unknown', 'claude_md_excludes_match_unverified')
        assert self.state(receipt, MANAGED) == ('declared_applicable', 'declared_managed_policy')

    def test_a_user_file_does_not_count_as_a_project_claude_md_for_agents_md(self):
        self.write('AGENTS.md', 'SYNTHETIC_AGENTS'); self.write('CLAUDE.md', 'SYNTHETIC_USER', 'user')
        receipt = self.read(claude_user_home='user')
        assert self.state(receipt, digest('AGENTS.md')) == ('unknown', 'instruction_files_setting_unknown')
        assert not any(e.kind == 'shadows' for e in receipt.edges)

    def test_changing_either_declared_file_invalidates_the_receipt(self):
        self.write('CLAUDE.md', 'SYNTHETIC_ONE', 'managed'); user = self.write('CLAUDE.md', 'SYNTHETIC_ONE', 'user')
        first = self.both().sha256
        user.write_text('SYNTHETIC_TWO'); second = self.both().sha256
        self.write('CLAUDE.md', 'SYNTHETIC_TWO', 'managed'); third = self.both().sha256
        assert len({first, second, third}) == 3

    def test_an_unrecognized_influence_origin_is_not_an_instruction_node(self):
        with pytest.raises(ValueError):
            InstructionNode(digest('a'), digest('b'), None, None, 'missing', 'unknown', 'missing', origin='claude_enterprise')
