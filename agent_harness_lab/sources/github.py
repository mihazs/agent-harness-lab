"""Explicit read-only GitHub captures; comments and approvals are not task grades."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import time
from urllib.parse import parse_qsl
from .execution import CoverageManifest, ExecutionMetadata, RecordRef, ScanBound
from .graph import EvidenceGraph, JoinReceipt
from .schema import Contract, EvidenceRecord, SourceSpec, digest, timestamp
from .snapshot import Snapshot
from .transport import JsonAPI, TransportError, _json_exchange, endpoint

API_VERSION = '2026-03-10'
SOURCE_VERSION = API_VERSION+'-v1'

THREAD_FIELDS = '''fragment ThreadFields on PullRequestReviewThread {
  id repository { id } pullRequest { id number headRefOid }
  isResolved isOutdated path diffSide startDiffSide subjectType
  line startLine originalLine originalStartLine
  comments(first: 100, after: $commentsAfter) {
    totalCount pageInfo { hasNextPage endCursor }
    nodes { id fullDatabaseId body path createdAt updatedAt outdated
      line startLine originalLine originalStartLine
      repository { id } pullRequest { id } pullRequestReview { fullDatabaseId }
      commit { oid } originalCommit { oid }
    }
  }
}'''
THREADS_QUERY = '''query ReviewThreads($owner: String!, $name: String!, $number: Int!,
    $after: String, $commentsAfter: String) {
  repository(owner: $owner, name: $name) { id nameWithOwner
    pullRequest(number: $number) { id number headRefOid
      reviewThreads(first: 100, after: $after) {
        totalCount pageInfo { hasNextPage endCursor } nodes { ...ThreadFields }
      }
    }
  }
}''' + THREAD_FIELDS
THREAD_COMMENTS_QUERY = '''query ReviewThreadComments($thread: ID!, $commentsAfter: String) {
  node(id: $thread) { ...ThreadFields }
}''' + THREAD_FIELDS


def integer(value, *, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**63-1: raise ValueError('invalid GitHub integer')
    return value


def text_hash(value, *, required=False):
    if required and (not isinstance(value, str) or not value): raise ValueError('required GitHub text identity missing')
    if value is not None and (not isinstance(value, str) or len(value) > 4*1024*1024): raise ValueError('invalid GitHub text')
    return digest(value) if value is not None else None


def moment(value):
    if value is not None: timestamp(value)
    return value


def commit_hash(value):
    if value is not None and (not isinstance(value, str) or re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', value) is None):
        raise ValueError('full GitHub commit identity required')
    return digest(value) if value is not None else None


def opaque_id(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 1024 or any(not 32 < ord(c) < 127 for c in value):
        raise ValueError('invalid GitHub node identity or cursor')
    return value


def bigint(value):
    if isinstance(value, str) and re.fullmatch(r'[1-9][0-9]{0,18}', value): value = int(value)
    return integer(value, minimum=1)


@dataclass(frozen=True)
class GitHubSelection(Contract):
    repo_id: int
    issues: tuple = ()
    pulls: tuple = ()
    schema_version: int = 1
    details: tuple = ()
    commits: tuple = ()

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version not in (1, 2, 3, 4, 5): raise ValueError('unsupported GitHub selection version')
        integer(self.repo_id, minimum=1)
        allowed = (('checks', 'graph', 'threads', 'timeline') if self.schema_version == 5 else
            ('checks', 'threads', 'timeline') if self.schema_version >= 3 else ('checks', 'timeline'))
        if (type(self.details) is not tuple or len(self.details) > len(allowed) or any(d not in allowed for d in self.details)
                or tuple(sorted(set(self.details))) != self.details or self.schema_version == 1 and self.details):
            raise ValueError('invalid explicit GitHub details')
        for numbers in (self.issues, self.pulls):
            if type(numbers) is not tuple or len(numbers) > 10: raise ValueError('invalid GitHub selection')
            for number in numbers: integer(number, minimum=1)
            if tuple(sorted(set(numbers))) != numbers: raise ValueError('GitHub numbers must be unique and ascending')
        if len(set(self.issues)|set(self.pulls)) > 10: raise ValueError('GitHub selection exceeds ten issue/PR numbers')
        if type(self.commits) is not tuple or len(self.commits) > 10 or self.schema_version < 4 and self.commits:
            raise ValueError('invalid explicit GitHub commits')
        for sha in self.commits:
            if commit_hash(sha) is None: raise ValueError('selected GitHub commit identity missing')
        if tuple(sorted(set(self.commits))) != self.commits: raise ValueError('GitHub commits must be unique and ascending')

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict): raise ValueError('invalid GitHub selection')
        fields = set(cls.__dataclass_fields__)
        if value.get('schema_version') not in (2, 3, 4, 5): fields.remove('details')
        if value.get('schema_version') not in (4, 5): fields.remove('commits')
        if set(value) != fields: raise ValueError('invalid GitHub selection fields')
        if not all(isinstance(value[k], (list, tuple)) for k in ('issues', 'pulls')): raise ValueError('invalid GitHub number collections')
        if 'details' in value and not isinstance(value['details'], (list, tuple)): raise ValueError('invalid GitHub details collection')
        if 'commits' in value and not isinstance(value['commits'], (list, tuple)): raise ValueError('invalid GitHub commit collection')
        return cls(**{**value, 'issues': tuple(value['issues']), 'pulls': tuple(value['pulls']),
            'details': tuple(value.get('details', ())), 'commits': tuple(value.get('commits', ()))})

    def to_dict(self):
        value = super().to_dict()
        if self.schema_version == 1: value.pop('details')
        if self.schema_version < 4: value.pop('commits')
        return value

    @property
    def source_version(self): return API_VERSION+'-v'+str(self.schema_version)

    @property
    def project_sha256(self): return digest(('github.repository', self.repo_id))

    @property
    def sha256(self): return digest(self.to_dict())


class GitHubAPI:
    def __init__(self, base_url, selection, *, authorization=None, **limits):
        self.transport = JsonAPI(base_url, **limits)
        parsed, path = endpoint(base_url)
        if (self.transport.origin != ('api.github.com', 443) or parsed.query
                or re.fullmatch(r'/repos/[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}', path) is None):
            raise TransportError('only an explicit GitHub.com repository endpoint is supported')
        if not isinstance(selection, GitHubSelection): raise TransportError('explicit GitHub selection required')
        self.selection = GitHubSelection.parse(selection.to_dict())
        self.authorization = authorization

    def _url(self, value, suffix):
        parsed, path = endpoint(value)
        if ((parsed.hostname, parsed.port or 443) != self.transport.origin or parsed.query
                or path.casefold() != (self.transport.path+suffix).casefold()):
            raise TransportError('GitHub native resource exceeds the selected repository/parent')

    def _get(self, suffix, state):
        target, query = self.transport._target(self.transport.base+suffix)
        return self._request(target+('?' + query if query else ''), state)

    def _request(self, target, state, *, body=None):
        if state['requests'] >= self.transport.max_pages or state['used'] >= self.transport.max_bytes or time.monotonic() >= state['deadline']:
            raise TransportError('GitHub capture budget exhausted')
        headers = {'Accept': 'application/vnd.github+json', 'Accept-Encoding': 'identity', 'User-Agent': 'agent-harness-lab-reader'}
        if body is None: headers['X-GitHub-Api-Version'] = API_VERSION
        else: headers.update(Accept='application/json', **{'Content-Type': 'application/json'})
        if self.authorization is not None: headers['Authorization'] = self.authorization
        state['requests'] += 1
        value, metadata, size = _json_exchange(self.transport.origin, target, headers, body=body,
            timeout=self.transport.timeout, deadline=state['deadline'], max_bytes=self.transport.max_bytes-state['used'])
        state['used'] += size
        metadata = {k.lower(): v for k, v in metadata.items()}
        if (body is None and metadata.get('x-github-api-version-selected') != API_VERSION
                or metadata.get('content-type', '').split(';')[0].strip() not in ('application/json', 'application/vnd.github+json')):
            raise TransportError('GitHub API version/content capability is unavailable')
        return value, metadata

    def _graphql(self, query, variables, state):
        if query not in (THREADS_QUERY, THREAD_COMMENTS_QUERY) or self.authorization is None:
            raise TransportError('explicit authenticated GitHub thread query required')
        value, _ = self._request('/graphql', state, body=json.dumps(dict(query=query, variables=variables)).encode())
        if not isinstance(value, dict) or 'errors' in value or not isinstance(value.get('data'), dict):
            raise ValueError('GitHub GraphQL capability is blocked or incomplete')
        return value['data']

    @staticmethod
    def _connection(fetch):
        result, identities, cursors, cursor, total = [], set(), set(), None, None
        while True:
            value = fetch(cursor)
            count = integer(value['totalCount'])
            if count > 10000 or total is not None and total != count: raise ValueError('GitHub GraphQL total changed or excessive')
            total, nodes, page = count, value['nodes'], value['pageInfo']
            if not isinstance(nodes, list) or len(nodes) > 100 or type(page['hasNextPage']) is not bool:
                raise ValueError('invalid GitHub GraphQL connection')
            for node in nodes:
                native = opaque_id(node['id'])
                if native in identities: raise ValueError('duplicate GitHub GraphQL identity')
                identities.add(native)
            result.extend(nodes)
            if len(result) > total: raise ValueError('GitHub GraphQL total exceeded')
            if not page['hasNextPage']:
                if len(result) != total: raise ValueError('incomplete GitHub GraphQL connection')
                return result
            cursor = opaque_id(page['endCursor'])
            if not nodes or cursor in cursors: raise ValueError('GitHub GraphQL pagination cycle')
            cursors.add(cursor)

    def _thread(self, value, repository, pull):
        opaque_id(value['id'])
        if (value['repository']['id'] != repository['node_id'] or value['pullRequest']['id'] != pull['node_id']
                or integer(value['pullRequest']['number'], minimum=1) != pull['number'] or value['pullRequest']['headRefOid'] != pull['head']['sha']
                or type(value['isResolved']) is not bool or type(value['isOutdated']) is not bool
                or value['diffSide'] not in ('LEFT', 'RIGHT') or value['startDiffSide'] not in (None, 'LEFT', 'RIGHT')
                or value['subjectType'] not in ('LINE', 'FILE')):
            raise ValueError('GitHub review thread scope/head/state mismatch')
        return dict(parent_sha256=digest((self.selection.repo_id, 'pull', pull['number'])),
            head_sha256=commit_hash(pull['head']['sha']), path_sha256=text_hash(value['path'], required=True),
            resolved=value['isResolved'], outdated=value['isOutdated'], diff_side=value['diffSide'],
            start_diff_side=value['startDiffSide'], subject_type=value['subjectType'], **self._lines(value))

    @staticmethod
    def _lines(value):
        lines = {name: integer(value[field], minimum=1) if value[field] is not None else None
            for name, field in (('line', 'line'), ('start_line', 'startLine'),
                ('original_line', 'originalLine'), ('original_start_line', 'originalStartLine'))}
        for start, end in (('start_line', 'line'), ('original_start_line', 'original_line')):
            if lines[start] is not None and lines[end] is not None and lines[start] > lines[end]:
                raise ValueError('invalid GitHub line range')
        return lines

    def _threads(self, repository, pull, comments, state):
        def bind(value, kind, native):
            value = opaque_id(value)
            identities = state.setdefault('graphql_ids', {})
            identity = kind, native
            if value in identities and identities[value] != identity:
                raise ValueError('conflicting GitHub GraphQL node identity')
            identities[value] = identity
        bind(repository['node_id'], 'repository', self.selection.repo_id)
        bind(pull['node_id'], 'pull', (self.selection.repo_id, pull['id'], pull['number']))
        owner, name = self.transport.path[len('/repos/'):].split('/')
        def fetch(cursor):
            data = self._graphql(THREADS_QUERY, dict(owner=owner, name=name, number=pull['number'],
                after=cursor, commentsAfter=None), state)['repository']
            child = data['pullRequest']
            if (data['id'] != repository['node_id'] or data['nameWithOwner'].casefold() != repository['full_name'].casefold()
                    or child['id'] != pull['node_id'] or integer(child['number'], minimum=1) != pull['number'] or child['headRefOid'] != pull['head']['sha']):
                raise ValueError('GitHub GraphQL repository or PR identity changed')
            return child['reviewThreads']
        threads, links, known = {}, {}, {comment['id']: comment for comment in comments}
        for thread in self._connection(fetch):
            native = thread['id']; node = self._thread(thread, repository, pull)
            bind(native, 'thread', (self.selection.repo_id, pull['id'], native))
            def fetch_comments(cursor):
                current = thread if cursor is None else self._graphql(THREAD_COMMENTS_QUERY,
                    dict(thread=native, commentsAfter=cursor), state)['node']
                if current['id'] != native or self._thread(current, repository, pull) != node:
                    raise ValueError('GitHub thread changed during comment pagination')
                return current['comments']
            members = []
            for comment in self._connection(fetch_comments):
                number = bigint(comment['fullDatabaseId']); before = known.get(number)
                if before is None or number in links or comment['id'] != opaque_id(before['node_id']):
                    raise ValueError('GitHub thread comment has no unique REST identity')
                bind(comment['id'], 'comment', (self.selection.repo_id, pull['id'], number))
                if (comment['repository']['id'] != repository['node_id'] or comment['pullRequest']['id'] != pull['node_id']
                        or text_hash(comment['path'], required=True) != node['path_sha256']
                        or bigint(comment['pullRequestReview']['fullDatabaseId']) != before['pull_request_review_id']
                        or any(comment[field] != before[original] for field, original in
                            (('body', 'body'), ('path', 'path'), ('createdAt', 'created_at'), ('updatedAt', 'updated_at')))
                        or (comment['commit']['oid'] if comment['commit'] else None) != before['commit_id']
                        or (comment['originalCommit']['oid'] if comment['originalCommit'] else None) != before['original_commit_id']
                        or type(comment['outdated']) is not bool):
                    raise ValueError('GitHub thread comment parent/revision mismatch')
                thread_key = digest(('github_review_thread', self.selection.repo_id, native))
                links[number] = dict(thread_sha256=thread_key, resolved=node['resolved'], outdated=comment['outdated'], **self._lines(comment))
                members.append(digest(('github_review_comment', self.selection.repo_id, number)))
            threads[native] = dict(**node, comment_ids_sha256=sorted(members))
        if set(links) != set(known): raise ValueError('GitHub thread membership is incomplete')
        return threads, links

    def _next(self, link, suffix, current):
        if link is None: return None
        if not isinstance(link, str) or len(link) > 8192: raise ValueError('invalid GitHub pagination header')
        following = None
        for part in link.split(','):
            match = re.fullmatch(r'\s*<([^<>]+)>;\s*rel="(next|prev|first|last)"\s*', part)
            if match is None: raise ValueError('unsupported GitHub pagination')
            if match[2] != 'next': continue
            if following is not None: raise ValueError('duplicate GitHub next page')
            parsed, path = endpoint(match[1])
            if (parsed.hostname, parsed.port or 443) != self.transport.origin or path.casefold() != (self.transport.path+suffix).casefold():
                raise ValueError('GitHub pagination escaped its collection')
            pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
            values = dict(pairs)
            if (len(pairs) != 2 or set(values) != {'page', 'per_page'} or values['per_page'] != '100'
                    or re.fullmatch(r'[1-9][0-9]{0,6}', values['page']) is None or int(values['page']) != current+1):
                raise ValueError('GitHub pagination skipped or repeated a page')
            following = int(values['page'])
        return following

    def _list(self, suffix, state, key='id', *, envelope=None):
        page, result, seen, total = 1, [], set(), None
        while page is not None:
            value, headers = self._get(suffix+f'?per_page=100&page={page}', state)
            if envelope is not None:
                if envelope != 'check_runs' or not isinstance(value, dict): raise ValueError('unsupported GitHub listing envelope')
                count = integer(value['total_count'])
                if total is not None and total != count: raise ValueError('GitHub listing total changed')
                total, value = count, value[envelope]
            if not isinstance(value, list) or len(value) > 100 or len(result)+len(value) > 10000:
                raise ValueError('GitHub listing is malformed or excessive')
            for item in value:
                if not isinstance(item, dict): raise ValueError('invalid GitHub listing item')
                if key is not None:
                    if key not in item or digest(item[key]) in seen: raise ValueError('duplicate or missing GitHub listing identity')
                    seen.add(digest(item[key]))
            result.extend(value)
            page = self._next(headers.get('link'), suffix, page)
        if total is not None and len(result) != total: raise ValueError('GitHub listing total is incomplete')
        return result

    def _repository(self, value):
        self._url(value['url'], '')
        if (integer(value['id'], minimum=1) != self.selection.repo_id or type(value['private']) is not bool
                or value['visibility'] not in ('public', 'private', 'internal')
                or value['visibility'] == 'public' and value['private']
                or value['visibility'] == 'private' and not value['private']
                or value['full_name'].casefold() != self.transport.path[len('/repos/'):].casefold()
                or (value['private'] or value['visibility'] != 'public') and self.authorization is None):
            raise ValueError('GitHub repository identity or read access mismatch')
        return dict(repository_sha256=self.selection.project_sha256, visibility=value['visibility'], private=value['private'])

    @staticmethod
    def _actor(value):
        return digest(integer(value['id'], minimum=1)) if value is not None else None

    def _discussion(self, value):
        return dict(body_sha256=text_hash(value['body']), created_at=moment(value['created_at']),
            updated_at=moment(value['updated_at']), actor_sha256=self._actor(value['user']))

    def _commit(self, value):
        sha = value['sha']; self._url(value['url'], '/commits/'+sha)
        detail, parents = value['commit'], value['parents']
        if not isinstance(parents, list) or len(parents) > 100: raise ValueError('GitHub parent cap exceeded')
        return dict(commit_sha256=commit_hash(sha), parents_sha256=[commit_hash(p['sha']) for p in parents],
            message_sha256=text_hash(detail['message']), author_at=moment(detail['author']['date']), committer_at=moment(detail['committer']['date']))

    @staticmethod
    def _file(value):
        if value['status'] not in ('added', 'removed', 'modified', 'renamed', 'copied', 'changed', 'unchanged'):
            raise ValueError('unsupported GitHub file state')
        return dict(path_sha256=text_hash(value['filename'], required=True), status=value['status'],
            previous_path_sha256=text_hash(value.get('previous_filename')),
            **{k: integer(value[k]) for k in ('additions', 'deletions', 'changes')})

    def _commit_detail(self, sha, state):
        page, before, files = 1, None, {}
        while page is not None:
            raw, headers = self._get('/commits/'+sha+f'?per_page=100&page={page}', state)
            commit = self._commit(raw)
            tree = commit_hash(raw['commit']['tree']['sha'])
            stats = {k: integer(raw['stats'][k]) for k in ('additions', 'deletions', 'total')}
            count = integer(raw['commit']['comment_count'])
            if raw['sha'] != sha or tree is None or stats['total'] != stats['additions']+stats['deletions']:
                raise ValueError('GitHub commit identity/tree/stats mismatch')
            current = dict(commit=commit, tree_sha256=tree, stats=stats, comment_count=count)
            if before is not None and current != before: raise ValueError('GitHub commit metadata changed between file pages')
            before, rows = current, raw['files']
            if not isinstance(rows, list) or len(rows) > 100: raise ValueError('invalid GitHub commit file page')
            for row in rows:
                file = dict(**self._file(row), commit_sha256=commit['commit_sha256'], blob_sha256=commit_hash(row.get('sha')))
                if file['changes'] != file['additions']+file['deletions']: raise ValueError('invalid GitHub commit file statistics')
                path = file['path_sha256']
                if path in files: raise ValueError('duplicate GitHub commit file identity')
                files[path] = file
            if len(files) >= 3000: raise ValueError('GitHub commit file inventory reaches its native cap')
            page = self._next(headers.get('link'), '/commits/'+sha, page)
        if any(sum(f[k] for f in files.values()) != stats[k] for k in ('additions', 'deletions')):
            raise ValueError('GitHub commit file statistics are incomplete')
        comments = {}
        for raw in self._list('/commits/'+sha+'/comments', state):
            native = integer(raw['id'], minimum=1); self._url(raw['url'], '/comments/'+str(native))
            if raw['commit_id'] != sha: raise ValueError('GitHub commit comment references another commit')
            comments[native] = dict(**self._discussion(raw), commit_sha256=commit['commit_sha256'],
                path_sha256=text_hash(raw['path'], required=raw['path'] is not None),
                line=integer(raw['line'], minimum=1) if raw['line'] is not None else None)
        if len(comments) != count: raise ValueError('GitHub commit comment listing is incomplete')
        return dict(**before, files=files, comments=comments)

    def _issue(self, value, number):
        self._url(value['url'], f'/issues/{number}')
        if integer(value['number'], minimum=1) != number or value['state'] not in ('open', 'closed'): raise ValueError('GitHub issue mismatch')
        labels = value['labels']
        if not isinstance(labels, list) or len(labels) > 1000: raise ValueError('GitHub label bound exceeded')
        milestone = value['milestone']
        pull = value.get('pull_request')
        if pull is not None: self._url(pull['url'], f'/pulls/{number}')
        return dict(id_sha256=digest(integer(value['id'], minimum=1)), number_sha256=digest(number),
            state=value['state'], title_sha256=text_hash(value['title']), body_sha256=text_hash(value['body']),
            comments=integer(value['comments']), created_at=moment(value['created_at']), updated_at=moment(value['updated_at']),
            closed_at=moment(value['closed_at']), actor_sha256=self._actor(value['user']), is_pull=pull is not None,
            labels_sha256=sorted(digest((integer(v['id'], minimum=1), text_hash(v['name']))) for v in labels),
            milestone_sha256=digest(integer(milestone['id'], minimum=1)) if milestone else None)

    def _pull(self, value, number):
        self._url(value['url'], f'/pulls/{number}'); self._url(value['issue_url'], f'/issues/{number}')
        if (integer(value['number'], minimum=1) != number or integer(value['base']['repo']['id']) != self.selection.repo_id
                or value['state'] not in ('open', 'closed') or type(value['draft']) is not bool or type(value['merged']) is not bool):
            raise ValueError('GitHub PR scope/state mismatch')
        head, base = commit_hash(value['head']['sha']), commit_hash(value['base']['sha'])
        if head is None or base is None: raise ValueError('GitHub PR revision missing')
        merge = commit_hash(value.get('merge_commit_sha'))
        return dict(id_sha256=digest(integer(value['id'], minimum=1)), number_sha256=digest(number),
            state=value['state'], draft=value['draft'], merged=value['merged'],
            head_sha256=head, base_sha256=base, merge_sha256=merge,
            merge_reference_kind='unknown' if merge is None else 'merged_result' if value['merged'] else 'test_merge',
            head_repository_sha256=digest(integer(value['head']['repo']['id'], minimum=1)) if value['head']['repo'] else None,
            title_sha256=text_hash(value['title']), body_sha256=text_hash(value['body']),
            created_at=moment(value['created_at']), updated_at=moment(value['updated_at']),
            closed_at=moment(value['closed_at']), merged_at=moment(value['merged_at']),
            **{k: integer(value[k]) for k in ('comments', 'review_comments', 'changed_files', 'commits')})

    def _comment(self, value, number, *, inline=False, head=None):
        native = integer(value['id'], minimum=1)
        self._url(value['url'], f'/pulls/comments/{native}' if inline else f'/issues/comments/{native}')
        self._url(value['pull_request_url' if inline else 'issue_url'], f'/pulls/{number}' if inline else f'/issues/{number}')
        node = dict(parent_sha256=digest((self.selection.repo_id, 'pull' if inline else 'issue', number)), **self._discussion(value))
        if inline:
            node.update(path_sha256=text_hash(value['path'], required=True), commit_sha256=commit_hash(value['commit_id']),
                original_commit_sha256=commit_hash(value['original_commit_id']),
                review_sha256=digest(integer(value['pull_request_review_id'], minimum=1)),
                matches_head=None if value['commit_id'] is None else commit_hash(value['commit_id']) == head,
                resolved='unknown', outdated='unknown')
        return node

    def _review(self, value, number, head, *, timeline=False):
        self._url(value['pull_request_url'], f'/pulls/{number}')
        status = value['state'].upper() if timeline else value['state']
        if status not in ('APPROVED', 'CHANGES_REQUESTED', 'COMMENTED', 'DISMISSED', 'PENDING'): raise ValueError('unsupported GitHub review state')
        return dict(parent_sha256=digest((self.selection.repo_id, 'pull', number)), state=status,
            commit_sha256=commit_hash(value['commit_id']), body_sha256=text_hash(value['body']),
            submitted_at=moment(value['submitted_at']), actor_sha256=self._actor(value['user']),
            matches_head=None if value['commit_id'] is None or head is None else commit_hash(value['commit_id']) == head)

    def read(self, source):
        try:
            if not isinstance(source, SourceSpec) or (source.provider, source.version) != ('github', self.selection.source_version): raise ValueError('unsupported GitHub source')
            self.transport._authorize(source, project_sha256=self.selection.project_sha256,
                scope_sha256=self.selection.sha256, authorization=self.authorization)
            if 'threads' in self.selection.details and (self.authorization is None or any(n > 2**31-1 for n in self.selection.pulls)):
                raise ValueError('GitHub threads require explicit authentication and GraphQL PR numbers')
            return self._capture(source)
        except Exception:
            raise TransportError('GitHub capture blocked or incomplete; no automatic retry or checkpoint commit') from None

    def _checks(self, sha, parent, state, emit, relations):
        head = commit_hash(sha)
        if head is None: raise ValueError('check capture requires a full head identity')
        for run in self._list('/commits/'+sha+'/check-runs', state, envelope='check_runs'):
            native = integer(run['id'], minimum=1); self._url(run['url'], '/check-runs/'+str(native))
            if (run['head_sha'] != sha or run['status'] not in ('queued', 'in_progress', 'completed', 'waiting', 'requested', 'pending')
                    or run['conclusion'] not in (None, 'action_required', 'cancelled', 'failure', 'neutral', 'success', 'skipped', 'stale', 'timed_out', 'startup_failure')):
                raise ValueError('GitHub check head/status mismatch')
            value = dict(head_sha256=head, name_sha256=text_hash(run['name'], required=True), status=run['status'],
                conclusion=run['conclusion'], started_at=moment(run['started_at']), completed_at=moment(run['completed_at']),
                suite_sha256=digest(integer(run['check_suite']['id'], minimum=1)),
                app_sha256=digest(integer(run['app']['id'], minimum=1)) if run['app'] else None)
            key = emit('github_check_run', native, value, value['started_at']); relations.add(('check_on', key, parent))
        for status in self._list('/commits/'+sha+'/statuses', state):
            self._url(status['url'], '/statuses/'+sha)
            if status['state'] not in ('error', 'failure', 'pending', 'success'): raise ValueError('unsupported GitHub commit status')
            value = dict(head_sha256=head, state=status['state'], context_sha256=text_hash(status['context'], required=True),
                description_sha256=text_hash(status['description']), target_sha256=text_hash(status['target_url']),
                created_at=moment(status['created_at']), updated_at=moment(status['updated_at']), actor_sha256=self._actor(status['creator']))
            key = emit('github_commit_status', integer(status['id'], minimum=1), value, value['created_at'])
            relations.add(('status_on', key, parent))

    def _timeline(self, number, parent, issues, pulls, state, emit, nodes, relations):
        unmapped = []
        common = ('assigned', 'unassigned', 'closed', 'reopened', 'merged', 'referenced', 'connected', 'disconnected',
            'labeled', 'unlabeled', 'milestoned', 'demilestoned', 'renamed', 'head_ref_force_pushed', 'head_ref_deleted',
            'head_ref_restored', 'review_requested', 'review_request_removed', 'ready_for_review', 'convert_to_draft',
            'base_ref_changed', 'locked', 'unlocked', 'pinned', 'unpinned', 'subscribed', 'unsubscribed')
        for event in self._list(f'/issues/{number}/timeline', state, key=None):
            kind = event.get('event')
            if kind == 'cross-referenced':
                if event['source']['type'] != 'issue': raise ValueError('unsupported GitHub reference origin')
                origin = event['source']['issue']; native = integer(origin['id'], minimum=1); other = integer(origin['number'], minimum=1)
                parsed, path = endpoint(origin['url'])
                if ((parsed.hostname, parsed.port or 443) != self.transport.origin or parsed.query
                        or re.fullmatch(r'/repos/[^/]+/[^/]+/issues/'+str(other), path) is None):
                    raise ValueError('invalid GitHub native cross-reference')
                local = path.casefold() == (self.transport.path+f'/issues/{other}').casefold()
                value = dict(parent_sha256=parent, source_issue_sha256=digest(native), source_url_sha256=digest(('https://api.github.com', path.casefold())),
                    source_scope='selected_repository' if local else 'foreign_repository', occurrence_identity='unknown',
                    created_at=moment(event['created_at']), updated_at=moment(event['updated_at']), actor_sha256=self._actor(event['actor']))
                key = emit('github_cross_reference', (parent, native, value['source_url_sha256']), value, value['created_at'])
                relations.add(('timeline_on', key, parent))
                if local and other in issues:
                    if issues[other]['id_sha256'] != digest(native): raise ValueError('GitHub reference has conflicting native identity')
                    relations.add(('references_issue', key, digest(('github_issue', self.selection.repo_id, native))))
                continue
            if kind not in (*common, 'commented', 'reviewed') or type(event.get('id')) is not int:
                unmapped.append(dict(parent_sha256=parent, event_type_sha256=digest(kind), content_sha256=digest(event)))
                continue
            native = integer(event['id'], minimum=1)
            value = dict(parent_sha256=parent, event=kind, created_at=moment(event.get('created_at', event.get('submitted_at'))),
                actor_sha256=self._actor(event.get('actor', event.get('user'))), source_content_sha256=digest(event))
            namespace = 'issue_event'
            related = None
            if kind == 'commented':
                comment = self._comment(event, number)
                related = emit('github_issue_comment', native, comment, comment['created_at']); namespace = 'comment'
            elif kind == 'reviewed':
                review = self._review(event, number, pulls.get(number, {}).get('head_sha256'), timeline=True)
                namespace = 'review'
                value.update(commit_sha256=commit_hash(event['commit_id']), body_sha256=text_hash(event['body']))
                candidate = digest(('github_review', self.selection.repo_id, native))
                if candidate in nodes:
                    if nodes[candidate] != dict(kind='github_review', native_id_sha256=candidate, **review):
                        raise ValueError('GitHub timeline review conflicts with captured parent/revision')
                    related = candidate
            else:
                self._url(event['url'], '/issues/events/'+str(native))
                value['commit_sha256'] = commit_hash(event.get('commit_id'))
            key = emit('github_timeline_event', (namespace, native), value, value['created_at'])
            relations.add(('timeline_on', key, parent))
            if related is not None: relations.add(('timeline_comment' if kind == 'commented' else 'timeline_review', key, related))
        return unmapped

    def _capture(self, source):
        state = dict(requests=0, used=0, deadline=time.monotonic()+self.transport.seconds)
        records, nodes, relations = {}, {}, set()
        collected = datetime.now(timezone.utc).isoformat()
        def emit(kind, native, node, original=None):
            key = digest((kind, self.selection.repo_id, native))
            value = dict(kind=kind, native_id_sha256=key, **node)
            revision = digest(value)
            record = EvidenceRecord(source.source_key, key, revision, revision,
                'contextual_assertion' if kind in ('github_issue', 'github_issue_comment', 'github_review_comment', 'github_review', 'github_review_thread', 'github_commit_comment', 'github_cross_reference') else 'development_artifact',
                kind, collected, original_at=original, provenance='native_field', capture_completeness='partial')
            if key in records and records[key].revision_sha256 != revision: raise ValueError('conflicting GitHub native revision')
            records[key], nodes[key] = record, value
            if len(records) > 10000: raise ValueError('GitHub record cap exceeded')
            return key
        raw_repository = self._get('', state)[0]
        repository = self._repository(raw_repository)
        emit('github_repository', self.selection.repo_id, repository)
        issues, pulls, issue_keys = {}, {}, {}
        for number in sorted(set(self.selection.issues)|set(self.selection.pulls)):
            raw = self._get(f'/issues/{number}', state)[0]
            node = self._issue(raw, number); issues[number] = node
            parent = emit('github_issue', integer(raw['id'], minimum=1), node, node['created_at']); issue_keys[number] = parent
            comments = self._list(f'/issues/{number}/comments', state)
            if len(comments) != node['comments']: raise ValueError('GitHub conversation listing is incomplete')
            for comment in comments:
                child = self._comment(comment, number)
                key = emit('github_issue_comment', comment['id'], child, child['created_at'])
                relations.add(('conversation_on', key, parent))
        thread_reads = {}
        for number in self.selection.pulls:
            raw = self._get(f'/pulls/{number}', state)[0]
            node = self._pull(raw, number); pulls[number] = node
            if not issues[number]['is_pull'] or node['comments'] != issues[number]['comments']: raise ValueError('GitHub PR/issue relation changed')
            parent = emit('github_pull', integer(raw['id'], minimum=1), node, node['created_at'])
            relations.add(('pull_issue', parent, issue_keys[number]))
            files = self._list(f'/pulls/{number}/files', state, 'filename')
            commits = self._list(f'/pulls/{number}/commits', state, 'sha')
            comments = self._list(f'/pulls/{number}/comments', state)
            if (len(files), len(commits), len(comments)) != (node['changed_files'], node['commits'], node['review_comments']):
                raise ValueError('GitHub PR listing is incomplete or capped')
            for file in files:
                value = dict(**self._file(file), head_sha256=node['head_sha256'], blob_sha256=commit_hash(file['sha']))
                key = emit('github_pull_file', (raw['id'], value['path_sha256']), value)
                relations.add(('file_on', key, parent))
            for commit in commits:
                value = self._commit(commit)
                key = emit('github_commit', commit['sha'], value, value['committer_at']); relations.add(('commit_on', key, parent))
            review_keys = {}
            for review in self._list(f'/pulls/{number}/reviews', state):
                value = self._review(review, number, node['head_sha256'])
                key = emit('github_review', integer(review['id'], minimum=1), value, value['submitted_at'])
                review_keys[review['id']] = key
                relations.add(('review_on', key, parent))
            thread_links = {}
            if 'threads' in self.selection.details:
                threads, thread_links = self._threads(raw_repository, raw, comments, state)
                thread_reads[number] = raw, comments, (threads, thread_links)
                for native, value in threads.items():
                    key = emit('github_review_thread', native, value)
                    relations.add(('thread_on', key, parent))
            for comment in comments:
                value = self._comment(comment, number, inline=True, head=node['head_sha256'])
                if comment['id'] in thread_links: value.update(thread_links[comment['id']])
                key = emit('github_review_comment', comment['id'], value, value['created_at'])
                relations.add(('inline_on', key, parent))
                if comment['id'] in thread_links: relations.add(('thread_comment', value['thread_sha256'], key))
                if comment['pull_request_review_id'] in review_keys:
                    relations.add(('inline_review', key, review_keys[comment['pull_request_review_id']]))
            if 'checks' in self.selection.details: self._checks(raw['head']['sha'], parent, state, emit, relations)
        commit_reads = {}
        for sha in self.selection.commits:
            captured = self._commit_detail(sha, state); commit_reads[sha] = captured
            value = captured['commit']; parent = emit('github_commit', sha, value, value['committer_at'])
            detail = dict(commit_sha256=value['commit_sha256'], tree_sha256=captured['tree_sha256'],
                stats=captured['stats'], comment_count=captured['comment_count'])
            key = emit('github_commit_detail', sha, detail); relations.add(('details_of', key, parent))
            for path, value in captured['files'].items():
                key = emit('github_commit_file', (sha, path), value); relations.add(('commit_file_on', key, parent))
            for native, value in captured['comments'].items():
                key = emit('github_commit_comment', native, value, value['created_at']); relations.add(('commit_conversation_on', key, parent))
        if self.selection.commits:
            known = {v['commit_sha256']: k for k, v in nodes.items() if v['kind'] == 'github_commit'}
            for key, value in nodes.items():
                if value['kind'] == 'github_commit':
                    for parent in value['parents_sha256']:
                        if parent in known: relations.add(('commit_parent', key, known[parent]))
                if value['kind'] == 'github_pull':
                    for field, relation in (('head_sha256', 'pr_head_commit'), ('base_sha256', 'pr_base_commit'),
                            ('merge_sha256', 'pr_merge_commit' if value['merged'] else 'pr_test_merge_commit')):
                        if value[field] in known: relations.add((relation, key, known[value[field]]))
        unmapped = []
        if 'timeline' in self.selection.details:
            for number, parent in issue_keys.items():
                unmapped.extend(self._timeline(number, parent, issues, pulls, state, emit, nodes, relations))
        for number, before in issues.items():
            if self._issue(self._get(f'/issues/{number}', state)[0], number) != before: raise ValueError('GitHub issue changed during capture')
        for number, before in pulls.items():
            if self._pull(self._get(f'/pulls/{number}', state)[0], number) != before: raise ValueError('GitHub PR changed during capture')
        for raw, comments, before in thread_reads.values():
            if self._threads(raw_repository, raw, comments, state) != before: raise ValueError('GitHub review threads changed during capture')
        for sha, before in commit_reads.items():
            if self._commit_detail(sha, state) != before: raise ValueError('GitHub commit details or comments changed during capture')
        if self._repository(self._get('', state)[0]) != repository: raise ValueError('GitHub repository changed during capture')
        summary = dict(schema_version=1, api_version=API_VERSION, source_identity='repository_id_checked',
            repository=repository, nodes=[nodes[k] for k in sorted(nodes)], relations=sorted(relations),
            capture_completeness='partial', warnings=['selected_resources_only', 'thread_resolution_unknown', 'execution_binding_unknown'])
        if self.selection.schema_version >= 2:
            summary.update(detail_capabilities=self.selection.details, unmapped_timeline=unmapped)
            if unmapped: summary['warnings'].append('timeline_event_identity_unknown')
            if 'checks' in self.selection.details: summary['warnings'].append('latest_checks_only')
            if 'threads' in self.selection.details:
                summary['warnings'].remove('thread_resolution_unknown')
                summary['warnings'].append('thread_readbacks_non_atomic')
            if self.selection.commits: summary['warnings'].append('commit_file_inventory_limited')
        coverage = CoverageManifest(source.source_key, source.scope_sha256, source.scope_sha256, self.selection.source_version,
            'github-rest-graphql-v1' if 'threads' in self.selection.details else 'github-rest-v'+str(self.selection.schema_version),
            (ScanBound('records', 10000, len(records)), ScanBound('pages', self.transport.max_pages, state['requests']),
            ScanBound('bytes', self.transport.max_bytes, state['used'])), gaps=('not_captured',), reported_completeness='partial')
        graph = None
        if 'graph' in self.selection.details:
            refs = {k: RecordRef(r.source_sha256, r.native_id_sha256, r.revision_sha256) for k, r in records.items()}
            joins = tuple(JoinReceipt(refs[left], refs[right], relation, 'github.native', 'github-claims-v1',
                (refs[left], refs[right]), basis='native_fields') for relation, left, right in sorted(relations))
            graph = EvidenceGraph(source.source_key, source.project_sha256, source.scope_sha256, source.version, tuple(refs.values()), joins)
            graph.validate(source, records.values())
        snapshot = Snapshot(digest(summary), tuple(records[k] for k in sorted(records)), ExecutionMetadata((), coverage), graph)
        self.summary = summary
        return snapshot
