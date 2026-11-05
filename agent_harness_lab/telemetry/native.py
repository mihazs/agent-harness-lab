"""Bounded native JSONL I/O and content-free correlation shared by pinned profiles."""
from datetime import datetime
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from .adapters import correlate
from .schema import event
from ..evaluation.integrity import unique_object

def text(value):
    if not isinstance(value, str) or not value: raise ValueError('invalid native metadata string')
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


def optional_limit(value):
    if value is not None and (type(value) is not int or value <= 0): raise ValueError('invalid native capture limit')
    return value


def lines(folder, name, *, max_bytes=None, max_line_bytes=None):
    optional_limit(max_bytes); optional_limit(max_line_bytes)
    with folder.open(name, max_bytes) as opened:
        if opened is None: raise ValueError('native capture is unavailable')
        with os.fdopen(os.dup(opened[0]), 'rb') as stream:
            line = bytearray()
            while True:
                folder.check()
                part = stream.readline(65536 if max_line_bytes is None else min(65536, max_line_bytes-len(line)+1))
                if not part:
                    if line: yield bytes(line)
                    break
                folder.bytes += len(part); line.extend(part)
                if max_line_bytes is not None and len(line) > max_line_bytes: raise ValueError('native capture line limit exceeded')
                if part.endswith(b'\n'):
                    yield bytes(line)
                    line.clear()


class WalkLimit(Exception):
    """A bounded directory walk stopped at a depth or visited-entry bound."""
    def __init__(self, reason, name):
        super().__init__(reason); self.reason, self.name = reason, name


def listing(folder, name, budget=None):
    """List one directory without following links; a budget counts every visited entry."""
    descriptor = folder.directory(name)
    try:
        folder.observe(name, os.fstat(descriptor)); found = []
        with os.scandir(descriptor) as entries:
            for entry in entries:
                folder.entries += 1; folder.check()
                if budget is not None:
                    budget[0] -= 1
                    if budget[0] < 0: raise WalkLimit('entries', name)
                found.append((entry.name, entry.stat(follow_symlinks=False)))
        return sorted(found, key=lambda item: item[0])
    finally: os.close(descriptor)


def bounded_walk(folder, name, max_depth, max_entries):
    """Iterative depth-first walk of regular single-link files; links and special files are skipped and counted."""
    budget, stack = [max_entries], [(name, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > max_depth: raise WalkLimit('depth', current)
        directories = []
        for entry, info in listing(folder, current, budget):
            path = str(PurePosixPath(current, entry))
            if stat.S_ISDIR(info.st_mode): directories.append((path, depth+1))
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1: yield path
            else: folder.unsafe_entries += 1
        stack.extend(reversed(directories))


def jsonl(folder, name, max_records, consume, **limits):
    optional_limit(max_records)
    sha256 = hashlib.sha256(); count = 0; partial = False
    with closing(lines(folder, name, **limits)) as stream:
        for position, line in enumerate(stream):
            count += 1
            sha256.update(line)
            if max_records is not None and position == max_records: raise ValueError('native capture record budget exceeded')
            if not line.endswith(b'\n'): partial = True; break
            try:
                row = json.loads(line, object_pairs_hook=unique_object, parse_constant=invalid_constant)
                if not isinstance(row, dict): raise ValueError('invalid native record')
                consume(row, position)
            except RecursionError: raise ValueError('native capture nesting limit exceeded') from None
            except (UnicodeError, json.JSONDecodeError): raise ValueError('invalid native capture JSON') from None
    return sha256.hexdigest(), partial, count


def correlated(records, check):
    common = {k: records[0][k] for k in ('case_id', 'root_case_id', 'lineage_verified', 'capture_complete',
        'source_format', 'source_version', 'runtime_fingerprint')}
    for row in correlate(records, max_pending=len(records)):
        check()
        yield event({**common, **row})
    check()



def guarded(path, project, decoder, max_records, *, deadline=None, **limits):
    from ..config import supported
    if not supported(): raise NotImplementedError('native capture requires safe filesystem APIs')
    from ..sources.git_view import Folder
    optional_limit(max_records)
    path = Path(path).absolute()
    with Folder(path.parent, deadline, limit_total_bytes=False, limit_tree=False) as folder:
        rows, summary, sha256 = decoder(folder, path.name, project, max_records, **limits)
        records = []
        for record in correlated(rows, folder.check):
            if max_records is not None and len(records) >= max_records: raise ValueError('native normalized event budget exceeded')
            records.append(record)
        folder.verify()
    return tuple(records), summary, sha256
