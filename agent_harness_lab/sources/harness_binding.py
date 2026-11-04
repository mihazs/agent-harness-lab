"""Owned controller harness input versus an owned local-Git snapshot; never loading or authority."""
import os
from pathlib import PurePosixPath
import time
from weakref import WeakKeyDictionary

from agent_harness_lab.evaluation import controller_receipts as owned
from agent_harness_lab.evaluation.integrity import tree_digest

PROFILE = 'ahl-harness-snapshot-binding-v1'
_ISSUED = WeakKeyDictionary()


class HarnessCapture:
    """Opaque issuance handle; its facts live only in this process's private registry."""
    __slots__ = ('__weakref__',)


def issue(source, snapshot, roots, files):
    """Called by the built-in Git reader after its source readback; facts stay process-local."""
    handle = HarnessCapture()
    _ISSUED[handle] = dict(pid=os.getpid(), captured_ns=time.monotonic_ns(), source_sha256=source.source_key,
        project_sha256=source.project_sha256, snapshot_sha256=snapshot.sha256, head_sha256=snapshot.head_sha256,
        roots=dict(roots), files=tuple(files))
    return handle


def _receipt(status, reason, **values):
    result = dict(schema_version=1, profile=PROFILE, status=status, reason=reason,
        controller_profile=owned.PROFILE, controller_attempt_sha256=None, controller_harness_input_sha256=None,
        harness_snapshot_sha256=None, head_sha256=None, git_source_sha256=None,
        comparison='controller_input_tree_equals_clean_selected_snapshot_files',
        revision_scope='selected_paths_at_captured_head', effective_loading='unverified',
        native_identity='unverified', task_outcomes_verified=False, authority_granted=False, publication_allowed=False)
    if status != 'unknown': result.update(values)
    return result


def _compare(captured, observed_sha256, location_sha256, started_ns):
    """Pure comparison of already-owned facts; unknown is coverage, never a failure label."""
    if observed_sha256 is None: return 'unknown', 'controller_harness_input_unobserved'
    if captured['captured_ns'] >= started_ns: return 'unknown', 'snapshot_not_captured_before_execution'
    root = captured['roots'].get(location_sha256) if location_sha256 is not None else None
    if root is None: return 'unknown', 'harness_location_not_in_captured_checkout'
    base = PurePosixPath(root)
    tree = {}
    for path, content, mode, committed, committed_mode in captured['files']:
        name = PurePosixPath(path)
        inside = root == '.' or base in name.parents
        if not inside:
            if content is not None or committed is not None: return 'unknown', 'snapshot_exceeds_harness_root'
            continue
        if content is None and committed is None: continue
        if committed_mode == 0o120000: return 'unknown', 'unsupported_harness_file'
        if (content != committed or committed_mode not in (0o100644, 0o100755)
                or bool(mode & 0o100) != (committed_mode == 0o100755)):
            return 'unknown', 'snapshot_not_clean'
        tree[str(name if root == '.' else name.relative_to(base))] = dict(sha256=content, mode=mode)
    return ('verified', None) if tree_digest(dict(sorted(tree.items()))) == observed_sha256 else ('mismatch', 'input_tree_differs')


def bind(attempt, harness_capture):
    """Bind one closed live controller attempt to one Git capture issued earlier in this process."""
    captured = _ISSUED.get(harness_capture) if type(harness_capture) is HarnessCapture else None
    if captured is None or captured['pid'] != os.getpid(): return _receipt('unknown', 'owned_harness_capture_required')
    if type(attempt) is not owned._Attempt: return _receipt('unknown', 'owned_controller_capture_required')
    try: data = attempt.read()
    except ValueError: return _receipt('unknown', 'owned_controller_capture_required')
    if captured['project_sha256'] != attempt.source.project_sha256: return _receipt('unknown', 'declared_project_differs')
    executor = data['kernels'][data['grade']['executor_ordinal']]
    observed = executor['input_digests'].get('harness')
    status, reason = _compare(captured, observed, executor.get('harness_location_sha256'), attempt.started_ns)
    return _receipt(status, reason, controller_attempt_sha256=data['attempt_sha256'], controller_harness_input_sha256=observed,
        harness_snapshot_sha256=captured['snapshot_sha256'], head_sha256=captured['head_sha256'],
        git_source_sha256=captured['source_sha256'])
