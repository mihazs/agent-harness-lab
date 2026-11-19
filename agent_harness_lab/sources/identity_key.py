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


def main(argv):
    import argparse
    import sys
    parser = argparse.ArgumentParser(prog='ahl sources identity-key')
    commands = parser.add_subparsers(dest='operation', required=True)
    new = commands.add_parser('create', help='Create a new owner-only identity key without overwriting')
    new.add_argument('--output', required=True)
    hashed = commands.add_parser('fingerprint', help='Fingerprint one JSON value without printing it')
    hashed.add_argument('--key-file', required=True)
    hashed.add_argument('--value-file', required=True)
    args = parser.parse_args(argv)
    try:
        if args.operation == 'create':
            result = dict(identity_key_id=create(args.output))
        else:
            key = load(args.key_file)
            value = json.loads(read_regular(args.value_file, 65536), object_pairs_hook=unique_object)
            result = dict(identity_key_id=hashlib.sha256(key).hexdigest(), fingerprint=fingerprint(key, value))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, RecursionError):
        print('identity key operation rejected', file=sys.stderr)
        return 2
