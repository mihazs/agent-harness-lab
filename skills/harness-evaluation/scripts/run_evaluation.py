#!/usr/bin/env python3
"""Execute paired Codex tasks; zero grader exit means success, never model prose."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
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


def trace_result(run):
    if run['timeout'] or run['exit_code'] != 0:
        return 'execution_error', None
    try:
        events = [json.loads(line) for line in run['stdout'].splitlines() if line.strip()]
        if any(not isinstance(e, dict) for e in events):
            return 'invalid_trace', None
        completed = [e for e in events if e.get('type') == 'turn.completed']
        if not completed or any(e.get('type') in ('turn.failed', 'error') for e in events):
            return 'incomplete', None
        return 'completed', completed[-1].get('usage')
    except json.JSONDecodeError:
        return 'invalid_trace', None


def evaluate(args):
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text())
    versions = {v: Path(getattr(args, v)).resolve() for v in ('baseline', 'candidate')}
    output = Path(args.output).resolve()
    skills = manifest.get('skills', [])
    tasks = manifest['tasks']
    if not tasks or any(not isinstance(s, str) or not s or '/' in s or s in ('.', '..') for s in skills):
        raise ValueError('tasks must be nonempty and skill names must be simple directory names')
    ids = [t['id'] for t in tasks]
    if len(ids) != len(set(ids)) or any(not i or '/' in i or i in ('.', '..') for i in ids):
        raise ValueError('task ids must be unique simple directory names')
    timeout = float(manifest.get('timeout_seconds', 180))
    budget = float(manifest.get('budget_seconds', 2700))
    if timeout <= 0 or budget <= 0 or len(tasks) * 2 > int(manifest.get('max_trials', 24)):
        raise ValueError('invalid timeout/budget or trial limit exceeded')
    for root in versions.values():
        if output == root or output.is_relative_to(root) or root.is_relative_to(output):
            raise ValueError('output and version inputs must not overlap')
        digest(root)
        for skill in skills:
            if not (root / skill / 'SKILL.md').is_file():
                raise ValueError(f'missing skill {root / skill}')
    for task in tasks:
        fixture = (manifest_path.parent / task['fixture']).resolve()
        if output == fixture or output.is_relative_to(fixture) or fixture.is_relative_to(output):
            raise ValueError('output and fixture must not overlap')
        digest(fixture)
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
    started = time.monotonic()
    version_info = execute([args.codex, '--version'], manifest_path.parent, min(timeout, budget))
    config_path = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
    config = {}
    try:
        raw_config = tomllib.loads(config_path.read_text())
        config = {k: raw_config[k] for k in ('model', 'model_reasoning_effort')
                  if isinstance(raw_config.get(k), str)}
    except (OSError, ValueError):
        pass
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o700)
    results = dict(manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                   versions={k: digest(v) for k, v in versions.items()}, grader_files=grader_files,
                   fixtures={t['id']: digest((manifest_path.parent / t['fixture']).resolve()) for t in tasks}, trials=[],
                   runtime={'codex_version': version_info['stdout'].strip()[:200] if version_info['exit_code'] == 0 else 'unavailable',
                            'user_config_defaults': config, 'effective_settings_verified': False},
                   isolation='cooperative trusted fixtures; workspace-write; inherited configuration/auth/hooks; no security or network isolation claim')
    for version, root in versions.items():
        for task in tasks:
            remaining = budget - (time.monotonic() - started)
            record = dict(version=version, task=task['id'], split=task['split'])
            if remaining <= 0:
                record['status'] = 'budget_exhausted'
                results['trials'].append(record)
                continue
            trial = output / version / task['id']
            workspace = trial / 'workspace'
            fixture = (manifest_path.parent / task['fixture']).resolve()
            shutil.copytree(fixture, workspace)
            loaded = []
            for skill in skills:
                target = workspace / '.agents' / 'skills' / skill
                if target.exists():
                    shutil.rmtree(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(root / skill, target)
                loaded.append(str(target / 'SKILL.md'))
            disabled = []
            for skill in skills:
                for home in (Path.home() / '.agents/skills', Path.home() / '.codex/skills'):
                    if (home / skill).exists():
                        disabled.append(dict(path=str(home / skill), enabled=False))
            argv = [args.codex, 'exec', '--ephemeral', '--skip-git-repo-check',
                    '--sandbox', 'workspace-write', '--json', '-c', 'approval_policy="never"']
            if disabled:
                argv += ['-c', 'skills.config=' + json.dumps(disabled).replace(': ', ' = ')]
            prompt = ('Load these exact evaluation skill files before the task: ' + ', '.join(loaded) + '\n' if loaded and task.get('preload_skills', True) else '')
            prompt += 'Operate only in this fixture workspace. Do not modify external files.\n' + task['prompt']
            argv.append('-')
            record['injected_skill_hashes'] = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in loaded}
            run = execute(argv, workspace, min(timeout, remaining), prompt)
            trace = trial / 'trace.jsonl'
            trace.write_text(run.pop('stdout'))
            (trial / 'stderr.txt').write_text(run.pop('stderr'))
            record['execution'] = run
            check = {**run, 'stdout': trace.read_text()}
            record['status'], record['usage'] = trace_result(check)
            if record['status'] == 'completed':
                remaining = budget - (time.monotonic() - started)
                if remaining <= 0:
                    record['status'] = 'budget_exhausted'
                else:
                    grader = [a.replace('{workspace}', str(workspace)).replace('{trace}', str(trace)) for a in task['grader']]
                    grade = execute(grader, manifest_path.parent, min(timeout, remaining))
                    record['grader'] = grade
                    record['status'] = ('grader_error' if grade['timeout'] or grade['exit_code'] is None
                                        else 'passed' if grade['exit_code'] == 0 else 'failed')
            results['trials'].append(record)
            (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    results['elapsed_seconds'] = time.monotonic() - started
    results['inputs_unchanged'] = (all(digest(p) == results['versions'][v] for v, p in versions.items())
                                   and all(digest((manifest_path.parent / t['fixture']).resolve()) == results['fixtures'][t['id']] for t in tasks)
                                   and hashlib.sha256(manifest_path.read_bytes()).hexdigest() == results['manifest_sha256']
                                   and all(Path(p).is_file() and hashlib.sha256(Path(p).read_bytes()).hexdigest() == h
                                           for p, h in grader_files.items()))
    (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    return 0 if results['inputs_unchanged'] and all(t['status'] == 'passed' for t in results['trials']) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'baseline', 'candidate', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--codex', default='codex')
    args = parser.parse_args()
    try:
        return evaluate(args)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f'evaluation setup error: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
