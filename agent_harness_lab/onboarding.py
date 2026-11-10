"""Read-only readiness and explicit, configuration-only initialization."""
import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import stat
import sys
from . import config


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    status: str
    required_for_profile: bool
    evidence_kind: str
    observed_value: str
    remediation: str
    capability_version: str = 'readiness-v1'


def doctor(project, profile=None, *, probe_runtime=False, proposed=None, source_cache=None, source_project_sha256=None, source_scope_sha256=None):
    from .cli import assets
    checks = []
    def add(name, status, required, evidence, observed, remediation):
        checks.append(asdict(CheckResult(name, status, required, evidence, observed, remediation)))
    add('python', 'pass' if sys.version_info >= (3, 11) else 'blocked', True, 'current_process',
        '.'.join(map(str, sys.version_info[:3])), 'Use Python 3.11 or later.')
    supported = config.supported()
    add('platform', 'pass' if supported else 'blocked', True, 'filesystem_api_presence', sys.platform,
        'Use a POSIX host with descriptor-relative, no-follow filesystem APIs; no unsafe fallback is enabled.')
    project = Path(project).absolute()
    safe = project.is_dir() and not any(p.is_symlink() for p in (project, *project.parents))
    add('project', 'pass' if safe else 'blocked', True, 'filesystem_metadata',
        'regular_directory' if safe else 'unavailable_or_symlink', 'Select an existing project directory without symlink ancestors.')
    installed = all((assets()/name).is_file() for name in ('skills/harness-evaluation/SKILL.md', 'skills/harness-evaluation/scripts/run_evaluation.py', 'skills/harness-self-improvement/SKILL.md'))
    add('assets', 'pass' if installed else 'blocked', True, 'filesystem_metadata',
        'present' if installed else 'missing', 'Reinstall AHL from a verified release or use a complete source checkout.')
    effective, invalid_config = None, False
    try:
        if not safe:
            raise ValueError()
        if not supported:
            raise NotImplementedError()
        resolved = config.resolve(project, proposed=proposed)
        effective = resolved['settings'] if resolved['settings'] is not None else config.defaults()
        add('configuration', 'pass', True, 'validated_schema', 'version_1', 'Use ahl init to preview a valid configuration.')
    except (ValueError, OSError, TypeError, KeyError, NotImplementedError) as exc:
        invalid_config = supported and not isinstance(exc, PermissionError) and safe and (proposed is not None or os.path.lexists(project/'ahl.json'))
        add('configuration', 'blocked', True, 'validated_schema', 'permission_denied' if isinstance(exc, PermissionError) else 'invalid_or_unreadable',
            'Repair ahl.json schema or read access without elevation; diagnostics do not display file contents.')
    profile = profile or (effective['profile'] if effective else 'offline')
    if profile not in ('offline', 'cooperative', 'isolated'):
        raise ValueError('invalid readiness profile')
    if effective:
        from .evaluation.task_bank import validate
        try:
            bank = config.bank_path(project, effective)
            reviewed = validate(bank, verify_contents=False)
            matched = reviewed['bank_sha256'] == effective['bank']['sha256']
            add('bank', 'pass' if matched else 'blocked', True, 'validated_bank_digest',
                'matched' if matched else 'digest_mismatch', 'Select a valid bank and explicitly review its SHA-256 digest.')
            add('bank_contents', 'not_checked', False, 'not_observed', 'not_read',
                'Use an explicit bank audit or evaluation for fixture/oracle integrity; doctor reads metadata only.')
        except (ValueError, OSError, TypeError, KeyError):
            add('bank', 'blocked', True, 'validated_bank_digest', 'invalid_or_unavailable', 'Select an existing supported task bank within the project, or the bundled starter bank.')
        runtime = effective['runtime']
        controlled=runtime['name']=='responses'
        executable = runtime.get('executable')
        present = executable is not None and Path(executable).is_file() and os.access(executable, os.X_OK)
        add('runtime_executable', 'not_checked' if controlled else 'pass' if present else 'not_checked' if profile == 'offline' else 'blocked',
            profile != 'offline' and not controlled, 'filesystem_metadata', 'not_applicable_responses' if controlled else 'present' if present else 'not_configured_or_missing',
            'Configure an explicit trusted runtime executable; no PATH search or login is performed.')
        if controlled:
            selected=config.resolve(project,dict(agent='auto',timeout=None),proposed=effective)['options']
            try:
                config.response_references(project,selected)
                ready=True
            except (ValueError,OSError,TypeError,KeyError,NotImplementedError):ready=False
            add('controlled_references','pass' if ready else 'blocked',profile!='offline','local_metadata_and_settings_schema',
                'valid' if ready else 'unavailable_or_unsafe','Select readable settings, a private credential reference, pinned image, delegated cgroup and bounded disk profile.')
            add('controlled_execution','not_checked',False,'not_observed','requires_actual_pipeline_preflight',
                'ahl improve performs the actual bounded capability check; doctor starts no worker or provider.')
    controlled=bool(effective and effective['runtime']['name']=='responses')
    add('authentication', 'unknown', profile != 'offline' and not controlled, 'not_observed', 'unverified',
        'Verify authentication separately with your selected runtime; AHL does not inspect credential files.')
    add('runtime_version', 'unknown' if probe_runtime else 'not_checked', profile != 'offline' and not controlled, 'not_observed',
        'no_verified_offline_probe', 'Verify the runtime version separately; arbitrary configured executables are not launched by doctor.')
    if profile == 'isolated' or probe_runtime:
        capability = 'not_probed'
        if probe_runtime and sys.platform == 'linux' and not controlled:
            binary = Path('/usr/bin/bwrap')
            try:
                trusted = all(p.stat().st_uid == 0 and not p.stat().st_mode & 0o022 for p in (binary, *binary.parents))
                if trusted and stat.S_ISREG(binary.stat().st_mode):
                    from .evaluation.sandbox import SandboxBackend
                    capability = SandboxBackend(str(binary)).capability()['status']
            except (ValueError, OSError):
                capability = 'unavailable'
        add('offline_namespace_probe', 'warn' if capability == 'available' else 'not_checked' if capability == 'not_probed' else 'blocked',
            False, 'bounded_explicit_probe' if probe_runtime and not controlled else 'not_observed', capability,
            'Use sandbox-check on a supported Linux host; a successful offline probe does not certify model isolation.')
        add('isolated_execution', 'blocked', profile == 'isolated', 'current_contract', 'certification_prerequisites_pending',
            'Controlled Responses workflows remain recommendation-only; audited lineage and effective runtime verification are still required.')
    from .sources.context import load
    context = load(source_cache, source_project_sha256, source_scope_sha256)
    if context is not None:
        add('source_context', 'warn', False, 'scoped_private_metadata', context['status'],
            'Confirm target-project binding and declared coverage before using source context as evidence.')
    result = dict(schema_version=1, profile=profile, status='invalid' if invalid_config else 'blocked' if any(c['required_for_profile'] and c['status'] != 'pass' for c in checks) else 'ready',
                  checks=checks, automatic_promotion=False, external_runtime_executed=False)
    if controlled:result['readiness_scope']='local_configuration_and_references_only'
    if context is not None: result['source_context'] = context
    return result


