"""Explicit offline falsification of a frozen draft oracle; no bank admission."""
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from agent_harness_lab.monitoring.state import atomic_json
from .bank_tools import materialize_sources, regrade_artifacts
from .integrity import artifacts, contained, digest, read_regular, unique_object
from .regressions import fields, invalid_constant, load, sha256
from .sandbox import Limits
from .task_bank import TaskSpec
from .workspace import names

STATES = ('broken', 'partial', 'complete', 'alternative_valid', 'superficial')


def read_draft(path):
    raw = read_regular(path, 256 * 1024)
    try:
        value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except RecursionError:
        raise ValueError('draft JSON nesting limit exceeded') from None
    fields(value, 'schema_version proposal_sha256 task controls')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('unsupported regression draft version')
    sha256(value['proposal_sha256'])
    task = TaskSpec.parse(value['task'])
    if task.partition not in ('construction', 'development') or task.review_status != 'unreviewed' or task.independence != 'unverified':
        raise ValueError('regression drafts must remain unreviewed development/construction data')
    if task.permission != 'approved' or not names(task.artifacts):
        raise ValueError('draft needs explicit source permission and bounded artifact names')
    controls = value['controls']
    if not isinstance(controls, list) or len(controls) != len(STATES):
        raise ValueError('draft requires five distinct falsification/validity controls')
    for control in controls:
        fields(control, 'state fixture digest')
        if not isinstance(control['state'], str) or control['state'] not in STATES:
            raise ValueError('unsupported draft control')
        sha256(control['digest'])
    if {c['state'] for c in controls} != set(STATES):
        raise ValueError('duplicate or missing draft control')
    return value, task, hashlib.sha256(raw).hexdigest()


def containment(run, expected_image):
    attestation = run.get('attestation') or {}
    image = attestation.get('runtime_image') or {}
    group = attestation.get('cgroup_v2') or {}
    return (attestation.get('backend') == 'bubblewrap-offline-v1' and attestation.get('role') == 'grader'
            and attestation.get('network') == 'none' and attestation.get('aggregate_disk_bound_verified') is True
            and image.get('sha256') == expected_image
            and image.get('verified_before') is True and image.get('verified_after') is True
            and group.get('empty_after_cleanup') is True)


