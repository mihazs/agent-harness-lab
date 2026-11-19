"""Opt-in checks against local repositories, without recording their contents.

The forge check needs the operator's credentials; run it with pytest --noconftest.
"""
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlencode
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

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
        git = subprocess.Popen([*command, 'log', '--no-show-signature', '--no-notes', '--no-decorate',
                                '--no-color', '--no-ext-diff', '--no-textconv', '--binary', '--full-index',
                                '--diff-merges=first-parent', '--format=commit %H%n', '--patch', '--all', head],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env={**os.environ, 'GIT_OPTIONAL_LOCKS':'0', 'GIT_NO_REPLACE_OBJECTS':'1'})
        try:
            expected_digest, expected_length = hashlib.sha256(), 0
            for chunk in iter(lambda: git.stdout.read(65536), b''):
                expected_digest.update(chunk)
                expected_length += len(chunk)
            assert git.wait(timeout=10) == 0
        finally:
            if git.poll() is None:
                git.kill()
                git.wait()
            git.stdout.close()
        assert (length, digest.hexdigest()) == (expected_length, expected_digest.hexdigest())


def test_real_untracked_work_is_captured_without_modifying_source(checkout, request):
    project = Path(request.node.callspec.params['checkout']).resolve(strict=True)
    output = subprocess.check_output(['git', '-c', 'core.fsmonitor=false', '-C', str(project),
                                      'status', '--porcelain=v1', '-z', '--untracked-files=all'])
    source = None
    for entry in output.split(b'\0'):
        if not entry.startswith(b'?? '):
            continue
        relative = Path(os.fsdecode(entry[3:]))
        if relative.is_absolute() or '..' in relative.parts or relative.suffix.lower() != '.svg':
            continue
        candidate = project/relative
        if any(path.is_symlink() for path in (candidate, *candidate.parents) if path.is_relative_to(project)):
            continue
        info = candidate.lstat()
        if stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= 65536:
            source = candidate
            break
    if source is None:
        pytest.skip('no bounded existing untracked SVG file')
    target = checkout/source.relative_to(project)
    assert not any(path.is_symlink() for path in (target, *target.parents) if path.is_relative_to(checkout))
    assert not target.exists() and not target.is_symlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    expected = hashlib.sha256(source.read_bytes()).digest()
    problems = set()
    entries = completed_work._git_status(checkout, problems, time.monotonic()+30)
    with tempfile.TemporaryFile() as content:
        records = completed_work._untracked(checkout, entries, content, problems, time.monotonic()+30)
        match = [row for row in records if row['path'] == str(target.relative_to(checkout))]
        assert not problems and len(match) == 1
        content.seek(match[0]['offset'])
        actual = content.read(match[0]['length'])
        assert hashlib.sha256(actual).digest() == expected
        assert match[0]['sha256'] == expected.hex()
    assert hashlib.sha256(source.read_bytes()).digest() == expected


