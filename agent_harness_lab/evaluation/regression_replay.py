"""Descriptive offline A/A and A/B replay of explicit frozen snapshot views."""
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from agent_harness_lab.monitoring.state import atomic_json
from .bank_tools import materialize_sources
from .integrity import artifacts, contained, digest, read_regular, unique_object
from .regression_controls import audit, containment, read_draft
from .regressions import fields, invalid_constant, load, references, sha256

COMPONENTS = ('harness', 'config', 'memory')


def read_spec(path):
    raw = read_regular(path, 256 * 1024)
    try:
        value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except RecursionError:
        raise ValueError('replay JSON nesting limit exceeded') from None
    fields(value, 'schema_version proposal_sha256 draft_sha256 runtime_ref snapshot_format before after task_start offline_executor earlier_valid')
    if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['snapshot_format'] != 'ahl-tree-v1':
        raise ValueError('unsupported replay schema/snapshot format')
    sha256(value['proposal_sha256']); sha256(value['draft_sha256'])
    command = value['offline_executor']
    if (not isinstance(command, list) or not 0 < len(command) <= 128
            or any(not isinstance(a, str) or not a or '\0' in a or len(a) > 4096 for a in command)
            or len(json.dumps(command).encode()) > 32768):
        raise ValueError('replay requires an explicit bounded offline executor argv')
    for role in ('before', 'after'): fields(value[role], 'harness config memory')
    earlier = value['earlier_valid']
    if earlier is not None:
        fields(earlier, 'paths identities evidence_refs')
        fields(earlier['paths'], 'harness config memory')
        fields(earlier['identities'], 'harness_sha256 config_sha256 memory_sha256')
        for identity in earlier['identities'].values(): sha256(identity)
        references(earlier['evidence_refs'])
        if not earlier['evidence_refs']: raise ValueError('earlier-valid snapshots require explicit evidence references')
    return value, hashlib.sha256(raw).hexdigest()


def outcome_rows(results, task, expected_image):
    if not isinstance(results, dict): return None, 'incomplete'
    if results.get('status') == 'blocked': return None, 'unsupported'
    if results.get('status') == 'budget_exhausted': return None, 'budget_exhausted'
    if results.get('inputs_unchanged') is not True: return None, 'invalid_input'
    trials = results.get('trials', [])
    expected = {(name, version) for name in ('recorded', 'minimized') for version in ('baseline', 'candidate')}
    if (not isinstance(trials, list) or len(trials) != 4
            or any(not isinstance(r, dict) or not isinstance(r.get('task'), str) or not isinstance(r.get('version'), str) for r in trials)
            or {(r['task'], r['version']) for r in trials} != expected):
        return None, 'incomplete'
    rows = {}
    for record in trials:
        if record.get('status') == 'budget_exhausted': return None, 'budget_exhausted'
        if record.get('status') == 'invalid_input': return None, 'invalid_input'
        grade = record.get('grader', {})
        if not isinstance(grade, dict) or record.get('status') not in ('passed', 'failed') or type(grade.get('outcome')) is not bool:
            return None, 'environment_or_grader_defect'
        expected_status = 'passed' if grade['outcome'] else 'failed'
        if record['status'] != expected_status or grade.get('status') != expected_status:
            return None, 'environment_or_grader_defect'
        if not containment(record.get('execution', {}), expected_image, 'executor') or not containment(grade.get('execution', {}), expected_image):
            return None, 'containment_error'
        for run, code, status in ((record['execution'], 0, 'completed'),
                                  (grade['execution'], 0 if grade['outcome'] else 1, 'completed' if grade['outcome'] else 'failed')):
            if (type(run.get('exit_code')) is not int or run['exit_code'] != code
                    or run.get('status') != status or run.get('inputs_unchanged') is not True):
                return None, 'environment_or_grader_defect'
        identity = grade.get('grader_identity')
        if not isinstance(identity, dict) or identity.get('grader_version') != task.grader_version:
            return None, 'environment_or_grader_defect'
        rows[(record['task'], record['version'])] = grade['outcome']
    return rows, None


