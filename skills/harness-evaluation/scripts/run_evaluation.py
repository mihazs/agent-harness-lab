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

DIGEST_CHUNK_BYTES = 1024 * 1024


def application_modules():
    checkout = Path(__file__).resolve().parents[3]
    if (checkout / 'agent_harness_lab').is_dir() and str(checkout) not in sys.path:
        sys.path.insert(0, str(checkout))


def digest(root):
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f'evaluation input must be an existing directory: {root}')
    h = hashlib.sha256()
    buffer = bytearray(DIGEST_CHUNK_BYTES)
    view = memoryview(buffer)
    for p in sorted(root.rglob('*')):
        if p.is_symlink():
            raise ValueError(f'symlinks are not allowed in evaluation inputs: {p}')
        if p.is_file():
            # Same bytes in the same order as hashing path, NUL and whole contents, one reused chunk at a time.
            h.update(str(p.relative_to(root)).encode() + b'\0')
            with p.open('rb') as handle:
                while size := handle.readinto(buffer):
                    h.update(view[:size])
    return h.hexdigest()


def write_results(path, results):
    """Write json.dumps(results, indent=2) + newline bytes without building the document string.

    A temporary sibling replaces the file only after a complete write, so a serialization error
    leaves the previous results.json intact as it did when the string was built first.
    """
    temporary = path.with_name(path.name + '.tmp')
    try:
        with open(temporary, 'w') as handle:
            json.dump(results, handle, indent=2)
            handle.write('\n')
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def managed_environment(parent_id, agent, inherit=False):
    environment=native_environment(agent, inherit)
    environment['AHL_MANAGED_PREPARATION']=str(parent_id)
    return environment


def execute(argv, cwd, timeout, stdin=None,*,env=None):
    start = time.monotonic()
    try:
        process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True,**({} if env is None else dict(env=env)))
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


def jsonl_lines(text):
    """Yield JSON Lines records split on LF only, tolerating CRLF line endings.

    str.splitlines() also breaks on U+2028, U+2029, U+0085 and other separators
    that JSON permits unescaped inside strings, splitting one record in two.
    """
    start = 0
    while start < len(text):
        end = text.find('\n', start)
        if end < 0:
            end = len(text)
        yield text[start:end].removesuffix('\r')
        start = end + 1


def trace_result(run, agent="codex"):
    if run['timeout'] or run['exit_code'] != 0:
        return 'execution_error', None
    # One pass that keeps only the flags, counts and usage the classification needs. Every line is
    # still parsed before classifying, so parse errors and non-object records win exactly as before.
    non_object = error_event = False
    results = turns = 0
    first_result_ok = False
    usage = None
    try:
        for line in jsonl_lines(run['stdout']):
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict):
                non_object = True
                continue
            kind = event.get('type')
            if agent == 'claude':
                if kind == 'result':
                    results += 1
                    if results == 1:
                        first_result_ok = event.get('subtype') == 'success' and event.get('is_error') is False
                        usage = event.get('usage')
                elif kind == 'error':
                    error_event = True
            elif kind == 'turn.completed':
                turns += 1
                usage = event.get('usage')
            elif kind in ('turn.failed', 'error'):
                error_event = True
    except json.JSONDecodeError:
        return 'invalid_trace', None
    if non_object:
        return 'invalid_trace', None
    if agent == 'claude':
        if results != 1 or not first_result_ok or error_event:
            return 'incomplete', None
        return 'completed', usage
    if not turns or error_event:
        return 'incomplete', None
    return 'completed', usage


def agent_command(agent, binary, plugin=None, skills=(), *, skill_homes=None):
    if agent == 'claude':
        argv = [binary, '--print', '--output-format', 'stream-json', '--verbose',
                '--no-session-persistence']
        if plugin is not None:
            argv += ['--plugin-dir', str(plugin)]
        return argv
    homes = (Path.home() / '.agents/skills', Path.home() / '.codex/skills') if skill_homes is None else skill_homes
    disabled = [dict(path=str(home / skill), enabled=False) for skill in skills
                for home in homes if (home / skill).exists()]
    argv = [binary, 'exec', '--ephemeral', '--skip-git-repo-check',
            '--sandbox', 'workspace-write', '--json', '-c', 'approval_policy="never"']
    if disabled:
        argv += ['-c', 'skills.config=' + json.dumps(disabled).replace(': ', ' = ')]
    return argv + ['-']


def native_environment(agent, inherit=False, environ=None):
    """Filter the cooperative runtime's host environment unless inheritance was requested."""
    source = os.environ if environ is None else environ
    if inherit:
        return dict(source)
    common = {'PATH', 'HOME', 'LANG', 'LANGUAGE', 'TERM', 'COLORTERM', 'TERMINFO', 'TMPDIR', 'LC_ALL', 'LC_CTYPE', 'LC_NUMERIC', 'LC_TIME', 'LC_COLLATE',
              'LC_MONETARY', 'LC_MESSAGES', 'LC_PAPER', 'LC_NAME', 'LC_ADDRESS', 'LC_TELEPHONE',
              'LC_MEASUREMENT', 'LC_IDENTIFICATION'}
    auth = {'CODEX_API_KEY', 'CODEX_HOME'} if agent == 'codex' else ({
        'CLAUDE_CONFIG_DIR', 'CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN',
        'ANTHROPIC_BASE_URL', 'ANTHROPIC_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL',
        'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL'} if agent == 'claude' else set())
    if agent == 'claude':
        providers = {
            'CLAUDE_CODE_USE_BEDROCK': {'AWS_PROFILE', 'AWS_REGION', 'AWS_DEFAULT_REGION',
                'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN', 'AWS_BEARER_TOKEN_BEDROCK',
                'AWS_CONFIG_FILE', 'AWS_SHARED_CREDENTIALS_FILE', 'AWS_WEB_IDENTITY_TOKEN_FILE', 'AWS_ROLE_ARN'},
            'CLAUDE_CODE_USE_VERTEX': {'GOOGLE_APPLICATION_CREDENTIALS', 'ANTHROPIC_VERTEX_PROJECT_ID',
                'ANTHROPIC_VERTEX_BASE_URL', 'CLOUD_ML_REGION'},
            'CLAUDE_CODE_USE_FOUNDRY': {'ANTHROPIC_FOUNDRY_API_KEY', 'ANTHROPIC_FOUNDRY_AUTH_TOKEN',
                'ANTHROPIC_FOUNDRY_RESOURCE', 'ANTHROPIC_FOUNDRY_BASE_URL', 'AZURE_CLIENT_ID',
                'AZURE_TENANT_ID', 'AZURE_CLIENT_SECRET', 'AZURE_CONFIG_DIR'},
        }
        for enabled, names in providers.items():
            if source.get(enabled) in ('1', 'true'):
                auth |= {enabled} | names
    return {key: value for key, value in source.items() if key in common or key in auth}