def test_real_tracked_work_matches_git_before_and_after_staging(checkout, request):
    project = Path(request.node.callspec.params['checkout']).resolve(strict=True)
    status = subprocess.check_output(['git', '-c', 'core.fsmonitor=false', '-C', str(project),
                                      'status', '--porcelain=v1', '-z', '--untracked-files=no'])
    source = None
    for entry in status.split(b'\0'):
        if not entry.startswith(b' M '):
            continue
        relative = Path(os.fsdecode(entry[3:]))
        if relative.is_absolute() or '..' in relative.parts:
            continue
        candidate = project/relative
        if any(path.is_symlink() for path in (candidate, *candidate.parents) if path.is_relative_to(project)):
            continue
        info = candidate.lstat()
        if stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 1024*1024:
            source = candidate
            break
    if source is None:
        pytest.skip('no bounded existing unstaged tracked file')
    target = checkout/source.relative_to(project)
    assert not any(path.is_symlink() for path in (target, *target.parents) if path.is_relative_to(checkout))
    assert target.is_file()
    original = hashlib.sha256(source.read_bytes()).digest()
    shutil.copyfile(source, target)
    target.chmod(stat.S_IMODE(source.stat().st_mode))
    git = ['git', '-c', 'core.fsmonitor=false', '-C', str(checkout)]
    head = subprocess.check_output([*git, 'rev-parse', 'HEAD'], text=True).strip()
    for staged in (False, True):
        if staged:
            subprocess.run([*git, 'add', '--', str(target.relative_to(checkout))], check=True)
        options = ['diff', '--no-color', '--no-ext-diff', '--no-textconv', '--binary', '--full-index']
        options += ['--cached', head, '--'] if staged else ['--']
        expected = subprocess.check_output([*git, *options])
        assert expected
        problems = set()
        with tempfile.TemporaryFile() as content:
            result = completed_work._working_patch(checkout, head, content, problems, time.monotonic()+30, staged=staged)
            content.seek(result['offset'])
            actual = content.read(result['length'])
        assert not problems and hashlib.sha256(actual).digest() == hashlib.sha256(expected).digest()
        assert result['sha256'] == hashlib.sha256(expected).hexdigest()
    assert hashlib.sha256(source.read_bytes()).digest() == original


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

        def captured_text(value):
            if isinstance(value, str):
                return value.encode('utf-8', 'surrogatepass')
            assert value['file'] == 'history.bin' and value['encoding'] == 'utf-8-surrogatepass'
            content.seek(value['offset'])
            data = content.read(value['length'])
            assert len(data) == value['length']
            return data

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
            expected = {}
            for row in rows:
                if provider == 'github' and (kind == 'merged_pull_requests' and not row.get('merged_at')
                        or kind == 'closed_issues' and row.get('pull_request')):
                    continue
                stamp = row.get('merged_at' if kind == 'merged_pull_requests' else 'closed_at')
                if stamp and datetime.fromisoformat(stamp.replace('Z', '+00:00')) > cutoff:
                    continue
                expected[row['number' if provider == 'github' else 'iid']] = row
            captured = {row['number']: row for row in result[kind]}
            assert captured.keys() == expected.keys()
            for number, row in expected.items():
                for field, source in (('title', 'title'), ('body', 'description' if provider == 'gitlab' else 'body')):
                    actual_hash = hashlib.sha256(captured_text(captured[number][field])).digest()
                    expected_hash = hashlib.sha256((row.get(source) or '').encode('utf-8', 'surrogatepass')).digest()
                    assert actual_hash == expected_hash


