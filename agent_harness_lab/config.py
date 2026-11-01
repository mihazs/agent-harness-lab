"""Strict project configuration data; reading it never authorizes execution."""
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import uuid
from .evaluation.integrity import contained, relative, read_regular, unique_object


def starter():
    return Path(__file__).parent/'evaluation/fixtures/starter-v1/bank.json'


def defaults():
    return dict(schema_version=1, profile='offline', runtime=dict(name='none', executable=None),
                bank=dict(path='starter', sha256=hashlib.sha256(starter().read_bytes()).hexdigest()),
                harness_paths=[], limits=dict(seconds=2700, trials=20), recommendation_only=True)


def validate(value):
    fields = {'schema_version', 'profile', 'runtime', 'bank', 'harness_paths', 'limits', 'recommendation_only'}
    if not isinstance(value, dict) or set(value) != fields or type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('unsupported project configuration schema')
    if value['profile'] not in ('offline', 'cooperative', 'isolated') or value['recommendation_only'] is not True:
        raise ValueError('invalid profile or recommendation policy')
    runtime = value['runtime']
    if not isinstance(runtime, dict) or set(runtime) != {'name', 'executable'} or runtime['name'] not in ('none', 'codex', 'claude'):
        raise ValueError('invalid configured runtime')
    executable = runtime['executable']
    if runtime['name'] == 'none':
        if executable is not None or value['profile'] != 'offline':
            raise ValueError('execution profiles require an explicit runtime')
    elif not isinstance(executable, str) or not executable or len(executable) > 4096 or not Path(executable).is_absolute() or any(ord(c) < 32 for c in executable):
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
    limits = value['limits']
    if not isinstance(limits, dict) or set(limits) != {'seconds', 'trials'}:
        raise ValueError('invalid configured resource caps')
    if type(limits['seconds']) not in (int, float) or not math.isfinite(limits['seconds']) or limits['seconds'] <= 0 or type(limits['trials']) is not int or limits['trials'] <= 0:
        raise ValueError('invalid configured resource caps')
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
        result[key] = {**result[key], **value} if key in ('runtime', 'bank', 'limits') and isinstance(value, dict) else value
    return validate(result)


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
    reviewed = validate_bank(bank)
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
    if value['profile'] == 'isolated':
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
    if not supported():
        raise NotImplementedError('safe configuration I/O is unavailable')
    validate(config)
    project = Path(project).absolute()
    if any(p.is_symlink() for p in (project, *project.parents)):
        raise ValueError('project path crosses a symlink')
    directory = os.open(project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
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
