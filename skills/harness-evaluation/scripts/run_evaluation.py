#!/usr/bin/env python3
"""Execute paired Codex or Claude Code tasks; zero grader exit means success, never model prose."""
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
        if (not isinstance(result,dict) or result.get('schema_version')!=1 or result.get('valid') is not True
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
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text())
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
    if not math.isfinite(timeout) or not math.isfinite(budget) or timeout <= 0 or budget <= 0 or len(tasks) * 2 > int(manifest.get('max_trials', 24)):
        raise ValueError('invalid timeout/budget or trial limit exceeded')
    for root in versions.values():
        if output == root or output.is_relative_to(root) or root.is_relative_to(output):
            raise ValueError('output and version inputs must not overlap')
        digest(root)
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
        digest(fixture)
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
        checkout = Path(__file__).resolve().parents[3]
        if (checkout / 'agent_harness_lab').is_dir():
            sys.path.insert(0, str(checkout))
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
    binary = args.claude if agent == 'claude' else args.codex
    started = time.monotonic()
    version_info = execute([binary, '--version'], manifest_path.parent, min(timeout, budget))
    config_path = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
    config = {}
    try:
        raw_config = tomllib.loads(config_path.read_text()) if agent == 'codex' else {}
        config = {k: raw_config[k] for k in ('model', 'model_reasoning_effort')
                  if isinstance(raw_config.get(k), str)}
    except (OSError, ValueError):
        pass
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o700)
    results = dict(manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                   versions={k: digest(v) for k, v in versions.items()}, grader_files=grader_files,
                   fixtures={t['id']: digest((manifest_path.parent / t['fixture']).resolve()) for t in tasks}, trials=[],
                   runtime={'agent': agent, agent + '_version': version_info['stdout'].strip()[:200] if version_info['exit_code'] == 0 else 'unavailable',
                            'user_config_defaults': config, 'effective_settings_verified': False},
                   isolation=('cooperative trusted fixtures; ' + ('workspace-write; ' if agent == 'codex' else 'inherited Claude permissions/sandbox; ') + 'inherited configuration/auth/hooks; no security or network isolation claim'))
    plan = None
    if 'experiment' in manifest:
        checkout = Path(__file__).resolve().parents[3]
        if (checkout / 'agent_harness_lab').is_dir():
            sys.path.insert(0, str(checkout))
        from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
        plan = ExperimentSpec.parse(manifest['experiment'])
        if {t[0] for t in plan.tasks} != {t['id'] for t in tasks}:
            raise ValueError('experiment task set differs from manifest')
        if (plan.baseline_hash, plan.candidate_hash) != (results['versions']['baseline'], results['versions']['candidate']):
            raise ValueError('experiment harness hashes differ from inputs')
        ordered = schedule(plan)
        if len(ordered) > int(manifest.get('max_trials', 24)):
            raise ValueError('repeated plan exceeds caller trial ceiling')
        results['experiment'] = manifest['experiment']
        results['plan_digest'] = plan.digest
    else:
        ordered = [dict(version=version, task=task['id'], repetition=0) for version in versions for task in tasks]
    results['profile'] = plan.profile if plan else 'descriptive_smoke'
    results['automatic_promotion'] = False
    results['grader_bundle_digests'] = frozen_bundles
    for scheduled in ordered:
        version, task = scheduled['version'], next(t for t in tasks if t['id'] == scheduled['task'])
        root = versions[version]
        remaining = budget - (time.monotonic() - started)
        record = dict(scheduled, split=task['split'])
        record.setdefault('root',task.get('root'))
        record.setdefault('family',task.get('family'))
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
        argv = agent_command(agent, binary, plugin if agent == 'claude' else None, skills)
        prompt = ('Load these exact evaluation skill files before the task: ' + ', '.join(loaded) + '\n' if loaded and task.get('preload_skills', True) else '')
        if manifest.get('no_nested_agents', False):
            prompt += 'Do not launch child agents or nested model trials; the parent owns the evaluation budget.\n'
        prompt += 'Operate only in this fixture workspace. Do not modify external files.\n' + task['prompt']
        record['injected_skill_hashes'] = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in loaded}
        run = execute(argv, workspace, min(timeout, remaining), prompt)
        trace = trial / 'trace.jsonl'
        trace.write_text(run.pop('stdout'))
        (trial / 'stderr.txt').write_text(run.pop('stderr'))
        record['execution'] = run
        check = {**run, 'stdout': trace.read_text()}
        record['status'], record['usage'] = trace_result(check, agent)
        record['skill_namespace'] = 'harness-trial' if agent == 'claude' else None
        if record['status'] == 'completed':
            remaining = budget - (time.monotonic() - started)
            if remaining <= 0:
                record['status'] = 'budget_exhausted'
            else:
                grader = [a.replace('{workspace}', str(workspace)).replace('{trace}', str(trace)) for a in task['grader']]
                grade = execute(grader, manifest_path.parent, min(timeout, remaining))
                record['grader'] = grade
                record['status'] = grading_status(grade,typed=bool(frozen_bundles))
        results['trials'].append(record)
        (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    results['elapsed_seconds'] = time.monotonic() - started
    results['inputs_unchanged'] = (all(digest(p) == results['versions'][v] for v, p in versions.items())
                                   and all(digest((manifest_path.parent / t['fixture']).resolve()) == results['fixtures'][t['id']] for t in tasks)
                                   and hashlib.sha256(manifest_path.read_bytes()).hexdigest() == results['manifest_sha256']
                                   and all(Path(p).is_file() and hashlib.sha256(Path(p).read_bytes()).hexdigest() == h
                                           for p, h in grader_files.items()))
    if frozen_bundles:
        results['inputs_unchanged'] = results['inputs_unchanged'] and all(bundle_digest(Path(p)) == h for p, h in frozen_bundles.items())
    (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    return 0 if results['inputs_unchanged'] and all(t['status'] == 'passed' for t in results['trials']) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'baseline', 'candidate', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--agent', choices=('codex', 'claude'), default='codex')
    parser.add_argument('--codex', default='codex', help='Codex executable path')
    parser.add_argument('--claude', default='claude', help='Claude Code executable path')
    args = parser.parse_args()
    try:
        return evaluate(args)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f'evaluation setup error: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
