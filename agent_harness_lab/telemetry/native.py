"""Bounded native JSONL I/O and content-free correlation shared by pinned profiles."""
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import time
from .adapters import correlate
from .schema import event
from ..evaluation.integrity import unique_object

MAX_BYTES = 64*1024*1024
MAX_LINE_BYTES = 1024*1024


def text(value):
    if not isinstance(value, str) or not 0 < len(value) <= 4096: raise ValueError('invalid native metadata string')
    return value


def number(value, maximum=2**63-1):
    if type(value) is not int or not 0 <= value <= maximum: raise ValueError('invalid native metadata integer')
    return value


def invalid_constant(_): raise ValueError('nonfinite native JSON is unsupported')



def timestamp(value):
    value = text(value)
    try: parsed = datetime.fromisoformat(value)
    except ValueError: raise ValueError('invalid native timestamp') from None
    if parsed.tzinfo is None: raise ValueError('native timestamp requires a timezone')
    return value


def jsonl(folder, name, max_records, consume):
    raw = folder.read(name, max(0, MAX_BYTES-folder.bytes))
    if raw is None: raise ValueError('native capture is unavailable')
    stream = io.BytesIO(raw); count = 0; partial = False
    for position in range(max_records+1):
        folder.check(); line = stream.readline(MAX_LINE_BYTES+1)
        if not line: break
        count += 1
        if position == max_records or len(line) > MAX_LINE_BYTES: raise ValueError('native capture record budget exceeded')
        if not line.endswith(b'\n'): partial = True; break
        try:
            row = json.loads(line, object_pairs_hook=unique_object, parse_constant=invalid_constant)
            if not isinstance(row, dict): raise ValueError('invalid native record')
            consume(row, position)
        except RecursionError: raise ValueError('native capture nesting limit exceeded') from None
        except (UnicodeError, json.JSONDecodeError): raise ValueError('invalid native capture JSON') from None
    return hashlib.sha256(raw).hexdigest(), partial, count


def correlated(records, check):
    common = {k: records[0][k] for k in ('case_id', 'root_case_id', 'lineage_verified', 'capture_complete',
        'source_format', 'source_version', 'runtime_fingerprint')}
    for row in correlate(records):
        check()
        yield event({**common, **row})
    check()



def guarded(path, project, decoder, max_records):
    from ..config import supported
    if not supported(): raise NotImplementedError('native capture requires safe filesystem APIs')
    from ..sources.git_view import Folder
    if type(max_records) is not int or not 0 < max_records <= 100000: raise ValueError('invalid native capture record limit')
    path = Path(path).absolute()
    with Folder(path.parent, time.monotonic()+10) as folder:
        rows, summary, sha256 = decoder(folder, path.name, project, max_records)
        records = []
        for record in correlated(rows, folder.check):
            if len(records) >= max_records: raise ValueError('native normalized event budget exceeded')
            records.append(record)
        folder.verify()
    return tuple(records), summary, sha256
