#!/usr/bin/env python3
"""Execute paired agent or offline fixture tasks; external artifact grades decide success."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import tomllib


def application_modules():
    checkout = Path(__file__).resolve().parents[3]
    if (checkout / 'agent_harness_lab').is_dir() and str(checkout) not in sys.path:
        sys.path.insert(0, str(checkout))


def digest(root):
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f'evaluation input must be an existing directory: {root}')
    h = hashlib.sha256()
    for p in sorted(root.rglob('*')):
        if p.is_symlink():
            raise ValueError(f'symlinks are not allowed in evaluation inputs: {p}')
        if p.is_file():
            h.update(str(p.relative_to(root)).encode() + b'\0' + p.read_bytes())
    return h.hexdigest()


def execute(argv, cwd, timeout, stdin=None):
    start = time.monotonic()
    try:
        process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
        try:
            out, err = process.communicate(stdin, timeout=timeout)
            return dict(exit_code=process.returncode, stdout=out, stderr=err,
                        elapsed_seconds=time.monotonic() - start, timeout=False)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            out, err = process.communicate()
            return dict(exit_code=process.returncode, stdout=out, stderr=err,
                        elapsed_seconds=time.monotonic() - start, timeout=True)
        except BaseException:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.communicate()
            raise
    except OSError as exc:
        return dict(exit_code=None, stdout='', stderr=str(exc),
                    elapsed_seconds=time.monotonic() - start, timeout=False)



def grading_status(grade, typed=False):
    if grade['timeout'] or grade['exit_code'] is None:
        return 'grader_error'
    if not typed:
        return 'passed' if grade['exit_code'] == 0 else 'failed'
    try:
        result=json.loads(grade['stdout'])
        if (not isinstance(result,dict) or type(result.get('schema_version')) is not int or result.get('schema_version')!=1 or result.get('valid') is not True
                or type(result.get('outcome')) is not bool
                or any(not isinstance(result.get(k),str) or not 0<len(result[k])<=128 for k in ('grader_id','grader_version'))
                or type(grade['exit_code']) is not int or grade['exit_code']!=(0 if result['outcome'] else 1)):
            return 'grader_error'
        grade.update({k:result[k] for k in ('valid','outcome','grader_id','grader_version')})
        return 'passed' if result['outcome'] else 'failed'
    except (ValueError,TypeError,KeyError):
        return 'grader_error'


def trace_result(run, agent="codex"):
    if run['timeout'] or run['exit_code'] != 0:
        return 'execution_error', None
    try:
        events = [json.loads(line) for line in run['stdout'].splitlines() if line.strip()]
        if any(not isinstance(e, dict) for e in events):
            return 'invalid_trace', None
        if agent == 'claude':
            completed = [e for e in events if e.get('type') == 'result']
            if (len(completed) != 1 or completed[0].get('subtype') != 'success'
                    or completed[0].get('is_error') is not False
                    or any(e.get('type') == 'error' for e in events)):
                return 'incomplete', None
            return 'completed', completed[0].get('usage')
        completed = [e for e in events if e.get('type') == 'turn.completed']
        if not completed or any(e.get('type') in ('turn.failed', 'error') for e in events):
            return 'incomplete', None
        return 'completed', completed[-1].get('usage')
    except json.JSONDecodeError:
        return 'invalid_trace', None


def agent_command(agent, binary, plugin=None, skills=()):
    if agent == 'claude':
        argv = [binary, '--print', '--output-format', 'stream-json', '--verbose',
                '--no-session-persistence']
        if plugin is not None:
            argv += ['--plugin-dir', str(plugin)]
        return argv
    disabled = [dict(path=str(home / skill), enabled=False) for skill in skills
                for home in (Path.home() / '.agents/skills', Path.home() / '.codex/skills')
                if (home / skill).exists()]
    argv = [binary, 'exec', '--ephemeral', '--skip-git-repo-check',
            '--sandbox', 'workspace-write', '--json', '-c', 'approval_policy="never"']
    if disabled:
        argv += ['-c', 'skills.config=' + json.dumps(disabled).replace(': ', ' = ')]
    return argv + ['-']


def evaluate(args):
    sandbox_graders = getattr(args, 'sandbox_graders', False)
    if type(sandbox_graders) is not bool: raise ValueError('sandbox grader mode must be explicit')
    cgroup_root = getattr(args, 'cgroup_root', None)
    disk_bytes = getattr(args, 'disk_bytes', None)
    image_root, image_sha256 = getattr(args, 'image_root', None), getattr(args, 'image_sha256', None)
    if (image_root is None) != (image_sha256 is None):
        raise ValueError('runtime image root and SHA-256 pin must be supplied together')
    if (cgroup_root is not None or image_root is not None or disk_bytes is not None) and args.agent != 'offline' and not sandbox_graders:
        raise ValueError('resource boundaries currently require the offline executor')
    manifest_path = Path(args.manifest).resolve()
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode('utf-8'))
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    versions = {v: Path(getattr(args, v)).resolve() for v in ('baseline', 'candidate')}
    output = Path(args.output).resolve()
    skills = manifest.get('skills', [])
    tasks = manifest['tasks']
    harness_paths = manifest.get('harness_paths', [])
    if not isinstance(harness_paths, list) or any(not isinstance(p, str) or not p or '\\' in p
            or Path(p).is_absolute() or any(part in ('.', '..', '.git') for part in Path(p).parts)
            for p in harness_paths):
        raise ValueError('harness_paths must contain safe relative file paths')
    if len(harness_paths) != len(set(harness_paths)):
        raise ValueError('duplicate harness path')
    if not tasks or any(not isinstance(s, str) or not s or '/' in s or s in ('.', '..') for s in skills):
        raise ValueError('tasks must be nonempty and skill names must be simple directory names')
    ids = [t['id'] for t in tasks]
    if len(ids) != len(set(ids)) or any(not i or '/' in i or i in ('.', '..') for i in ids):
        raise ValueError('task ids must be unique simple directory names')
    timeout = float(manifest.get('timeout_seconds', 180))
    budget = float(manifest.get('budget_seconds', 2700))
    trial_limit = int(manifest.get('max_trials', 24))
    configured = None
    binary = args.claude if args.agent == 'claude' else args.codex
    if getattr(args, 'config', None) is not None:
        configuration_started = time.monotonic()
        application_modules()
        from agent_harness_lab.config import evaluation_settings, evaluation_unchanged
        configured = evaluation_settings(Path(args.config), manifest_path, manifest, versions, args.agent, binary)
        budget = min(budget, configured['configuration']['limits']['seconds'])
        trial_limit = min(trial_limit, configured['configuration']['limits']['trials'])
        binary = configured['binary']
    if not math.isfinite(timeout) or not math.isfinite(budget) or timeout <= 0 or budget <= 0 or len(tasks) * 2 > trial_limit:
        raise ValueError('invalid timeout/budget or trial limit exceeded')
    frozen_versions, frozen_fixtures = {}, {}
    for version, root in versions.items():
        if output == root or output.is_relative_to(root) or root.is_relative_to(output):
            raise ValueError('output and version inputs must not overlap')
        frozen_versions[version] = digest(root)
        for relative in harness_paths:
            path = root / relative
            if path.exists() and not path.is_file():
                raise ValueError('harness_paths must name files')
        for skill in skills:
            if not (root / skill / 'SKILL.md').is_file():
                raise ValueError(f'missing skill {root / skill}')
    for task in tasks:
        fixture = (manifest_path.parent / task['fixture']).resolve()
        if output == fixture or output.is_relative_to(fixture) or fixture.is_relative_to(output):
            raise ValueError('output and fixture must not overlap')
        frozen_fixtures[task['id']] = digest(fixture)
        for argument in task['grader']:
            if not isinstance(argument, str):
                raise ValueError('grader arguments must be strings')
            if '{workspace}' not in argument and '{trace}' not in argument:
                program = (manifest_path.parent / argument).resolve()
                if program.is_file() and program.is_relative_to(fixture):
                    raise ValueError('grader programs must be outside fixture workspaces')
        if task['split'] not in ('development', 'regression', 'heldout'):
            raise ValueError('invalid split')
        if not task['grader'] or not all(isinstance(a, str) for a in task['grader']):
            raise ValueError('grader must be a nonempty argv list')
        if any('{workspace}' in a and a != '{workspace}' for a in task['grader']):
            raise ValueError('keep grader programs outside the workspace; pass {workspace} as a separate argument')
    grader_files = {}
    if not (sandbox_graders or args.agent == 'offline'):
        for task in tasks:
            for arg in task['grader']:
                if '{workspace}' in arg or '{trace}' in arg:
                    continue
                file = (manifest_path.parent / arg).resolve()
                if file.is_file():
                    grader_files[str(file)] = hashlib.sha256(file.read_bytes()).hexdigest()
    declared_bundles = manifest.get('grader_bundles', {})
    frozen_bundles = {}
    if declared_bundles:
        application_modules()
        from agent_harness_lab.evaluation.integrity import contained, digest as bundle_digest
        if set(declared_bundles) != set(ids):
            raise ValueError('every task must declare its complete grader bundle')
        for task_id, bundle in declared_bundles.items():
            folder = contained(manifest_path.parent, bundle['path'])
            if output==folder or output.is_relative_to(folder) or folder.is_relative_to(output):
                raise ValueError('output and grader bundle must not overlap')
            if any(folder == (manifest_path.parent / t['fixture']).resolve() or folder.is_relative_to((manifest_path.parent / t['fixture']).resolve()) or (manifest_path.parent / t['fixture']).resolve().is_relative_to(folder) for t in tasks):
                raise ValueError('grader bundle cannot be inside a task fixture')
            if bundle_digest(folder) != bundle['digest']:
                raise ValueError('declared grader bundle integrity mismatch')
            frozen_bundles[str(folder)] = bundle['digest']
    agent = args.agent
    def inputs_unchanged():
        try:
            return (all(digest(path) == frozen_versions[name] for name, path in versions.items())
                    and all(digest((manifest_path.parent / t['fixture']).resolve()) == frozen_fixtures[t['id']] for t in tasks)
                    and hashlib.sha256(manifest_path.read_bytes()).hexdigest() == manifest_hash
                    and all(Path(p).is_file() and hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in grader_files.items())
                    and (not frozen_bundles or all(bundle_digest(Path(p)) == h for p, h in frozen_bundles.items()))
                    and (configured is None or (evaluation_unchanged(Path(args.config), configured)
                         and all(bundle_digest(root) == configured['harness_digests'][name] for name, root in versions.items())
                         and all(bundle_digest((manifest_path.parent/t['fixture']).resolve()) == configured['fixture_digests'][t['id']] for t in tasks))))
        except (ValueError, OSError):
            return False
    def verify_inputs(record):
        if inputs_unchanged():
            return True
        record.update(status='invalid_input', reason='frozen_inputs_changed')
        return False
    offline = agent == 'offline'
    backend = None
    if offline or sandbox_graders:
        if not frozen_bundles:
            raise ValueError('sandboxed grading requires every frozen grader bundle')
        if offline and (skills or harness_paths):
            raise ValueError('offline execution requires frozen grader bundles and a directly mounted harness, without skill injection')
        from agent_harness_lab.evaluation.sandbox import SandboxBackend, Limits
        from agent_harness_lab.evaluation.bank_tools import frozen_grader, regrade_artifacts
        for task in tasks:
            frozen_grader(manifest_path.parent, task, declared_bundles[task['id']])
            if offline:
                command = task.get('offline_executor')
                if (not isinstance(command, list) or not command
                        or not all(isinstance(a, str) and a and '\0' not in a for a in command)):
                    raise ValueError('offline tasks require an explicit executor argv and bounded artifact allowlist')
        options = dict(cgroup_root=cgroup_root)
        if image_root is not None: options.update(image_root=image_root, image_sha256=image_sha256)
        if disk_bytes is not None: options['disk_bytes'] = disk_bytes
        backend = SandboxBackend(**options)
    plan = None
    if 'experiment' in manifest:
        application_modules()
        from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
        plan = ExperimentSpec.parse(manifest['experiment'])
        if {t[0] for t in plan.tasks} != {t['id'] for t in tasks}:
            raise ValueError('experiment task set differs from manifest')
        if (plan.baseline_hash, plan.candidate_hash) != (frozen_versions['baseline'], frozen_versions['candidate']):
            raise ValueError('experiment harness hashes differ from inputs')
        if configured and {t[0]: t[1:] for t in plan.tasks} != {t['id']: (t['root'], t['family']) for t in tasks}:
            raise ValueError('experiment lineage differs from configured bank')
        ordered = schedule(plan)
        if len(ordered) > trial_limit:
            raise ValueError('repeated plan exceeds caller trial ceiling')
    else:
        ordered = [dict(version=version, task=task['id'], repetition=0) for version in versions for task in tasks]
    started = configuration_started if configured else time.monotonic()
    blocked = configured and configured['blocked_reason']
    if configured and not evaluation_unchanged(Path(args.config), configured):
        blocked = 'configuration_changed_before_execution'
    available = budget - (time.monotonic() - started)
    if available <= 0:
        blocked = 'preflight_budget_exhausted'
    capability = backend.capability(seconds=min(3, available)) if (offline or sandbox_graders) and not blocked else None
    if sandbox_graders and not offline and capability is not None and capability['status'] != 'available':
        blocked = 'grader_sandbox_unavailable'
    available = budget - (time.monotonic() - started)
    if available <= 0: blocked = 'preflight_budget_exhausted'
    version_info = dict(exit_code=None, stdout='') if offline or blocked else execute([binary, '--version'], manifest_path.parent, min(timeout, available))
    config_path = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
    config = {}
    try:
        raw_config = tomllib.loads(config_path.read_text()) if agent == 'codex' and not blocked else {}
        config = {k: raw_config[k] for k in ('model', 'model_reasoning_effort')
                  if isinstance(raw_config.get(k), str)}
    except (OSError, ValueError):
        pass
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o700)
    results = dict(manifest_sha256=manifest_hash,
                   versions=frozen_versions, grader_files=grader_files,
                   fixtures=frozen_fixtures, trials=[],
                   runtime={'agent': agent, agent + '_version': version_info['stdout'].strip()[:200] if version_info['exit_code'] == 0 else 'unavailable',
                            'user_config_defaults': config, 'effective_settings_verified': False},
                   isolation=('cooperative trusted fixtures; ' + ('workspace-write; ' if agent == 'codex' else 'inherited Claude permissions/sandbox; ') + 'inherited configuration/auth/hooks; no security or network isolation claim'))
    results['grader_bundle_digests'] = frozen_bundles
    if sandbox_graders:
        results['runtime']['grader_capability'] = capability
        results['isolation'] = dict(executor='cooperative native CLI; no role isolation claim',
            grader='fresh offline namespace with readonly artifacts and frozen oracle', full_certification=False)
    if configured:
        results['effective_configuration'] = dict(configured, applied_limits=dict(seconds=budget, trials=trial_limit),
                                                  requested_agent=agent, effective_runtime_settings_verified=False)
    if blocked:
        results['isolation'] = dict(status='not_executed', full_certification=False)
        results.update(status='budget_exhausted' if blocked == 'preflight_budget_exhausted' else 'blocked',
                       reason=blocked, automatic_promotion=False, elapsed_seconds=time.monotonic()-started)
        (output/'results.json').write_text(json.dumps(results, indent=2)+'\n')
        return 1 if results['status'] == 'budget_exhausted' else 3
    if offline:
        results['runtime'] = dict(agent='offline', model_inference=False, effective_settings_verified=False,
                                  capability=capability, commands={t['id']: t['offline_executor'] for t in tasks})
        results['isolation'] = dict(executor='offline namespace with fixture and selected readonly harness',
                                  grader='fresh namespace with extracted readonly artifacts and oracle',
                                  full_certification=False)
        results['automatic_promotion'] = False
        if capability['status'] != 'available':
            results.update(status='blocked', reason='offline_sandbox_unavailable', elapsed_seconds=time.monotonic()-started)
            (output/'results.json').write_text(json.dumps(results, indent=2)+'\n')
            return 3
    if plan:
        results['experiment'] = manifest['experiment']
        results['plan_digest'] = plan.digest
    results['profile'] = plan.profile if plan else 'descriptive_smoke'
    results['automatic_promotion'] = False
    for scheduled in ordered:
        version, task = scheduled['version'], next(t for t in tasks if t['id'] == scheduled['task'])
        root = versions[version]
        remaining = budget - (time.monotonic() - started)
        record = dict(scheduled, split=task['split'])
        record.setdefault('root',task.get('root'))
        record.setdefault('family',task.get('family'))
        if not verify_inputs(record):
            results['trials'].append(record)
            continue
        if remaining <= 0:
            record['status'] = 'budget_exhausted'
            results['trials'].append(record)
            continue
        trial = output / version / task['id']
        if plan:
            trial = trial / str(scheduled['repetition'])
        workspace = trial / 'workspace'
        fixture = (manifest_path.parent / task['fixture']).resolve()
        shutil.copytree(fixture, workspace)
        for relative in harness_paths:
            target = workspace / relative
            if target.exists():
                if not target.is_file():
                    raise ValueError('fixture harness path must be a file')
                target.unlink()
            source = root / relative
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        loaded = []
        plugin = workspace / '.harness-trial'
        if agent == 'claude':
            (plugin / '.claude-plugin').mkdir(parents=True)
            (plugin / '.claude-plugin/plugin.json').write_text(json.dumps({'name': 'harness-trial'}))
        for skill in skills:
            target = ((plugin / 'skills') if agent == 'claude' else (workspace / '.agents/skills')) / skill
            if target.exists():
                shutil.rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(root / skill, target)
            loaded.append(str(target / 'SKILL.md'))
        argv = task['offline_executor'] if offline else agent_command(agent, binary, plugin if agent == 'claude' else None, skills)
        prompt = ('Load these exact evaluation skill files before the task: ' + ', '.join(loaded) + '\n' if loaded and task.get('preload_skills', True) else '')
        if manifest.get('no_nested_agents', False):
            prompt += 'Do not launch child agents or nested model trials; the parent owns the evaluation budget.\n'
        prompt += 'Operate only in this fixture workspace. Do not modify external files.\n' + task['prompt']
        record['injected_skill_hashes'] = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in loaded}
        remaining = budget - (time.monotonic() - started)
        if remaining <= 0:
            record['status'] = 'budget_exhausted'
            results['trials'].append(record)
            continue
        if offline:
            try:
                extraction = dict(artifact_allowlist=task['artifacts']) if disk_bytes is not None else {}
                run = backend.run(argv, workspace, {'harness': root}, 'executor',
                                  Limits(seconds=min(timeout, remaining)), **extraction)
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                record.update(status='execution_error', error=str(exc))
                results['trials'].append(record)
                (output/'results.json').write_text(json.dumps(results, indent=2)+'\n')
                continue
            run['timeout'] = run['status'] == 'timeout'
        else:
            run = execute(argv, workspace, min(timeout, remaining), prompt)
        trace = trial / ('stdout.txt' if offline else 'trace.jsonl')
        trace.write_text(run.pop('stdout'))
        (trial / 'stderr.txt').write_text(run.pop('stderr'))
        record['execution'] = run
        check = {**run, 'stdout': trace.read_text()}
        if offline:
            record['status'] = 'completed' if (run['status'] == 'completed' and type(run['exit_code']) is int
                                               and run['exit_code'] == 0 and run['inputs_unchanged'] is True) else 'execution_error'
            record['usage'] = None
        else:
            record['status'], record['usage'] = trace_result(check, agent)
        record['skill_namespace'] = 'harness-trial' if agent == 'claude' else None
        if record['status'] == 'completed' and verify_inputs(record):
            remaining = budget - (time.monotonic() - started)
            if remaining <= 0:
                record['status'] = 'budget_exhausted'
            else:
                if offline or sandbox_graders:
                    bundle = contained(manifest_path.parent, declared_bundles[task['id']]['path'])
                    try:
                        grade = regrade_artifacts(workspace, task['artifacts'], bundle, frozen_bundles[str(bundle)],
                                                  backend, limits=Limits(seconds=min(timeout, remaining)))
                        record['grader'] = grade
                        record['status'] = grade['status']
                    except (ValueError, OSError, subprocess.SubprocessError) as exc:
                        record.update(status='grader_error', error=str(exc))
                else:
                    grader = [a.replace('{workspace}', str(workspace)).replace('{trace}', str(trace)) for a in task['grader']]
                    grade = execute(grader, manifest_path.parent, min(timeout, remaining))
                    record['grader'] = grade
                    record['status'] = grading_status(grade,typed=bool(frozen_bundles))
        verify_inputs(record)
        results['trials'].append(record)
        (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    results['elapsed_seconds'] = time.monotonic() - started
    results['inputs_unchanged'] = inputs_unchanged()
    (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    return 0 if results['inputs_unchanged'] and all(t['status'] == 'passed' for t in results['trials']) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'baseline', 'candidate', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--config', help='Explicit project configuration; restricts bank, harness scope, runtime and resource caps')
    parser.add_argument('--agent', choices=('codex', 'claude', 'offline'), default='codex',
                        help='offline executes explicit fixture commands without model inference and requires bubblewrap')
    parser.add_argument('--codex', default='codex', help='Codex executable path')
    parser.add_argument('--claude', default='claude', help='Claude Code executable path')
    parser.add_argument('--sandbox-graders', action='store_true', help='Require fresh offline frozen artifact graders for cooperative CLI trials')
    parser.add_argument('--cgroup-root', type=Path, help='Explicit predelegated cgroup v2 subtree for offline execution or sandboxed grader resources')
    parser.add_argument('--image-root', type=Path, help='Explicit private extracted runtime rootfs for offline execution or sandboxed grading')
    parser.add_argument('--image-sha256', help='Preapproved rootfs content pin; required with --image-root')
    parser.add_argument('--disk-bytes', type=int, help='Aggregate tmpfs data capacity for offline roles or sandboxed graders; requires --cgroup-root')
    args = parser.parse_args()
    try:
        return evaluate(args)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f'evaluation setup error: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
