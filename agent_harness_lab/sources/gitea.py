"""Explicit read-only Gitea REST captures; closure, merges and approvals are never task grades."""
from dataclasses import dataclass
import re
from .forge import CaptureLedger
from .github import commit_hash, integer, moment, text_hash
from .remote_config import ConfiguredAPI
from .schema import Contract, SourceSpec, digest
# DEFAULT_LIMITS, MAX_RESPONSE_BYTES and MAX_UNPAGED_ROWS are the shared forge bounds this profile applies.
from .transport import (DEFAULT_LIMITS, MAX_RESPONSE_BYTES, MAX_UNPAGED_ROWS, TransportError, bounded_api,  # noqa: F401
    endpoint, instance_root, next_page)

PROVIDER = 'gitea'
SOURCE_VERSION = 'v1'
API_PREFIX = '/api/v1'
PAGE_SIZE = 50
CAPTURE = ('comments', 'issues', 'pulls', 'reviews')
REVIEW_STATES = ('APPROVED', 'COMMENT', 'PENDING', 'REQUEST_CHANGES', 'REQUEST_REVIEW')
CONTEXTUAL = ('gitea_issue', 'gitea_comment', 'gitea_review')
NAME = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}')


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
        if type(self.schema_version) is not int or self.schema_version not in (1, 2): raise ValueError('unsupported Gitea selection version')
        instance_root(self.base_url, API_PREFIX)
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
    def instance(self): return instance_root(self.base_url, API_PREFIX)

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
    def source_version(self): return f'v{self.schema_version}'

    @property
    def sha256(self): return digest(self.to_dict())