@pytest.mark.parametrize('kind', ['closed_issues', 'merged_pull_requests'])
def test_real_forge_discussions_match_rest(checkout, kind):
    if os.environ.get('REAL_COMPLETED_WORK_FORGE') != '1':
        pytest.skip('set REAL_COMPLETED_WORK_FORGE=1 and use the real forge credentials')
    from agent_harness_lab.sources.github import GitHubAPI, GitHubSelection
    from agent_harness_lab.sources.transport import JsonAPI, capture_state
    host, port, parts = completed_work._repository(checkout)
    provider = completed_work.detect(checkout)['provider']
    if provider not in ('github', 'gitlab'):
        pytest.skip('this comparison requires an authenticated GitHub or GitLab CLI')
    token = completed_work._credential(provider, host, port)
    assert token
    if provider == 'github':
        base = '/repos/' + '/'.join(parts)
        repo_id = int(subprocess.check_output(['gh', 'api', base, '--jq', '.id'], cwd=checkout))
        listing = json.loads(subprocess.check_output(['gh', 'api', base+'/issues?state=closed&per_page=100'], cwd=checkout))
        choices = [row['number'] for row in listing if 0 < row.get('comments', 0) <= 20 and
                   (not row.get('pull_request') if kind == 'closed_issues' else (row.get('pull_request') or {}).get('merged_at'))]
        if not choices:
            pytest.skip('no suitable completed item with discussions in the first page')
        number = choices[0]
        pages = json.loads(subprocess.check_output(['gh', 'api', '--paginate', '--slurp',
            base+f'/issues/{number}/comments?per_page=100'], cwd=checkout))
        expected = [row for page in pages for row in page]
        api = GitHubAPI('https://api.github.com'+base, GitHubSelection(repo_id=repo_id),
                        authorization='Bearer '+token, max_pages=30, max_bytes=8*1024*1024, timeout=5, seconds=60)
        found = api._graphql_comments([number], capture_state(api.transport)).get(number)
    else:
        authority = f'[{host}]' if ':' in host else host
        if port is not None:
            authority += f':{port}'
        prefix = 'projects/' + quote('/'.join(parts), safe='')
        cli = ['glab', 'api', '--hostname', authority]
        repo = json.loads(subprocess.check_output([*cli, prefix], cwd=checkout))
        resource = 'issues' if kind == 'closed_issues' else 'merge_requests'
        state = 'closed' if kind == 'closed_issues' else 'merged'
        listing = json.loads(subprocess.check_output([*cli, prefix+f'/{resource}?state={state}&per_page=100'], cwd=checkout))
        choices = [row['iid'] for row in listing if 0 < row.get('user_notes_count', 0) <= 20]
        if not choices:
            pytest.skip('no suitable completed item with discussions in the first page')
        number = choices[0]
        raw = subprocess.check_output([*cli, '--paginate', '--output', 'ndjson',
                                       prefix+f'/{resource}/{number}/discussions?per_page=100'], cwd=checkout)
        expected = [note for line in raw.splitlines() for note in json.loads(line)['notes']]
        api = JsonAPI('https://'+authority+'/api', max_pages=30, max_bytes=8*1024*1024, timeout=5, seconds=60)
        api._work_authorization = 'Bearer '+token
        found = completed_work._gitlab_discussions(api, capture_state(api), parts, kind,
                                                    [dict(number=number)], repo['id']).get(number)
    if found is None:
        pytest.skip('forge response requires the collector REST fallback')
    assert expected and found
    actual_hashes = {row['id']: hashlib.sha256((row.get('body') or '').encode('utf-8', 'surrogatepass')).digest() for row in found}
    expected_hashes = {row['id']: hashlib.sha256((row.get('body') or '').encode('utf-8', 'surrogatepass')).digest() for row in expected}
    assert actual_hashes == expected_hashes


def test_real_github_reviews_match_rest(checkout):
    if os.environ.get('REAL_COMPLETED_WORK_FORGE') != '1':
        pytest.skip('set REAL_COMPLETED_WORK_FORGE=1 and use the real forge credentials')
    from agent_harness_lab.sources.github import GitHubAPI, GitHubSelection
    from agent_harness_lab.sources.transport import capture_state
    host, port, parts = completed_work._repository(checkout)
    if completed_work.detect(checkout)['provider'] != 'github':
        pytest.skip('this comparison requires a GitHub repository')
    token = completed_work._credential('github', host, port)
    assert token
    base = '/repos/' + '/'.join(parts)
    repo_id = int(subprocess.check_output(['gh', 'api', base, '--jq', '.id'], cwd=checkout))
    numbers = json.loads(subprocess.check_output(['gh', 'api', base+'/pulls?state=closed&per_page=100',
                                                  '--jq', '[.[] | select(.merged_at != null) | .number]'], cwd=checkout))
    api = GitHubAPI('https://api.github.com'+base, GitHubSelection(repo_id=repo_id),
                    authorization='Bearer '+token, max_pages=30, max_bytes=8*1024*1024, timeout=5, seconds=90)
    state = capture_state(api.transport)
    found = None
    for offset in range(0, len(numbers), 10):
        batch = [dict(number=number) for number in numbers[offset:offset+10]]
        reviews = completed_work._github_review_counts(api, batch, state, repo_id)
        found = next(((number, notes) for number, (count, _, notes) in reviews.items() if count and notes), None)
        if found:
            break
    if found is None:
        pytest.skip('no bounded merged pull request with reviews in the first page')
    number, actual = found
    pages = json.loads(subprocess.check_output(['gh', 'api', '--paginate', '--slurp',
                                               base+f'/pulls/{number}/reviews?per_page=100'], cwd=checkout))
    expected = [row for page in pages for row in page]
    assert expected and actual
    def hashes(rows):
        return {row['id']: hashlib.sha256((row.get('body') or '').encode('utf-8', 'surrogatepass')).digest() for row in rows}
    assert hashes(actual) == hashes(expected)