def replay(proposal_path, draft_path, spec_path, output, backend, *, budget_seconds=60):
    started = time.monotonic()
    if type(budget_seconds) not in (int, float) or not math.isfinite(budget_seconds) or not 0 < budget_seconds <= 300:
        raise ValueError('replay budget must be positive and at most 300 seconds')
    deadline = started+budget_seconds
    proposal_path, draft_path, spec_path, output = (Path(p).absolute() for p in (proposal_path, draft_path, spec_path, output))
    if any('..' in p.parts for p in (proposal_path, draft_path, spec_path, output)):
        raise ValueError('replay paths must be canonical without parent traversal')
    if backend.cgroup_root is None or backend.image is None or backend.disk_bytes is None:
        raise ValueError('replay requires cgroups, a pinned image and private disk quotas')
    proposal, proposal_hash = load(proposal_path)
    draft, task, draft_hash = read_draft(draft_path)
    spec, spec_hash = read_spec(spec_path)
    value = proposal.value
    if spec['proposal_sha256'] != proposal_hash or spec['draft_sha256'] != draft_hash or draft['proposal_sha256'] != proposal_hash:
        raise ValueError('replay proposal/draft identities mismatch')
    if spec['runtime_ref'] != value['environment']['runtime_ref'] or backend.image.sha256 != value['environment']['image_sha256']:
        raise ValueError('replay runtime must match the declared environment')
    if value['reproduction']['disposition'] != 'reproduced':
        raise ValueError('replay requires complete recorded snapshot identities')
    if not all(getattr(task, name) == value[name] for name in ('root_id', 'family')) or task.source_kind != value['source']['kind']:
        raise ValueError('replay task provenance does not match its proposal')
    root, draft_root = spec_path.absolute().parent, draft_path.absolute().parent
    fixture, bundle = contained(draft_root, task.fixture), contained(draft_root, task.grader_bundle)
    recorded = contained(root, spec['task_start'])
    if fixture.is_relative_to(bundle) or bundle.is_relative_to(fixture):
        raise ValueError('task fixture and hidden oracle overlap')
    frozen = {}
    sources = [(fixture, task.fixture_digest), (bundle, task.grader_digest), (recorded, value['task_start']['sha256'])]
    sources.extend((contained(draft_root, c['fixture']), c['digest']) for c in draft['controls'])
    for path, identity in sources:
        if path in frozen and frozen[path] != identity: raise ValueError('conflicting task-start identity')
        frozen[path] = identity
    snapshots = {role: (spec[role], value[role]) for role in ('before', 'after')}
    if spec['earlier_valid'] is not None:
        earlier = spec['earlier_valid']; snapshots['earlier'] = (earlier['paths'], earlier['identities'])
    paths = {}
    for role, (locations, identities) in snapshots.items():
        paths[role] = {}
        for component in COMPONENTS:
            path = contained(root, locations[component])
            identity = identities[component+'_sha256']
            if identity is None or path in frozen and frozen[path] != identity:
                raise ValueError('missing or conflicting replay snapshot identity')
            if path.is_relative_to(bundle) or bundle.is_relative_to(path):
                raise ValueError('snapshot view and hidden oracle overlap')
            paths[role][component] = path; frozen[path] = identity
    if recorded.is_relative_to(bundle) or bundle.is_relative_to(recorded):
        raise ValueError('recorded task and hidden oracle overlap')
    if any(output.absolute().is_relative_to(p.absolute()) or p.absolute().is_relative_to(output.absolute())
           for p in (*frozen, proposal_path, draft_path, spec_path, backend.image.root, Path(backend.cgroup_root))):
        raise ValueError('replay output and source/runtime inputs overlap')
    if any(p.is_symlink() for p in (output, *output.absolute().parents)):
        raise ValueError('replay output crosses a symlink')

    prepared = {}
    def unchanged():
        try:
            return (load(proposal_path)[1] == proposal_hash and read_draft(draft_path)[2] == draft_hash
                    and read_spec(spec_path)[1] == spec_hash and all(digest(p) == h for p, h in (*frozen.items(), *prepared.items())))
        except (OSError, ValueError): return False

    if not unchanged(): raise ValueError('recorded replay input integrity mismatch')
    output.mkdir(mode=0o700, exist_ok=False)
    report = dict(proposal.receipt(proposal_hash), draft_sha256=draft_hash, replay_spec_sha256=spec_hash,
        status='incomplete', reproduction_disposition='insufficient_evidence', profile='descriptive_smoke',
        execution_kind='offline_replay',
        changed_components=[name for name in COMPONENTS if value['before'][name+'_sha256'] != value['after'][name+'_sha256']],
        snapshot_format='ahl-tree-v1', execution_view='harness/config/memory-v1',
        recorded_fixture_format='plain-tree-v1', minimized_fixture_format='ahl-bank-source-files-v1',
        max_task_trials=12 if 'earlier' in paths else 8, task_trials=0, phases={}, budget_seconds=budget_seconds,
        earlier_valid_recorded='earlier' in paths, independence='unverified', causal_effect='not_established',
        temporal_regression='not_established', bank_admission=False)
    receipt = output/'replay.json'; atomic_json(receipt, report)
    try:
        inputs = output/'inputs'; inputs.mkdir(mode=0o700)
        oracle = inputs/'oracle'; shutil.copytree(bundle, oracle)
        fixtures = {}
        for name, source in (('recorded', recorded), ('minimized', fixture)):
            target = inputs/name; shutil.copytree(source, target)
            if digest(target) != frozen[source]: raise ValueError('fixture changed during replay freeze')
            if name == 'minimized': materialize_sources(target)
            fixtures[name] = target
        views = {}
        for role, components in paths.items():
            view = inputs/role; view.mkdir()
            for component, source in components.items():
                target = view/component; shutil.copytree(source, target)
                if digest(target) != frozen[source]: raise ValueError('snapshot changed during replay freeze')
            views[role] = view
        if not unchanged() or digest(oracle) != task.grader_digest: raise ValueError('replay inputs changed during preparation')
        prepared.update({p:digest(p) for p in (oracle, *views.values(), *fixtures.values())})
        report['execution_view_sha256'] = {name:prepared[path] for name, path in views.items()}
        report['materialized_fixture_sha256'] = {name:prepared[path] for name, path in fixtures.items()}
        if time.monotonic() >= deadline: raise TimeoutError('replay preparation exhausted budget')
        controls = audit(proposal_path, draft_path, output/'controls', backend,
                         budget_seconds=budget_seconds, parent_deadline=deadline)
        report['control_status'] = controls['status']
        report['control_receipt_sha256'] = hashlib.sha256(read_regular(output/'controls/review.json', 1024*1024)).hexdigest()
        if controls['status'] != 'grader_controls_passed':
            report['status'] = controls['status']
        else:
            from agent_harness_lab.cli import evaluator
            runner = evaluator()
            phases = [('aa', 'before', 'before'), ('ab', 'before', 'after')]
            if 'earlier' in views: phases.append(('earlier', 'earlier', 'before'))
            observed = {}
            for phase, baseline, candidate in phases:
                if not unchanged(): report['status'] = 'invalid_input'; break
                remaining = deadline-time.monotonic()
                if remaining <= 0: report['status'] = 'budget_exhausted'; break
                tasks = [dict(id=name, root=task.root_id, family=task.family, split='development',
                    fixture=str(path.relative_to(output)), prompt=value['expected_boundary'], artifacts=list(task.artifacts),
                    offline_executor=spec['offline_executor'],
                    grader=[sys.executable, str(oracle/'grade.py.txt'), '{workspace}', str(oracle/'expected.json')])
                    for name, path in fixtures.items()]
                manifest = dict(schema_version=1, profile='descriptive_smoke', tasks=tasks, skills=[], harness_paths=[],
                    grader_bundles={t['id']:dict(path='inputs/oracle', digest=task.grader_digest) for t in tasks},
                    max_trials=4, budget_seconds=budget_seconds, timeout_seconds=5)
                manifest_path = output/(phase+'-manifest.json'); atomic_json(manifest_path, manifest)
                phase_output = output/phase
                args = SimpleNamespace(manifest=manifest_path, baseline=views[baseline], candidate=views[candidate],
                    output=phase_output, agent='offline', codex='must-not-launch', claude='must-not-launch',
                    cgroup_root=backend.cgroup_root, image_root=backend.image.root,
                    image_sha256=backend.image.sha256, disk_bytes=backend.disk_bytes, parent_deadline=deadline)
                runner.evaluate(args)
                raw_result = read_regular(phase_output/'results.json', 8*1024*1024)
                result = json.loads(raw_result, object_pairs_hook=unique_object, parse_constant=invalid_constant)
                rows, error = outcome_rows(result, task, backend.image.sha256)
                records = result.get('trials', []) if isinstance(result, dict) else []
                if error == 'incomplete': report['task_trials'] = None
                else: report['task_trials'] += sum(isinstance(r, dict) and 'execution' in r for r in records) if isinstance(records, list) else 0
                report['phases'][phase] = dict(status=error or 'graded',
                    results_sha256=hashlib.sha256(raw_result).hexdigest())
                if error: report['status'] = error; break
                observed[phase] = rows
                for record in result['trials']:
                    workspace = phase_output/record['version']/record['task']/'workspace'
                    key = record['task']+'/'+record['version']
                    report['phases'][phase].setdefault('outcomes', {})[key] = dict(outcome=rows[(record['task'], record['version'])],
                        artifacts_sha256=hashlib.sha256(json.dumps({name:hashlib.sha256(raw).hexdigest()
                            for name, raw in artifacts(workspace, task.artifacts).items()}, sort_keys=True).encode()).hexdigest())
                atomic_json(receipt, report)
            else:
                aa, ab = observed['aa'], observed['ab']
                stable = all(aa[(name, 'baseline')] == aa[(name, 'candidate')] == ab[(name, 'baseline')]
                             for name in fixtures)
                minimized = all(ab[('recorded', version)] == ab[('minimized', version)] for version in ('baseline', 'candidate'))
                if 'earlier' in observed:
                    stable = stable and all(observed['earlier'][(name, 'candidate')] == ab[(name, 'baseline')] for name in fixtures)
                    minimized = minimized and observed['earlier'][('recorded', 'baseline')] == observed['earlier'][('minimized', 'baseline')]
                disposition = ('unstable_control' if not stable else 'minimization_boundary_changed' if not minimized
                    else 'baseline_already_passes' if ab[('recorded', 'baseline')] else 'reproduced')
                report.update(status='replay_completed', reproduction_disposition=disposition, aa_observed_agreement=stable,
                    minimization_boundary_preserved=minimized, replay_task_failure_observed=not ab[('recorded', 'baseline')],
                    repair_observed=not ab[('recorded', 'baseline')] and ab[('recorded', 'candidate')])
                if 'earlier' in observed:
                    report['earlier_valid_observed'] = all(observed['earlier'][(name, 'baseline')] for name in fixtures)
                    if disposition == 'reproduced' and not report['earlier_valid_observed']:
                        report['reproduction_disposition'] = 'earlier_valid_not_reproduced'
    except TimeoutError:
        report['status'] = 'budget_exhausted'
    except (OSError, ValueError, RecursionError, subprocess.SubprocessError):
        report['status'] = 'environment_or_grader_defect'
    if not unchanged(): report['status'] = 'invalid_input'
    elif time.monotonic() >= deadline: report['status'] = 'budget_exhausted'
    if report['status'] != 'replay_completed':
        report['reproduction_disposition'] = 'insufficient_evidence'
        for key in ('aa_observed_agreement', 'minimization_boundary_preserved', 'replay_task_failure_observed', 'repair_observed', 'earlier_valid_observed'):
            if key in report: report[key] = None
    report['elapsed_seconds'] = time.monotonic()-started
    atomic_json(receipt, report)
    return report
