"""Synthetic native Git repositories; source code/config must never be executed."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
import unittest
import zlib
from dataclasses import FrozenInstanceError
from unittest.mock import patch

from agent_harness_lab.cli import main
from agent_harness_lab.sources.git import GitExport, HarnessSnapshot
from agent_harness_lab.sources.git_view import Folder, GitView
from agent_harness_lab.sources.schema import SourceSpec, digest


@unittest.skipUnless(Path('/usr/bin/git').is_file(), 'explicit system Git is required')
class GitSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = subprocess.check_output(['/usr/bin/git', '--version'], env={'PATH': '/usr/bin:/bin'})
        match = re.match(rb'git version 2\.(\d+)\.', raw)
        if not match or int(match[1]) < 43: raise unittest.SkipTest('Git 2.43+ in major version 2 is required')

    def setUp(self):
        fixtures = Path.home()/'dev/.worktrees'; fixtures.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='ahl-git-fixture-', dir=fixtures); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
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
        return subprocess.check_output(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
            '-c', 'commit.gpgsign=false', *arguments], cwd=cwd or self.repo, env=self.env, stderr=subprocess.DEVNULL)

    def commit(self):
        self.git('add', '--all'); self.git('commit', '-m', 'SYNTHETIC_PRIVATE_MESSAGE')

    def declare(self):
        self.source = SourceSpec('local_git', 'git', 'v1', digest(str(self.root.resolve())), digest('synthetic-project'),
            digest(self.selection), identity_provenance='source_declared_id', enabled=True)
        (self.root/'selection.json').write_text(json.dumps(self.selection))
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='selection.json')))

    def read(self):
        collector = GitExport(self.manifest); snapshot = collector.read(self.source)
        output = json.dumps(collector.summary)
        for private in ('SYNTHETIC_PRIVATE_INSTRUCTION', 'SYNTHETIC_PRIVATE_MESSAGE', str(self.root), 'AGENTS.md', 'refs/heads/main'):
            self.assertNotIn(private, output)
        return collector, snapshot

    def invoke(self, operation, *extra):
        out, error = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(error):
            code = main(['sources', operation, '--manifest', str(self.manifest),
                '--project-sha256', self.source.project_sha256, '--scope-sha256', self.source.scope_sha256, *extra])
        for private in (str(self.root), 'SYNTHETIC_PRIVATE'):
            self.assertNotIn(private, out.getvalue()+error.getvalue())
        return code, json.loads(out.getvalue()) if out.getvalue() else None

    def test_frozen_head_history_refs_and_harness_are_deterministic_and_read_only(self):
        before = {str(p.relative_to(self.repo)): (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
                  for p in self.repo.rglob('*') if p.is_file()}
        collector, first = self.read(); _, second = self.read()
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(len(collector.summary['commits']), 1)
        self.assertEqual(collector.summary['head_sha256'], digest(self.git('rev-parse', 'HEAD').decode().strip()))
        file = collector.summary['harness']['files'][0]
        self.assertEqual(file['content_sha256'], file['committed_sha256'])
        self.assertEqual(file['path_sha256'], collector.summary['commits'][0]['changes'][0]['path_sha256'])
        self.assertEqual(collector.summary['harness']['observed_loading'], 'unknown')
        self.assertEqual(collector.summary['harness']['effective_settings'], 'unknown')
        after = {str(p.relative_to(self.repo)): (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
                 for p in self.repo.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertTrue(all(r.independence == 'unknown' and r.causal_status == 'unknown' for r in first.records))

    def test_dirty_and_untracked_files_have_distinct_current_and_committed_identities(self):
        (self.repo/'AGENTS.md').write_text('SYNTHETIC_CHANGED')
        (self.repo/'new.md').write_text('SYNTHETIC_UNTRACKED')
        self.selection['harness_paths'] += ['new.md', 'missing.md']; self.declare()
        collector, _ = self.read()
        files = {f['path_sha256']: f for f in collector.summary['harness']['files']}
        self.assertNotEqual(files[digest('AGENTS.md')]['content_sha256'], files[digest('AGENTS.md')]['committed_sha256'])
        self.assertIsNone(files[digest('new.md')]['committed_sha256'])
        self.assertIsNone(files[digest('missing.md')]['content_sha256'])

    def test_source_configuration_includes_hooks_filters_fsmonitor_and_signatures_are_inert(self):
        # Directories expose accidental execution even under a zero file-size limit.
        marker = self.root/'EXECUTED'
        program = self.root/'evil'; program.write_text('#!/bin/sh\nmkdir '+str(marker)+'\n'); program.chmod(0o700)
        with (self.repo/'.git/config').open('a') as config:
            config.write(f'\n[core]\nfsmonitor = {program}\nhooksPath = {self.root}\n[diff "evil"]\ncommand = {program}\ntextconv = {program}\n[filter "evil"]\nclean = {program}\nsmudge = {program}\n[gpg]\nprogram = {program}\n[log]\nshowSignature = true\n[include]\npath = {self.root}/not-a-readable-config\n')
        self.read()
        self.assertFalse(marker.exists())

    def test_cli_preview_import_and_reimport_preserve_unknown_capture_and_private_metadata(self):
        code, result = self.invoke('preview'); self.assertEqual(code, 0)
        self.assertIn('git_summary', result)
        cache = self.root/'cache.json'
        self.assertEqual(self.invoke('import', '--cache', str(cache))[0], 0)
        code, result = self.invoke('import', '--cache', str(cache))
        self.assertEqual(code, 0); self.assertEqual(result['imported'], 0)
        self.assertGreater(result['skipped'], 0)
        self.assertNotIn(b'SYNTHETIC_PRIVATE', cache.read_bytes())
        self.assertNotIn(str(self.root).encode(), cache.read_bytes())

    def test_probe_and_scope_mismatch_do_not_invoke_git_or_read_objects(self):
        with patch.object(GitView, 'run', side_effect=AssertionError('metadata probe must not run Git')):
            self.assertEqual(self.invoke('probe')[0], 0)
            value = json.loads((self.root/'selection.json').read_text()); value['max_commits'] = 3
            (self.root/'selection.json').write_text(json.dumps(value))
            self.assertEqual(self.invoke('preview')[0], 2)

    def test_detached_head_and_dangling_refs_do_not_fabricate_branch_or_ancestry(self):
        self.git('checkout', '--detach')
        (self.repo/'.git/refs/heads/dangling').write_text('f'*40+'\n')
        collector, _ = self.read()
        self.assertIsNone(collector.summary['branch_sha256'])
        self.assertIn('dangling_refs', collector.summary['warnings'])
        self.assertEqual(len(collector.summary['refs']), 1)

    def test_linked_worktree_requires_explicit_metadata_paths_and_matching_backlink(self):
        work = self.root/'worktree'
        self.git('worktree', 'add', '-b', 'linked', str(work))
        self.selection.update(checkout='worktree', git_dir='repo/.git/worktrees/worktree')
        self.declare(); collector, _ = self.read()
        self.assertEqual(collector.summary['head_sha256'], digest(self.git('rev-parse', 'HEAD').decode().strip()))
        self.selection['git_dir'] = 'repo/.git'; self.declare()
        self.assertEqual(self.invoke('preview')[0], 2)

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
        self.assertEqual({r['name_sha256'] for r in collector.summary['refs']}, {digest(n) for n in expected})

    def test_corrupt_unselected_blob_cannot_forge_native_change_statistics(self):
        (self.repo/'other.txt').write_text('one line\n'); self.commit()
        oid = self.git('rev-parse', 'HEAD:other.txt').decode().strip()
        raw = b'forged\nextra\n'
        target = self.repo/'.git/objects'/oid[:2]/oid[2:]
        target.chmod(0o600)
        target.write_bytes(zlib.compress(b'blob '+str(len(raw)).encode()+b'\0'+raw))
        self.assertEqual(self.invoke('preview')[0], 2)

    def test_sha256_objects_annotated_tags_and_packfiles_are_verified(self):
        repo = self.root/'sha256'; repo.mkdir()
        self.git('init', '--object-format=sha256', '-b', 'main', cwd=repo)
        (repo/'AGENTS.md').write_text('SYNTHETIC_SHA256')
        self.git('add', '--all', cwd=repo); self.git('commit', '-m', 'SYNTHETIC_MESSAGE', cwd=repo)
        self.git('tag', '-a', 'release', '-m', 'SYNTHETIC_TAG', cwd=repo)
        self.git('gc', '--prune=now', cwd=repo)
        self.selection.update(checkout='sha256', git_dir='sha256/.git', common_dir='sha256/.git'); self.declare()
        collector, _ = self.read()
        self.assertEqual(collector.summary['head_sha256'], digest(self.git('rev-parse', 'HEAD', cwd=repo).decode().strip()))
        self.assertEqual({r['object_type'] for r in collector.summary['refs']}, {'commit', 'tag'})

    def test_harness_snapshot_is_immutable_and_cannot_claim_observed_loading(self):
        collector, _ = self.read()
        value = collector.summary['harness']
        with self.assertRaises(ValueError): HarnessSnapshot(value['head_sha256'], [])
        with self.assertRaises(ValueError): HarnessSnapshot(value['head_sha256'], (), observed_loading='loaded')
        snapshot = HarnessSnapshot(value['head_sha256'], ())
        with self.assertRaises(FrozenInstanceError): snapshot.effective_settings = 'observed'

    def test_ref_and_project_file_identifiers_are_distinct(self):
        path = self.repo/'refs/heads/main'; path.parent.mkdir(parents=True)
        path.write_text('SYNTHETIC_PROJECT_FILE'); self.commit()
        self.selection['harness_paths'] = ['refs/heads/main']; self.declare()
        _, captured = self.read()
        self.assertEqual(len({r.native_id_sha256 for r in captured.records}), len(captured.records))

    def test_parent_traversal_and_nested_repositories_are_not_scanned(self):
        with Folder(self.root, time.monotonic()+2) as folder:
            with self.assertRaises(ValueError): folder.read('../outside')
        (self.repo/'nested').mkdir(); (self.repo/'nested/.git').mkdir()
        (self.repo/'nested/AGENTS.md').write_text('SYNTHETIC_FOREIGN')
        self.selection['harness_paths'] = ['nested/AGENTS.md']; self.declare()
        self.assertEqual(self.invoke('preview')[0], 3)

    def test_gitlinks_are_rejected_before_reading_a_submodule_file(self):
        oid = self.git('rev-parse', 'HEAD').decode().strip()
        self.git('update-index', '--add', '--cacheinfo', f'160000,{oid},vendor')
        self.git('commit', '-m', 'SYNTHETIC_GITLINK')
        (self.repo/'vendor').mkdir(); (self.repo/'vendor/AGENTS.md').write_text('SYNTHETIC_FOREIGN')
        self.selection['harness_paths'] = ['vendor/AGENTS.md']; self.declare()
        original = Folder.read
        def guarded(folder, name, *args):
            self.assertNotEqual(name, 'repo/vendor/AGENTS.md')
            return original(folder, name, *args)
        with patch.object(Folder, 'read', guarded): self.assertEqual(self.invoke('preview')[0], 3)

    def test_missing_promisor_object_cannot_contact_configured_remote(self):
        oid = self.git('rev-parse', 'HEAD:AGENTS.md').decode().strip()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen(); listener.settimeout(.1)
            self.git('config', 'core.repositoryformatversion', '1')
            self.git('config', 'extensions.partialclone', 'origin')
            self.git('config', 'remote.origin.promisor', 'true')
            self.git('config', 'remote.origin.url', f'http://127.0.0.1:{listener.getsockname()[1]}/objects')
            (self.repo/'.git/objects'/oid[:2]/oid[2:]).unlink()
            self.assertEqual(self.invoke('preview')[0], 2)
            with self.assertRaises(TimeoutError): listener.accept()

    def test_git_command_timeout_cleans_up_a_process_that_closed_stdout(self):
        binary = self.root/'controlled-git'
        binary.write_text("#!/usr/bin/python3 -I\nimport os,sys,time\nif '--version' in sys.argv: print('git version 2.56.0')\nelse: os.close(1); time.sleep(60)\n")
        binary.chmod(0o700)
        original, children = subprocess.Popen, []
        def launch(*args, **kwargs):
            child = original(*args, **kwargs); children.append(child); return child
        with patch('agent_harness_lab.sources.git_view.subprocess.Popen', side_effect=launch):
            with GitView(time.monotonic()+.5, binary=binary) as view:
                with self.assertRaises(TimeoutError): view.run('fsck')
        self.assertEqual(len(children), 2)
        self.assertTrue(all(child.poll() is not None for child in children))

    def test_snapshot_import_can_return_to_prior_harness_content_without_erasing_revisions(self):
        original = (self.repo/'AGENTS.md').read_bytes()
        cache = self.root/'history.json'
        self.assertEqual(self.invoke('import', '--cache', str(cache))[0], 0)
        (self.repo/'AGENTS.md').write_text('SYNTHETIC_TEMPORARY_EDIT')
        self.assertEqual(self.invoke('import', '--cache', str(cache))[0], 0)
        (self.repo/'AGENTS.md').write_bytes(original)
        code, receipt = self.invoke('import', '--cache', str(cache))
        self.assertEqual(code, 0)
        self.assertEqual(receipt['imported'], 0)
        _, status = self.invoke('status', '--cache', str(cache))
        self.assertEqual(status['checkpoints'], 3)
        self.assertEqual(status['revisions'], 4)

    def test_captured_harness_identity_does_not_follow_later_head_changes(self):
        collector, capture = self.read()
        before, old_root = collector.harness, capture.sha256
        (self.repo/'AGENTS.md').write_text('SYNTHETIC_NEXT_REVISION'); self.commit()
        newer = collector.read(self.source)
        self.assertNotEqual(before.head_sha256, collector.harness.head_sha256)
        self.assertNotEqual(old_root, newer.sha256)
        self.assertEqual(before.observed_loading, 'unknown')
        self.assertNotEqual(before.files[0].content_sha256, collector.harness.files[0].content_sha256)

    def test_tabs_newlines_and_pathspec_magic_in_file_names_remain_literal(self):
        name = 'strange\tname\n:(glob)*'
        (self.repo/name).write_text('SYNTHETIC_LITERAL_PATH'); self.commit()
        self.selection['harness_paths'] = [name]; self.declare()
        collector, _ = self.read()
        file = collector.harness.files[0]
        self.assertEqual(file.path_sha256, digest(name))
        self.assertEqual(file.content_sha256, file.committed_sha256)
        self.assertIn(file.path_sha256, {c['path_sha256'] for c in collector.summary['commits'][0]['changes']})

    def test_corrupt_packfile_is_rejected_before_a_snapshot_is_returned(self):
        self.git('gc', '--prune=now')
        target = next((self.repo/'.git/objects/pack').glob('*.pack'))
        target.chmod(0o600)
        raw = bytearray(target.read_bytes()); raw[len(raw)//2] ^= 1; target.write_bytes(raw)
        self.assertEqual(self.invoke('preview')[0], 2)

    def test_shallow_clone_and_history_ceiling_are_explicit(self):
        (self.repo/'AGENTS.md').write_text('SYNTHETIC_SECOND'); self.commit()
        self.selection['max_commits'] = 1; self.declare()
        collector, _ = self.read(); self.assertIn('history_limit_reached', collector.summary['warnings'])
        self.git('clone', '--depth=1', self.repo.as_uri(), str(self.root/'shallow'), cwd=self.root)
        self.selection.update(checkout='shallow', git_dir='shallow/.git', common_dir='shallow/.git')
        self.declare(); collector, _ = self.read()
        self.assertIn('shallow_history', collector.summary['warnings'])
        self.assertIsNone(collector.summary['commits'][0]['diff_sha256'])
        self.assertEqual(len(collector.summary['commits'][0]['parents_sha256']), 1)

    def test_links_alternates_nested_repositories_and_bare_repositories_are_blocked(self):
        objects = self.repo/'.git/objects'
        (objects/'escape').symlink_to(self.root)
        self.assertEqual(self.invoke('preview')[0], 2); (objects/'escape').unlink()
        (objects/'info/alternates').write_text(str(self.root)+'\n')
        self.assertEqual(self.invoke('preview')[0], 3); (objects/'info/alternates').unlink()
        (self.repo/'AGENTS.md').unlink(); (self.repo/'AGENTS.md').symlink_to(self.root/'source.json')
        self.assertEqual(self.invoke('preview')[0], 2)
        self.git('init', '--bare', str(self.root/'bare'), cwd=self.root)
        self.selection.update(checkout='bare', git_dir='bare', common_dir='bare'); self.declare()
        self.assertEqual(self.invoke('preview')[0], 3)

    def test_source_change_during_capture_cannot_advance_a_checkpoint(self):
        cache = self.root/'cache.json'; self.assertEqual(self.invoke('import', '--cache', str(cache))[0], 0)
        before = cache.read_bytes(); original = GitExport.objects
        def mutate(collector, common):
            original(collector, common)
            (self.repo/'AGENTS.md').write_text('SYNTHETIC_CONCURRENT')
            (self.repo/'.git/refs/heads/main').write_text('e'*40+'\n')
        with patch.object(GitExport, 'objects', mutate):
            self.assertEqual(self.invoke('import', '--cache', str(cache))[0], 2)
        self.assertEqual(cache.read_bytes(), before)

    def test_read_and_output_limits_fail_without_committing_partial_imports(self):
        with patch('agent_harness_lab.sources.git_view.MAX_BYTES', 10):
            self.assertEqual(self.invoke('preview')[0], 2)
        with patch('agent_harness_lab.sources.git_view.MAX_OUTPUT', 10):
            self.assertEqual(self.invoke('preview')[0], 2)
        with Folder(self.root, 0) as folder:
            with self.assertRaises(TimeoutError): folder.read('source.json')
        with GitView(time.monotonic()+2) as view:
            with self.assertRaises(ValueError): view.run('fetch')


if __name__ == '__main__': unittest.main()
