"""Opt-in checks against local repositories, without recording their contents."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

import pytest

from agent_harness_lab.sources import completed_work


PROJECTS = json.loads(os.environ.get('REAL_COMPLETED_WORK_PROJECTS', '[]'))
HOST_HOME = Path.home()


@pytest.fixture(params=PROJECTS or [None], ids=lambda _: 'private-project')
def checkout(request):
    if request.param is None:
        pytest.skip('set REAL_COMPLETED_WORK_PROJECTS to local repository paths')
    project = Path(request.param).resolve(strict=True)
    base = HOST_HOME/'dev/.worktrees'
    with tempfile.TemporaryDirectory(prefix='ahl-completed-work-', dir=base) as folder:
        worktree = Path(folder)/'project'
        git = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'index.threads=1', '-C', str(project)]
        subprocess.run([*git, 'worktree', 'add', '-q', '--detach', str(worktree), 'HEAD'], check=True)
        try:
            yield worktree
        finally:
            subprocess.run([*git, 'worktree', 'remove', '--force', str(worktree)], check=True)


def test_real_git_history_and_patches_cover_every_reachable_commit(checkout):
    command = ['git', '-c', 'core.hooksPath=/dev/null', '-C', str(checkout)]
    head = subprocess.check_output([*command, 'rev-parse', 'HEAD'], text=True).strip()
    expected = set(subprocess.check_output([*command, 'rev-list', '--all', head], text=True).splitlines())
    problems = set()
    history = completed_work._git_history(checkout, head, problems, time.monotonic()+120)
    assert not problems
    assert len(history) == len(expected)
    assert {row['commit'] for row in history} == expected
    with tempfile.TemporaryFile() as content:
        result = completed_work._git_patches(checkout, head, content, problems, time.monotonic()+300)
        assert not problems
        assert result['offset'] == 0 and result['length'] == content.tell()
        content.seek(0)
        digest = hashlib.sha256()
        for chunk in iter(lambda: content.read(65536), b''):
            digest.update(chunk)
        assert digest.hexdigest() == result['sha256']
        content.seek(0)
        digest, length, patches = hashlib.sha256(), 0, set()
        with gzip.GzipFile(fileobj=content) as stream:
            for chunk in iter(lambda: stream.readline(1024*1024), b''):
                digest.update(chunk)
                length += len(chunk)
                if re.fullmatch(rb'commit [0-9a-f]{40,64}\n', chunk):
                    patches.add(chunk[7:-1].decode('ascii'))
        assert (length, digest.hexdigest()) == (result['decoded_length'], result['decoded_sha256'])
        assert patches == expected