def environment_receipt(environment, inherited, run_root):
    def digest(values):
        return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    normalized = {key: value.replace(str(run_root), '<run>') for key, value in environment.items()}
    if normalized.get('AHL_MANAGED_PREPARATION') == Path(run_root).name:
        normalized['AHL_MANAGED_PREPARATION'] = '<run>'
    return dict(names=sorted(environment), sha256=digest(environment), comparison_sha256=digest(normalized),
                inherited=inherited)


def trial_command(agent, binary, plugin, skills, trial, workspace, defaults, environ=None):
    """Argv and environment for one cooperative trial with the gated user-layer exclusions requested.

    Claude gets project-only setting sources, a strict empty MCP config and hook-disabling flag settings;
    Codex gets a temporary CODEX_HOME. Neither the real CODEX_HOME nor ~/.claude is named in either.
    """
    application_modules()
    from agent_harness_lab.evaluation import trial_surfaces
    environ = dict(os.environ if environ is None else environ)
    if agent == 'claude':
        environ.pop('CODEX_HOME', None)
        return agent_command(agent, binary, plugin) + trial_surfaces.claude_arguments(trial), environ
    environment = trial_surfaces.codex_environment(trial, workspace, defaults, environ)
    staged = environment.get('CODEX_HOME') != environ.get('CODEX_HOME')
    homes = trial_surfaces.home_skills(environ) if staged else None
    return agent_command(agent, binary, None, skills, skill_homes=homes), environment


def evaluate(args):
    if not any(getattr(args,name,None) is not None for name in ('source_sink','source_cache','source_project_sha256','source_scope_sha256')):
        return _evaluate(args)
    application_modules()
    from agent_harness_lab.evaluation.controller_receipts import session
    with session(): return _evaluate(args)


