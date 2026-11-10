"""Explicit read-only GitLab REST v4 captures; merges, closures and approvals are never task grades."""
from dataclasses import dataclass
import re
from .forge import CaptureLedger, count_header
from .github import commit_hash, integer, moment, text_hash
from .remote_config import ConfiguredAPI
from .schema import Contract, SourceSpec, digest
from .transport import MAX_UNPAGED_ROWS, TransportError, Unavailable, bounded_api, instance_root, next_page

PROVIDER = 'gitlab'
SOURCE_VERSION = 'v4'
API_PREFIX = '/api/v4'
PAGE_SIZE = 100
MAX_MR_COMMITS = 10000
MAX_MR_CLOSING_ISSUES = 10000
CAPTURE = ('approvals', 'closing_issues', 'commits', 'discussions', 'issues', 'merge_requests', 'notes')
ISSUE_STATES = ('opened', 'closed')
MERGE_STATES = ('opened', 'closed', 'locked', 'merged')
VISIBILITY = ('private', 'internal', 'public')
# Ascending creation order keeps offset pages stable while new notes are appended.
NOTE_QUERY = dict(order_by='created_at', sort='asc', per_page=PAGE_SIZE)
CONTEXTUAL = ('gitlab_issue', 'gitlab_note', 'gitlab_approvals', 'gitlab_discussion', 'gitlab_mr_closing_issue')
# One namespace or project path segment: no leading separator, dot segments, encodings or trailing dot.
SEGMENT = re.compile(r'[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,253}[A-Za-z0-9_-])?')
MAX_DEPTH = 21


def project_path(value):
    """A literal ``group/subgroup/project`` path; it becomes one URL-encoded ``:id`` segment."""
    if not isinstance(value, str) or len(value) > 1024: raise ValueError('invalid GitLab project path')
    parts = value.split('/')
    if (not 2 <= len(parts) <= MAX_DEPTH or any(SEGMENT.fullmatch(part) is None or '..' in part
            or part.casefold().endswith(('.git', '.atom')) for part in parts)):
        raise ValueError('invalid GitLab project path')
    return value


@dataclass(frozen=True)
class GitLabSelection(Contract):
    base_url: str
    project: object
    capture: tuple
    issues: tuple = ()
    merge_requests: tuple = ()
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version not in (1, 2, 3, 4, 5): raise ValueError('unsupported GitLab selection version')
        instance_root(self.base_url, API_PREFIX)
        if type(self.project) is int: integer(self.project, minimum=1)
        else: project_path(self.project)
        if (type(self.capture) is not tuple or tuple(sorted(set(self.capture))) != self.capture
                or any(kind not in CAPTURE for kind in self.capture)):
            raise ValueError('GitLab capture kinds must be unique, ascending and known')
        for numbers in (self.issues, self.merge_requests):
            if type(numbers) is not tuple: raise ValueError('invalid GitLab selection')
            for number in numbers: integer(number, minimum=1)
            if tuple(sorted(set(numbers))) != numbers: raise ValueError('GitLab IIDs must be unique and ascending')
        # Issues and merge requests have separate IID sequences, so one IID may name both.
        if (('issues' in self.capture) != bool(self.issues) or ('merge_requests' in self.capture) != bool(self.merge_requests)
                or 'approvals' in self.capture and not self.merge_requests
                or 'commits' in self.capture and (self.schema_version not in (4, 5) or not self.merge_requests)
                or 'closing_issues' in self.capture and (self.schema_version != 5 or not self.merge_requests)
                or 'discussions' in self.capture and (self.schema_version == 1 or self.schema_version == 2 and not self.merge_requests)
                or not (self.issues or self.merge_requests)):
            raise ValueError('GitLab capture kinds must match the explicitly selected IIDs')

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__): raise ValueError('invalid GitLab selection fields')
        if not all(isinstance(value[k], (list, tuple)) for k in ('capture', 'issues', 'merge_requests')):
            raise ValueError('invalid GitLab collections')
        return cls(**{**value, **{k: tuple(value[k]) for k in ('capture', 'issues', 'merge_requests')}})

    @property
    def instance(self): return instance_root(self.base_url, API_PREFIX)

    @property
    def instance_sha256(self): return digest(('gitlab.instance', *self.instance))

    @property
    def selector(self):
        """The URL ``:id`` for the first project read: a numeric ID or the encoded path."""
        return str(self.project) if type(self.project) is int else self.project.replace('/', '%2F')

    @property
    def project_sha256(self):
        """The declared scope: instance plus numeric ID or case-insensitive path, known before any read."""
        kind = 'id' if type(self.project) is int else 'path'
        return digest(('gitlab.project_selector', *self.instance, kind,
            self.project if kind == 'id' else self.project.casefold()))

    def project_identity(self, project_id):
        """Records key on the numeric project ID the server reports, never on a path that can be renamed."""
        return digest(('gitlab.project', *self.instance, integer(project_id, minimum=1)))

    @property
    def source_version(self): return SOURCE_VERSION

    @property
    def sha256(self): return digest(self.to_dict())



