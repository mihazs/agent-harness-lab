"""Strict project configuration data; reading it never authorizes execution."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import uuid
import stat
from .evaluation.areas import parse_registry
from .evaluation.integrity import contained, open_directory_nofollow, relative, read_regular, unique_object
from . import surfaces

RESPONSE_PATHS=('inference_settings','cgroup_root','image_root')
RESPONSE_OPTIONS=(*RESPONSE_PATHS,'image_sha256','disk_bytes','permission_audit')
INVOCATION_OPTIONS=('agent','codex','claude','timeout','construction_fraction',*RESPONSE_OPTIONS,'sandbox_graders')
PLUGIN_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}@[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
MAX_PLUGIN_CHECKOUTS = 32
DEFAULT_TRIALS = 20


def plugin_checkouts(value):
    """Optional caller selection {"name@marketplace": absolute Git checkout}; validating it reads nothing."""
    if value is None: return {}
    if not isinstance(value, dict) or len(value) > MAX_PLUGIN_CHECKOUTS: raise ValueError('invalid plugin checkout selection')
    for key, path in value.items():
        if (not isinstance(key, str) or PLUGIN_ID.fullmatch(key) is None or not isinstance(path, str) or len(path) > 4096
                or any(ord(c) < 32 for c in path) or not Path(path).is_absolute() or '..' in Path(path).parts):
            raise ValueError('plugin checkouts map name@marketplace to an absolute path')
    if len({os.path.normpath(path) for path in value.values()}) != len(value): raise ValueError('plugin checkouts must be distinct')
    return dict(value)


def reference_path(project,value):
    if not isinstance(value,str) or not value or len(value)>4096 or any(ord(c)<32 for c in value) or '..' in Path(value).parts:
        raise ValueError('invalid controlled Responses reference')
    if not Path(value).is_absolute():relative(value)
    return Path(value) if Path(value).is_absolute() else Path(project)/value


def starter():
    return Path(__file__).parent/'evaluation/fixtures/starter-v1/bank.json'


def defaults():
    return dict(schema_version=1, profile='offline', runtime=dict(name='none', executable=None),
                bank=dict(path='starter', sha256=hashlib.sha256(starter().read_bytes()).hexdigest()),
                harness_paths=[], limits=dict(seconds=2700, trials=DEFAULT_TRIALS), recommendation_only=True)


def validate(value):
    if isinstance(value, dict) and {'scope', 'global', 'global_root', 'harness_scope'} & set(value):
        raise ValueError('a project configuration cannot select a harness scope; only the --scope option on the command line can include the user-level harness')
    fields = {'schema_version', 'profile', 'runtime', 'bank', 'harness_paths', 'limits', 'recommendation_only'}
    if not isinstance(value, dict) or set(value) - {'plugin_checkouts', 'areas'} != fields or type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('unsupported project configuration schema')
    if 'plugin_checkouts' in value: plugin_checkouts(value['plugin_checkouts'])
    if 'areas' in value: parse_registry(value['areas'])
    if value['profile'] not in ('offline', 'cooperative', 'isolated') or value['recommendation_only'] is not True:
        raise ValueError('invalid profile or recommendation policy')
    runtime = value['runtime']
    if not isinstance(runtime,dict):
        raise ValueError('invalid configured runtime')
    if runtime.get('name')=='responses':
        required={'name',*RESPONSE_OPTIONS}-{'permission_audit'}
        if set(runtime) not in (required,required|{'permission_audit'}):raise ValueError('invalid configured Responses fields')
        for name in RESPONSE_PATHS:reference_path(Path('/'),runtime[name])
        if (not isinstance(runtime['image_sha256'],str) or len(runtime['image_sha256'])!=64
                or any(c not in '0123456789abcdef' for c in runtime['image_sha256'])
                or runtime.get('permission_audit') not in (None,'permission-results-v1')):
            raise ValueError('invalid controlled Responses profile')
        from .evaluation.workspace import policy
        if type(runtime['disk_bytes']) is not int or runtime['disk_bytes']>2**63-1:raise ValueError('invalid configured disk bound')
        policy(runtime['disk_bytes'])
    else:
        if set(runtime)!={'name','executable'} or runtime['name'] not in ('none','codex','claude'):
            raise ValueError('invalid configured runtime')
        executable=runtime['executable']
        if runtime['name']=='none':
            if executable is not None or value['profile']!='offline':raise ValueError('execution profiles require an explicit runtime')
        elif not isinstance(executable,str) or not executable or len(executable)>4096 or not Path(executable).is_absolute() or any(ord(c)<32 for c in executable):
            raise ValueError('runtime requires an explicit absolute executable path')
    bank = value['bank']
    if not isinstance(bank, dict) or set(bank) != {'path', 'sha256'}:
        raise ValueError('invalid configured bank')
    relative(bank['path'])
    if not isinstance(bank['sha256'], str) or len(bank['sha256']) != 64 or any(c not in '0123456789abcdef' for c in bank['sha256']):
        raise ValueError('invalid configured bank digest')
    paths = value['harness_paths']
    if not isinstance(paths, list) or len(paths) > 100 or any(not isinstance(p, str) for p in paths) or len(set(paths)) != len(paths):
        raise ValueError('invalid harness path list')
    for path in [bank['path'], *paths]:
        relative(path)
        if len(path) > 4096 or any(ord(c) < 32 for c in path) or '.git' in Path(path).parts:
            raise ValueError('unsafe configured path')
    # Any classified kind may be selected (staging re-classifies it); private files (local instructions, local
    # settings, memory, caches) never are.
    if any(surfaces.classify(path).writability == 'private_excluded' for path in paths):
        raise ValueError('unsafe configured path')
    limits = value['limits']
    if not isinstance(limits, dict) or not {'seconds', 'trials'} <= set(limits) <= {'seconds', 'trials', 'construction_fraction'}:
        raise ValueError('invalid configured resource caps')
    if type(limits['seconds']) not in (int, float) or not math.isfinite(limits['seconds']) or limits['seconds'] <= 0 or type(limits['trials']) is not int or limits['trials'] <= 0:
        raise ValueError('invalid configured resource caps')
    # The construction share of the whole budget (#739): a finite number strictly between 0 and 1, as --construction-fraction takes.
    if 'construction_fraction' in limits and (type(limits['construction_fraction']) not in (int, float)
            or not 0 < limits['construction_fraction'] < 1):
        raise ValueError('invalid configured construction fraction')
    return value


def supported():
    names = ('O_DIRECTORY', 'O_NOFOLLOW', 'O_NONBLOCK', 'fchmod', 'fsync')
    return (os.name == 'posix' and all(hasattr(os, n) for n in names)
            and all(getattr(os, n) in os.supports_dir_fd for n in ('open', 'stat', 'link', 'unlink')))


def read_json(path):
    if not supported():
        raise NotImplementedError('safe configuration I/O is unavailable')
    try:
        return json.loads(read_regular(path, 65536), object_pairs_hook=unique_object)
    except (ValueError, UnicodeError):
        raise ValueError('malformed or unsafe configuration JSON') from None


def prepare(settings):
    result = defaults()
    if not isinstance(settings, dict) or set(settings) - set(result):
        raise ValueError('unknown initialization settings')
    for key, value in settings.items():
        result[key] = ({**result[key], **value} if key in ('runtime','bank','limits') and isinstance(value,dict)
            and not (key=='runtime' and value.get('name')=='responses') else value)
    return validate(result)


def execution_options(project,settings,requested,explicit):
    """Pure selection: configuration supplies defaults, never execution authority."""
    result=dict(requested);runtime=settings['runtime'] if settings else None
    if result.get('timeout') is None:result['timeout']=settings['limits']['seconds'] if settings else 2700
    if result.get('construction_fraction') is None and settings:result['construction_fraction']=settings['limits'].get('construction_fraction')
    if result.get('agent','auto')=='auto' and runtime and '--agent' not in explicit:result['agent']=runtime['name']
    if runtime and runtime['name'] in ('codex','claude') and '--'+runtime['name'] not in explicit:
        result[runtime['name']]=runtime['executable']
    if result.get('agent')=='responses' and runtime and runtime['name']=='responses':
        for name in RESPONSE_OPTIONS:
            if '--'+name.replace('_','-') not in explicit and result.get(name) is None:
                value=runtime.get(name)
                result[name]=reference_path(project,value) if name in RESPONSE_PATHS else value
    if result.get('agent')!='responses' and result.get('inference_settings') is not None:
        raise ValueError('inference settings require the Responses runtime')
    if result.get('agent')=='responses' and settings:
        overlapping_harness(project,[result[name] for name in RESPONSE_PATHS if result.get(name) is not None],settings['harness_paths'])
    return result


def resolve(project, requested=None, explicit=(), *, proposed=None):
    """Resolve validated project settings and invocation defaults without writing or granting authority."""
    path = Path(project) / 'ahl.json'
    if proposed is not None:
        settings, digest, source = validate(proposed), None, 'proposed'
    elif os.path.lexists(path):
        settings, digest = load(path)
        source = 'project'
    else:
        settings, digest, source = None, None, 'defaults'
    return dict(settings=settings, configuration_sha256=digest, source=source,
                options=execution_options(project, settings, requested, set(explicit)) if requested is not None else {},
                trial_limit=settings['limits']['trials'] if settings else DEFAULT_TRIALS)


def reference_metadata(path,*,private=False,directory=False):
    path=Path(path).absolute()
    if '..' in path.parts or any(part.is_symlink() for part in (path,*path.parents)):raise ValueError('controlled reference crosses a symlink or traversal')
    try:info=path.lstat()
    except OSError:raise ValueError('controlled Responses reference is unavailable') from None
    if ((not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode) or info.st_nlink!=1)
            or private and (info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)&0o077)):
        raise ValueError('unsafe controlled Responses reference metadata')
    attributes=[info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid]
    if not directory:attributes += [info.st_size,info.st_mtime_ns,info.st_ctime_ns]
    return dict(path=str(path),attributes=attributes)


def stable_references(records):
    """Watch fingerprint view: a refreshed credential keeps its file identity."""
    credential=records['credential']
    return dict(records,credential=dict(credential,attributes=credential['attributes'][:5]))


def overlapping_harness(project,references,harness_paths):
    """Reject references that would be copied into model-visible harness snapshots."""
    root=Path(project).absolute()
    for value in references:
        path=Path(value).absolute()
        for name in harness_paths:
            harness=root/name
            if path==harness or path.is_relative_to(harness) or harness.is_relative_to(path):
                raise ValueError('controlled Responses references must stay outside harness paths')


def response_references(project,options):
    """Local bounded reads only. Credential bytes, probes and model calls are excluded."""
    from .evaluation import responses,cgroup
    from .evaluation.permission_audit import supported as audit_supported
    from .evaluation.workspace import policy
    if sys.platform!='linux' or options.get('permission_audit') is not None and not audit_supported():
        raise ValueError('controlled Responses platform is unsupported')
    if any(options.get(name) is None for name in RESPONSE_OPTIONS if name!='permission_audit'):
        raise ValueError('controlled Responses references are incomplete')
    policy(options['disk_bytes'])
    records={name:reference_metadata(options[name],directory=name!='inference_settings',private=name=='image_root') for name in RESPONSE_PATHS}
    if stat.S_IMODE(Path(options['image_root']).stat().st_mode)!=0o700:raise ValueError('runtime image requires private mode 0700')
    cgroup.delegated(options['cgroup_root'])
    settings,pin=responses.read_settings(options['inference_settings'])
    from .cli import assets
    forbidden=[project,options['image_root'],assets()/'skills']
    forbidden.extend(options[name] for name in ('output','evaluation_store') if options.get(name) is not None)
    key=responses.credential_path(settings,forbidden)
    records['credential']=reference_metadata(key,private=True)
    if not 0<key.stat().st_size<=8192:raise ValueError('controlled credential reference has invalid size')
    records.update(settings_sha256=pin,image_sha256=options['image_sha256'],disk_bytes=options['disk_bytes'],permission_audit=options.get('permission_audit'))
    return records


def bank_path(project, config):
    return starter() if config['bank']['path'] == 'starter' else contained(project, config['bank']['path'])


def load(path):
    value = validate(read_json(path))
    digest = hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return value, digest


def evaluation_settings(path, manifest_path, manifest, versions, agent, binary):
    """Explicit config narrows an already requested run; it never grants execution."""
    import tempfile
    from .evaluation.bank_tools import export_manifest
    from .evaluation.integrity import digest, tree
    from .evaluation.task_bank import validate as validate_bank
    value, configuration_digest = load(path)
    bank = bank_path(Path(path).absolute().parent, value)
    reviewed = validate_bank(bank, registry=value.get('areas', []))
    if reviewed['bank_sha256'] != value['bank']['sha256'] or manifest.get('bank_digest') != value['bank']['sha256']:
        raise ValueError('configured bank digest does not match the evaluation')
    if manifest.get('skills') or not set(manifest.get('harness_paths', [])) <= set(value['harness_paths']):
        raise ValueError('evaluation injection exceeds configured harness paths')
    fixture_digests = {}
    with tempfile.TemporaryDirectory() as folder:
        pristine = export_manifest(bank, Path(folder)/'bank', max_trials=2*reviewed['tasks'])
        approved = {t['id']: t for t in pristine['tasks']}
        for task in manifest['tasks']:
            reference = approved.get(task['id'])
            if reference is None or any(task.get(k) != reference[k] for k in ('root', 'family', 'split', 'prompt', 'artifacts')):
                raise ValueError('task differs from configured bank')
            fixture = contained(manifest_path.parent, task['fixture'])
            bundle = manifest.get('grader_bundles', {}).get(task['id'], {})
            actual = contained(manifest_path.parent, bundle.get('path'))
            expected = Path(folder)/'bank'/pristine['grader_bundles'][task['id']]['path']
            if digest(fixture) != digest(Path(folder)/'bank'/reference['fixture']) or digest(actual) != digest(expected):
                raise ValueError('fixture or oracle differs from configured bank')
            fixture_digests[task['id']] = digest(Path(folder)/'bank'/reference['fixture'])
            if task['grader'][1:] != [str(actual/'grade.py.txt'), '{workspace}', str(actual/'expected.json')]:
                raise ValueError('configured evaluation requires the frozen artifact grader contract')
            if Path(task['grader'][0]).resolve() != Path(sys.executable).resolve():
                raise ValueError('configured evaluation requires the current Python grader interpreter')
    reason = None
    if value['runtime']['name']=='responses' and value['profile']!='offline':
        reason='configured_responses_requires_controlled_improvement'
    elif value['profile'] == 'isolated':
        reason = 'configured_isolated_runtime_unsupported'
    elif value['profile'] == 'offline':
        if agent != 'offline':
            reason = 'offline_configuration_forbids_model_execution'
        else:
            binary = None
        if agent == 'offline' and any(not set(tree(root)) <= set(value['harness_paths']) for root in versions.values()):
            raise ValueError('offline harness mount exceeds configured harness paths')
    elif agent != value['runtime']['name']:
        reason = 'configured_runtime_differs_from_requested_runtime'
    else:
        executable = value['runtime']['executable']
        if binary not in (agent, executable):
            reason = 'configured_executable_differs_from_requested_executable'
        elif not Path(executable).is_file() or not os.access(executable, os.X_OK):
            reason = 'configured_executable_unavailable'
        binary = executable
    return dict(schema_version=1, configuration=value, canonical_sha256=configuration_digest,
                bank_sha256=reviewed['bank_sha256'], binary=binary, blocked_reason=reason,
                harness_digests={name: digest(root) for name, root in versions.items()}, fixture_digests=fixture_digests,
                execution_authorized_by_configuration=False)


def evaluation_unchanged(path, receipt):
    from .evaluation.task_bank import validate as validate_bank
    try:
        current, digest = load(path)
        bank = bank_path(Path(path).absolute().parent, current)
        return digest == receipt['canonical_sha256'] and validate_bank(bank)['bank_sha256'] == receipt['bank_sha256']
    except (ValueError, OSError, TypeError, KeyError):
        return False


def create(project, config):
    from .read_only import require_writable
    require_writable(project)
    if not supported():
        raise NotImplementedError('safe configuration I/O is unavailable')
    validate(config)
    project = Path(project).absolute()
    if any(p.is_symlink() for p in (project, *project.parents)):
        raise ValueError('project path crosses a symlink')
    directory = open_directory_nofollow(project)
    temporary = '.ahl-init-'+uuid.uuid4().hex
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        owned = None
        try:
            with os.fdopen(descriptor, 'w') as stream:
                owned = os.fstat(stream.fileno())
                os.fchmod(stream.fileno(), 0o600)
                json.dump(config, stream, indent=2, allow_nan=False); stream.write('\n')
                stream.flush(); os.fsync(stream.fileno())
            # link is atomic and exclusive: an existing file is never replaced.
            require_writable(project)
            os.link(temporary, 'ahl.json', src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            os.fsync(directory)
        except BaseException:
            try:
                current = os.stat('ahl.json', dir_fd=directory, follow_symlinks=False)
                if owned is not None and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                    os.unlink('ahl.json', dir_fd=directory)
            except FileNotFoundError:
                pass
            raise
        finally:
            os.unlink(temporary, dir_fd=directory)
    finally:
        os.close(directory)
