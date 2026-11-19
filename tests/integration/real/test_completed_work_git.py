"""Opt-in checks against local repositories, without recording their contents.

The forge check needs the operator's credentials; run it with pytest --noconftest.
"""
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from datetime import datetime
from urllib.parse import quote

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


def test_real_forge_collection_matches_forge_cli(checkout):
    if os.environ.get('REAL_COMPLETED_WORK_FORGE') != '1':
        pytest.skip('set REAL_COMPLETED_WORK_FORGE=1 and use the real forge credentials')
    with tempfile.TemporaryFile() as content:
        result = completed_work.capture(checkout, seconds=600, content=content)
    assert result.get('enumeration_complete'), result.get('problems')
    provider = result['provider']
    assert provider in ('github', 'gitlab')
    host, port, parts = completed_work._repository(checkout)
    cutoff = datetime.fromisoformat(result['captured_at'])
    for kind, resource in (('merged_pull_requests', 'pulls' if provider == 'github' else 'merge_requests'),
                           ('closed_issues', 'issues')):
        state = 'closed' if provider == 'github' or kind == 'closed_issues' else 'merged'
        if provider == 'github':
            target = '/repos/' + '/'.join(parts) + f'/{resource}?state={state}&per_page=100'
            args = ['gh', 'api', '--paginate', '--slurp', target]
            pages = json.loads(subprocess.check_output(args, cwd=checkout))
            rows = [row for page in pages for row in page]
        else:
            target = 'projects/' + quote('/'.join(parts), safe='') + f'/{resource}?state={state}&per_page=100'
            authority = f'[{host}]' if ':' in host else host
            if port is not None:
                authority += f':{port}'
            args = ['glab', 'api', '--hostname', authority, '--paginate', '--output', 'ndjson', target]
            rows = [json.loads(line) for line in subprocess.check_output(args, cwd=checkout).splitlines()]
        expected = set()
        for row in rows:
            if provider == 'github' and (kind == 'merged_pull_requests' and not row.get('merged_at')
                    or kind == 'closed_issues' and row.get('pull_request')):
                continue
            stamp = row.get('merged_at' if kind == 'merged_pull_requests' else 'closed_at')
            if stamp and datetime.fromisoformat(stamp.replace('Z', '+00:00')) > cutoff:
                continue
            expected.add(row['number' if provider == 'github' else 'iid'])
        assert {row['number'] for row in result[kind]} == expected
