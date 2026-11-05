"""Explicit read-only Gitea REST captures; closure, merges and approvals are never task grades."""
from dataclasses import dataclass
from datetime import datetime, timezone
import re
import time
from urllib.parse import parse_qsl
from .execution import CoverageManifest, ExecutionMetadata, ScanBound
from .github import commit_hash, integer, moment, text_hash
from .remote_config import ConfiguredAPI
from .schema import Contract, EvidenceRecord, SourceSpec, digest
from .snapshot import Snapshot
from .transport import HTTPStatusError, JsonAPI, TransportError, _json_exchange, endpoint, next_link

PROVIDER = 'gitea'
SOURCE_VERSION = 'v1'
API_PREFIX = '/api/v1'
PAGE_SIZE = 50
CAPTURE = ('comments', 'issues', 'pulls', 'reviews')
REVIEW_STATES = ('APPROVED', 'COMMENT', 'PENDING', 'REQUEST_CHANGES', 'REQUEST_REVIEW')
DENIED = {401: 'unauthorized', 403: 'forbidden', 404: 'not_found', 429: 'rate_limited'}
# Reviews absent from a server (404 on an already-read PR) are a declared capability gap, not a block.
CHECKPOINTABLE = ('captured', 'unsupported_endpoint')
CONTEXTUAL = ('gitea_issue', 'gitea_comment', 'gitea_review')
NAME = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}')


class Unavailable(Exception):
    """A native read that produced an explicit coverage state instead of data."""

    def __init__(self, state):
        super().__init__(state)
        self.state = state


@dataclass(frozen=True)
class GiteaSelection(Contract):
    base_url: str
    owner: str
    repo: str
    capture: tuple
    issues: tuple = ()
    pulls: tuple = ()
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1: raise ValueError('unsupported Gitea selection version')
        parsed, path = endpoint(self.base_url)
        host = f'[{parsed.hostname}]' if ':' in parsed.hostname else parsed.hostname
        port = '' if parsed.port in (None, 443) else f':{parsed.port}'
        if (parsed.query or self.base_url != f'https://{host}{port}{path}' or path.endswith('/')
                or path.casefold().endswith(API_PREFIX)):
            raise ValueError('Gitea base URL must be a canonical HTTPS instance root')
        for name in (self.owner, self.repo):
            if not isinstance(name, str) or NAME.fullmatch(name) is None: raise ValueError('invalid Gitea owner or repository name')
        if (type(self.capture) is not tuple or tuple(sorted(set(self.capture))) != self.capture
                or any(kind not in CAPTURE for kind in self.capture)):
            raise ValueError('Gitea capture kinds must be unique, ascending and known')
        for numbers in (self.issues, self.pulls):
            if type(numbers) is not tuple: raise ValueError('invalid Gitea selection')
            for number in numbers: integer(number, minimum=1)
            if tuple(sorted(set(numbers))) != numbers: raise ValueError('Gitea numbers must be unique and ascending')
        # Issues and pulls share one per-repository index, so a number is never both.
        if (set(self.issues) & set(self.pulls) or ('issues' in self.capture) != bool(self.issues)
                or ('pulls' in self.capture) != bool(self.pulls) or 'reviews' in self.capture and not self.pulls
                or not (self.issues or self.pulls)):
            raise ValueError('Gitea capture kinds must match the explicitly selected numbers')

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__): raise ValueError('invalid Gitea selection fields')
        if not all(isinstance(value[k], (list, tuple)) for k in ('capture', 'issues', 'pulls')): raise ValueError('invalid Gitea collections')
        return cls(**{**value, **{k: tuple(value[k]) for k in ('capture', 'issues', 'pulls')}})

    @property
    def instance(self):
        parsed, path = endpoint(self.base_url)
        return parsed.hostname, parsed.port or 443, path

    @property
    def full_name(self): return f'{self.owner}/{self.repo}'

    def repository_sha256(self, full_name):
        """Host, port, instance path and case-insensitive owner/repository name identify a repository."""
        if not isinstance(full_name, str) or len(full_name.split('/')) != 2 or any(
                NAME.fullmatch(part) is None for part in full_name.split('/')):
            raise ValueError('invalid Gitea repository name')
        return digest(('gitea.repository', *self.instance, full_name.casefold()))

    @property
    def instance_sha256(self): return digest(('gitea.instance', *self.instance))

    @property
    def project_sha256(self): return self.repository_sha256(self.full_name)

    @property
    def source_version(self): return SOURCE_VERSION

    @property
    def sha256(self): return digest(self.to_dict())