def _evaluate(args):
    started = time.monotonic()
    parent_deadline = getattr(args, 'parent_deadline', None)
    if parent_deadline is not None and (type(parent_deadline) not in (int, float) or not math.isfinite(parent_deadline)):
        raise ValueError('parent deadline must be a finite monotonic timestamp')
    inherit_env = getattr(args, 'inherit_env', False)
    if type(inherit_env) is not bool: raise ValueError('environment inheritance must be explicit')
    sandbox_graders = getattr(args, 'sandbox_graders', False)
    if type(sandbox_graders) is not bool: raise ValueError('sandbox grader mode must be explicit')
    controlled = args.agent == 'responses'
    if inherit_env and args.agent not in ('codex', 'claude'):
        raise ValueError('--inherit-env applies only to cooperative native agents')
    supplied_client = getattr(args,'inference_client',None)
    supplied_backend = getattr(args,'sandbox_backend',None)
    if not controlled and (supplied_client is not None or supplied_backend is not None):
        raise ValueError('internal controlled runtime cannot be mixed with cooperative agents')
    inference_path = getattr(args, 'inference_settings', None)
    if controlled != (inference_path is not None):
        raise ValueError('the responses executor requires explicit --inference-settings; other agents cannot use it')
    if controlled and getattr(args, 'config', None) is not None:
        raise ValueError('Responses execution uses explicit inference settings, not cooperative runtime configuration')
    cgroup_root = getattr(args, 'cgroup_root', None)
    disk_bytes = getattr(args, 'disk_bytes', None)
    permission_audit=getattr(args,'permission_audit',None)
    if permission_audit is not None and (permission_audit!='permission-results-v1' or args.agent not in ('offline','responses')):
        raise ValueError('permission audit requires the supported offline or controller executor')
    image_root, image_sha256 = getattr(args, 'image_root', None), getattr(args, 'image_sha256', None)
    if (image_root is None) != (image_sha256 is None):
        raise ValueError('runtime image root and SHA-256 pin must be supplied together')
    if (cgroup_root is not None or image_root is not None or disk_bytes is not None) and args.agent not in ('offline','responses') and not sandbox_graders:
        raise ValueError('resource boundaries currently require the offline executor')
    if controlled and any(value is None for value in (cgroup_root,image_root,image_sha256,disk_bytes)):
        raise ValueError('Responses execution requires explicit cgroup, pinned image and private disk profiles')
    source_sink = getattr(args,'source_sink',None)
    source_options = [getattr(args,name,None) for name in ('source_cache','source_project_sha256','source_scope_sha256')]
    if source_sink is not None or any(value is not None for value in source_options):
        application_modules()
        from agent_harness_lab.sources.controller import SourceSink
        if any(value is not None for value in source_options):
            if source_sink is not None or any(value is None for value in source_options):
                raise ValueError('controller source cache and expected project/scope are required together')
            source_sink = SourceSink(*source_options)
        if type(source_sink) is not SourceSink or args.agent not in ('offline','responses') or any(
                value is None for value in (cgroup_root,image_root,image_sha256,disk_bytes)):
            raise ValueError('controller sources require actual pinned offline worker and grader profiles')
    if controlled:
        application_modules()
        from agent_harness_lab.evaluation.integrity import contained, read_regular, tree as bounded_tree
    manifest_path = Path(args.manifest).resolve()
    manifest_bytes = read_regular(manifest_path,1024*1024) if controlled else manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode('utf-8'))
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    if (isinstance(manifest.get('experiment'),dict) and manifest['experiment'].get('profile') == 'certification'
            or 'protected_access' in manifest or getattr(args, '_protected_access', None) is not None):
        application_modules()
        from agent_harness_lab.evaluation.certification_access import admission
        admission(args, manifest)
    versions = {v: Path(getattr(args, v)).resolve() for v in ('baseline', 'candidate')}
    output = Path(args.output).resolve()
    if source_sink is not None and any(source_sink.store.path.is_relative_to(root) or root.is_relative_to(source_sink.store.path)
            for root in (manifest_path.parent,output,*versions.values(),Path(image_root).resolve())):
        raise ValueError('controller source journal must remain outside all role inputs and run output')
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
        application_modules()
        from agent_harness_lab.config import evaluation_settings, evaluation_unchanged
        configured = evaluation_settings(Path(args.config), manifest_path, manifest, versions, args.agent, binary)
        budget = min(budget, configured['configuration']['limits']['seconds'])
        trial_limit = min(trial_limit, configured['configuration']['limits']['trials'])
        binary = configured['binary']
    initial_trial_ceiling = len(tasks) * 2
    if isinstance(manifest.get('experiment'), dict) and manifest['experiment'].get('sequential') is not None:
        application_modules()
        from agent_harness_lab.evaluation.plan import ExperimentSpec
        from agent_harness_lab.evaluation.sequential import planned_trials, validate_selection
        selected_plan = ExperimentSpec.parse(manifest['experiment'])
        selected_plan.validate()
        validate_selection(manifest)
        if {t[0]: t[1:] for t in selected_plan.tasks} != {t['id']: (t['root'], t['family']) for t in tasks}:
            raise ValueError('experiment task lineage differs from manifest')
        initial_trial_ceiling = planned_trials(selected_plan)
    if not math.isfinite(timeout) or not math.isfinite(budget) or timeout <= 0 or budget <= 0 or initial_trial_ceiling > trial_limit:
        raise ValueError('invalid timeout/budget or trial limit exceeded')
    if parent_deadline is not None:
        budget = min(budget, max(0, parent_deadline - started))
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
        fixture = contained(manifest_path.parent,task['fixture']) if controlled else (manifest_path.parent / task['fixture']).resolve()
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
        if 'cwd' in task:
            if not controlled:
                raise ValueError('task cwd is supported only by the controlled Responses executor')
            application_modules()
            from agent_harness_lab.evaluation.instruction_context import chain_paths
            chain_paths(task['cwd'])
        if task['split'] not in ('development', 'regression', 'heldout'):
            raise ValueError('invalid split')
        if not task['grader'] or not all(isinstance(a, str) for a in task['grader']):
            raise ValueError('grader must be a nonempty argv list')
        if any('{workspace}' in a and a != '{workspace}' for a in task['grader']):
            raise ValueError('keep grader programs outside the workspace; pass {workspace} as a separate argument')
    grader_files = {}
    if not (sandbox_graders or args.agent in ('offline','responses')):
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
        from agent_harness_lab.evaluation.manifest_contract import ContractError
        for index, (task_id, bundle) in enumerate(declared_bundles.items()):
            if not isinstance(bundle, dict) or 'path' not in bundle or 'digest' not in bundle:
                # The controller freezes every bundle digest before evaluation; an unfrozen manifest is an AHL defect.
                raise ContractError('internal_contract_error', 'evaluation', f'grader_bundles[{index}].' + ('digest' if isinstance(bundle, dict) and 'path' in bundle else 'path'))
            folder = contained(manifest_path.parent, bundle['path'])
            if output==folder or output.is_relative_to(folder) or folder.is_relative_to(output):
                raise ValueError('output and grader bundle must not overlap')
            if any(folder == (manifest_path.parent / t['fixture']).resolve() or folder.is_relative_to((manifest_path.parent / t['fixture']).resolve()) or (manifest_path.parent / t['fixture']).resolve().is_relative_to(folder) for t in tasks):
                raise ValueError('grader bundle cannot be inside a task fixture')
            if bundle_digest(folder) != bundle['digest']:
                raise ValueError('declared grader bundle integrity mismatch')
            frozen_bundles[str(folder)] = bundle['digest']
    # Controller-computed control digests; a control changed after freeze invalidates the run.
    frozen_controls = {(manifest_path.parent / c['fixture']).resolve(): c['digest']
                       for c in manifest.get('grader_self_tests', []) if isinstance(c, dict) and 'digest' in c}
    if frozen_controls:
        application_modules()
        from agent_harness_lab.evaluation.integrity import digest as control_digest
        if any(control_digest(p) != h for p, h in frozen_controls.items()):
            raise ValueError('declared grader control integrity mismatch')
    agent = args.agent
    def inputs_unchanged():
        try:
            return (all(digest(path) == frozen_versions[name] for name, path in versions.items())
                    and all(digest((manifest_path.parent / t['fixture']).resolve()) == frozen_fixtures[t['id']] for t in tasks)
                    and hashlib.sha256(manifest_path.read_bytes()).hexdigest() == manifest_hash
                    and all(Path(p).is_file() and hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in grader_files.items())
                    and (not frozen_bundles or all(bundle_digest(Path(p)) == h for p, h in frozen_bundles.items()))
                    and all(control_digest(p) == h for p, h in frozen_controls.items())
                    and (not controlled or response_runtime.read_settings(inference_path)[1] == inference_hash)
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
    if offline or sandbox_graders or controlled:
        if not frozen_bundles:
            raise ValueError('sandboxed grading requires every frozen grader bundle')
        if (offline or controlled) and (skills or harness_paths):
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
        if permission_audit is not None:options['permission_audit']=permission_audit
        backend = supplied_backend if supplied_backend is not None else SandboxBackend(**options)
        if (supplied_backend is not None or permission_audit is not None) and getattr(backend,'permission_audit',None)!=permission_audit:
            raise ValueError('shared backend must preserve the requested permission audit profile')
        if controlled:
            from agent_harness_lab.evaluation import responses as response_runtime
            from agent_harness_lab.evaluation.working_state import WorkingState
            inference_settings,inference_hash = response_runtime.read_settings(inference_path)
            cwd_tasks = [task['cwd'] for task in tasks if 'cwd' in task]
            if cwd_tasks and inference_settings.get('instruction_protocol') is None:
                raise ValueError('task cwd requires the controller instruction protocol in the inference settings')
            from agent_harness_lab.evaluation.instruction_context import chain_size
            for task_cwd in cwd_tasks:
                for root in versions.values():
                    chain_size(root, task_cwd)
            fixtures = [contained(manifest_path.parent,task['fixture']) for task in tasks]
            for root in versions.values():
                bounded_tree(root)
                if any(root.is_relative_to(fixture) or fixture.is_relative_to(root) for fixture in fixtures):
                    raise ValueError('executor harness and task fixture views must not overlap')
            if any(left != right and (left.is_relative_to(right) or right.is_relative_to(left)) for left in fixtures for right in fixtures):
                raise ValueError('executor task fixtures must not be nested')
            for folder in frozen_bundles:
                if any(Path(folder).is_relative_to(root) or root.is_relative_to(Path(folder)) for root in versions.values()):
                    raise ValueError('executor harness views must not overlap grader bundles')
            try:
                for task in tasks:
                    WorkingState((manifest_path.parent/task['fixture']).resolve(),started+budget)
            except TimeoutError:
                pass  # Budget reporting below blocks before credentials or inference.
    plan = None
    precision_report = None
    precision_cohorts = None
    root_similarity = None
    root_similarity_path = None
    sequential_controller = None
    if 'experiment' in manifest:
        application_modules()
        from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
        from agent_harness_lab.evaluation.oracle import identity as oracle_identity
        plan = ExperimentSpec.parse(manifest['experiment'])
        if {t[0] for t in plan.tasks} != {t['id'] for t in tasks}:
            raise ValueError('experiment task set differs from manifest')
        if (plan.baseline_hash, plan.candidate_hash) != (frozen_versions['baseline'], frozen_versions['candidate']):
            raise ValueError('experiment harness hashes differ from inputs')
        if configured and {t[0]: t[1:] for t in plan.tasks} != {t['id']: (t['root'], t['family']) for t in tasks}:
            raise ValueError('experiment lineage differs from configured bank')
        oracle_hash = oracle_identity(manifest_path.parent, tasks, declared_bundles)
        if plan.grader_hash != oracle_hash:
            raise ValueError('experiment oracle differs from frozen plan; create a new plan and rerun both arms')
        if plan.cohort_manifest_sha256 is not None:
            from agent_harness_lab.evaluation import cohort_history
            from agent_harness_lab.evaluation.oracle_history import bank_authority
            bank_tasks=bank_authority(manifest,export_root=manifest_path.parent)
            captured_cohorts=cohort_history.capture(manifest_path.parent,manifest,plan,bank_tasks)
            from agent_harness_lab.evaluation.integrity import read_regular
            from agent_harness_lab.evaluation.cohorts import MAX_BYTES
            precision_cohorts=read_regular(captured_cohorts['manifest_path'],MAX_BYTES)
        if plan.sequential is not None:
            from agent_harness_lab.evaluation.sequential import validate_selection
            validate_selection(manifest)
        if plan.sequential is not None and not (offline or controlled):
            raise ValueError('sequential execution requires the controlled offline or Responses path')
        if plan.sequential is not None and budget>0:
            if cgroup_root is None or image_root is None or disk_bytes is None or plan.runtime_hash!=image_sha256:
                raise ValueError('sequential execution requires the full pinned resource profile and plan runtime')
        supplied_sequential=getattr(args,'_sequential_controller',None)
        if plan.sequential is not None and supplied_sequential is not None and budget>0:
            from agent_harness_lab.evaluation.sequential_state import Controller
            from agent_harness_lab.evaluation.sequential import input_pin
            if type(supplied_sequential) is not Controller or supplied_sequential.path!=output.parent/'sequential' or output.name!='execution':
                raise ValueError('sequential controller does not own this execution directory')
            supplied_sequential.claim(plan,input_pin(manifest_hash,plan,frozen_fixtures))
        ordered = schedule(plan)
        selected_trial_ceiling=len(ordered)
        if plan.sequential is not None:
            from agent_harness_lab.evaluation.sequential import planned_trials
            selected_trial_ceiling=planned_trials(plan)
        if selected_trial_ceiling > trial_limit:
            raise ValueError('repeated plan exceeds caller trial ceiling')
    else:
        ordered = [dict(version=version, task=task['id'], repetition=0) for version in versions for task in tasks]
    if manifest.get('cohort_manifest') is not None and (plan is None or plan.cohort_manifest_sha256 is None):
        raise ValueError('cohort declarations require a frozen experiment plan pin')
    trajectories = {}
    if frozen_bundles:
        from agent_harness_lab.evaluation import trajectory_assertions
        for task in tasks:
            bundle = contained(manifest_path.parent, declared_bundles[task['id']]['path'])
            binding = trajectory_assertions.capture_policy(bundle, frozen_bundles[str(bundle)],
                dict(task_id=task['id'], root_id=task.get('root'), family=task.get('family')))
            if binding is not None:
                if not controlled: raise ValueError('trajectory assertions require the controller Responses executor')
                if plan is not None and (task.get('root'), task.get('family')) != next(t[1:] for t in plan.tasks if t[0] == task['id']):
                    raise ValueError('trajectory lineage differs from frozen experiment')
                trajectories[task['id']] = binding
    if plan is not None and plan.sequential is None and budget > 0:
        if plan.root_similarity_sha256 is not None:
            from agent_harness_lab.evaluation.root_similarity import controller_evidence
            root_similarity,root_similarity_path = controller_evidence(plan, manifest, manifest_path, export_root=manifest_path.parent)
        from agent_harness_lab.evaluation.precision import preflight as precision_preflight
        precision_report=precision_preflight(plan,cohort_manifest_json=precision_cohorts,
            trial_limit=trial_limit,budget_seconds=budget,root_similarity_evidence=root_similarity,
            root_similarity_bank=root_similarity_path)
    blocked = configured and configured['blocked_reason']
    if precision_report is not None and not precision_report['execution_allowed'] and not blocked:
        blocked='precision_target_infeasible'
    if configured and not evaluation_unchanged(Path(args.config), configured):
        blocked = 'configuration_changed_before_execution'
    available = budget - (time.monotonic() - started)
    if available <= 0:
        blocked = 'preflight_budget_exhausted'
    capability = backend.capability(seconds=min(3, available)) if (offline or sandbox_graders or controlled) and not blocked else None
    if sandbox_graders and not offline and capability is not None and capability['status'] != 'available':
        blocked = 'grader_sandbox_unavailable'
    if controlled and capability is not None and capability['status'] != 'available':
        blocked = 'inference_sandbox_unavailable'
    available = budget - (time.monotonic() - started)
    if available <= 0: blocked = 'preflight_budget_exhausted'
    version_info = dict(exit_code=None, stdout='') if offline or controlled or blocked else execute(
        [binary, '--version'], manifest_path.parent, min(timeout, available), env=native_environment(agent, inherit_env))
    config_path = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
    config = {}
    try:
        raw_config = tomllib.loads(config_path.read_text()) if agent == 'codex' and not blocked else {}
        config = {k: raw_config[k] for k in ('model', 'model_reasoning_effort')
                  if isinstance(raw_config.get(k), str)}
    except (OSError, ValueError):
        pass
    authentication = None
    if agent == 'codex' and not blocked:
        application_modules()
        from agent_harness_lab.evaluation import trial_surfaces
        authentication = trial_surfaces.codex_authentication(
            version_info['stdout'] if version_info['exit_code'] == 0 else '', os.environ)
        if authentication == 'unavailable':
            blocked = 'codex_credentials_unavailable'
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o700)
    results = dict(manifest_sha256=manifest_hash,
                   versions=frozen_versions, grader_files=grader_files,
                   fixtures=frozen_fixtures, trials=[],
                   runtime={'agent': agent, agent + '_version': version_info['stdout'].strip()[:200] if version_info['exit_code'] == 0 else 'unavailable',
                            'user_config_defaults': config, 'effective_settings_verified': False},
                   isolation=dict(status='not_executed', full_certification=False))
    if authentication is not None:
        results['runtime']['codex_authentication'] = authentication
    if agent in ('codex', 'claude'):
        results['runtime']['environment_inherited'] = inherit_env
        application_modules()
        from agent_harness_lab.evaluation import trial_surfaces
        results['isolation'] = trial_surfaces.descriptor('claude-code' if agent == 'claude' else 'codex', config)
    results['grader_bundle_digests'] = frozen_bundles
    if permission_audit is not None:results['isolation_qualification']=backend.permission_qualification()
    if precision_report is not None:
        results['precision_preflight']=precision_report
        (output/'precision-preflight.json').write_text(json.dumps(precision_report,indent=2,allow_nan=False)+'\n')
        (output/'precision-preflight.json').chmod(0o600)
    if sandbox_graders:
        results['runtime']['grader_capability'] = capability
        results['isolation'] = dict(results['isolation'],
            grader='fresh offline namespace with readonly artifacts and frozen oracle', full_certification=False)
    if configured:
        results['effective_configuration'] = dict(configured, applied_limits=dict(seconds=budget, trials=trial_limit),
                                                  requested_agent=agent, effective_runtime_settings_verified=False)
    if blocked:
        results['isolation'] = dict(status='not_executed', full_certification=False)
        results.update(status='budget_exhausted' if blocked == 'preflight_budget_exhausted' else 'blocked',
                       reason=blocked, automatic_promotion=False, elapsed_seconds=time.monotonic()-started)
        write_results(output / 'results.json', results)
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
            write_results(output / 'results.json', results)
            return 3
    if plan is not None and plan.sequential is not None:
        from agent_harness_lab.evaluation.sequential_state import Controller
        from agent_harness_lab.evaluation.sequential import input_pin
        sequential_inputs=input_pin(manifest_hash,plan,frozen_fixtures)
        sequential_controller=getattr(args,'_sequential_controller',None)
        if sequential_controller is None:
            from agent_harness_lab.evaluation.oracle_history import behavior_policies
            from agent_harness_lab.evaluation.sequential_state import records as sequential_records
            policies=behavior_policies({pin:Path(folder) for folder,pin in frozen_bundles.items()},manifest,plan)
            sequential_controller=Controller(plan,output/'sequential',started+budget,inputs_sha256=sequential_inputs,cohort_json=precision_cohorts,
                checker=lambda rows:sequential_records(plan,rows,policies))
            sequential_controller.claim(plan,sequential_inputs)
        if type(sequential_controller) is not Controller:raise ValueError('owned sequential controller required')
        sequential_controller.validate(plan,inputs_sha256=sequential_inputs)
        original_guard=sequential_controller.guard
        def sequential_guard():
            if original_guard is not None:original_guard()
            if not inputs_unchanged():raise ValueError('sequential frozen inputs changed')
        sequential_controller.guard=sequential_guard
        ordered=sequential_controller.schedule
        results.update(sequential_profile='conditional-finite-bank-sequential-v1',publication_allowed=False)
        results.update(experiment=manifest['experiment'],plan_digest=plan.digest,grader_hash=oracle_hash,
            profile=plan.profile,automatic_promotion=False)
        if plan.cohort_manifest_sha256 is not None:
            results.update(cohort_manifest_sha256=plan.cohort_manifest_sha256,cohort_qualification='current_membership_only')
        try:ready=sequential_controller.before(ordered[0],[])
        except (ValueError,OSError,TimeoutError):
            sequential_controller.stop('incomplete','root_evidence_or_authority_incomplete',[]);ready=False
        if not ready:
            results['sequential_analysis']=sequential_controller.finish([])
            results['inputs_unchanged']=inputs_unchanged()
            write_results(output / 'results.json', results)
            return 3 if results['sequential_analysis']['status']=='inconclusive' else 1

    client = None
    if controlled:
        forbidden = [output,image_root,*versions.values(),*map(Path,frozen_bundles),
                     *((manifest_path.parent/task['fixture']).resolve() for task in tasks)]
        if supplied_client is not None:
            response_runtime.credential_path(inference_settings,forbidden)
            if not isinstance(supplied_client,response_runtime.Client) or supplied_client.settings != inference_settings:
                raise ValueError('shared inference client must preserve the exact selected settings')
            client = supplied_client
        else:
            credential = response_runtime.read_credential(inference_settings,forbidden)
            client = response_runtime.Client(inference_settings,credential,started+budget)
        results['runtime'] = dict(agent='responses',model_inference=True,capability=capability,
            inference_settings_sha256=inference_hash,effective_settings_verified=False,inference=client.report())
        results['isolation'] = dict(executor='controller inference with offline commands and bounded in-memory working state',
            grader='fresh offline namespace with readonly declared artifacts and frozen oracle',full_certification=False)
    if plan:
        results['experiment'] = manifest['experiment']
        results['plan_digest'] = plan.digest
        results['grader_hash'] = oracle_hash
        if plan.cohort_manifest_sha256 is not None:
            results.update(cohort_manifest_sha256=plan.cohort_manifest_sha256,
                cohort_qualification='current_membership_only',publication_allowed=False)
    results['profile'] = plan.profile if plan else 'descriptive_smoke'
    results['automatic_promotion'] = False
    if source_sink is not None:
        results.update(controller_source_profile='ahl-controller-live-v1',publication_allowed=False)
    if trajectories:
        results.update(trajectory_profile=trajectory_assertions.PROFILE, publication_allowed=False,
            history_authority='unverified', trajectory_policy_digests={key:b['policy_sha256'] for key,b in trajectories.items()})
    observed_critical_trajectory = False
    def record_trajectory(record, binding, capture):
        nonlocal observed_critical_trajectory
        receipt = trajectory_assertions.evaluate(binding, capture)
        if receipt.get('trial_id') != record['controller_trial_id']:
            receipt = trajectory_assertions.evaluate(binding, None)
        grade = trajectory_assertions.compose(binding, record.get('grader'), receipt)
        observed_critical_trajectory |= grade['critical_violation']
        record['controller_grade'] = grade
        return grade
    application_modules()
    from agent_harness_lab.evaluation.skills import activation_receipt
    for scheduled in ordered:
        if sequential_controller is not None:
            try:
                if not sequential_controller.before(scheduled,results['trials']):break
            except (ValueError,OSError,TimeoutError):
                sequential_controller.stop('incomplete','root_evidence_or_authority_incomplete',results['trials']);break
            timeout=plan.sequential['arm_seconds']
        version, task = scheduled['version'], next(t for t in tasks if t['id'] == scheduled['task'])
        root = versions[version]
        remaining = (sequential_controller.root_deadline-time.monotonic() if sequential_controller is not None
                     else budget - (time.monotonic() - started))
        record = dict(scheduled, split=task['split'], skill_preload_requested=bool(skills and task.get('preload_skills', True)))
        record['skill_activation_receipt'] = activation_receipt([], case_id=None)
        audit_offset=len(backend.permission_reports) if permission_audit is not None else 0
        record.setdefault('root',task.get('root'))
        record.setdefault('family',task.get('family'))
        if client is not None and client.poisoned:
            record.update(status='not_executed',reason='inference_aborted_after_failure')
            results['trials'].append(record)
            continue
        if not verify_inputs(record):
            results['trials'].append(record)
            continue
        if remaining < (timeout if sequential_controller is not None else 0) or remaining <= 0:
            record['status'] = 'budget_exhausted'
            results['trials'].append(record)
            continue
        trial = output / version / task['id']
        if plan:
            trial = trial / str(scheduled['repetition'])
        workspace = trial / 'workspace'
        fixture = (manifest_path.parent / task['fixture']).resolve()
        shutil.copytree(fixture, workspace)
        if harness_paths:
            application_modules()
            from agent_harness_lab.evaluation.trial_surfaces import stage_harness
            record['workspace_surfaces'] = stage_harness(root, workspace, harness_paths)
            from agent_harness_lab.evaluation.trial_surfaces import harness_skill_availability
            availability = harness_skill_availability(record['workspace_surfaces'])
        else:
            availability = []
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
            from agent_harness_lab.evaluation.skills import skill_availability
            availability.append(skill_availability('skills', skill, {
                f.relative_to(target).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
                for f in target.rglob('*') if f.is_file()}))
        environment = None
        if offline or controlled:
            argv = task['offline_executor'] if offline else None
        else:
            argv, environment = trial_command(agent, binary, plugin if agent == 'claude' else None, skills, trial, workspace, config,
                                              native_environment(agent, inherit_env))
        prompt = ('Load these exact evaluation skill files before the task: ' + ', '.join(loaded) + '\n' if record['skill_preload_requested'] else '')
        if manifest.get('no_nested_agents', False):
            prompt += 'Do not launch child agents or nested model trials; the parent owns the evaluation budget.\n'
        prompt += 'Operate only in this fixture workspace. Do not modify external files.\n' + task['prompt']
        record['skill_availability'] = availability
        record['injected_skill_hashes'] = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in loaded}
        remaining = (sequential_controller.root_deadline-time.monotonic() if sequential_controller is not None
                     else budget - (time.monotonic() - started))
        if remaining < (timeout if sequential_controller is not None else 0) or remaining <= 0:
            record['status'] = 'budget_exhausted'
            results['trials'].append(record)
            continue
        grade_workspace = workspace
        binding, trajectory_capture = trajectories.get(task['id']), None
        source_capture = source_sink.begin(task['artifacts'],started+budget) if source_sink is not None else None
        comparison=getattr(args,'_comparison_custody',None)
        if comparison is not None:
            from agent_harness_lab.evaluation.comparison_custody import current
            if current() is not comparison:raise ValueError('current comparison custody required')
            comparison.before(scheduled,workspace,root,client)
        if controlled:
            trial_id = record.get('trial_id') or 'trial-'+str(len(results['trials'])+1)
            if binding is not None: record['controller_trial_id'] = trial_id
            try:
                run = response_runtime.run_trial(client,backend,workspace,root,prompt,task['artifacts'],trial/'artifacts',min(timeout,remaining),
                    trial_id=trial_id, trajectory=binding, **({'task_cwd': task['cwd']} if 'cwd' in task else {}))
                trajectory_capture = run.pop('_trajectory_capture', None)
                record['controller_actions'] = run.pop('controller_actions')
                grade_workspace = Path(run.pop('artifact_workspace'))
            except (response_runtime.InferenceError,ValueError,OSError,subprocess.SubprocessError) as exc:
                client.abort()
                record.update(status='budget_exhausted' if isinstance(exc,TimeoutError) else 'execution_error',error=str(exc),
                    inference=client.report(),controller_actions=client.action_report())
                if binding is not None: record_trajectory(record, binding, client.actions.trajectory)
                if source_capture is not None: source_capture.cancel()
                results['trials'].append(record)
                continue
        elif offline:
            try:
                extraction = dict(artifact_allowlist=task['artifacts']) if disk_bytes is not None else {}
                run = backend.run(argv, workspace, {'harness': root}, 'executor',
                                  Limits(seconds=min(timeout, remaining)), **extraction)
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                record.update(status='execution_error', error=str(exc))
                if source_capture is not None: source_capture.cancel()
                results['trials'].append(record)
                write_results(output / 'results.json', results)
                continue
            run['timeout'] = run['status'] == 'timeout'
        else:
            record['environment'] = environment_receipt(environment, inherit_env, output)
            run = execute(argv, workspace, min(timeout, remaining), prompt, env=environment)
        trace = trial / ('stdout.txt' if offline or controlled else 'trace.jsonl')
        trace.write_text(run.pop('stdout'))
        (trial / 'stderr.txt').write_text(run.pop('stderr'))
        record['execution'] = run
        check = {**run, 'stdout': trace.read_text()}
        if offline or controlled:
            record['status'] = 'completed' if (run['status'] == 'completed' and type(run['exit_code']) is int
                                               and run['exit_code'] == 0 and run['inputs_unchanged'] is True) else 'execution_error'
            record['usage'] = None
        else:
            record['status'], record['usage'] = trace_result(check, agent)
        record['skill_namespace'] = 'harness-trial' if agent == 'claude' else None
        if record['status'] == 'completed' and verify_inputs(record):
            remaining = (sequential_controller.root_deadline-time.monotonic() if sequential_controller is not None
                     else budget - (time.monotonic() - started))
            if remaining < (timeout if sequential_controller is not None else 0) or remaining <= 0:
                record['status'] = 'budget_exhausted'
            else:
                if offline or sandbox_graders or controlled:
                    bundle = contained(manifest_path.parent, declared_bundles[task['id']]['path'])
                    try:
                        grade = regrade_artifacts(grade_workspace, task['artifacts'], bundle, frozen_bundles[str(bundle)],
                                                  backend, limits=Limits(seconds=min(timeout, remaining)))
                        record['grader'] = grade
                        record['status'] = grade['status']
                    except (ValueError, OSError, subprocess.SubprocessError) as exc:
                        record.update(status='grader_error', error=str(exc))
                else:
                    grader = [a.replace('{workspace}', str(workspace)).replace('{trace}', str(trace)) for a in task['grader']]
                    grader_env = native_environment('grader')
                    record['grader_environment'] = environment_receipt(grader_env, False, output)
                    grade = execute(grader, manifest_path.parent, min(timeout, remaining), env=grader_env)
                    record['grader'] = grade
                    record['status'] = grading_status(grade,typed=bool(frozen_bundles))
        if binding is not None:
            grade = record_trajectory(record, binding, trajectory_capture)
            if record['status'] in ('passed', 'failed'):
                record['status'] = ('passed' if grade['outcome'] is True and grade['valid'] else
                                    'failed' if grade['outcome'] is False else 'trajectory_unknown')
        if permission_audit is not None:
            from agent_harness_lab.evaluation.permission_audit import qualification
            record['isolation_qualification']=qualification(backend.permission_reports[audit_offset:])
            results['isolation_qualification']=backend.permission_qualification()
            if controlled and results['isolation_qualification']['qualification']!='qualified':
                client.abort()
        source_inputs_unchanged = verify_inputs(record)
        if source_capture is not None:
            record['source_capture'] = source_sink.finish(source_capture,source_inputs_unchanged)
        if comparison is not None:comparison.after(record)
        results['trials'].append(record)
        write_results(output / 'results.json', results)
    results['elapsed_seconds'] = time.monotonic() - started
    if client is not None: results['runtime']['inference'] = client.report()
    results['inputs_unchanged'] = inputs_unchanged()
    if sequential_controller is not None:
        results['sequential_analysis']=sequential_controller.finish(results['trials'])
        if not results['inputs_unchanged']:
            results['sequential_analysis'].update(status='blocked',reason='frozen_inputs_changed')
    if trajectories and plan is not None and sequential_controller is None:
        from agent_harness_lab.evaluation.statistics import TrialRecord, analyze
        records = []
        for trial in results['trials']:
            grade = trial.get('controller_grade', trial.get('grader', {}))
            outcome = grade.get('outcome') if trial['status'] in ('passed', 'failed') else None
            if permission_audit is not None and trial.get('isolation_qualification')!='qualified':outcome=None
            records.append(TrialRecord(trial['trial_id'], outcome,
                'graded' if type(outcome) is bool and (grade.get('valid') is True if 'controller_grade' in trial else True) else 'unknown',
                'executable_grader', plan.baseline_hash, plan.candidate_hash, plan.grader_hash, plan.runtime_hash,
                grade.get('critical_violation') is True))
        results['trajectory_analysis'] = (analyze(plan, records, root_similarity_evidence=root_similarity,
            root_similarity_bank=root_similarity_path) if results['inputs_unchanged'] else
            dict(status='blocked', reason='frozen_inputs_changed'))
        # A live verified trajectory violation can be observed before an artifact
        # grade exists. Keep it separate from the executable grader's validity.
        if (observed_critical_trajectory and
                results['trajectory_analysis'].get('reason') == 'unknown_or_invalid_grade'):
            results['trajectory_analysis'] = dict(status='rejected', reason='critical_regression')
        results['trajectory_analysis'].update(oracle_validity='unverified', native_identity='unverified',
            root_independence='unverified', inference_scope='conditional_on_declared_root_lineage',
            history_authority='unverified', automatic_promotion=False, publication_allowed=False)
    if permission_audit is not None:
        results['isolation_qualification']=backend.permission_qualification()
        if sequential_controller is not None and results['isolation_qualification']['qualification']!='qualified' and results['sequential_analysis']['status']!='rejected':
            results['sequential_analysis'].update(status='blocked',reason='isolation_qualification_unverified')
    write_results(output / 'results.json', results)
    if sequential_controller is not None:
        status=results['sequential_analysis']['status']
        return {'supported':0,'rejected':0,'inconclusive':3,'blocked':3}.get(status,1)
    return 0 if (permission_audit is None or results['isolation_qualification']['qualification']=='qualified') and results['inputs_unchanged'] and all(t['status'] == 'passed' for t in results['trials']) else 1