def audit(proposal_path, draft_path, output, backend, *, budget_seconds=60):
    started = time.monotonic()
    if type(budget_seconds) not in (int, float) or not math.isfinite(budget_seconds) or not 0 < budget_seconds <= 300:
        raise ValueError('draft audit budget must be positive and at most 300 seconds')
    deadline = started + budget_seconds
    if backend.cgroup_root is None or backend.image is None or backend.disk_bytes is None:
        raise ValueError('draft controls require aggregate cgroups, a pinned image and private disk quotas')
    proposal_path, draft_path, output = map(Path, (proposal_path, draft_path, output))
    if any('..' in path.parts for path in (proposal_path, draft_path, output)):
        raise ValueError('draft audit paths must be canonical without parent traversal')
    proposal, proposal_hash = load(proposal_path)
    draft, task, draft_hash = read_draft(draft_path)
    if draft['proposal_sha256'] != proposal_hash:
        raise ValueError('draft proposal identity mismatch')
    value = proposal.value
    if value['reproduction']['disposition'] != 'reproduced':
        raise ValueError('draft controls require a complete declared reproduction')
    if (task.root_id, task.family, task.source_kind) != (value['root_id'], value['family'], value['source']['kind']):
        raise ValueError('draft root/family/source must match its proposal')
    if backend.image.sha256 != value['environment']['image_sha256']:
        raise ValueError('draft runtime image must match its declared reproduction identity')
    root = draft_path.absolute().parent
    fixture, bundle = contained(root, task.fixture), contained(root, task.grader_bundle)
    if fixture.is_relative_to(bundle) or bundle.is_relative_to(fixture):
        raise ValueError('draft fixture and hidden grader overlap')
    frozen = {fixture: task.fixture_digest, bundle: task.grader_digest}
    for name in ('grade.py.txt', 'expected.json'):
        read_regular(bundle/name, 1024 * 1024)
    controls = []
    for control in draft['controls']:
        path = contained(root, control['fixture'])
        if path.is_relative_to(fixture) or fixture.is_relative_to(path):
            raise ValueError('control and task fixtures overlap')
        if path in frozen and frozen[path] != control['digest']:
            raise ValueError('conflicting frozen draft identities')
        frozen[path] = control['digest']
        controls.append((control['state'], path))
    if any(output.absolute().is_relative_to(p.absolute()) or p.absolute().is_relative_to(output.absolute())
           for p in (*frozen, proposal_path, draft_path, backend.image.root)):
        raise ValueError('draft output and readonly inputs must not overlap')
    if any(p.is_symlink() for p in (output, *output.absolute().parents)):
        raise ValueError('draft output crosses a symlink')

    def unchanged():
        try:
            return (load(proposal_path)[1] == proposal_hash and read_draft(draft_path)[2] == draft_hash
                    and all(digest(path) == expected for path, expected in frozen.items()))
        except (OSError, ValueError):
            return False

    if not unchanged(): raise ValueError('draft input integrity mismatch')
    report = dict(proposal.receipt(proposal_hash), draft_sha256=draft_hash, status='incomplete',
                  task_fixture_sha256=task.fixture_digest, grader_bundle_sha256=task.grader_digest,
                  draft_review_status='unreviewed', independence='unverified', checks=[],
                  budget_seconds=budget_seconds, execution_mode='offline_grader_controls')
    with tempfile.TemporaryDirectory() as folder:
        staging = Path(folder)
        oracle = staging/'oracle'; shutil.copytree(bundle, oracle)
        if digest(oracle) != task.grader_digest: raise ValueError('draft grader changed during freezing')
        prepared = []
        for index, (state, source) in enumerate(controls):
            workspace = staging/str(index); shutil.copytree(source, workspace)
            if digest(workspace) != frozen[source]: raise ValueError('draft control changed during freezing')
            materialize_sources(workspace)
            artifacts(workspace, task.artifacts)
            prepared.append((state, source, workspace, digest(workspace)))
        if not unchanged(): raise ValueError('draft inputs changed during preflight')
        output.mkdir(mode=0o700, exist_ok=False)
        receipt = output/'review.json'
        atomic_json(receipt, report)
        try:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                report['status'] = 'budget_exhausted'
            else:
                capability = backend.capability(seconds=min(3, remaining))
                if time.monotonic() >= deadline: report['status'] = 'budget_exhausted'
                elif capability.get('status') != 'available': report['status'] = 'blocked'
                else:
                    for state, source, workspace, materialized in prepared:
                        remaining = deadline-time.monotonic()
                        if remaining <= 0:
                            report['status'] = 'budget_exhausted'; break
                        if not unchanged() or digest(oracle) != task.grader_digest or digest(workspace) != materialized:
                            report['status'] = 'invalid_input'; break
                        remaining = deadline-time.monotonic()
                        if remaining <= 0:
                            report['status'] = 'budget_exhausted'; break
                        grade = regrade_artifacts(workspace, task.artifacts, oracle, task.grader_digest,
                                                  backend, limits=Limits(seconds=min(5, remaining)))
                        run = grade['execution']
                        expected = state in ('complete', 'alternative_valid')
                        report['checks'].append(dict(control=state, expected=expected, observed=grade['outcome'],
                            input_sha256=frozen[source], materialized_sha256=materialized,
                            grader_status=grade['status'], attestation=run.get('attestation')))
                        if not unchanged() or digest(workspace) != materialized:
                            report['status'] = 'invalid_input'; break
                        if time.monotonic() >= deadline:
                            report['status'] = 'budget_exhausted'; break
                        if run.get('status') == 'timeout':
                            report['status'] = 'grader_timeout'; break
                        if not containment(run, backend.image.sha256):
                            report['status'] = 'containment_error'; break
                        if grade['outcome'] is None or grade['grader_identity']['grader_version'] != task.grader_version:
                            report['status'] = 'grader_error'; break
                        if grade['outcome'] is not expected:
                            report['status'] = 'controls_failed'; break
                        atomic_json(receipt, report)
                    else: report['status'] = 'grader_controls_passed'
        except TimeoutError:
            report['status'] = 'budget_exhausted'
        except (OSError, ValueError, subprocess.SubprocessError):
            report['status'] = 'grader_error'
        if not unchanged(): report['status'] = 'invalid_input'
        elif time.monotonic() >= deadline: report['status'] = 'budget_exhausted'
        report['elapsed_seconds'] = time.monotonic()-started
        atomic_json(receipt, report)
    return report
