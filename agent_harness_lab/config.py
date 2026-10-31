"""Strict project configuration data; reading it never authorizes execution."""
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import uuid
from .evaluation.integrity import contained, relative


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


def read_json(path):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.absolute().parents)):
        raise ValueError('configuration crosses a symlink')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536:
            raise ValueError('configuration must be a bounded single-link regular file')
        try:
            raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError()
            def unique(pairs):
                value = dict(pairs)
                if len(value) != len(pairs):
                    raise ValueError()
                return value
            return json.loads(raw, object_pairs_hook=unique)
        except (ValueError, UnicodeError):
            raise ValueError('malformed configuration JSON') from None


def prepare(settings):
    result = defaults()
    if not isinstance(settings, dict) or set(settings) - set(result):
        raise ValueError('unknown initialization settings')
    for key, value in settings.items():
        result[key] = {**result[key], **value} if key in ('runtime', 'bank', 'limits') and isinstance(value, dict) else value
    return validate(result)


def bank_path(project, config):
    return starter() if config['bank']['path'] == 'starter' else contained(project, config['bank']['path'])


def create(project, config):
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
