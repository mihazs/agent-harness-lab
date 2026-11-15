"""Synthetic declared Codex managed configuration and requirements; a declared folder is never loading proof."""
from dataclasses import asdict
import hashlib
import json
import os
import time
from unittest.mock import patch

import pytest

from agent_harness_lab.sources.git_view import Folder
from agent_harness_lab.sources.instructions import capture
from agent_harness_lab.sources.schema import digest

CONFIG, REQUIREMENTS = digest(['codex_managed', 'managed_config.toml']), digest(['codex_managed', 'requirements.toml'])


class TestCodexManagedInfluences:
    @pytest.fixture(autouse=True)
    def _inventory(self, tmp_path):
        self.root = tmp_path
        (self.root/'project/src/task').mkdir(parents=True)
        self.profile = dict(schema_version=1, runtime='codex', version='0.166.0', task_cwd='src/task', fallback_filenames=[])

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

    def managed(self, **options): return self.read(codex_managed_root='managed', **options)

    def nodes(self, receipt): return {node.path_sha256: node for node in receipt.nodes}

    def edges(self, receipt): return {(e.kind, e.source_sha256, e.target_sha256) for e in receipt.edges}

    def state(self, receipt, key):
        node = self.nodes(receipt)[key]
        return node.presence, node.applicability, node.reason

    def test_the_managed_folder_is_never_read_or_discovered_unless_the_caller_declares_it(self):
        self.write('AGENTS.md', 'SYNTHETIC_PROJECT')
        self.write('managed_config.toml', 'SYNTHETIC_MANAGED', 'managed'); self.write('requirements.toml', 'SYNTHETIC_REQ', 'managed')
        original, names = Folder.open, []
        def recording(folder, name, *args, **kwargs):
            names.append(name); return original(folder, name, *args, **kwargs)
        with patch.object(Folder, 'open', recording), patch.dict(os.environ, CODEX_HOME=str(self.root/'managed')):
            undeclared = self.read()
        assert names and all(name.startswith('project/') for name in names), names
        assert not any(n.origin == 'codex_managed' for n in undeclared.nodes)

    def test_declared_files_are_hashed_read_only_influences_with_unknown_applicability(self):
        self.write('managed_config.toml', 'SYNTHETIC_MANAGED', 'managed'); self.write('requirements.toml', 'SYNTHETIC_REQ', 'managed')
        receipt = self.managed(); nodes = self.nodes(receipt)
        assert (nodes[CONFIG].origin, nodes[CONFIG].content_sha256, self.state(receipt, CONFIG)) == (
            'codex_managed', hashlib.sha256(b'SYNTHETIC_MANAGED').hexdigest(),
            ('present', 'unknown', 'managed_config_may_override_declared_settings'))
        assert (nodes[REQUIREMENTS].origin, nodes[REQUIREMENTS].content_sha256, self.state(receipt, REQUIREMENTS)) == (
            'codex_managed', hashlib.sha256(b'SYNTHETIC_REQ').hexdigest(),
            ('present', 'unknown', 'managed_requirements_effect_unverified'))
        assert receipt.resolution_status == 'declared'
        assert receipt.sha256 != self.read().sha256

    def test_declared_files_never_change_project_nodes_edges_or_the_byte_budget(self):
        self.profile['project_doc_max_bytes'] = 64
        self.write('AGENTS.md', 'SYNTHETIC_PROJECT')
        self.write('managed_config.toml', 'S'*4096, 'managed'); self.write('requirements.toml', 'S'*4096, 'managed')
        before, after = self.read(), self.managed()
        project = lambda r: [n for n in r.nodes if n.origin == 'project']
        assert (project(after), after.edges, after.resolution_status) == (project(before), before.edges, 'declared')

    @pytest.mark.parametrize('present, missing', [('managed_config.toml', REQUIREMENTS), ('requirements.toml', CONFIG)],
                             ids=['requirements-missing', 'config-missing'])
    def test_a_missing_declared_file_is_unknown_and_never_an_error(self, present, missing):
        self.write(present, 'SYNTHETIC_ONE', 'managed')
        assert self.state(self.managed(), missing) == ('missing', 'unknown', 'missing')

    def test_a_missing_declared_folder_makes_both_files_missing(self):
        receipt = self.managed()
        assert {self.state(receipt, key) for key in (CONFIG, REQUIREMENTS)} == {('missing', 'unknown', 'missing')}

    @pytest.mark.parametrize('content', [b'', b'\xff\xfe\x00', b'#'*(2*1024*1024)], ids=['empty', 'not-utf8', 'large'])
    def test_an_unusual_declared_file_is_hashed_without_being_decoded_and_stays_unknown(self, content):
        self.write('managed_config.toml', content, 'managed')
        receipt = self.managed(); node = self.nodes(receipt)[CONFIG]
        assert (node.presence, node.applicability, node.content_sha256) == ('present', 'unknown', hashlib.sha256(content).hexdigest())

    def test_instruction_looking_settings_in_a_declared_file_are_not_interpreted(self):
        self.write('AGENTS.md', 'SYNTHETIC_PROJECT')
        self.write('managed_config.toml', 'developer_instructions = "SYNTHETIC_RULE"\nproject_doc_max_bytes = 1\n', 'managed')
        receipt = self.managed()
        assert self.state(receipt, digest('AGENTS.md')) == ('present', 'declared_applicable', 'directory_precedence')
        assert self.state(receipt, CONFIG)[1] == 'unknown'

    @pytest.mark.parametrize('root', ['project', 'project/src', '.', '../managed'], ids=['checkout', 'inside-checkout', 'root', 'escaping'])
    def test_roots_inside_the_checkout_or_escaping_it_are_refused(self, root):
        self.write('requirements.toml', 'SYNTHETIC_REQ', 'managed')
        with pytest.raises(ValueError): self.read(codex_managed_root=root)

    @pytest.mark.parametrize('roots', [
        pytest.param(dict(codex_managed_root='shared', codex_home='shared'), id='same-as-codex-home'),
        pytest.param(dict(codex_managed_root='shared/etc', codex_home='shared'), id='inside-codex-home'),
        pytest.param(dict(codex_managed_root='shared', codex_home='shared/home'), id='above-codex-home'),
        pytest.param(dict(codex_managed_root='x/.git'), id='git-metadata'),
    ])
    def test_overlapping_or_git_roots_are_refused(self, roots):
        with pytest.raises(ValueError): self.read(**roots)

    def test_the_root_is_refused_for_a_runtime_that_does_not_own_it(self):
        self.profile = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='src/task')
        with pytest.raises(ValueError): self.read(codex_managed_root='managed')

    def test_an_unsupported_version_with_a_declared_root_reads_nothing_and_does_not_guess(self):
        self.write('requirements.toml', 'SYNTHETIC_REQ', 'managed'); self.profile['version'] = '0.165.0'
        names = []
        with patch.object(Folder, 'open', lambda *a, **k: names.append(a[1])):
            receipt = self.managed()
        assert (receipt.resolution_status, receipt.nodes, names) == ('unsupported', (), [])

    def test_a_linked_declared_file_fails_closed_instead_of_being_followed(self):
        target = self.write('target.toml', 'SYNTHETIC_SECRET', 'elsewhere'); (self.root/'managed').mkdir()
        os.symlink(target, self.root/'managed/requirements.toml')
        with pytest.raises(ValueError): self.managed()

    def test_a_hard_linked_declared_file_fails_closed(self):
        os.link(self.write('managed_config.toml', 'SYNTHETIC_MANAGED', 'managed'), self.root/'managed/other.toml')
        with pytest.raises(ValueError): self.managed()

    def test_a_linked_declared_folder_fails_closed(self):
        self.write('requirements.toml', 'SYNTHETIC_REQ', 'real'); os.symlink(self.root/'real', self.root/'managed')
        with pytest.raises(OSError): self.managed()

    def test_a_declared_folder_that_is_a_file_fails_closed(self):
        self.write('managed', 'SYNTHETIC_NOT_A_FOLDER', '.')
        with pytest.raises(OSError): self.managed()

    @pytest.mark.skipif(os.geteuid() == 0, reason='root ignores file permissions')
    def test_an_unreadable_declared_file_stays_unknown_and_blocks_the_receipt(self):
        path = self.write('requirements.toml', 'SYNTHETIC_REQ', 'managed'); path.chmod(0)
        try: receipt = self.managed()
        finally: path.chmod(0o600)
        assert (receipt.resolution_status, self.state(receipt, REQUIREMENTS)) == (
            'blocked', ('unknown', 'unknown', 'managed_file_unreadable'))

    def test_changing_either_declared_file_invalidates_the_receipt(self):
        config = self.write('managed_config.toml', 'SYNTHETIC_ONE', 'managed'); self.write('requirements.toml', 'SYNTHETIC_ONE', 'managed')
        first = self.managed().sha256
        config.write_text('SYNTHETIC_TWO'); second = self.managed().sha256
        self.write('requirements.toml', 'SYNTHETIC_TWO', 'managed'); third = self.managed().sha256
        assert len({first, second, third}) == 3

    def test_the_managed_folder_and_codex_home_are_separate_unshadowed_origins(self):
        self.write('AGENTS.md', 'SYNTHETIC_PROJECT'); self.write('AGENTS.md', 'SYNTHETIC_GLOBAL', 'home')
        self.write('requirements.toml', 'SYNTHETIC_REQ', 'managed')
        receipt = self.managed(codex_home='home')
        assert {n.origin for n in receipt.nodes} == {'project', 'codex_home', 'codex_managed'}
        assert not any(kind == 'shadows' and CONFIG in (a, b) or kind == 'shadows' and REQUIREMENTS in (a, b)
                       for kind, a, b in self.edges(receipt))
