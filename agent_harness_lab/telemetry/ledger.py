"""Locked, private append-only metadata ledger with content-idempotent ingestion."""
import fcntl
import hashlib
import json
import os
import stat
from pathlib import Path
import tempfile
from .schema import serialize


def register(record, identities, conflict):
    encoded = serialize(record)
    value = json.loads(encoded)
    identity = (value['case_id'], value['event_id'])
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    if identity in identities and identities[identity] != digest:
        raise ValueError(conflict)
    identities[identity] = digest
    return encoded, digest


class Ledger:
    def __init__(self, path, max_bytes=None, max_records=None):
        if any(value is not None and (type(value) is not int or value <= 0) for value in (max_bytes,max_records)):
            raise ValueError('ledger limits must be positive integers')
        self.path = Path(path)
        self.max_bytes, self.max_records = max_bytes, max_records

    def ingest(self, records):
        if any(parent.is_symlink() for parent in (self.path, *self.path.parents)):
            raise ValueError('ledger path crosses a symlink')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(descriptor, 'r+', encoding='utf-8') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('ledger must be an unlinked regular file')
            os.fchmod(stream.fileno(), 0o600)
            if self.max_bytes is not None and os.fstat(stream.fileno()).st_size > self.max_bytes:
                raise ValueError('ledger byte limit reached; explicitly rotate or delete retained data')
            seen, identities = set(), {}
            for index, line in enumerate(stream):
                if self.max_records is not None and index >= self.max_records or not line.endswith('\n'):
                    raise ValueError('ledger incomplete or record limit reached; retained for inspection')
                _, digest = register(json.loads(line), identities, 'conflicting event identity in ledger')
                seen.add(digest)
            count, size = 0, os.fstat(stream.fileno()).st_size
            # Validate the complete batch before appending, without retaining its bodies in memory.
            with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as additions:
                for record in records:
                    encoded, digest = register(record, identities, 'conflicting reingestion; existing ledger unchanged')
                    if digest in seen:
                        continue
                    seen.add(digest)
                    size += len((encoded + '\n').encode())
                    if (self.max_bytes is not None and size > self.max_bytes
                            or self.max_records is not None and len(identities) > self.max_records):
                        raise ValueError('ledger retention limit; existing ledger unchanged')
                    additions.write(encoded + '\n'); count += 1
                additions.seek(0)
                stream.writelines(additions)
            stream.flush(); os.fsync(stream.fileno())
            return count