def test_real_github_inline_review_comments_match_rest(checkout):
    if os.environ.get('REAL_COMPLETED_WORK_FORGE') != '1':
        pytest.skip('set REAL_COMPLETED_WORK_FORGE=1 and use the real forge credentials')
    from agent_harness_lab.sources.transport import JsonAPI, capture_state
    host, port, parts = completed_work._repository(checkout)
    if completed_work.detect(checkout)['provider'] != 'github':
        pytest.skip('this comparison requires a GitHub repository')
    token = completed_work._credential('github', host, port)
    assert token
    base = '/repos/' + '/'.join(parts)
    repo_id = int(subprocess.check_output(['gh', 'api', base, '--jq', '.id'], cwd=checkout))
    query = ('query($owner:String!,$name:String!,$endCursor:String) { repository(owner:$owner,name:$name) {'
             ' pullRequests(first:100,states:MERGED,after:$endCursor,orderBy:{field:UPDATED_AT,direction:DESC}) {'
             ' pageInfo { hasNextPage endCursor } nodes { number reviewThreads { totalCount } } } } }')
    command = ['gh', 'api', 'graphql', '-f', 'query='+query, '-f', 'owner='+parts[0], '-f', 'name='+parts[1]]
    numbers, cursor = [], None
    for _ in range(5):
        args = [*command, '-f', 'endCursor='+cursor] if cursor is not None else command
        page = json.loads(subprocess.check_output(args, cwd=checkout, timeout=20))['data']['repository']['pullRequests']
        numbers = [row['number'] for row in page['nodes'] if row['reviewThreads']['totalCount'] > 0]
        if numbers or not page['pageInfo']['hasNextPage']:
            break
        cursor = page['pageInfo']['endCursor']
    if not numbers:
        pytest.skip('no merged pull request with inline review threads')
    number = int(numbers[0])
    pages = json.loads(subprocess.check_output(['gh', 'api', '--paginate', '--slurp',
                                               base+f'/pulls/{number}/comments?per_page=100'], cwd=checkout))
    expected = [row for page in pages for row in page]
    api = JsonAPI('https://api.github.com'+base, max_pages=30, max_bytes=8*1024*1024, timeout=5, seconds=90)
    api._work_provider, api._work_authorization, api._work_repository_id = 'github', 'Bearer '+token, repo_id
    row, problems = dict(number=number, comments=[]), set()
    completed_work._comments(api, capture_state(api), '', 'github', 'merged_pull_requests', row,
                             datetime.now(timezone.utc), problems, [0, None], conversation=False, reviews=False)
    assert not problems and expected and row['comments']
    def hashes(rows):
        return {item['id']: hashlib.sha256((item.get('body') or '').encode('utf-8', 'surrogatepass')).digest() for item in rows}
    assert hashes(row['comments']) == hashes(expected)


def test_real_gitea_api_documents_collector_routes(checkout):
    if os.environ.get('REAL_COMPLETED_WORK_FORGE') != '1':
        pytest.skip('set REAL_COMPLETED_WORK_FORGE=1 for live forge checks')
    if completed_work.detect(checkout)['provider'] != 'gitea':
        pytest.skip('this check requires a Gitea remote')
    host, port, _ = completed_work._repository(checkout)
    authority = ('[' + host + ']' if ':' in host else host) + (':' + str(port) if port else '')
    with build_opener(ProxyHandler({})).open('https://' + authority + '/swagger.v1.json', timeout=10) as response:
        raw = response.read(4*1024*1024+1)
    assert len(raw) <= 4*1024*1024
    api = json.loads(raw)
    paths = api['paths']
    for suffix in ('/pulls', '/issues', '/issues/{index}/comments', '/pulls/{index}/reviews',
                   '/pulls/{index}/reviews/{id}/comments'):
        assert 'get' in paths['/repos/{owner}/{repo}' + suffix]
    for name, fields in {'Issue': ('number', 'title', 'body', 'closed_at', 'updated_at'),
                         'PullRequest': ('number', 'title', 'body', 'merged_at', 'updated_at'),
                         'Comment': ('id', 'body', 'created_at', 'updated_at'),
                         'PullReview': ('id', 'body', 'submitted_at', 'updated_at'),
                         'PullReviewComment': ('id', 'body', 'created_at', 'updated_at')}.items():
        assert set(fields) <= api['definitions'][name]['properties'].keys()