def main(argv):
    parser = argparse.ArgumentParser(prog='ahl',description='Inspect local readiness or explicitly initialize project configuration; neither command grants execution authority.')
    commands = parser.add_subparsers(dest='command', required=True,help='Select read-only doctor diagnostics or configuration-only init')
    check = commands.add_parser('doctor', help='Inspect readiness without inference or project changes',
        description='Inspect project configuration, bank and runtime metadata. Authentication and unobserved isolation remain unknown.',
        epilog='Example: ahl doctor . --json. No input is requested. Exit 0 ready, 2 invalid configuration, 3 blocked; --probe-runtime explicitly allows an offline namespace probe.')
    check.add_argument('project', nargs='?', type=Path, default=Path.cwd(),help='Existing project directory to inspect without modification (default: current directory)')
    check.add_argument('--profile', choices=('offline', 'cooperative', 'isolated'),help='Readiness requirements to inspect (default: configured profile, otherwise offline); does not change ahl.json')
    check.add_argument('--probe-runtime', action='store_true', help='Allow a bounded offline namespace probe; agent authentication stays unverified')
    check.add_argument('--json', action='store_true',help='Emit the structured readiness report as JSON on stdout instead of human-readable checks')
    check.add_argument('--source-cache', type=Path, help='Explicit private source journal; no source or credential access')
    check.add_argument('--source-project-sha256',help='Exact project identity SHA-256 to select metadata from --source-cache')
    check.add_argument('--source-scope-sha256',help='Exact authorized scope SHA-256 paired with --source-project-sha256 for the private source journal')
    init = commands.add_parser('init', help='Preview project configuration; --write explicitly creates ahl.json',
        description='Validate and preview a schema-1 ahl.json configuration with conservative offline defaults; never overwrite an existing configuration or authorize execution.',
        epilog='Examples: ahl init . --non-interactive --json; ahl init . --non-interactive --config-input settings.json --write. No file is created without --write. Exit 0 preview/created, 1 cancelled, 2 invalid input, 3 filesystem blocked.')
    init.add_argument('project', nargs='?', type=Path, default=Path.cwd(),help='Existing project directory for the preview or exclusive ahl.json creation (default: current directory)')
    init.add_argument('--non-interactive', action='store_true',help='Skip prompts and use --config-input plus conservative defaults; required when stdin is not a terminal')
    init.add_argument('--config-input', type=Path,help='Local JSON object containing configuration overrides; schema/path/runtime constraints are validated before preview or creation')
    init.add_argument('--write', action='store_true',help='Explicitly create ahl.json exclusively after validation; default is preview and existing files are preserved')
    init.add_argument('--json', action='store_true',help='Emit the preview/creation receipt and configuration as JSON on stdout')
    args = parser.parse_args(argv)
    try:
        if args.command == 'doctor':
            result = doctor(args.project, args.profile, probe_runtime=args.probe_runtime, source_cache=args.source_cache,
                source_project_sha256=args.source_project_sha256, source_scope_sha256=args.source_scope_sha256)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"{result['profile']}: {result['status']}")
                for item in result['checks']:
                    print(f"{item['check_id']}: {item['status']} ({item['observed_value']}). {item['remediation']}")
                context = result.get('source_context')
                if context is not None:
                    print(f"source context: {context['status']}; sources: {context['source_count']}; omitted: {context['omitted_sources']}; truncated: {context['truncated']}")
                    for source in context['sources']:
                        coverage, execution = source['declared_coverage'], source['execution_metadata']
                        print(f"source {source['source_sha256']}: declared coverage: {coverage['reported_completeness'] if coverage else 'unknown'}; execution identities: {execution['identities'] if execution else 'unknown'}")
            return {'ready': 0, 'invalid': 2, 'blocked': 3}[result['status']]
        if not args.project.is_dir() or os.path.lexists(args.project/'ahl.json'):
            raise ValueError('select an existing project without ahl.json; existing configuration is never overwritten')
        if not config.supported():
            raise NotImplementedError()
        settings = config.read_json(args.config_input) if args.config_input else {}
        if not isinstance(settings, dict):
            raise ValueError('initialization settings must be an object')
        if not args.non_interactive:
            if not sys.stdin.isatty():
                raise ValueError('non-TTY initialization requires --non-interactive')
            def ask(prompt, default):
                answer = input(f'{prompt} [{default}] (q cancels): ').strip()
                if answer.lower() in ('q', 'cancel'):
                    raise KeyboardInterrupt()
                return answer or default
            if 'profile' not in settings:
                settings['profile'] = ask('Profile: offline, cooperative, isolated', 'offline')
            runtime = settings.get('runtime', {})
            if not isinstance(runtime, dict):
                raise ValueError('invalid runtime settings')
            if settings['profile'] != 'offline' or runtime.get('name', 'none') != 'none':
                if 'name' not in runtime:
                    runtime['name'] = ask('Runtime: codex, claude, responses', 'codex')
                if runtime['name']=='responses':
                    for key in config.RESPONSE_OPTIONS:
                        if key=='permission_audit':continue
                        if key not in runtime:runtime[key]=ask('Controlled '+key.replace('_',' '),'')
                    if isinstance(runtime['disk_bytes'],str) and runtime['disk_bytes'].isdecimal():runtime['disk_bytes']=int(runtime['disk_bytes'])
                elif 'executable' not in runtime:
                    runtime['executable'] = ask('Trusted absolute executable path', '')
                settings['runtime'] = runtime
        proposed = config.resolve(args.project, proposed=config.prepare(settings))['settings']
        checked = doctor(args.project, 'offline', proposed=proposed)
        if checked['status'] != 'ready':
            raise ValueError('configuration or bank preflight failed; use doctor for safe diagnostics')
        result = dict(schema_version=1, status='created' if args.write else 'preview', path='ahl.json', configuration=proposed,
                      execution_authorized=False)
        if args.write:
            config.create(args.project, proposed)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print('Created ahl.json.' if args.write else 'Preview only; use --write to create ahl.json.')
            print(json.dumps(proposed, indent=2))
        return 0
    except (KeyboardInterrupt, EOFError):
        print('Operation cancelled.')
        return 1
    except (PermissionError, NotImplementedError):
        print('onboarding blocked: required filesystem access/capabilities unavailable; no elevation attempted', file=sys.stderr)
        return 3
    except (ValueError, OSError, TypeError, KeyError):
        parser.exit(2, 'onboarding error: invalid or unavailable project/configuration; existing files were preserved\n')
