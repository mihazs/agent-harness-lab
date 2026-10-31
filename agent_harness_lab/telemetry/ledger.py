"""Locked, private append-only metadata ledger with content-idempotent ingestion."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
from .schema import serialize


class Ledger:
    def __init__(self, path, max_bytes=64 * 1024 * 1024, max_records=100000):
        self.path = Path(path)
        self.max_bytes, self.max_records = max_bytes, max_records

    def ingest(self, records):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'r+', encoding='utf-8') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            os.fchmod(stream.fileno(), 0o600)
            if os.fstat(stream.fileno()).st_size > self.max_bytes:
                raise ValueError('ledger byte limit reached; explicitly rotate or delete retained data')
            seen, identities = set(), {}
            for index, line in enumerate(stream):
                if index >= self.max_records or not line.endswith('\n'):
                    raise ValueError('ledger incomplete or record limit reached; retained for inspection')
                encoded = serialize(json.loads(line))
                value = json.loads(encoded)
                identity = (value['case_id'], value['event_id'])
                digest = hashlib.sha256(encoded.encode()).hexdigest()
                if identity in identities and identities[identity] != digest:
                    raise ValueError('conflicting event identity in ledger')
                identities[identity] = digest
                seen.add(digest)
            additions, size = [], os.fstat(stream.fileno()).st_size
            for record in records:
                encoded = serialize(record)
                value = json.loads(encoded)
                identity = (value['case_id'], value['event_id'])
                digest = hashlib.sha256(encoded.encode()).hexdigest()
                if identity in identities and identities[identity] != digest:
                    raise ValueError('conflicting reingestion; existing ledger unchanged')
                if digest in seen:
                    continue
                seen.add(digest); identities[identity] = digest
                size += len((encoded + '\n').encode())
                if size > self.max_bytes or len(identities) > self.max_records:
                    raise ValueError('ledger retention limit; existing ledger unchanged')
                additions.append(encoded + '\n')
            stream.writelines(additions)
            stream.flush(); os.fsync(stream.fileno())
            return len(additions)