def test_real_private_gitea_without_credentials_is_incomplete(checkout):
    if os.environ.get('REAL_COMPLETED_WORK_FORGE') != '1':
        pytest.skip('set REAL_COMPLETED_WORK_FORGE=1 for live forge checks')
    if completed_work.detect(checkout)['provider'] != 'gitea':
        pytest.skip('this check requires a Gitea remote')
    host, port, parts = completed_work._repository(checkout)
    if completed_work._credential('gitea', host, port) is not None:
        pytest.skip('this check requires an unconfigured Gitea token')
    authority = f'[{host}]' if ':' in host else host
    if port is not None:
        authority += f':{port}'
    endpoint = 'https://'+authority+'/api/v1/repos/'+'/'.join(parts)
    try:
        with build_opener(ProxyHandler({})).open(endpoint, timeout=8):
            pytest.skip('the repository is accessible without credentials')
    except HTTPError as error:
        assert error.code in (401, 403, 404)
    with tempfile.TemporaryFile() as content:
        result = completed_work.capture(checkout, seconds=30, content=content)
    assert result['provider'] == 'gitea' and result['complete'] is False
    assert not result.get('enumeration_complete', False)
    assert 'forge_repository_unavailable_or_mismatched' in result['problems']


def test_real_authenticated_gitea_inventory(checkout):
    if os.environ.get('REAL_COMPLETED_WORK_FORGE') != '1':
        pytest.skip('set REAL_COMPLETED_WORK_FORGE=1 for live forge checks')
    if completed_work.detect(checkout)['provider'] != 'gitea':
        pytest.skip('this check requires a Gitea remote')
    from agent_harness_lab.sources.forge_remote import _NoRedirect
    host, port, parts = completed_work._repository(checkout)
    token = completed_work._credential('gitea', host, port)
    if token is None:
        pytest.skip('a host-bound Gitea token is not configured')
    authority = f'[{host}]' if ':' in host else host
    if port is not None:
        authority += f':{port}'
    base = 'https://'+authority+'/api/v1/repos/'+'/'.join(parts)
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    with tempfile.TemporaryFile() as content:
        result = completed_work.capture(checkout, seconds=600, content=content)
        assert result.get('enumeration_complete'), result.get('problems')
        cutoff = datetime.fromisoformat(result['captured_at'])
        for kind, resource in (('merged_pull_requests', 'pulls'), ('closed_issues', 'issues')):
            expected = {}
            for page in range(1, 21):
                target = base+'/'+resource+'?'+urlencode(dict(state='closed', limit=50, page=page))
                request = Request(target, headers={'Authorization': 'token '+token, 'Accept': 'application/json'})
                with opener.open(request, timeout=8) as response:
                    data = response.read(8*1024*1024+1)
                assert len(data) <= 8*1024*1024
                rows = json.loads(data)
                assert isinstance(rows, list)
                if not rows:
                    break
                for row in rows:
                    if kind == 'merged_pull_requests' and not row.get('merged_at'):
                        continue
                    if kind == 'closed_issues' and row.get('pull_request') is not None:
                        continue
                    stamp = row.get('merged_at' if kind == 'merged_pull_requests' else 'closed_at')
                    if stamp and datetime.fromisoformat(stamp.replace('Z', '+00:00')) > cutoff:
                        continue
                    expected[row['number']] = row
            else:
                pytest.skip('independent Gitea listing exceeded the test page bound')
            captured = {row['number']: row for row in result[kind]}
            assert captured.keys() == expected.keys()
            for number, row in expected.items():
                for field in ('title', 'body'):
                    value = captured[number][field]
                    if isinstance(value, dict):
                        assert value['file'] == 'history.bin' and value['encoding'] == 'utf-8-surrogatepass'
                        content.seek(value['offset'])
                        actual = content.read(value['length'])
                        assert len(actual) == value['length']
                    else:
                        actual = value.encode('utf-8', 'surrogatepass')
                    assert hashlib.sha256(actual).digest() == hashlib.sha256((row.get(field) or '').encode('utf-8', 'surrogatepass')).digest()