def hook_main(argv):
    """Hook evaluation mode: one staged hook unit and a nonce canary in a NEW private trial workspace only."""
    application_modules()
    from agent_harness_lab import hooks_validation
    from agent_harness_lab.evaluation.sandbox import SandboxBackend
    parser = argparse.ArgumentParser(prog='run_evaluation.py hooks', formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Validate one staged hook unit beside a harmless nonce canary inside a NEW private trial workspace.\n'
        'Hooks run only there, never in the candidate, fixture or project, with no credentials in the environment.\n'
        'By default the run is sandboxed with bubblewrap: no network, a read-only trial workspace and no host home;\n'
        'without bubblewrap or user namespaces nothing executes (inconclusive). --sandbox none runs unisolated as you.',
        epilog='The candidate hooks key must pass hook_contract_v1 or nothing executes (unsupported).\n'
        'A real runtime runs only with --native-canary (one short model call); --runtime-trace reads a synthetic\n'
        'fake runtime and is never native evidence. Codex stays inconclusive while --dangerously-bypass-hook-trust is unverified.\n'
        'Writes hook-validation.json in --output. Exit 0: effective candidate or clean observed canary; 1: executed but\n'
        'failed, disabled or unmatched; 2: setup error; 3: skipped, unsupported or inconclusive.')
    parser.add_argument('--runtime', choices=('claude-code', 'codex'), required=True,
                        help='Native runtime whose hook configuration layer is validated')
    parser.add_argument('--candidate', type=Path, help='Existing candidate directory with one hook configuration and its scripts; omit for a canary-only run')
    parser.add_argument('--fixture', type=Path, help='Existing fixture directory copied into the trial; its configuration is the before state')
    parser.add_argument('--output', type=Path, required=True, help='New private directory outside every Git checkout and input; holds the trial and results')
    parser.add_argument('--binary', help='Runtime executable name or path (default: claude or codex)')
    parser.add_argument('--native-canary', action='store_true', help='Opt in to executing the real runtime once inside the trial workspace')
    parser.add_argument('--runtime-trace', choices=(hooks_validation.FAKE_TRACE,), help='Read per-handler execution records from a synthetic fake runtime; not native evidence')
    parser.add_argument('--timeout', type=float, default=120, help='Runtime deadline in seconds for the single trial run (default: %(default)s)')
    parser.add_argument('--sandbox', choices=('bubblewrap', 'none'), default='bubblewrap',
                        help='bubblewrap (default): no network, read-only workspace, no host home, never an unsandboxed fallback; '
                        'none: run as you with host files and network reachable (host_isolation: none)')
    args = parser.parse_args(argv)
    try:
        if args.native_canary and args.runtime_trace: raise ValueError('--native-canary and --runtime-trace are exclusive')
        if not 0 < args.timeout <= 1800: raise ValueError('timeout must be within (0, 1800] seconds')
        inputs = [path for path in (args.candidate, args.fixture) if path is not None]
        if any(not path.is_dir() or path.is_symlink() for path in inputs): raise ValueError('inputs must be existing directories')
        digests = {str(path): digest(path) for path in inputs}
        unit = None if args.candidate is None else hooks_validation.candidate_unit(args.runtime, args.candidate, args.fixture)
        root, document = hooks_validation.validate(
            args.output, args.runtime, execute, unit=unit, project=args.fixture or args.candidate, fixture=args.fixture,
            binary=args.binary, native=args.native_canary, trace=args.runtime_trace, timeout=args.timeout,
            protected=inputs, sandbox=SandboxBackend() if args.sandbox == 'bubblewrap' else None)
        if any(digest(Path(path)) != value for path, value in digests.items()): raise ValueError('hook trial inputs changed')
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f'hook evaluation setup error: {exc}\n')
    print(json.dumps(dict(candidate=document['candidate']['status'], canary=document['canary']['status'],
                          host_isolation=document['host_isolation'], results=str(root / hooks_validation.RESULTS))))
    return hooks_validation.exit_status(document)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Requires a JSON task manifest and separate existing baseline/candidate harness directories.\n'
        'Creates a NEW private directory with trial workspaces, artifact grades and results.json; checks input digests afterward.\n'
        'Claude keeps authentication but loads only project and managed settings, so put trial permissions in the fixture .claude/settings.json;\n'
        'Codex gets a credential-free temporary CODEX_HOME and blocks without CODEX_API_KEY (results: inherited_unverified);\n'
        'offline/Responses require supported Linux isolation.\n'
        'Read results.json for machine output; passing trials never authorize promotion. Exit 0: passing trials or supported/rejected sequential decision;\n'
        '1: failed/incomplete execution; 2: setup/input error; 3: blocked/inconclusive sequential analysis.\n'
        'Example: ahl evaluate --manifest tasks.json --baseline baseline --candidate candidate --output /tmp/ahl-evaluation\n'
        'Hook mode: ahl evaluate hooks --help validates one staged hook unit and a nonce canary in a trial workspace only.')
    if 'AHL_MANAGED_PREPARATION' in os.environ:
        parser.exit(3,'evaluation: managed preparation cannot start nested trials\n')
    if sys.argv[1:2] == ['hooks']:
        return hook_main(sys.argv[2:])
    for name in ('manifest', 'baseline', 'candidate', 'output'):
        parser.add_argument('--' + name, required=True, help={
            'manifest':'Existing JSON task manifest with tasks and fixture/grader references resolved relative to its directory',
            'baseline':'Existing baseline harness directory; its exact file-tree digest is recorded and checked after execution',
            'candidate':'Existing candidate harness directory to compare against baseline under the same task manifest',
            'output':'New private results directory; must not already exist, trial workspaces and results.json are written here'}[name])
    parser.add_argument('--config', help='Existing ahl.json restricting bank, harness scope, cooperative runtime and caps; not supported with Responses execution')
    parser.add_argument('--agent', choices=('codex', 'claude', 'offline', 'responses'), default='codex',
                        help='Executor: cooperative codex/claude, Linux offline fixture commands without inference, or controlled responses (default: %(default)s)')
    parser.add_argument('--codex', default='codex', help='Codex executable name/path for --agent codex (default: %(default)s)')
    parser.add_argument('--claude', default='claude', help='Claude Code executable name/path for --agent claude (default: %(default)s)')
    parser.add_argument('--inherit-env', action='store_true', help='Pass the host environment to native trials; records this opt-in and exposes host secrets')
    parser.add_argument('--sandbox-graders', action='store_true', help='Require fresh offline frozen artifact graders for cooperative codex/claude trials; does not isolate the agent CLI')
    parser.add_argument('--inference-settings', type=Path, help='Existing private Responses settings JSON referencing a credential file and shared request/tool caps; required only with --agent responses')
    parser.add_argument('--cgroup-root', type=Path, help='Existing delegated cgroup v2 subtree for isolated roles/graders; required with --agent responses')
    parser.add_argument('--image-root', type=Path, help='Existing extracted read-only runtime rootfs for isolated roles/graders; supply --image-sha256 with it')
    parser.add_argument('--image-sha256', help='Expected SHA-256 content pin for --image-root; root and pin must be supplied together')
    parser.add_argument('--permission-audit',choices=('permission-results-v1',),help='Require observed Linux x86_64 permission-result qualification for offline/Responses roles; unavailable for cooperative CLIs')
    parser.add_argument('--disk-bytes', type=int, help='Tmpfs data capacity in bytes for isolated roles/graders; requires --cgroup-root and is mandatory for Responses')
    parser.add_argument('--source-cache', type=Path, help='Private live-controller source journal outside all run inputs/output; requires both expected source hashes and pinned offline/Responses execution')
    parser.add_argument('--source-project-sha256', help='Expected declared project SHA-256 fingerprint; supply with --source-cache and --source-scope-sha256')
    parser.add_argument('--source-scope-sha256', help='Expected declared source-scope SHA-256 fingerprint; supply with --source-cache and --source-project-sha256')
    args = parser.parse_args()
    try:
        return evaluate(args)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f'evaluation setup error: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