def absent_or(value, expected):
    """An optional native integer must be missing or exactly the expected one; a bool is never an integer."""
    return value is None or type(value) is int and value == expected


class GitLabAPI:
    """GET-only reader for selected issues and merge requests of one explicitly named project."""

    def __init__(self, base_url, selection, *, authorization=None, **limits):
        if not isinstance(selection, GitLabSelection): raise TransportError('explicit GitLab selection required')
        self.selection = GitLabSelection.parse(selection.to_dict())
        if base_url != self.selection.base_url: raise TransportError('GitLab endpoint must equal the selected instance root')
        self.transport = bounded_api(base_url, limits)
        self.authorization = authorization
        self.project_id = None

    def read(self, source, *, prior=None):
        try:
            if not isinstance(source, SourceSpec) or (source.provider, source.version) != (PROVIDER, SOURCE_VERSION):
                raise ValueError('unsupported GitLab source')
            self.transport._authorize(source, project_sha256=self.selection.project_sha256,
                scope_sha256=self.selection.sha256, authorization=self.authorization)
            if prior is not None: raise ValueError('the GitLab reader does not reconcile absences')
            return self._capture(source)
        except Exception:
            raise TransportError('GitLab capture failed an integrity check; no automatic retry or checkpoint commit') from None

    @property
    def project_root(self): return f'{API_PREFIX}/projects/{self.project_id}'

    def _list(self, collection, ledger, echoed, project, *, query=NOTE_QUERY, identity=None):
        """Offset page/per_page listing of projected rows; X-Next-Page and a Link rel="next" must agree when both are sent."""
        def following(headers, page):
            linked = next_page(self.transport, headers.get('link'), collection, page,
                {k: str(v) for k, v in query.items()}, echoed=echoed)
            if headers.get('x-page') not in (None, str(page)) or headers.get('x-per-page') not in (None, str(PAGE_SIZE)):
                raise ValueError('GitLab page headers disagree with the request')
            declared = headers.get('x-next-page')
            if declared is None: return linked, False
            # An explicit empty X-Next-Page marks the last page even when X-Total is omitted.
            after = None if declared == '' else count_header(declared)
            if after not in (None, page+1) or linked not in (None, after):
                raise ValueError('GitLab pagination skipped a page or disagreed with its Link header')
            return after, after is None
        return ledger.listing(collection, query, PAGE_SIZE, total_header='x-total', following=following, project=project, identity=identity)

    def _actor(self, value):
        if value is None: return None
        if not isinstance(value, dict): raise ValueError('invalid GitLab user')
        return digest(('gitlab.user', self.selection.instance_sha256, integer(value.get('id'), minimum=1)))

    def _native(self, kind, value):
        """A global ID, typed by resource: issues, merge requests, milestones and notes use separate sequences."""
        return digest(('gitlab.global_id', self.selection.instance_sha256, kind, integer(value, minimum=1)))

    def _project(self, value):
        selection = self.selection
        if not isinstance(value, dict) or value.get('visibility') not in VISIBILITY: raise ValueError('GitLab project identity mismatch')
        native, path = integer(value.get('id'), minimum=1), value.get('path_with_namespace')
        project_path(path)
        if (native != selection.project if type(selection.project) is int else path.casefold() != selection.project.casefold()):
            raise ValueError('GitLab project does not match the selection')
        if self.project_id not in (None, native): raise ValueError('GitLab project identity changed during capture')
        if value['visibility'] != 'public' and self.authorization is None: raise ValueError('GitLab project read access mismatch')
        forked = value.get('forked_from_project')
        if forked is not None and not isinstance(forked, dict): raise ValueError('invalid GitLab fork parent')
        self.project_id = native
        return dict(project_sha256=selection.project_identity(native), selector_sha256=selection.project_sha256,
            path_sha256=digest(path.casefold()), visibility=value['visibility'],
            default_branch_sha256=text_hash(value.get('default_branch')),
            forked_from_sha256=selection.project_identity(forked.get('id')) if forked else None)

    def _common(self, value, iid, states, noteable):
        """Fields shared by issues and merge requests; IID and global ID stay separate identities."""
        if (not isinstance(value, dict) or integer(value.get('iid'), minimum=1) != iid
                or integer(value.get('project_id'), minimum=1) != self.project_id or value.get('state') not in states):
            raise ValueError('GitLab issue or merge request scope/state mismatch')
        labels, milestone = value.get('labels') or [], value.get('milestone')
        if not isinstance(labels, list) or milestone is not None and not isinstance(milestone, dict):
            raise ValueError('invalid GitLab relations')
        return dict(id_sha256=self._native(noteable, value.get('id')), iid_sha256=digest(('gitlab.iid', iid)), state=value['state'],
            title_sha256=text_hash(value.get('title')), body_sha256=text_hash(value.get('description')),
            user_notes_count=integer(value.get('user_notes_count')), created_at=moment(value.get('created_at')),
            updated_at=moment(value.get('updated_at')), closed_at=moment(value.get('closed_at')),
            actor_sha256=self._actor(value.get('author')),
            labels_sha256=sorted(text_hash(label, required=True) for label in labels),
            milestone_sha256=self._native('Milestone', milestone.get('id')) if milestone else None)

    def _issue(self, value, iid):
        node = self._common(value, iid, ISSUE_STATES, 'Issue')
        confidential = value.get('confidential')
        return dict(node, confidential=confidential if type(confidential) is bool else 'unknown')

    def _merge_request(self, value, iid):
        node, selection = self._common(value, iid, MERGE_STATES, 'MergeRequest'), self.selection
        # GitLab nulls `source_project_id` when the source fork is deleted; the merge request is still recorded.
        # A response without the key at all is malformed, not a deleted fork.
        if 'source_project_id' not in value: raise ValueError('GitLab merge request omits its source project')
        raw_source = value['source_project_id']
        source = None if raw_source is None else integer(raw_source, minimum=1)
        target = integer(value.get('target_project_id'), minimum=1)
        if target != self.project_id: raise ValueError('GitLab merge request targets another project')
        draft = value.get('draft', value.get('work_in_progress'))
        refs = value.get('diff_refs')
        if refs is not None and not isinstance(refs, dict): raise ValueError('invalid GitLab diff refs')
        # A fork's source project keeps its own identity even when branch names and commits match.
        return dict(node, draft=draft if type(draft) is bool else 'unknown',
            source_project_sha256=None if source is None else selection.project_identity(source),
            target_project_sha256=selection.project_identity(target),
            source_project_state='unknown' if source is None else 'same_project' if source == target else 'other_project',
            source_branch_sha256=text_hash(value.get('source_branch'), required=True),
            target_branch_sha256=text_hash(value.get('target_branch'), required=True),
            head_sha256=commit_hash(value.get('sha') or None),
            base_sha256=commit_hash(refs.get('base_sha') or None) if refs else None,
            merge_commit_sha256=commit_hash(value.get('merge_commit_sha') or None),
            squash_commit_sha256=commit_hash(value.get('squash_commit_sha') or None),
            merged_at=moment(value.get('merged_at')), merged_by_sha256=self._actor(value.get('merged_by')))

    def _note(self, value, noteable, iid, owner, parent):
        """A note must name its parent's type and global ID; system notes are server events, not comments."""
        if (type(value.get('system')) is not bool or value.get('noteable_type') != noteable
                or self._native(noteable, value.get('noteable_id')) != owner['id_sha256']
                or not absent_or(value.get('noteable_iid'), iid) or not absent_or(value.get('project_id'), self.project_id)):
            raise ValueError('GitLab note belongs to another project or parent')
        flags = {name: value[name] if type(value.get(name)) is bool else 'unknown' for name in ('internal', 'resolvable', 'resolved')}
        if flags['internal'] == 'unknown' and type(value.get('confidential')) is bool:
            flags['internal'] = value['confidential']  # older servers name internal notes `confidential`
        return dict(parent_sha256=parent, system=value['system'], **flags, body_sha256=text_hash(value.get('body')),
            created_at=moment(value.get('created_at')), updated_at=moment(value.get('updated_at')),
            actor_sha256=self._actor(value.get('author')))

    def _approvals(self, value, iid, owner, parent):
        if (not isinstance(value, dict) or not absent_or(value.get('iid'), iid)
                or value.get('id') is not None and self._native('MergeRequest', value['id']) != owner['id_sha256']
                or not absent_or(value.get('project_id'), self.project_id)):
            raise ValueError('GitLab approvals belong to another merge request')
        approvers = value.get('approved_by') or []
        if not isinstance(approvers, list) or len(approvers) > MAX_UNPAGED_ROWS or any(not isinstance(a, dict) for a in approvers):
            raise ValueError('invalid GitLab approvers')
        approved = value.get('approved')
        counts = {name: integer(value[name]) if value.get(name) is not None else None for name in ('approvals_required', 'approvals_left')}
        # An approver whose user object is missing or carries no ID is counted as unidentified, never fatal.
        # A user that is not an object still fails the capture.
        actors = [None if a.get('user') is None or isinstance(a['user'], dict) and a['user'].get('id') is None
            else self._actor(a['user']) for a in approvers]
        return dict(parent_sha256=parent, approved=approved if type(approved) is bool else 'unknown', **counts,
            approvers_sha256=sorted(a for a in actors if a is not None), approvers_unidentified=actors.count(None))

    def _discussions(self, ledger, key, iid, owner, parent, budget, seen_notes, *, noteable, collection):
        """Capture one selected issue or MR's bounded discussions and nested note claims."""
        target = f'{self.project_root}/{collection}/{iid}/discussions'
        def identity(row):
            native = row.get('id')
            if not isinstance(native, str) or not native or len(native) > 256:
                raise ValueError('invalid GitLab discussion identity')
            return digest(('gitlab.discussion', self.selection.instance_sha256, native))
        def project(row):
            budget[0] += 1
            if budget[0] > 10000 or type(row.get('individual_note')) is not bool or not isinstance(row.get('notes'), list):
                raise ValueError('invalid or excessive GitLab discussions')
            notes = []
            for value in row['notes']:
                budget[1] += 1
                if budget[1] > 10000 or not isinstance(value, dict): raise ValueError('invalid or excessive GitLab discussion notes')
                native = integer(value.get('id'), minimum=1)
                if native in seen_notes: raise ValueError('GitLab discussion note repeated')
                seen_notes.add(native)
                notes.append((native, self._note(value, noteable, iid, owner, parent)))
            return dict(parent_sha256=parent, individual_note=row['individual_note'], note_count=len(notes)), notes
        iid_field = 'issue_iid' if noteable == 'Issue' else 'merge_request_iid'
        rows, outcome = self._list(target, ledger, dict(id=str(self.project_id), **{iid_field: str(iid)}), project,
            query=dict(per_page=PAGE_SIZE), identity=identity)
        ledger.note('discussions', outcome, parent)
        for native, (thread_node, notes) in rows:
            thread_identity = (iid, native) if self.selection.schema_version == 2 else (noteable, iid, native)
            thread = ledger.emit(key('gitlab_discussion', thread_identity), 'gitlab_discussion', thread_node)
            ledger.relations.add(('discussion_on', thread, parent))
            for note_id, note in notes:
                kind = 'gitlab_system_note' if note['system'] else 'gitlab_note'
                child = ledger.emit(key(kind, note_id), kind, note, note['created_at'])
                ledger.relations.add(('note_in_discussion', child, thread))

    def _commits(self, ledger, key, iid, parent, budget):
        """Capture reported MR membership of full commit SHAs without inferring ancestry."""
        target = f'{self.project_root}/merge_requests/{iid}/commits'
        def identity(row):
            value = commit_hash(row.get('id'))
            if value is None: raise ValueError('GitLab commit identity missing')
            return value
        def project(row):
            budget[0] += 1
            if budget[0] > MAX_MR_COMMITS: raise ValueError('excessive GitLab merge request commits')
            return dict(parent_sha256=parent, commit_sha256=identity(row), committed_at=moment(row.get('committed_date')),
                membership='server_reported', ancestry='unknown')
        rows, outcome = self._list(target, ledger, dict(id=str(self.project_id), merge_request_iid=str(iid)), project,
            query=dict(per_page=PAGE_SIZE), identity=identity)
        ledger.note('commits', outcome, parent)
        for native, node in rows:
            child = ledger.emit(key('gitlab_mr_commit', (iid, native)), 'gitlab_mr_commit', node, node['committed_at'])
            ledger.relations.add(('reported_commit_in_mr', child, parent))

    def _closing_issues(self, ledger, key, iid, parent, budget):
        """Capture scoped references to issues the server says may close on merge."""
        target = f'{self.project_root}/merge_requests/{iid}/closes_issues'
        def identity(row):
            project_id = row.get('project_id')
            if project_id is not None: project_id = integer(project_id, minimum=1)
            if project_id == self.project_id: return self._native('Issue', row.get('id'))
            # External trackers may supply string IDs and omit project_id; these rows are never emitted.
            native = row.get('id')
            if type(native) is int: integer(native, minimum=1)
            elif (not isinstance(native, str) or not native or len(native) > 256 or
                    any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in native)):
                raise ValueError('invalid unscoped GitLab issue reference identity')
            return digest(('gitlab.unscoped_closing_issue', self.selection.instance_sha256, project_id, native))
        def project(row):
            budget[0] += 1
            if budget[0] > MAX_MR_CLOSING_ISSUES: raise ValueError('excessive GitLab closing issue references')
            project_id = row.get('project_id')
            if project_id is None: return None
            project_id = integer(project_id, minimum=1)
            if project_id != self.project_id: return None
            confidential = row.get('confidential')
            if confidential is not None and type(confidential) is not bool:
                raise ValueError('invalid GitLab issue confidentiality')
            if confidential is not False and self.authorization is None:
                raise ValueError('GitLab issue visibility requires authorization')
            target_iid = integer(row.get('iid'), minimum=1)
            return dict(parent_sha256=parent, issue_sha256=identity(row), iid_sha256=digest(('gitlab.iid', target_iid)),
                project_sha256=self.selection.project_identity(project_id), relationship='server_reported_may_close_on_merge'), target_iid
        rows, outcome = self._list(target, ledger, dict(id=str(self.project_id), merge_request_iid=str(iid)), project,
            query=dict(per_page=PAGE_SIZE), identity=identity)
        if any(node is None for _, node in rows):
            if outcome[0] == 'captured': outcome = ('pagination_incomplete', 'unscoped_references_omitted')
            else: ledger.note('closing_issues', ('not_observed', 'unscoped_references_omitted'), parent)
        ledger.note('closing_issues', outcome, parent)
        for native, item in rows:
            if item is None: continue
            node, target_iid = item
            selected = key('gitlab_issue', target_iid)
            if selected in ledger.nodes and ledger.nodes[selected]['id_sha256'] != node['issue_sha256']:
                raise ValueError('GitLab selected issue identity conflicts with closing reference')
            child = ledger.emit(key('gitlab_mr_closing_issue', (iid, native)), 'gitlab_mr_closing_issue', node)
            ledger.relations.add(('reported_closing_reference_on_mr', child, parent))
            if selected in ledger.nodes:
                ledger.relations.add(('reported_reference_to_selected_issue', child, selected))

    def _capture(self, source):
        selection = self.selection
        ledger = CaptureLedger(source, self.transport, self.authorization, CONTEXTUAL)
        version, version_state = ledger.version(API_PREFIX+'/version')
        ledger.emit(digest(('gitlab_instance', selection.instance_sha256)), 'gitlab_instance',
            dict(instance_sha256=selection.instance_sha256, server_version=version or 'unknown', server_version_state=version_state))
        project = ledger.read('project', f'{API_PREFIX}/projects/{selection.selector}', self._project)
        reads, system_notes, discussion_budget, discussion_notes, commit_budget, closing_budget = [], 0, [0, 0], set(), [0], [0]
        if project is not None:
            scope = project['project_sha256']

            def key(kind, native): return digest((kind, scope, native))
            ledger.emit(key('gitlab_project', 'project'), 'gitlab_project', project)
            parts = (('gitlab_issue', 'Issue', 'issues', selection.issues, self._issue),
                ('gitlab_merge_request', 'MergeRequest', 'merge_requests', selection.merge_requests, self._merge_request))
            for kind, noteable, collection, iids, parse in parts:
                for iid in iids:
                    parent, target = key(kind, iid), f'{self.project_root}/{collection}/{iid}'
                    node = ledger.read(kind[len('gitlab_'):], target, lambda raw, n=iid, p=parse: p(raw, n), parent)
                    if node is None:
                        if 'discussions' in selection.capture and (kind == 'gitlab_merge_request' or selection.schema_version in (3, 4, 5)):
                            ledger.note('discussions', ('not_observed', kind[len('gitlab_'):]+'_unavailable'), parent)
                        if kind == 'gitlab_merge_request' and 'commits' in selection.capture:
                            ledger.note('commits', ('not_observed', 'merge_request_unavailable'), parent)
                        if kind == 'gitlab_merge_request' and 'closing_issues' in selection.capture:
                            ledger.note('closing_issues', ('not_observed', 'merge_request_unavailable'), parent)
                        continue
                    reads.append((kind, target, parse, iid, node)); ledger.emit(parent, kind, node, node['created_at'])
                    if 'notes' in selection.capture:
                        rows, outcome = self._list(f'{target}/notes', ledger, dict(id=str(self.project_id), noteable_id=str(iid)),
                            lambda row, t=noteable, n=iid, o=node, p=parent: self._note(row, t, n, o, p))
                        users = [note for _, note in rows if note['system'] is False]
                        if outcome[0] == 'captured' and len(users) != node['user_notes_count']:
                            outcome = ('pagination_incomplete', 'declared_count_mismatch')
                        ledger.note('notes', outcome, parent)
                        for native, note in rows:
                            system_notes += note['system']
                            child = ledger.emit(key('gitlab_system_note' if note['system'] else 'gitlab_note', native),
                                'gitlab_system_note' if note['system'] else 'gitlab_note', note, note['created_at'])
                            ledger.relations.add(('system_event_on' if note['system'] else 'conversation_on', child, parent))
                    if kind == 'gitlab_merge_request' and 'approvals' in selection.capture:
                        self._approval(ledger, key, iid, node, parent)
                    if 'discussions' in selection.capture and (kind == 'gitlab_merge_request' or selection.schema_version in (3, 4, 5)):
                        self._discussions(ledger, key, iid, node, parent, discussion_budget, discussion_notes,
                            noteable=noteable, collection=collection)
                    if kind == 'gitlab_merge_request' and 'commits' in selection.capture:
                        self._commits(ledger, key, iid, parent, commit_budget)
                    if kind == 'gitlab_merge_request' and 'closing_issues' in selection.capture:
                        self._closing_issues(ledger, key, iid, parent, closing_budget)
            # Readbacks narrow, but cannot remove, the window for a change between non-atomic reads.
            for kind, target, parse, iid, before in reads:
                after = ledger.read('readback', target, lambda raw, n=iid, p=parse: p(raw, n), key(kind, iid))
                if after not in (None, before): raise ValueError('GitLab issue or merge request changed during capture')
            if ledger.read('readback', self.project_root, self._project) not in (None, project):
                raise ValueError('GitLab project changed during capture')
        else:
            if 'discussions' in selection.capture:
                selected = (('merge_request', selection.merge_requests),)
                if selection.schema_version in (3, 4, 5): selected += (('issue', selection.issues),)
                for kind, iids in selected:
                    for iid in iids:
                        parent = digest(('gitlab.selection', selection.project_sha256, kind, iid))
                        ledger.note('discussions', ('not_observed', 'project_unavailable'), parent)
            if 'commits' in selection.capture:
                for iid in selection.merge_requests:
                    parent = digest(('gitlab.selection', selection.project_sha256, 'merge_request', iid))
                    ledger.note('commits', ('not_observed', 'project_unavailable'), parent)
            if 'closing_issues' in selection.capture:
                for iid in selection.merge_requests:
                    parent = digest(('gitlab.selection', selection.project_sha256, 'merge_request', iid))
                    ledger.note('closing_issues', ('not_observed', 'project_unavailable'), parent)
        ready = ledger.ready
        if selection.schema_version in (2, 3, 4, 5):
            system_notes = sum(node['kind'] == 'gitlab_system_note' for node in ledger.nodes.values())
        warnings = ['selected_resources_only', 'merge_closure_approval_not_task_outcome', 'readbacks_non_atomic',
            'system_note_coverage_unverified', 'merge_and_squash_shas_not_ancestry',
            'execution_binding_unknown']
        if 'discussions' not in selection.capture: warnings.insert(3, 'discussions_not_captured')
        if 'commits' in selection.capture: warnings.append('commit_membership_server_reported')
        if 'closing_issues' in selection.capture: warnings.append('closing_issue_visibility_unverified')
        if version is None: warnings.append('server_version_unknown')
        if ledger.unsupported: warnings.append('approvals_endpoint_unsupported')
        if not ready: warnings.append('capture_blocked_or_incomplete')
        summary = dict(schema_version=1, provider=PROVIDER, source_version=SOURCE_VERSION, api_prefix=API_PREFIX,
            server_version=version or 'unknown', server_version_state=version_state, source_identity='instance_project_id_checked',
            instance_sha256=selection.instance_sha256, project_selector_sha256=selection.project_sha256,
            project_sha256=project['project_sha256'] if project else None, capture=list(selection.capture),
            nodes=[ledger.nodes[k] for k in sorted(ledger.nodes)], relations=sorted(ledger.relations),
            coverage=ledger.coverage, system_notes=system_notes, capture_completeness='partial', checkpoint_ready=ready,
            task_outcomes_verified=False, warnings=warnings)
        self.summary = summary
        return ledger.snapshot(summary, SOURCE_VERSION, 'gitlab-rest-v4')

    def _approval(self, ledger, key, iid, owner, parent):
        """Approval state of one merge request; an endpoint the server lacks is a declared gap."""
        target = f'{self.project_root}/merge_requests/{iid}/approvals'
        try:
            node = self._approvals(ledger.fetch(target)[0], iid, owner, parent)
        except Unavailable as blocked:
            ledger.note('approvals', ('unsupported_endpoint', 'approvals_not_found') if blocked.state == 'not_found'
                else (blocked.state, None), parent)
            return
        ledger.note('approvals', ('captured', None), parent)
        child = ledger.emit(key('gitlab_approvals', iid), 'gitlab_approvals', node)
        ledger.relations.add(('approval_state_on', child, parent))


class GitLabConfigured(ConfiguredAPI):
    """Manifest-declared GitLab reader; the credential comes only from a named file or variable."""
    provider, selection_type, reader = PROVIDER, GitLabSelection, GitLabAPI
    environment_authorization = True