class GiteaAPI:
    """GET-only reader for selected issues/PRs of one explicitly named repository."""

    def __init__(self, base_url, selection, *, authorization=None, **limits):
        if not isinstance(selection, GiteaSelection): raise TransportError('explicit Gitea selection required')
        self.selection = GiteaSelection.parse(selection.to_dict())
        if base_url != self.selection.base_url: raise TransportError('Gitea endpoint must equal the selected instance root')
        self.transport = bounded_api(base_url, limits)
        self.authorization = authorization
        self.repo_path = f'{API_PREFIX}/repos/{self.selection.owner}/{self.selection.repo}'

    def read(self, source, *, prior=None):
        try:
            if not isinstance(source, SourceSpec) or (source.provider, source.version) != (PROVIDER, self.selection.source_version):
                raise ValueError('unsupported Gitea source')
            self.transport._authorize(source, project_sha256=self.selection.project_sha256,
                scope_sha256=self.selection.sha256, authorization=self.authorization)
            if prior is not None: raise ValueError('the Gitea reader does not reconcile absences')
            return self._capture(source)
        except Exception:
            raise TransportError('Gitea capture failed an integrity check; no automatic retry or checkpoint commit') from None

    def _list(self, collection, ledger, project):
        """Forward page/limit listing of projected rows; a full last page needs X-Total-Count to show nothing follows."""
        def following(headers, page):
            return next_page(self.transport, headers.get('link'), collection, page, dict(limit=str(PAGE_SIZE))), False
        return ledger.listing(collection, dict(limit=PAGE_SIZE), PAGE_SIZE, total_header='x-total-count', following=following,
            project=project)

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

    def _issue(self, value, number, repository_id):
        if not isinstance(value, dict) or integer(value.get('number'), minimum=1) != number or value.get('state') not in ('open', 'closed'):
            raise ValueError('Gitea issue mismatch')
        owner = value.get('repository')
        if (not isinstance(owner, dict) or self.selection.repository_sha256(owner.get('full_name')) != self.selection.project_sha256
                or self._native(owner.get('id')) != repository_id):
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

    def _pull(self, value, number, repository_id):
        if (not isinstance(value, dict) or integer(value.get('number'), minimum=1) != number
                or value.get('state') not in ('open', 'closed') or type(value.get('merged')) is not bool):
            raise ValueError('Gitea PR scope/state mismatch')
        base, head = self._branch(value.get('base')), self._branch(value.get('head'))
        if (base['repository_sha256'] != self.selection.project_sha256 or base['repository_id_sha256'] != repository_id
                or head['repository_sha256'] is not None and
                (head['repository_sha256'] == self.selection.project_sha256) != (head['repository_id_sha256'] == repository_id)):
            raise ValueError('Gitea PR repository identity mismatch')
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
        selection = self.selection
        ledger = CaptureLedger(source, self.transport, self.authorization, CONTEXTUAL)
        relations, coverage = ledger.relations, ledger.coverage

        repository = None

        def key(kind, native, node=None):
            if selection.schema_version == 1: return digest((kind, selection.project_sha256, native))
            if kind == 'gitea_instance': return digest((kind, selection.instance_sha256))
            object_id = node['id_sha256'] if kind in ('gitea_issue', 'gitea_pull') else native
            parent = node['parent_sha256'] if kind in ('gitea_comment', 'gitea_review') else None
            return digest(('gitea-v2', kind, selection.project_sha256, repository['id_sha256'], parent, object_id))

        def emit(kind, native, node, original=None): return ledger.emit(key(kind, native, node), kind, node, original)

        version, version_state = ledger.version(API_PREFIX+'/version')
        emit('gitea_instance', 'instance', dict(instance_sha256=selection.instance_sha256,
            server_version=version or 'unknown', server_version_state=version_state))
        repository = ledger.read('repository', self.repo_path, self._repository)
        issues, pulls, unrecognized = {}, {}, False
        if repository is not None:
            emit('gitea_repository', 'repository', repository)
            for number in sorted(selection.issues+selection.pulls):
                parent = key('gitea_issue', number) if selection.schema_version == 1 else None
                node = ledger.read('issue', f'{self.repo_path}/issues/{number}', lambda raw, n=number: self._issue(raw, n, repository['id_sha256']), parent)
                if node is None: continue
                parent = key('gitea_issue', number, node)
                if selection.schema_version == 2: coverage[-1]['parent_sha256'] = parent
                if number in selection.pulls and not node['is_pull']: raise ValueError('selected Gitea PR is a plain issue')
                issues[number] = node; emit('gitea_issue', number, node, node['created_at'])
                if 'comments' not in selection.capture: continue
                rows, outcome = self._list(f'{self.repo_path}/issues/{number}/comments', ledger,
                    lambda row, n=number, p=parent: self._comment(row, n, p))
                if outcome[0] == 'captured' and len(rows) != node['comments']: outcome = ('pagination_incomplete', 'declared_count_mismatch')
                ledger.note('comments', outcome, parent)
                for native, comment in rows:
                    child = emit('gitea_comment', native, comment, comment['created_at'])
                    relations.add(('conversation_on', child, parent))
            for number in selection.pulls:
                if number not in issues: continue
                parent = key('gitea_pull', number) if selection.schema_version == 1 else None
                node = ledger.read('pull', f'{self.repo_path}/pulls/{number}', lambda raw, n=number: self._pull(raw, n, repository['id_sha256']), parent)
                if node is None: continue
                parent = key('gitea_pull', number, node)
                if selection.schema_version == 2: coverage[-1]['parent_sha256'] = parent
                pulls[number] = node; emit('gitea_pull', number, node, node['created_at'])
                relations.add(('pull_issue', parent, key('gitea_issue', number, issues[number])))
                if 'reviews' not in selection.capture: continue
                rows, outcome = self._list(f'{self.repo_path}/pulls/{number}/reviews', ledger,
                    lambda row, n=number, p=parent, head=node['head']['sha256']: self._review(row, n, p, head))
                # Reviews absent from a server (404 on an already-read PR) are a declared capability gap, not a block.
                if outcome == ('not_found', None): outcome = ('unsupported_endpoint', 'review_listing_not_found')
                ledger.note('reviews', outcome, parent)
                for native, review in rows:
                    unrecognized |= review['state'] == 'unknown'
                    child = emit('gitea_review', native, review, review['submitted_at'])
                    relations.add(('review_on', child, parent))
            # Readbacks narrow, but cannot remove, the window for a change between non-atomic reads.
            for number, before in issues.items():
                after = ledger.read('readback', f'{self.repo_path}/issues/{number}', lambda raw, n=number: self._issue(raw, n, repository['id_sha256']), key('gitea_issue', number, before))
                if after not in (None, before): raise ValueError('Gitea issue changed during capture')
            for number, before in pulls.items():
                after = ledger.read('readback', f'{self.repo_path}/pulls/{number}', lambda raw, n=number: self._pull(raw, n, repository['id_sha256']), key('gitea_pull', number, before))
                if after not in (None, before): raise ValueError('Gitea PR changed during capture')
            if ledger.read('readback', self.repo_path, self._repository) not in (None, repository): raise ValueError('Gitea repository changed during capture')
        ready = ledger.ready
        warnings = ['selected_resources_only', 'closure_merge_approval_not_task_outcome', 'readbacks_non_atomic',
            'inline_review_comments_not_captured', 'execution_binding_unknown']
        if version is None: warnings.append('server_version_unknown')
        if ledger.unsupported: warnings.append('review_endpoint_unsupported')
        if unrecognized: warnings.append('review_state_unrecognized')
        if not ready: warnings.append('capture_blocked_or_incomplete')
        summary = dict(schema_version=1, provider=PROVIDER, source_version=selection.source_version, api_prefix=API_PREFIX,
            server_version=version or 'unknown', server_version_state=version_state, source_identity='instance_repository_name_checked',
            instance_sha256=selection.instance_sha256, repository_sha256=selection.project_sha256, capture=list(selection.capture),
            nodes=[ledger.nodes[k] for k in sorted(ledger.nodes)], relations=sorted(relations), coverage=coverage,
            capture_completeness='partial', checkpoint_ready=ready, task_outcomes_verified=False, warnings=warnings)
        self.summary = summary
        return ledger.snapshot(summary, selection.source_version, f'gitea-rest-{selection.source_version}')


class GiteaConfigured(ConfiguredAPI):
    """Manifest-declared Gitea reader; the credential comes only from a named file or variable."""
    provider, selection_type, reader = PROVIDER, GiteaSelection, GiteaAPI
    environment_authorization = True
