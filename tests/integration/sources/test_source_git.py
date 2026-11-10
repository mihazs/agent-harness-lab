"""Synthetic native Git repositories; source code/config must never be executed."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import socket
import subprocess
import tempfile
import time
import tracemalloc
import zlib
from dataclasses import FrozenInstanceError, replace
from unittest.mock import Mock, patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources.git import GitExport, HarnessSnapshot
from agent_harness_lab.sources import git_view
from agent_harness_lab.sources.git_view import Folder, GitView
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.store import SourceStore
from agent_harness_lab.sources import resolve
from agent_harness_lab.sources.graph import EvidenceGraph, JoinReceipt


@pytest.fixture(scope='module')
def system_git():
    if not Path('/usr/bin/git').is_file(): pytest.skip('explicit system Git is required')
    raw = subprocess.check_output(['/usr/bin/git', '--version'], env={'PATH': '/usr/bin:/bin'})
    match = re.match(rb'git version 2\.(\d+)\.', raw)
    if not match or int(match[1]) < 43: pytest.skip('Git 2.43+ in major version 2 is required')


@pytest.fixture
def git_root():
    # The fixtures stay under the developer worktree directory, as before, not under the pytest tmp_path.
    fixtures = Path.home()/'dev/.worktrees'; fixtures.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ahl-git-fixture-', dir=fixtures) as folder:
        yield Path(folder)


@pytest.mark.usefixtures('system_git')
class TestGitSource:
    @pytest.fixture(autouse=True)
    def _repository(self, git_root):
        self.root = git_root
        self.repo = self.root/'repo'; self.repo.mkdir()
        self.env = dict(PATH='/usr/bin:/bin', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
            GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='synthetic@example.invalid',
            GIT_COMMITTER_NAME='Synthetic', GIT_COMMITTER_EMAIL='synthetic@example.invalid',
            GIT_AUTHOR_DATE='2026-01-01T00:00:00Z', GIT_COMMITTER_DATE='2026-01-01T00:00:00Z')
        self.git('init', '-b', 'main')
        (self.repo/'AGENTS.md').write_text('SYNTHETIC_PRIVATE_INSTRUCTION\n')
        self.commit()
        self.selection = dict(schema_version=1, checkout='repo', git_dir='repo/.git', common_dir='repo/.git',
                              harness_paths=['AGENTS.md'], max_commits=100)
        self.manifest = self.root/'source.json'
        self.declare()

    def git(self, *arguments, cwd=None):
        # Fixture writes must not leave detached maintenance changing the read-only snapshot.
        return subprocess.check_output(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
            '-c', 'commit.gpgsign=false', '-c', 'maintenance.auto=false', *arguments], cwd=cwd or self.repo, env=self.env, stderr=subprocess.DEVNULL)

    def commit(self):
        self.git('add', '--all'); self.git('commit', '-m', 'SYNTHETIC_PRIVATE_MESSAGE')

    def declare(self):
        self.source = SourceSpec('local_git', 'git', getattr(self, 'source_version', 'v1'), digest(str(self.root.resolve())), digest('synthetic-project'),
            digest(self.selection), identity_provenance='source_declared_id', enabled=True)
        (self.root/'selection.json').write_text(json.dumps(self.selection))
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='selection.json')))

    def read(self):
        collector = GitExport(self.manifest); snapshot = collector.read(self.source)
        output = json.dumps(collector.summary)
        for private in ('SYNTHETIC_PRIVATE_INSTRUCTION', 'SYNTHETIC_PRIVATE_MESSAGE', str(self.root), 'AGENTS.md', 'refs/heads/main'):
            assert private not in output
        return collector, snapshot

    def invoke(self, operation, *extra):
        out, error = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(error):
            code = main(['sources', operation, '--manifest', str(self.manifest),
                '--project-sha256', self.source.project_sha256, '--scope-sha256', self.source.scope_sha256, *extra])
        for private in (str(self.root), 'SYNTHETIC_PRIVATE'):
            assert private not in out.getvalue()+error.getvalue()
        return code, json.loads(out.getvalue()) if out.getvalue() else None

    def test_frozen_head_history_refs_and_harness_are_deterministic_and_read_only(self):
        before = {str(p.relative_to(self.repo)): (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
                  for p in self.repo.rglob('*') if p.is_file()}
        collector, first = self.read(); _, second = self.read()
        assert first.sha256 == second.sha256
        assert len(collector.summary['commits']) == 1
        assert collector.summary['head_sha256'] == digest(self.git('rev-parse', 'HEAD').decode().strip())
        file = collector.summary['harness']['files'][0]
        assert file['content_sha256'] == file['committed_sha256']
        assert file['path_sha256'] == collector.summary['commits'][0]['changes'][0]['path_sha256']
        assert collector.summary['harness']['observed_loading'] == 'unknown'
        assert collector.summary['harness']['effective_settings'] == 'unknown'
        after = {str(p.relative_to(self.repo)): (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
                 for p in self.repo.rglob('*') if p.is_file()}
        assert before == after
        assert all(r.independence == 'unknown' and r.causal_status == 'unknown' for r in first.records)

    def test_dirty_and_untracked_files_have_distinct_current_and_committed_identities(self):
        (self.repo/'AGENTS.md').write_text('SYNTHETIC_CHANGED')
        (self.repo/'new.md').write_text('SYNTHETIC_UNTRACKED')
        self.selection['harness_paths'] += ['new.md', 'missing.md']; self.declare()
        collector, _ = self.read()
        files = {f['path_sha256']: f for f in collector.summary['harness']['files']}
        assert files[digest('AGENTS.md')]['content_sha256'] != files[digest('AGENTS.md')]['committed_sha256']
        assert files[digest('new.md')]['committed_sha256'] is None
        assert files[digest('missing.md')]['content_sha256'] is None

    def test_source_configuration_includes_hooks_filters_fsmonitor_and_signatures_are_inert(self):
        # Directories expose accidental execution even under a zero file-size limit.
        marker = self.root/'EXECUTED'
        program = self.root/'evil'; program.write_text('#!/bin/sh\nmkdir '+str(marker)+'\n'); program.chmod(0o700)
        with (self.repo/'.git/config').open('a') as config:
            config.write(f'\n[core]\nfsmonitor = {program}\nhooksPath = {self.root}\n[diff "evil"]\ncommand = {program}\ntextconv = {program}\n[filter "evil"]\nclean = {program}\nsmudge = {program}\n[gpg]\nprogram = {program}\n[log]\nshowSignature = true\n[include]\npath = {self.root}/not-a-readable-config\n')
        self.read()
        assert not marker.exists()

    def test_cli_preview_import_and_reimport_preserve_unknown_capture_and_private_metadata(self):
        code, result = self.invoke('preview'); assert code == 0
        assert 'git_summary' in result
        cache = self.root/'cache.json'
        assert self.invoke('import', '--cache', str(cache))[0] == 0
        code, result = self.invoke('import', '--cache', str(cache))
        assert code == 0; assert result['imported'] == 0
        assert result['skipped'] > 0
        assert b'SYNTHETIC_PRIVATE' not in cache.read_bytes()
        assert str(self.root).encode() not in cache.read_bytes()

    def test_probe_and_scope_mismatch_do_not_invoke_git_or_read_objects(self):
        with patch.object(GitView, 'run', side_effect=AssertionError('metadata probe must not run Git')):
            assert self.invoke('probe')[0] == 0
            value = json.loads((self.root/'selection.json').read_text()); value['max_commits'] = 3
            (self.root/'selection.json').write_text(json.dumps(value))
            assert self.invoke('preview')[0] == 2

    def test_detached_head_and_dangling_refs_do_not_fabricate_branch_or_ancestry(self):
        self.git('checkout', '--detach')
        (self.repo/'.git/refs/heads/dangling').write_text('f'*40+'\n')
        collector, _ = self.read()
        assert collector.summary['branch_sha256'] is None
        assert 'dangling_refs' in collector.summary['warnings']
        assert len(collector.summary['refs']) == 1

    def test_linked_worktree_requires_explicit_metadata_paths_and_matching_backlink(self):
        work = self.root/'worktree'
        self.git('worktree', 'add', '-b', 'linked', str(work))
        self.selection.update(checkout='worktree', git_dir='repo/.git/worktrees/worktree')
        self.declare(); collector, _ = self.read()
        assert collector.summary['head_sha256'] == digest(self.git('rev-parse', 'HEAD').decode().strip())
        self.selection['git_dir'] = 'repo/.git'; self.declare()
        assert self.invoke('preview')[0] == 2

    def test_linked_worktree_private_ref_namespaces_match_native_git_after_packing(self):
        self.git('worktree', 'add', '-b', 'linked', str(self.root/'worktree'))
        for prefix in ('bisect', 'worktree', 'rewritten'):
            self.git('update-ref', f'refs/{prefix}/main-private', 'HEAD')
            self.git('update-ref', f'refs/{prefix}/linked-private', 'HEAD', cwd=self.root/'worktree')
        self.git('pack-refs', '--all', '--prune')
        self.git('pack-refs', '--all', '--prune', cwd=self.root/'worktree')
        self.selection.update(checkout='worktree', git_dir='repo/.git/worktrees/worktree'); self.declare()
        collector, _ = self.read()
        expected = self.git('for-each-ref', '--format=%(refname)', cwd=self.root/'worktree').decode().splitlines()
        assert {r['name_sha256'] for r in collector.summary['refs']} == {digest(n) for n in expected}

    def test_corrupt_unselected_blob_cannot_forge_native_change_statistics(self):
        (self.repo/'other.txt').write_text('one line\n'); self.commit()
        oid = self.git('rev-parse', 'HEAD:other.txt').decode().strip()
        raw = b'forged\nextra\n'
        target = self.repo/'.git/objects'/oid[:2]/oid[2:]
        target.chmod(0o600)
        target.write_bytes(zlib.compress(b'blob '+str(len(raw)).encode()+b'\0'+raw))
        assert self.invoke('preview')[0] == 2

    def test_sha256_objects_annotated_tags_and_packfiles_are_verified(self):
        repo = self.root/'sha256'; repo.mkdir()
        self.git('init', '--object-format=sha256', '-b', 'main', cwd=repo)
        (repo/'AGENTS.md').write_text('SYNTHETIC_SHA256')
        self.git('add', '--all', cwd=repo); self.git('commit', '-m', 'SYNTHETIC_MESSAGE', cwd=repo)
        self.git('tag', '-a', 'release', '-m', 'SYNTHETIC_TAG', cwd=repo)
        self.git('gc', '--prune=now', cwd=repo)
        self.selection.update(checkout='sha256', git_dir='sha256/.git', common_dir='sha256/.git'); self.declare()
        collector, _ = self.read()
        assert collector.summary['head_sha256'] == digest(self.git('rev-parse', 'HEAD', cwd=repo).decode().strip())
        assert {r['object_type'] for r in collector.summary['refs']} == {'commit', 'tag'}

    def test_harness_snapshot_is_immutable_and_cannot_claim_observed_loading(self):
        collector, _ = self.read()
        value = collector.summary['harness']
        with pytest.raises(ValueError): HarnessSnapshot(value['head_sha256'], [])
        with pytest.raises(ValueError): HarnessSnapshot(value['head_sha256'], (), observed_loading='loaded')
        snapshot = HarnessSnapshot(value['head_sha256'], ())
        with pytest.raises(FrozenInstanceError): snapshot.effective_settings = 'observed'

    def test_ref_and_project_file_identifiers_are_distinct(self):
        path = self.repo/'refs/heads/main'; path.parent.mkdir(parents=True)
        path.write_text('SYNTHETIC_PROJECT_FILE'); self.commit()
        self.selection['harness_paths'] = ['refs/heads/main']; self.declare()
        _, captured = self.read()
        assert len({r.native_id_sha256 for r in captured.records}) == len(captured.records)

    def test_parent_traversal_and_nested_repositories_are_not_scanned(self):
        with Folder(self.root, time.monotonic()+2) as folder:
            with pytest.raises(ValueError): folder.read('../outside')
        (self.repo/'nested').mkdir(); (self.repo/'nested/.git').mkdir()
        (self.repo/'nested/AGENTS.md').write_text('SYNTHETIC_FOREIGN')
        self.selection['harness_paths'] = ['nested/AGENTS.md']; self.declare()
        assert self.invoke('preview')[0] == 3

    def test_gitlinks_are_rejected_before_reading_a_submodule_file(self):
        oid = self.git('rev-parse', 'HEAD').decode().strip()
        self.git('update-index', '--add', '--cacheinfo', f'160000,{oid},vendor')
        self.git('commit', '-m', 'SYNTHETIC_GITLINK')
        (self.repo/'vendor').mkdir(); (self.repo/'vendor/AGENTS.md').write_text('SYNTHETIC_FOREIGN')
        self.selection['harness_paths'] = ['vendor/AGENTS.md']; self.declare()
        original = Folder.read
        def guarded(folder, name, *args):
            assert name != 'repo/vendor/AGENTS.md'
            return original(folder, name, *args)
        with patch.object(Folder, 'read', guarded): assert self.invoke('preview')[0] == 3

    def test_missing_promisor_object_cannot_contact_configured_remote(self):
        oid = self.git('rev-parse', 'HEAD:AGENTS.md').decode().strip()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen(); listener.settimeout(.1)
            self.git('config', 'core.repositoryformatversion', '1')
            self.git('config', 'extensions.partialclone', 'origin')
            self.git('config', 'remote.origin.promisor', 'true')
            self.git('config', 'remote.origin.url', f'http://127.0.0.1:{listener.getsockname()[1]}/objects')
            (self.repo/'.git/objects'/oid[:2]/oid[2:]).unlink()
            assert self.invoke('preview')[0] == 2
            with pytest.raises(TimeoutError): listener.accept()

    def test_git_command_timeout_cleans_up_a_process_that_closed_stdout(self):
        binary = self.root/'controlled-git'
        binary.write_text("#!/usr/bin/python3 -I\nimport os,sys,time\nif '--version' in sys.argv: print('git version 2.56.0')\nelse: os.close(1); time.sleep(60)\n")
        binary.chmod(0o700)
        original, children = subprocess.Popen, []
        def launch(*args, **kwargs):
            child = original(*args, **kwargs); children.append(child); return child
        with patch('agent_harness_lab.sources.git_view.subprocess.Popen', side_effect=launch):
            with GitView(time.monotonic()+.5, binary=binary) as view:
                with pytest.raises(TimeoutError): view.run('fsck')
        assert len(children) == 2
        assert all(child.poll() is not None for child in children)

    def test_snapshot_import_can_return_to_prior_harness_content_without_erasing_revisions(self):
        original = (self.repo/'AGENTS.md').read_bytes()
        cache = self.root/'history.json'
        assert self.invoke('import', '--cache', str(cache))[0] == 0
        (self.repo/'AGENTS.md').write_text('SYNTHETIC_TEMPORARY_EDIT')
        assert self.invoke('import', '--cache', str(cache))[0] == 0
        (self.repo/'AGENTS.md').write_bytes(original)
        code, receipt = self.invoke('import', '--cache', str(cache))
        assert code == 0
        assert receipt['imported'] == 0
        _, status = self.invoke('status', '--cache', str(cache))
        assert status['checkpoints'] == 3
        assert status['revisions'] == 4

    def test_captured_harness_identity_does_not_follow_later_head_changes(self):
        collector, capture = self.read()
        before, old_root = collector.harness, capture.sha256
        (self.repo/'AGENTS.md').write_text('SYNTHETIC_NEXT_REVISION'); self.commit()
        newer = collector.read(self.source)
        assert before.head_sha256 != collector.harness.head_sha256
        assert old_root != newer.sha256
        assert before.observed_loading == 'unknown'
        assert before.files[0].content_sha256 != collector.harness.files[0].content_sha256

    def instruction_profile(self, cwd='src/task'):
        (self.repo/cwd).mkdir(parents=True, exist_ok=True)
        self.selection['instruction_profile'] = dict(schema_version=1, runtime='codex', version='0.166.0',
            task_cwd=cwd, fallback_filenames=[], project_doc_max_bytes=32768)
        self.declare()

    def test_optional_instruction_inventory_binds_declared_hierarchy_to_the_captured_harness(self):
        self.instruction_profile()
        (self.repo/'src/AGENTS.override.md').write_text('SYNTHETIC_NESTED_INSTRUCTION')
        collector, first = self.read(); before = collector.instructions
        assert before.git_harness_sha256 == collector.harness.sha256
        assert collector.summary['instruction_inventory_sha256'] == before.sha256
        active = {n.path_sha256 for n in before.nodes if n.applicability == 'declared_applicable'}
        assert active == {digest('AGENTS.md'), digest('src/AGENTS.override.md')}
        assert 'instruction_inventory' in {r.record_type for r in first.records}
        (self.repo/'src/AGENTS.override.md').write_text('SYNTHETIC_NEW_INSTRUCTION')
        newer = collector.read(self.source)
        assert first.sha256 != newer.sha256
        assert before.sha256 != collector.instructions.sha256
        assert before.observed_loading == 'unknown'

    def test_declared_claude_memory_imports_bind_to_the_captured_harness(self):
        self.instruction_profile()
        self.selection['instruction_profile'] = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='src/task')
        self.declare()
        (self.repo/'src/task/CLAUDE.md').write_text('@guide.md')
        guide = self.repo/'src/task/guide.md'; guide.write_text('SYNTHETIC_GUIDE')
        collector, first = self.read(); before = collector.instructions
        active = {n.path_sha256 for n in before.nodes if n.applicability == 'declared_applicable'}
        assert active == {digest('src/task/CLAUDE.md'), digest('src/task/guide.md')}
        assert (before.resolution_status, before.observed_loading) == ('declared', 'unknown')
        assert collector.summary['instruction_inventory_sha256'] == before.sha256
        code, preview = self.invoke('preview')
        assert code == 0; assert 'SYNTHETIC_GUIDE' not in json.dumps(preview)
        guide.write_text('SYNTHETIC_CHANGED_GUIDE')
        assert before.sha256 != (collector.read(self.source), collector.instructions.sha256)[1]

    def test_instruction_scope_cannot_cross_a_gitlink_or_nested_repository(self):
        oid = self.git('rev-parse', 'HEAD').decode().strip()
        self.git('update-index', '--add', '--cacheinfo', f'160000,{oid},vendor')
        self.git('commit', '-m', 'SYNTHETIC_GITLINK')
        self.instruction_profile('vendor/task')
        (self.repo/'vendor/task/AGENTS.md').write_text('SYNTHETIC_FOREIGN')
        original = Folder.read
        def guarded(folder, name, *args):
            assert name != 'repo/vendor/task/AGENTS.md'
            return original(folder, name, *args)
        with patch.object(Folder, 'read', guarded): assert self.invoke('preview')[0] == 3

    def test_instruction_file_race_does_not_advance_an_existing_journal(self):
        self.instruction_profile()
        target = self.repo/'src/AGENTS.md'; target.write_text('SYNTHETIC_BEFORE')
        cache = self.root/'instruction-journal.json'
        assert self.invoke('import', '--cache', str(cache))[0] == 0
        original, before = Folder.read, cache.read_bytes()
        def change_after_read(folder, name, *args):
            raw = original(folder, name, *args)
            if name == 'repo/src/AGENTS.md': target.write_text('SYNTHETIC_CHANGED')
            return raw
        with patch.object(Folder, 'read', change_after_read):
            assert self.invoke('import', '--cache', str(cache))[0] == 2
        assert cache.read_bytes() == before

    def test_optional_surface_inventory_extends_the_captured_harness_snapshot(self):
        self.selection['inventory_profile'] = dict(schema_version=1, runtime='codex', version='0.166.0', task_cwd='.')
        self.declare()
        collector, first = self.read()
        inventory = collector.inventory
        assert inventory.harness_sha256 == collector.harness.sha256
        assert {n.path_sha256 for n in inventory.nodes} == {f.path_sha256 for f in collector.harness.files}
        assert collector.summary['harness_inventory']['harness_inventory_sha256'] == inventory.sha256
        assert 'harness_inventory' in {r.record_type for r in first.records}
        self.selection['inventory_profile']['task_cwd'] = '../outside'; self.declare()
        with patch.object(GitView, 'run', side_effect=AssertionError('invalid profile must not run Git')):
            assert self.invoke('preview')[0] == 2

    def test_invalid_instruction_profile_is_rejected_before_running_git(self):
        self.instruction_profile()
        self.selection['instruction_profile']['task_cwd'] = '../outside'; self.declare()
        with patch.object(GitView, 'run', side_effect=AssertionError('invalid profile must not run Git')):
            assert self.invoke('preview')[0] == 2

    def test_tabs_newlines_and_pathspec_magic_in_file_names_remain_literal(self):
        name = 'strange\tname\n:(glob)*'
        (self.repo/name).write_text('SYNTHETIC_LITERAL_PATH'); self.commit()
        self.selection['harness_paths'] = [name]; self.declare()
        collector, _ = self.read()
        file = collector.harness.files[0]
        assert file.path_sha256 == digest(name)
        assert file.content_sha256 == file.committed_sha256
        assert file.path_sha256 in {c['path_sha256'] for c in collector.summary['commits'][0]['changes']}

    def test_corrupt_packfile_is_rejected_before_a_snapshot_is_returned(self):
        self.git('gc', '--prune=now')
        target = next((self.repo/'.git/objects/pack').glob('*.pack'))
        target.chmod(0o600)
        raw = bytearray(target.read_bytes()); raw[len(raw)//2] ^= 1; target.write_bytes(raw)
        assert self.invoke('preview')[0] == 2

    def test_shallow_clone_and_history_ceiling_are_explicit(self):
        (self.repo/'AGENTS.md').write_text('SYNTHETIC_SECOND'); self.commit()
        self.selection['max_commits'] = 1; self.declare()
        collector, _ = self.read(); assert 'history_limit_reached' in collector.summary['warnings']
        self.git('clone', '--depth=1', self.repo.as_uri(), str(self.root/'shallow'), cwd=self.root)
        self.selection.update(checkout='shallow', git_dir='shallow/.git', common_dir='shallow/.git')
        self.declare(); collector, _ = self.read()
        assert 'shallow_history' in collector.summary['warnings']
        assert collector.summary['commits'][0]['diff_sha256'] is None
        assert len(collector.summary['commits'][0]['parents_sha256']) == 1

    def test_links_alternates_nested_repositories_and_bare_repositories_are_blocked(self):
        objects = self.repo/'.git/objects'
        (objects/'escape').symlink_to(self.root)
        assert self.invoke('preview')[0] == 2; (objects/'escape').unlink()
        (objects/'info/alternates').write_text(str(self.root)+'\n')
        assert self.invoke('preview')[0] == 3; (objects/'info/alternates').unlink()
        (self.repo/'AGENTS.md').unlink(); (self.repo/'AGENTS.md').symlink_to(self.root/'source.json')
        assert self.invoke('preview')[0] == 2
        self.git('init', '--bare', str(self.root/'bare'), cwd=self.root)
        self.selection.update(checkout='bare', git_dir='bare', common_dir='bare'); self.declare()
        assert self.invoke('preview')[0] == 3

    def test_source_change_during_capture_cannot_advance_a_checkpoint(self):
        cache = self.root/'cache.json'; assert self.invoke('import', '--cache', str(cache))[0] == 0
        before = cache.read_bytes(); original = GitExport.objects
        def mutate(collector, common):
            original(collector, common)
            (self.repo/'AGENTS.md').write_text('SYNTHETIC_CONCURRENT')
            (self.repo/'.git/refs/heads/main').write_text('e'*40+'\n')
        with patch.object(GitExport, 'objects', mutate):
            assert self.invoke('import', '--cache', str(cache))[0] == 2
        assert cache.read_bytes() == before

    def test_capture_ignores_implicit_read_and_output_caps_but_preserves_direct_guards(self):
        with patch('agent_harness_lab.sources.git_view.MAX_BYTES', 10), patch('agent_harness_lab.sources.git_view.MAX_OUTPUT', 10):
            assert self.invoke('preview')[0] == 0
        with Folder(self.root, 0) as folder:
            with pytest.raises(TimeoutError): folder.read('source.json')
        with GitView(time.monotonic()+2) as view:
            with pytest.raises(ValueError): view.run('fetch')
            with patch('agent_harness_lab.sources.git_view.MAX_OUTPUT', 1):
                with pytest.raises(ValueError): view.run('--version')

    def test_explicit_unlimited_git_view_preserves_output_past_former_command_cap(self):
        configuration = self.root/'large-config'
        value = 'x'*(5*1024*1024)
        configuration.write_text('[synthetic]\nvalue='+value+'\n')
        with GitView(None, limit_output=False) as view:
            raw = view.run('config', '--file', str(configuration), '--null', '--list')
        assert raw == b'synthetic.value\n'+value.encode()+b'\0'

    def test_unlimited_capture_accepts_large_harness_and_preserves_all_selected_files(self):
        for index in range(105): (self.repo/f'selected-{index}.md').write_text('synthetic')
        (self.repo/'AGENTS.md').write_text('synthetic'*150000)
        self.commit()
        self.selection['harness_paths'] += [f'selected-{index}.md' for index in range(105)]
        self.selection.pop('max_commits'); self.declare()
        collector, snapshot = self.read()
        assert len(collector.harness.files) == 106
        assert len(collector.summary['commits']) == 2
        assert 'history_limit_reached' not in collector.summary['warnings']

    def test_more_than_one_hundred_explicit_commits_and_one_thousand_refs_are_accepted(self):
        self.selection['max_commits'] = 101
        refs = self.repo/'.git/refs/heads'
        oid = self.git('rev-parse', 'HEAD').decode().strip()
        for index in range(1001): (refs/f'synthetic-{index}').write_text(oid+'\n')
        self.declare(); collector, _ = self.read()
        assert len(collector.summary['refs']) == 1002

    def packed_and_loose(self, blob_bytes):
        # Synthetic incompressible blob packed by gc, then a loose commit on top.
        (self.repo/'blob.bin').write_bytes(random.Random(219).randbytes(blob_bytes)); self.commit()
        self.git('-c', 'gc.auto=0', 'gc', '--prune=now', '--quiet')
        (self.repo/'AGENTS.md').write_text('SYNTHETIC_LOOSE'); (self.repo/'small.bin').write_bytes(bytes(range(256))*16)
        self.commit()
        assert any((self.repo/'.git/objects/pack').glob('*.pack'))
        assert any(p.is_file() for p in (self.repo/'.git/objects').glob('[0-9a-f][0-9a-f]/*'))

    def frozen_capture(self):
        """Capture outputs that must stay byte-identical, with the record clock fixed."""
        class Fixed(datetime):
            @classmethod
            def now(cls, tz=None): return datetime(2026, 2, 2, tzinfo=timezone.utc)
        copies, original = {}, GitExport.objects
        def observe(collector, common):
            original(collector, common)
            base = collector.view.root/'objects'
            copies.update({str(p.relative_to(base)): (p.read_bytes(), p.stat().st_mode)
                           for p in base.rglob('*') if p.is_file()})
        cache = self.root/'equivalence-journal.json'; cache.unlink(missing_ok=True)
        with patch('agent_harness_lab.sources.git.datetime', Fixed), patch.object(GitExport, 'objects', observe):
            collector, snapshot = self.read()
            assert self.invoke('import', '--cache', str(cache))[0] == 0
        extra = (snapshot.graph.to_dict(), snapshot.metadata.to_dict()) if snapshot.graph is not None else None
        return (json.dumps(collector.summary, sort_keys=True), snapshot.sha256,
                [r.to_dict() for r in snapshot.records], extra, cache.read_bytes(), copies)

    @pytest.mark.parametrize('fmt', ['sha1', 'sha256'])
    def test_streamed_object_copies_and_digests_match_whole_buffer_reference(self, fmt):
        def whole_copy(folder, name, destination, maximum=None):
            raw = folder.read(name, maximum)
            if raw is None: return None
            Path(destination).write_bytes(raw); return len(raw)
        def whole_digest(view, *arguments): return hashlib.sha256(view.run(*arguments)).hexdigest()
        def whole_object(collector, kind, oid):
            raw = collector.view.run('cat-file', kind, oid.decode())
            header = kind.encode()+b' '+str(len(raw)).encode()+b'\0'
            if hashlib.new(collector.algorithm, header+raw).hexdigest().encode() != oid: raise ValueError('mismatch')
            return raw
        if fmt == 'sha256':
            shutil.rmtree(self.repo/'.git')
            (self.repo/'blob.bin').unlink(missing_ok=True); (self.repo/'small.bin').unlink(missing_ok=True)
            (self.repo/'AGENTS.md').write_text('SYNTHETIC_PRIVATE_INSTRUCTION\n')
            self.git('init', '-b', 'main', '--object-format=sha256'); self.commit()
        self.packed_and_loose(256*1024)
        for version in ('v1', 'v2'):
            self.source_version = version; self.declare()
            streamed = self.frozen_capture()
            with (patch.object(Folder, 'copy', whole_copy), patch.object(GitView, 'run_digest', whole_digest),
                    patch.object(GitExport, 'object', whole_object)):
                reference = self.frozen_capture()
            assert streamed == reference
            names = set(streamed[-1])
            assert any(n.endswith('.pack') for n in names) and any(n.endswith('.idx') for n in names)
            assert any(re.fullmatch(r'[0-9a-f]{2}/[0-9a-f]+', n) for n in names)
            for name, (raw, _) in streamed[-1].items():
                assert raw == (self.repo/'.git/objects'/name).read_bytes()
        summary = json.loads(streamed[0])
        assert summary['git_binary_sha256'] == hashlib.sha256(Path('/usr/bin/git').read_bytes()).hexdigest()
        head = self.git('rev-parse', 'HEAD').decode().strip()
        patch_bytes = self.git('diff-tree', '--root', '--no-commit-id', '-r', '--no-renames', '--no-ext-diff', '--no-textconv',
            '--patch', '--binary', '--full-index', '--no-color', head+'^', head)
        assert summary['commits'][0]['diff_sha256'] == hashlib.sha256(patch_bytes).hexdigest()

    def test_capture_peak_memory_is_bounded_by_chunk_size_not_pack_size(self):
        self.packed_and_loose(24*1024*1024)
        pack = max(p.stat().st_size for p in (self.repo/'.git/objects/pack').glob('*.pack'))
        bound = 4*git_view.CHUNK
        assert pack > 5*bound
        collector = GitExport(self.manifest)
        tracemalloc.start()
        try:
            collector.read(self.source); peak = tracemalloc.get_traced_memory()[1]
        finally: tracemalloc.stop()
        assert peak < bound

    def test_object_file_vanishing_after_enumeration_fails_closed_with_labelled_error(self):
        loose = next(path for path in (self.repo/'.git/objects').rglob('*') if path.is_file() and len(path.parent.name) == 2)
        original = Folder.walk
        def vanishing(folder, name, *args, **kwargs):
            for entry in original(folder, name, *args, **kwargs):
                if str(entry).endswith(loose.parent.name+'/'+loose.name): loose.unlink(missing_ok=True)
                yield entry
        with patch.object(Folder, 'walk', vanishing), pytest.raises(ValueError, match='Git input changed during reading'):
            self.read()

    def test_streamed_reads_keep_byte_output_and_binary_thresholds(self):
        target = self.root/'payload'; target.write_bytes(b'x'*(3*git_view.CHUNK+5)); size = target.stat().st_size
        with Folder(self.root, None) as folder:
            assert folder.copy('absent', self.root/'never') is None
            assert not (self.root/'never').exists()
            assert folder.copy('payload', self.root/'copied') == size
            assert folder.bytes == size
            assert (self.root/'copied').read_bytes() == target.read_bytes()
            with pytest.raises(ValueError, match='bounded single-link'): folder.copy('payload', self.root/'small', size-1)
            assert folder.copy('payload', self.root/'exact', size) == size
        for limit, accepted in ((size, True), (size-1, False)):
            with patch('agent_harness_lab.sources.git_view.MAX_BYTES', limit), Folder(self.root, None) as folder:
                if accepted: assert folder.copy('payload', self.root/'budget') == size
                else:
                    with pytest.raises(ValueError, match='byte budget'): folder.copy('payload', self.root/'budget')
        original = os.read
        def growing(descriptor, count):
            block = original(descriptor, count)
            if block and target.stat().st_size == size: target.write_bytes(b'y'*(size+1))
            return block
        with Folder(self.root, None) as folder, patch('agent_harness_lab.sources.git_view.os.read', growing):
            with pytest.raises(ValueError, match='changed'): folder.copy('payload', self.root/'raced')
        configuration = self.root/'output-config'; configuration.write_text('[synthetic]\nvalue='+'v'*300000+'\n')
        arguments = ('config', '--file', str(configuration), '--null', '--list')
        with GitView(None) as view:
            raw = view.run(*arguments)
            assert view.run_digest(*arguments) == hashlib.sha256(raw).hexdigest()
            for limit, accepted in ((len(raw), True), (len(raw)-1, False)):
                with patch('agent_harness_lab.sources.git_view.MAX_OUTPUT', limit):
                    for operation in (view.run, view.run_digest):
                        if accepted: operation(*arguments)
                        else:
                            with pytest.raises(ValueError, match='output budget'): operation(*arguments)
            with pytest.raises(ValueError, match='allowlisted'): view.run_digest('fetch')
            with pytest.raises(ValueError, match='operation failed'): view.run_digest('cat-file', 'blob', '0'*40)
        binary = Path('/usr/bin/git').stat().st_size
        with patch('agent_harness_lab.sources.git_view.MAX_BINARY', binary), GitView(None) as view:
            assert view.binary_sha256 == hashlib.sha256(Path('/usr/bin/git').read_bytes()).hexdigest()
        with patch('agent_harness_lab.sources.git_view.MAX_BINARY', binary-1):
            with pytest.raises(ValueError, match='exceeds bound'): GitView(None).__enter__()

    def use_evidence_profile(self):
        self.source_version = 'v2'; self.declare()

    def test_v2_native_relationships_use_scoped_revisions_and_never_execution_identity(self):
        self.use_evidence_profile()
        (self.repo/'AGENTS.md').write_text('SYNTHETIC_NEXT'); self.commit()
        collector, snapshot = self.read()
        graph = snapshot.graph; graph.validate(self.source, snapshot.records)
        kinds = {(r.source_sha256, r.native_id_sha256, r.revision_sha256): r.record_type for r in snapshot.records}
        assert {j.relation for j in graph.joins} == {'commit_parent', 'git_ref_target', 'harness_based_on', 'harness_member'}
        parent = next(j for j in graph.joins if j.relation == 'commit_parent')
        assert kinds[parent.left.key] == 'git_commit'; assert kinds[parent.right.key] == 'git_commit'
        assert parent.left.revision_sha256 == collector.summary['head_sha256']
        assert all(j.status == 'candidate' and j.basis == 'native_fields' for j in graph.joins)
        assert snapshot.metadata.identities == (); assert graph.summary()['independent_roots'] is None
        assert snapshot.metadata.coverage.reported_completeness == 'partial'
        assert all(f.reason == 'no_effective_binding' for f in snapshot.metadata.coverage.missing_fields)
        for private in ('SYNTHETIC', 'AGENTS.md', str(self.repo), 'refs/heads/main'):
            assert private not in json.dumps(graph.to_dict())+json.dumps(snapshot.metadata.to_dict())

    def test_v2_history_bound_preserves_parent_references_without_inventing_captured_commits(self):
        (self.repo/'AGENTS.md').write_text('SYNTHETIC_NEXT'); self.commit()
        self.selection['max_commits'] = 1; self.use_evidence_profile()
        collector, snapshot = self.read()
        commits = [r for r in snapshot.records if r.record_type == 'git_commit']
        references = [r for r in snapshot.records if r.record_type == 'git_object_reference']
        assert len(commits) == 1; assert len(references) == 1
        join = next(j for j in snapshot.graph.joins if j.relation == 'commit_parent')
        assert join.right.native_id_sha256 == references[0].native_id_sha256
        coverage = snapshot.metadata.coverage
        assert coverage.truncated; assert coverage.limit_reached
        assert next(b.observed for b in coverage.scan_bounds if b.dimension == 'commits') == 1
        assert len(collector.summary['commits']) == 1

    def test_v2_annotated_tag_targets_stay_object_references_without_assumed_peeling(self):
        self.git('tag', '-a', 'synthetic-tag', '-m', 'SYNTHETIC_TAG')
        self.use_evidence_profile(); collector, snapshot = self.read()
        native = next(r for r in collector.summary['refs'] if r['object_type'] == 'tag')
        ref_id = digest(('git_ref', 'refs/tags/synthetic-tag'))
        join = next(j for j in snapshot.graph.joins if j.left.native_id_sha256 == ref_id)
        target = next(r for r in snapshot.records if r.native_id_sha256 == join.right.native_id_sha256)
        assert target.record_type == 'git_object_reference'
        assert target.native_id_sha256 == digest(('git_object_reference', native['target_sha256']))
        assert join.right.revision_sha256 != collector.summary['head_sha256']

    def test_v2_probe_remains_offline_and_cli_import_exposes_both_contract_facets(self):
        self.use_evidence_profile()
        with patch('subprocess.Popen', side_effect=AssertionError('no Git during probe')):
            code, result = self.invoke('probe')
        assert code == 0; assert not result['source_read']
        cache = self.root/'private/cache.json'
        with patch.object(socket, 'socket', side_effect=AssertionError('no network')):
            code, preview = self.invoke('preview'); assert code == 0
            assert 'execution_metadata' in preview; assert 'evidence_graph' in preview
            assert self.invoke('import', '--cache', str(cache))[0] == 0
            first = cache.read_bytes()
            result = self.invoke('import', '--cache', str(cache))[1]
            assert result['imported'] == 0; assert cache.read_bytes() == first
        store = SourceStore(cache, project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        assert store.graph(self.source) is not None
        context = store.inventory()['sources'][0]
        assert context['execution_metadata']['identities'] == 0
        assert context['declared_coverage']['reported_completeness'] == 'partial'

    def test_v2_dirty_harness_and_later_head_preserve_old_snapshot_and_graph(self):
        self.use_evidence_profile(); cache = self.root/'cache.json'
        assert self.invoke('import', '--cache', str(cache))[0] == 0
        store = SourceStore(cache, project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)
        old_cursor = store.status(self.source)['cursor_sha256']; old_graph = store.graph(self.source, old_cursor)
        _, old = self.read()
        (self.repo/'AGENTS.md').write_text('SYNTHETIC_DIRTY')
        collector, dirty = self.read()
        assert collector.harness.files[0].content_sha256 != collector.harness.files[0].committed_sha256
        assert old.graph.sha256 != dirty.graph.sha256
        assert self.invoke('import', '--cache', str(cache))[0] == 0
        self.commit(); assert self.invoke('import', '--cache', str(cache))[0] == 0
        assert store.graph(self.source, old_cursor) == old_graph
        assert old.metadata.identities == (); assert dirty.metadata.identities == ()

    def test_v2_shallow_capture_is_truncated_even_when_commit_limit_is_not_reached(self):
        self.git('update-ref', 'refs/heads/other', self.git('rev-parse', 'HEAD').decode().strip())
        (self.repo/'AGENTS.md').write_text('SYNTHETIC_NEXT'); self.commit()
        head = self.git('rev-parse', 'HEAD').decode().strip()
        (self.repo/'.git/shallow').write_text(head+'\n')
        self.use_evidence_profile(); _, snapshot = self.read()
        assert snapshot.metadata.coverage.truncated
        assert not snapshot.metadata.coverage.limit_reached
        assert snapshot.metadata.coverage.reported_completeness == 'partial'
        assert any(r.record_type == 'git_object_reference' for r in snapshot.records)

    def test_v2_instruction_inventory_is_bound_to_harness_without_claiming_loaded_state(self):
        self.selection['instruction_profile'] = dict(schema_version=1, runtime='codex', version='0.166.0', task_cwd='.',
            fallback_filenames=[], project_doc_max_bytes=32768)
        self.use_evidence_profile(); collector, snapshot = self.read()
        join = next(j for j in snapshot.graph.joins if j.relation == 'instruction_inventory_of')
        assert join.left.revision_sha256 == collector.instructions.sha256
        assert join.right.revision_sha256 == collector.harness.sha256
        assert collector.harness.observed_loading == 'unknown'; assert snapshot.metadata.identities == ()

    def test_v2_facet_failure_cannot_advance_existing_journal(self):
        self.use_evidence_profile(); cache = self.root/'cache.json'
        assert self.invoke('import', '--cache', str(cache))[0] == 0; original = cache.read_bytes()
        (self.repo/'AGENTS.md').write_text('SYNTHETIC_NEW')
        with patch('agent_harness_lab.sources.git.capture_evidence', side_effect=ValueError('synthetic rejected graph')):
            assert self.invoke('import', '--cache', str(cache))[0] == 2
        assert cache.read_bytes() == original

    def test_v1_retains_legacy_snapshot_and_no_new_facets(self):
        collector, snapshot = self.read()
        assert snapshot.sha256 == digest(collector.summary)
        assert snapshot.graph is None; assert snapshot.metadata is None
        assert not any(r.record_type in ('harness_snapshot', 'git_object_reference') for r in snapshot.records)

    def test_v1_to_v2_upgrade_preserves_source_identity_records_and_legacy_checkpoint(self):
        cache = self.root/'cache.json'
        result = self.invoke('import', '--cache', str(cache))[1]
        legacy = self.source
        store = SourceStore(cache, project_sha256=legacy.project_sha256, scope_sha256=legacy.scope_sha256)
        old_cursor = store.status(legacy)['cursor_sha256']
        self.use_evidence_profile()
        assert self.source.source_key == legacy.source_key
        upgraded = self.invoke('import', '--cache', str(cache))[1]
        assert upgraded['imported'] == 1; assert upgraded['skipped'] == result['imported']
        assert store.graph(legacy, old_cursor) is None; assert store.capture(legacy, old_cursor) is None
        assert store.graph(self.source) is not None; assert store.status(self.source)['checkpoints'] == 2

    @pytest.mark.parametrize('parents', [10000, 20001])
    def test_v2_large_native_parent_sets_preserve_all_graph_nodes_and_receipts(self, parents):
        self.use_evidence_profile()
        tree = self.git('rev-parse', 'HEAD^{tree}').strip()
        raw = (b'tree '+tree+b'\n'+b''.join(f'parent {n:040x}\n'.encode() for n in range(1, parents+1))
            + b'author Synthetic <synthetic@example.invalid> 1767225600 +0000\n'
            + b'committer Synthetic <synthetic@example.invalid> 1767225600 +0000\n\nSYNTHETIC_MANY_PARENTS\n')
        native = b'commit '+str(len(raw)).encode()+b'\0'+raw
        sha = hashlib.sha1(native).hexdigest()
        directory = self.repo/'.git/objects'/sha[:2]; directory.mkdir(exist_ok=True)
        (directory/sha[2:]).write_bytes(zlib.compress(native))
        (self.repo/'.git/refs/heads/main').write_text(sha+'\n'); (self.repo/'.git/shallow').write_text(sha+'\n')
        collector, snapshot = self.read()
        assert len(snapshot.graph.joins) >= parents
        assert len(snapshot.graph.nodes) >= parents

    def recheck_store(self):
        self.use_evidence_profile()
        self.cache = self.root/'journal.json'
        assert self.invoke('import', '--cache', str(self.cache))[0] == 0
        return SourceStore(self.cache, project_sha256=self.source.project_sha256, scope_sha256=self.source.scope_sha256)

    def test_git_recheck_verifies_exact_current_capture_without_network_or_any_source_mutation(self):
        store = self.recheck_store()
        before = {p.relative_to(self.root): (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns)
            for p in self.root.rglob('*') if p.is_file()}
        with patch.object(socket, 'socket', side_effect=AssertionError('no network')):
            code, result = self.invoke('recheck', '--cache', str(self.cache))
        assert code == 0; assert result['statuses']['verified'] == 3
        assert not result['network_used']; assert result['source_read']
        assert not result['historical_interval_verified']; assert not result['execution_authorized']
        assert not result['task_outcomes_verified']; assert result['independent_roots'] is None
        assert result['resolver_version'] == 'git-native-recheck-v1'
        assert result['proposed_checkpoint_sha256'] == store.status(self.source)['cursor_sha256']
        after = {p.relative_to(self.root): (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns)
            for p in self.root.rglob('*') if p.is_file()}
        assert before == after; assert store.graph(self.source).summary()['statuses']['verified'] == 0

    def test_git_recheck_dirty_files_and_later_head_do_not_rebind_historical_harness(self):
        store = self.recheck_store(); old_graph = store.graph(self.source); original = self.cache.read_bytes()
        (self.repo/'AGENTS.md').write_text('SYNTHETIC_DIRTY')
        code, result = self.invoke('recheck', '--cache', str(self.cache)); assert code == 0
        members = [r for r in result['results'] if r['receipt']['relation'] == 'harness_member']
        assert members[0]['resolution_reason'] == 'stale_revision'
        assert members[0]['receipt']['status'] == 'unlinked'
        self.commit()
        result = self.invoke('recheck', '--cache', str(self.cache))[1]
        bases = [r for r in result['results'] if r['receipt']['relation'] == 'harness_based_on']
        assert bases[0]['resolution_reason'] == 'stale_revision'
        assert self.cache.read_bytes() == original; assert store.graph(self.source) == old_graph

    def test_git_recheck_unrelated_root_with_same_file_does_not_fabricate_old_ancestry(self):
        self.recheck_store()
        tree = self.git('rev-parse', 'HEAD^{tree}').decode().strip()
        unrelated = self.git('commit-tree', tree, '-m', 'SYNTHETIC_UNRELATED').decode().strip()
        self.git('update-ref', 'refs/heads/main', unrelated)
        code, result = self.invoke('recheck', '--cache', str(self.cache)); assert code == 0
        bases = [r for r in result['results'] if r['receipt']['relation'] == 'harness_based_on']
        assert bases[0]['resolution_reason'] == 'missing_identity'
        assert bases[0]['receipt']['status'] == 'unlinked'
        assert result['independent_roots'] is None

    def test_git_recheck_partial_parent_and_annotated_tag_targets_remain_candidates(self):
        (self.repo/'AGENTS.md').write_text('SYNTHETIC_NEXT'); self.commit()
        self.git('tag', '-a', 'synthetic-tag', '-m', 'SYNTHETIC_TAG')
        self.selection['max_commits'] = 1; self.recheck_store()
        code, result = self.invoke('recheck', '--cache', str(self.cache)); assert code == 0
        parent = next(r for r in result['results'] if r['receipt']['relation'] == 'commit_parent')
        tag = next(r for r in result['results'] if r['receipt']['left']['native_id_sha256'] == digest(('git_ref', 'refs/tags/synthetic-tag')))
        for row in (parent, tag):
            assert row['receipt']['status'] == 'candidate'
            assert row['resolution_reason'] == 'native_identity_unverified'
        assert result['statuses']['verified'] > 0

    def test_git_recheck_claim_labels_forged_relation_conflict_and_missing_target_never_verify(self):
        store = self.recheck_store(); graph = store.graph(self.source)
        member = next(j for j in graph.joins if j.relation == 'harness_member')
        claims = (replace(member, relation='same_execution', namespace='SYNTHETIC_SECRET', resolver_version='SYNTHETIC_SECRET'),
            replace(member, left=member.right, right=member.left),
            replace(member, status='conflicted', reason='conflicting_evidence'),
            replace(member, right=None, reason='missing_identity', status='unlinked'))
        result = resolve.recheck_git(self.source, GitExport(self.manifest), replace(graph, joins=claims))
        assert result['statuses']['verified'] == 0; assert result['statuses']['candidate'] == 2
        assert result['statuses']['conflicted'] == 1; assert result['statuses']['unlinked'] == 1
        assert 'SYNTHETIC_SECRET' not in json.dumps(result)
        assert all(r['receipt']['namespace'] == 'git.recheck' for r in result['results'])
        assert {r['claim_sha256'] for r in result['results']} == {j.sha256 for j in claims}

    def test_git_recheck_refuses_fake_readers_foreign_graphs_and_old_profiles_before_git(self):
        store = self.recheck_store(); graph = store.graph(self.source)
        class ForeignReader(GitExport): pass
        fake = Mock()
        with patch('subprocess.Popen', side_effect=AssertionError('no Git for rejected recheck')):
            for reader in (fake, ForeignReader(self.manifest)):
                with pytest.raises(NotImplementedError): resolve.recheck_git(self.source, reader, graph)
            with pytest.raises(ValueError):
                resolve.recheck_git(self.source, GitExport(self.manifest), replace(graph, project_sha256=digest('foreign')))
            legacy = replace(self.source, version='v1'); legacy_graph = replace(graph, adapter_version='v1')
            with pytest.raises(NotImplementedError): resolve.recheck_git(legacy, GitExport(self.manifest), legacy_graph)
            assert self.invoke('recheck', '--cache', str(self.cache), '--project-sha256', digest('foreign'))[0] == 2
            self.source = replace(self.source, enabled=False)
            self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='selection.json')))
            assert self.invoke('recheck', '--cache', str(self.cache))[0] == 3
        fake.read.assert_not_called()

    def test_git_recheck_clears_claimed_history_and_cannot_persist_its_verified_report(self):
        store = self.recheck_store(); graph = store.graph(self.source); original = self.cache.read_bytes()
        member = next(j for j in graph.joins if j.relation == 'harness_member')
        dated = replace(member, effective_from='2020-01-01T00:00:00Z', effective_until='2020-01-02T00:00:00Z')
        result = resolve.recheck_git(self.source, GitExport(self.manifest), replace(graph, joins=(dated,)))
        receipt = JoinReceipt.parse(result['results'][0]['receipt'])
        assert receipt.status == 'verified'; assert receipt.effective_from is None; assert receipt.effective_until is None
        with pytest.raises(ValueError):
            store.ingest(self.source, (), cursor_before=store.status(self.source)['cursor_sha256'],
                snapshot_sha256=digest('forged'), graph=replace(graph, joins=(receipt,)))
        with pytest.raises(ValueError): EvidenceGraph.parse(result)
        assert self.cache.read_bytes() == original

    def test_git_recheck_changed_or_corrupt_source_returns_no_proof_and_preserves_checkpoint(self):
        self.recheck_store(); original = self.cache.read_bytes()
        (self.repo/'.git/HEAD').write_text('SYNTHETIC_INVALID_HEAD\n')
        code, result = self.invoke('recheck', '--cache', str(self.cache))
        assert code == 2; assert result is None; assert self.cache.read_bytes() == original

