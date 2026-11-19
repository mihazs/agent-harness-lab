"""Explicit owner-only key files for private source identities."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat

from agent_harness_lab.evaluation.integrity import open_nofollow, read_regular, unique_object
from agent_harness_lab.private_journal import sync_directory, write_bytes


def load(path):
    descriptor = open_nofollow(path)
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) & 0o077 or before.st_size != 32):
            raise ValueError('unsafe source identity key')
        key = os.read(descriptor, 33)
        after = os.fstat(descriptor)
        if (len(key) != 32 or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise ValueError('source identity key changed while reading')
        return key
    finally:
        os.close(descriptor)


def create(path):
    path = Path(path)
    write_bytes(path, secrets.token_bytes(32))
    sync_directory(path.parent)
    return hashlib.sha256(load(path)).hexdigest()


def fingerprint(key, value):
    if type(key) is not bytes or len(key) != 32: raise ValueError('identity key must be 32 bytes')
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return hmac.new(key, raw, hashlib.sha256).hexdigest()


def prepare_okf(manifest, scope_file, key_file, output):
    from agent_harness_lab.evaluation.integrity import contained
    from .okf import concept
    from .schema import SourceSpec, digest
    from .snapshot import LocalExport
    manifest, output = Path(manifest).absolute(), Path(output).absolute()
    if manifest.parent != output.parent: raise ValueError('new declaration must share the original root')
    manifest_raw = read_regular(manifest, 1024*1024)
    value = json.loads(manifest_raw, object_pairs_hook=unique_object)
    source = SourceSpec.parse(value['source'])
    if (source.schema_version, source.kind, source.provider, source.version) != (1, 'local_file', 'ai-memory-okf', '0.2'):
        raise ValueError('a legacy local memory declaration is required')
    scope_raw = read_regular(scope_file, 65536)
    scope = json.loads(scope_raw, object_pairs_hook=unique_object)
    old, declaration = LocalExport(manifest).declaration(project_sha256=source.project_sha256, scope_sha256=digest(scope))
    if old != source or declaration != value: raise ValueError('source declaration changed')
    root = contained(manifest.parent, declaration['data'])
    metadata_path = contained(root, '_meta.md')
    metadata_raw = read_regular(metadata_path, 1024*1024)
    metadata, _ = concept(metadata_raw)
    project = metadata.get('project')
    if not isinstance(project, str) or digest(project) != old.project_sha256:
        raise ValueError('memory project identity mismatch')
    key = load(key_file)
    new = dict(old.to_dict(), schema_version=2, identity_scheme='hmac-sha256-v1',
               identity_key_id=hashlib.sha256(key).hexdigest(), root_sha256=fingerprint(key, str(manifest.parent.resolve())),
               project_sha256=fingerprint(key, project), scope_sha256=fingerprint(key, scope))
    keyed_source = SourceSpec.parse(new)
    raw = (json.dumps(dict(schema_version=1, source=keyed_source.to_dict(), data=declaration['data']),
                      indent=2, allow_nan=False)+'\n').encode()
    if (load(key_file) != key or read_regular(manifest, 1024*1024) != manifest_raw
            or read_regular(scope_file, 65536) != scope_raw or read_regular(metadata_path, 1024*1024) != metadata_raw):
        raise ValueError('source identity input changed')
    write_bytes(output, raw)
    sync_directory(output.parent)
    return dict(source_sha256=keyed_source.source_key, project_sha256=keyed_source.project_sha256,
                scope_sha256=keyed_source.scope_sha256, identity_key_id=keyed_source.identity_key_id)


def main(argv):
    import argparse
    import sys
    parser = argparse.ArgumentParser(prog='ahl sources identity-key', description='Manage private keys and keyed local memory source identities.')
    commands = parser.add_subparsers(dest='operation', required=True, help='Choose an identity key operation')
    new = commands.add_parser('create', help='Create a new owner-only identity key without overwriting',
                              description='Create a new random owner-only source identity key.')
    new.add_argument('--output', required=True, help='New key file; existing files are never overwritten')
    hashed = commands.add_parser('fingerprint', help='Fingerprint one JSON value without printing it',
                                 description='Fingerprint a JSON value with a private source identity key.')
    hashed.add_argument('--key-file', required=True, help='Owner-only source identity key file')
    hashed.add_argument('--value-file', required=True, help='JSON file containing the value to fingerprint')
    prepare = commands.add_parser('prepare-okf', help='Prepare a separate keyed declaration from a legacy local memory source',
                                  description='Prepare a keyed declaration for a legacy local memory export.')
    prepare.add_argument('--manifest', required=True, help='Existing legacy local memory source declaration')
    prepare.add_argument('--scope-file', required=True, help='JSON file containing the original source scope')
    prepare.add_argument('--key-file', required=True, help='Owner-only source identity key file')
    prepare.add_argument('--output', required=True, help='New declaration alongside the original manifest')
    args = parser.parse_args(argv)
    try:
        if args.operation == 'create':
            result = dict(identity_key_id=create(args.output))
        elif args.operation == 'fingerprint':
            key = load(args.key_file)
            value = json.loads(read_regular(args.value_file, 65536), object_pairs_hook=unique_object)
            result = dict(identity_key_id=hashlib.sha256(key).hexdigest(), fingerprint=fingerprint(key, value))
        else:
            result = prepare_okf(args.manifest, args.scope_file, args.key_file, args.output)
        print(json.dumps(result, sort_keys=True))
        return 0
    except NotImplementedError:
        print('identity key operation unavailable', file=sys.stderr)
        return 3
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print('identity key operation rejected', file=sys.stderr)
        return 2