class GiteaAPI:
    """GET-only reader for selected issues/PRs of one explicitly named repository."""

    def __init__(self, base_url, selection, *, authorization=None, **limits):
        if not isinstance(selection, GiteaSelection): raise TransportError('explicit Gitea selection required')
        self.selection = GiteaSelection.parse(selection.to_dict())
        if base_url != self.selection.base_url: raise TransportError('Gitea endpoint must equal the selected instance root')
        self.transport = JsonAPI(base_url, **limits)
        self.authorization = authorization
        self.repo_path = f'{API_PREFIX}/repos/{self.selection.owner}/{self.selection.repo}'

    def read(self, source, *, prior=None):
        try:
            if not isinstance(source, SourceSpec) or (source.provider, source.version) != (PROVIDER, SOURCE_VERSION):
                raise ValueError('unsupported Gitea source')
            self.transport._authorize(source, project_sha256=self.selection.project_sha256,
                scope_sha256=self.selection.sha256, authorization=self.authorization)
            if prior is not None: raise ValueError('the Gitea reader does not reconcile absences')
            return self._capture(source)
        except Exception:
            raise TransportError('Gitea capture failed an integrity check; no automatic retry or checkpoint commit') from None

    def _fetch(self, target, state):
        """One GET; denials, rate limits and spent budgets become coverage states without retries."""
        api = self.transport
        if state['halted'] is not None: raise Unavailable(state['halted'])
        spent = (api.max_pages is not None and state['requests'] >= api.max_pages
            or api.max_bytes is not None and state['used'] >= api.max_bytes
            or state['deadline'] is not None and time.monotonic() >= state['deadline'])
        if spent:
            state['halted'] = 'budget_exhausted'
            raise Unavailable('budget_exhausted')
        headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity', 'User-Agent': 'agent-harness-lab-reader'}
        if self.authorization is not None: headers['Authorization'] = self.authorization
        state['requests'] += 1
        try:
            value, metadata, size = _json_exchange(api.origin, api.path+target, headers, timeout=api.timeout,
                deadline=state['deadline'], max_bytes=None if api.max_bytes is None else api.max_bytes-state['used'])
        except HTTPStatusError as error:
            if error.status not in DENIED: raise
            if error.status == 429: state['halted'] = 'rate_limited'
            raise Unavailable(DENIED[error.status]) from None
        state['used'] += size
        metadata = {k.lower(): v for k, v in metadata.items()}
        if metadata.get('content-type', '').split(';')[0].strip() != 'application/json':
            raise ValueError('Gitea JSON content capability is unavailable')
        return value, metadata

    def _following(self, link, collection, page):
        target = next_link(link)
        if target is None: return None
        parsed, path = endpoint(target)
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        if ((parsed.hostname, parsed.port or 443) != self.transport.origin
                or path.casefold() != (self.transport.path+collection).casefold()
                or len(pairs) != 2 or dict(pairs) != dict(page=str(page+1), limit=str(PAGE_SIZE))):
            raise ValueError('Gitea pagination escaped its collection or skipped a page')
        return page+1

    def _list(self, collection, state):
        """Forward page/limit listing; returns rows and an explicit coverage state with its basis."""
        rows, seen, total, page = [], set(), None, 1
        while True:
            try:
                value, headers = self._fetch(f'{collection}?page={page}&limit={PAGE_SIZE}', state)
            except Unavailable as blocked:
                return rows, (blocked.state, None) if page == 1 else ('pagination_incomplete', blocked.state)
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise ValueError('malformed Gitea listing')
            count = headers.get('x-total-count')
            if count is not None:
                if not isinstance(count, str) or re.fullmatch(r'0|[1-9][0-9]{0,18}', count) is None:
                    raise ValueError('invalid Gitea total count')
                if total is not None and int(count) != total: return rows, ('pagination_incomplete', 'total_count_changed')
                total = int(count)
            for item in value:
                native = integer(item.get('id'), minimum=1)
                if native in seen: raise ValueError('duplicate Gitea listing identity')
                seen.add(native)
            rows.extend(value)
            following = self._following(headers.get('link'), collection, page)
            if following is None: break
            if len(value) != PAGE_SIZE: raise ValueError('Gitea page size changed before the last page')
            page = following
        # A server that ignores page/limit may return one longer first page; a full last page needs a total.
        if len(value) > PAGE_SIZE and page != 1: raise ValueError('Gitea page exceeds the requested limit')
        if total is not None and len(rows) != total: return rows, ('pagination_incomplete', 'total_count_mismatch')
        if total is None and len(value) >= PAGE_SIZE: return rows, ('pagination_incomplete', 'page_size_unverified')
        return rows, ('captured', None)

    def _version(self, state):
        try:
            value, _ = self._fetch(API_PREFIX+'/version', state)
        except Unavailable as blocked:
            return None, blocked.state
        version = value.get('version') if isinstance(value, dict) else None
        if not isinstance(version, str) or re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z.+_~-]{0,63}', version) is None:
            return None, 'unrecognized'
        return version, 'reported'

    def _native(self, value):
        return digest((self.selection.instance_sha256, integer(value, minimum=1)))

    def _actor(self, value):
        if value is None: return None
        if not isinstance(value, dict): raise ValueError('invalid Gitea user')
        return digest(('gitea.user', self.selection.instance_sha256, integer(value.get('id'), minimum=1)))

    def _parent(self, url, number):
        """Native parent links must name this repository's issue/PR; they are checked, never followed."""
        if url in (None, ''): return
        parsed, path = endpoint(url)
        root = self.transport.path
        names = {f'{root}/{self.selection.full_name}/{kind}/{number}'.casefold() for kind in ('issues', 'pulls')}
        names.add(f'{root}{self.repo_path}/issues/{number}'.casefold())
        if (parsed.hostname, parsed.port or 443) != self.transport.origin or parsed.query or path.casefold() not in names:
            raise ValueError('Gitea native resource belongs to another repository or parent')

    def _repository(self, value):
        if (not isinstance(value, dict) or self.selection.repository_sha256(value.get('full_name')) != self.selection.project_sha256
                or type(value.get('private')) is not bool or type(value.get('fork')) is not bool
                or value['private'] and self.authorization is None):
            raise ValueError('Gitea repository identity or read access mismatch')
        return dict(repository_sha256=self.selection.project_sha256, id_sha256=self._native(value.get('id')),
            private=value['private'], fork=value['fork'], default_branch_sha256=text_hash(value.get('default_branch')))

    def _issue(self, value, number):
        if not isinstance(value, dict) or integer(value.get('number'), minimum=1) != number or value.get('state') not in ('open', 'closed'):
            raise ValueError('Gitea issue mismatch')
        owner = value.get('repository')
        if not isinstance(owner, dict) or self.selection.repository_sha256(owner.get('full_name')) != self.selection.project_sha256:
            raise ValueError('Gitea issue belongs to another repository')
        labels, milestone, pull = value.get('labels') or [], value.get('milestone'), value.get('pull_request')
        if not isinstance(labels, list) or pull is not None and not isinstance(pull, dict) or milestone is not None and not isinstance(milestone, dict):
            raise ValueError('invalid Gitea issue relations')
        return dict(id_sha256=self._native(value.get('id')), number_sha256=digest(number), state=value['state'],
            title_sha256=text_hash(value.get('title')), body_sha256=text_hash(value.get('body')),
            comments=integer(value.get('comments')), created_at=moment(value.get('created_at')),
            updated_at=moment(value.get('updated_at')), closed_at=moment(value.get('closed_at')),
            actor_sha256=self._actor(value.get('user')), is_pull=pull is not None,
            labels_sha256=sorted(digest((self._native(v.get('id')), text_hash(v.get('name')))) for v in labels),
            milestone_sha256=self._native(milestone.get('id')) if milestone else None)

    def _branch(self, value):
        """A PR side keeps its own repository identity, so fork heads never alias the base repository."""
        if not isinstance(value, dict): raise ValueError('invalid Gitea PR branch')
        owner, sha = value.get('repo'), commit_hash(value.get('sha'))
        if sha is None: raise ValueError('Gitea PR revision missing')
        if owner is not None and (not isinstance(owner, dict) or value.get('repo_id', owner.get('id')) != owner.get('id')):
            raise ValueError('Gitea PR branch repository mismatch')
        repository = self.selection.repository_sha256(owner.get('full_name')) if owner else None
        return dict(sha256=sha, ref_sha256=text_hash(value.get('ref'), required=True), repository_sha256=repository,
            repository_id_sha256=self._native(owner.get('id')) if owner else None)

    def _pull(self, value, number):
        if (not isinstance(value, dict) or integer(value.get('number'), minimum=1) != number
                or value.get('state') not in ('open', 'closed') or type(value.get('merged')) is not bool):
            raise ValueError('Gitea PR scope/state mismatch')
        base, head = self._branch(value.get('base')), self._branch(value.get('head'))
        if base['repository_sha256'] != self.selection.project_sha256: raise ValueError('Gitea PR base belongs to another repository')
        merge, draft = commit_hash(value.get('merge_commit_sha') or None), value.get('draft')
        side = ('unknown' if head['repository_sha256'] is None else
            'same_repository' if head['repository_sha256'] == base['repository_sha256'] else 'other_repository')
        return dict(id_sha256=self._native(value.get('id')), number_sha256=digest(number), state=value['state'],
            merged=value['merged'], draft=draft if type(draft) is bool else 'unknown',
            base=base, head=head, head_repository_state=side, merge_sha256=merge,
            merge_reference_kind='unknown' if merge is None else 'merged_result' if value['merged'] else 'unmerged_reference',
            merge_base_sha256=commit_hash(value.get('merge_base') or None),
            title_sha256=text_hash(value.get('title')), body_sha256=text_hash(value.get('body')),
            created_at=moment(value.get('created_at')), updated_at=moment(value.get('updated_at')),
            closed_at=moment(value.get('closed_at')), merged_at=moment(value.get('merged_at')),
            merged_by_sha256=self._actor(value.get('merged_by')), actor_sha256=self._actor(value.get('user')))

    def _comment(self, value, number, parent):
        self._parent(value.get('issue_url'), number); self._parent(value.get('pull_request_url'), number)
        return dict(parent_sha256=parent, body_sha256=text_hash(value.get('body')), created_at=moment(value.get('created_at')),
            updated_at=moment(value.get('updated_at')), actor_sha256=self._actor(value.get('user')))

    def _review(self, value, number, parent, head):
        self._parent(value.get('pull_request_url'), number)
        status, commit = value.get('state'), commit_hash(value.get('commit_id') or None)
        flags = {name: value[name] if type(value.get(name)) is bool else 'unknown' for name in ('dismissed', 'official', 'stale')}
        return dict(parent_sha256=parent, state=status if status in REVIEW_STATES else 'unknown', **flags,
            commit_sha256=commit, matches_head=None if commit is None else commit == head,
            body_sha256=text_hash(value.get('body')), submitted_at=moment(value.get('submitted_at')),
            actor_sha256=self._actor(value.get('user')))

    def _capture(self, source):
        selection, api = self.selection, self.transport
        state = dict(requests=0, used=0, halted=None,
            deadline=time.monotonic()+api.seconds if api.seconds is not None else None)
        records, nodes, relations, coverage = {}, {}, set(), []
        collected = datetime.now(timezone.utc).isoformat()

        def key(kind, native): return digest((kind, selection.project_sha256, native))

        def emit(kind, native, node, original=None):
            identity = key(kind, native)
            value = dict(kind=kind, native_id_sha256=identity, **node)
            revision = digest(value)
            if identity in records and records[identity].revision_sha256 != revision: raise ValueError('conflicting Gitea native revision')
            records[identity] = EvidenceRecord(source.source_key, identity, revision, revision,
                'contextual_assertion' if kind in CONTEXTUAL else 'development_artifact', kind, collected,
                original_at=original, provenance='native_field', capture_completeness='partial')
            nodes[identity] = value
            return identity

        def note(resource, outcome, parent=None):
            coverage.append(dict(resource=resource, parent_sha256=parent, state=outcome[0], basis=outcome[1]))

        def read(resource, target, parse, parent=None):
            try:
                value = parse(self._fetch(target, state)[0])
            except Unavailable as blocked:
                note(resource, (blocked.state, None), parent); return None
            note(resource, ('captured', None), parent)
            return value

        version, version_state = self._version(state)
        emit('gitea_instance', 'instance', dict(instance_sha256=selection.instance_sha256,
            server_version=version or 'unknown', server_version_state=version_state))
        repository = read('repository', self.repo_path, self._repository)
        issues, pulls, unrecognized = {}, {}, False
        if repository is not None:
            emit('gitea_repository', 'repository', repository)
            for number in sorted(selection.issues+selection.pulls):
                parent = key('gitea_issue', number)
                node = read('issue', f'{self.repo_path}/issues/{number}', lambda raw, n=number: self._issue(raw, n), parent)
                if node is None: continue
                if number in selection.pulls and not node['is_pull']: raise ValueError('selected Gitea PR is a plain issue')
                issues[number] = node; emit('gitea_issue', number, node, node['created_at'])
                if 'comments' not in selection.capture: continue
                rows, outcome = self._list(f'{self.repo_path}/issues/{number}/comments', state)
                if outcome[0] == 'captured' and len(rows) != node['comments']: outcome = ('pagination_incomplete', 'declared_count_mismatch')
                note('comments', outcome, parent)
                for row in rows:
                    child = emit('gitea_comment', row['id'], self._comment(row, number, parent), moment(row.get('created_at')))
                    relations.add(('conversation_on', child, parent))
            for number in selection.pulls:
                if number not in issues: continue
                parent = key('gitea_pull', number)
                node = read('pull', f'{self.repo_path}/pulls/{number}', lambda raw, n=number: self._pull(raw, n), parent)
                if node is None: continue
                pulls[number] = node; emit('gitea_pull', number, node, node['created_at'])
                relations.add(('pull_issue', parent, key('gitea_issue', number)))
                if 'reviews' not in selection.capture: continue
                rows, outcome = self._list(f'{self.repo_path}/pulls/{number}/reviews', state)
                if outcome == ('not_found', None): outcome = ('unsupported_endpoint', 'review_listing_not_found')
                note('reviews', outcome, parent)
                for row in rows:
                    review = self._review(row, number, parent, node['head']['sha256'])
                    unrecognized |= review['state'] == 'unknown'
                    child = emit('gitea_review', row['id'], review, review['submitted_at'])
                    relations.add(('review_on', child, parent))
            # Readbacks narrow, but cannot remove, the window for a change between non-atomic reads.
            for number, before in issues.items():
                after = read('readback', f'{self.repo_path}/issues/{number}', lambda raw, n=number: self._issue(raw, n), key('gitea_issue', number))
                if after not in (None, before): raise ValueError('Gitea issue changed during capture')
            for number, before in pulls.items():
                after = read('readback', f'{self.repo_path}/pulls/{number}', lambda raw, n=number: self._pull(raw, n), key('gitea_pull', number))
                if after not in (None, before): raise ValueError('Gitea PR changed during capture')
            if read('readback', self.repo_path, self._repository) not in (None, repository): raise ValueError('Gitea repository changed during capture')
        ready = all(entry['state'] in CHECKPOINTABLE for entry in coverage)
        unsupported = any(entry['state'] == 'unsupported_endpoint' for entry in coverage)
        warnings = ['selected_resources_only', 'closure_merge_approval_not_task_outcome', 'readbacks_non_atomic',
            'inline_review_comments_not_captured', 'execution_binding_unknown']
        if version is None: warnings.append('server_version_unknown')
        if unsupported: warnings.append('review_endpoint_unsupported')
        if unrecognized: warnings.append('review_state_unrecognized')
        if not ready: warnings.append('capture_blocked_or_incomplete')
        summary = dict(schema_version=1, provider=PROVIDER, source_version=SOURCE_VERSION, api_prefix=API_PREFIX,
            server_version=version or 'unknown', server_version_state=version_state, source_identity='instance_repository_name_checked',
            instance_sha256=selection.instance_sha256, repository_sha256=selection.project_sha256, capture=list(selection.capture),
            nodes=[nodes[k] for k in sorted(nodes)], relations=sorted(relations), coverage=coverage,
            capture_completeness='partial', checkpoint_ready=ready, task_outcomes_verified=False, warnings=warnings)
        gaps = ('not_captured',) + (('unsupported_version',) if unsupported else ()) + (() if ready else ('not_observed',))
        manifest = CoverageManifest(source.source_key, source.scope_sha256, source.scope_sha256, SOURCE_VERSION, 'gitea-rest-v1',
            (ScanBound('records', None, len(records)), ScanBound('pages', api.max_pages, state['requests']),
            ScanBound('bytes', api.max_bytes, state['used'])), gaps=gaps, reported_completeness='partial',
            limit_reached=state['halted'] == 'budget_exhausted',
            truncated=any(entry['state'] == 'pagination_incomplete' for entry in coverage))
        self.summary = summary
        return Snapshot(digest(summary), tuple(records[k] for k in sorted(records)), ExecutionMetadata((), manifest),
            checkpoint_ready=ready)


class GiteaConfigured(ConfiguredAPI):
    """Manifest-declared Gitea reader; the credential comes only from a named file or variable."""
    provider, selection_type, reader = PROVIDER, GiteaSelection, GiteaAPI
    environment_authorization = True
