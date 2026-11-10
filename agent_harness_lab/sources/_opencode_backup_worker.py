"""Resource-limited child for one OpenCode backup query; private IPC, no CLI entrypoint."""
import json
import math
import resource
import sqlite3
import struct
import sys
import time


def main():
    # Native SQLite parsing occurs only after the hard process limits are in force.
    for kind, value in ((resource.RLIMIT_AS, 256*1024*1024), (resource.RLIMIT_CPU, 2),
                        (resource.RLIMIT_FSIZE, 0), (resource.RLIMIT_NOFILE, 16)):
        resource.setrlimit(kind, (value, value))
    stream = sys.stdin.buffer
    size_bytes = stream.read(4)
    if len(size_bytes) != 4: raise ValueError('invalid request')
    size = struct.unpack('!I', size_bytes)[0]
    if size > 16384: raise ValueError('invalid request')
    request = json.loads(stream.read(size))
    raw = stream.read(16*1024*1024+1)
    if len(raw) > 16*1024*1024: raise ValueError('backup exceeds limit')
    session, project = request['session'], request['project']
    connection = sqlite3.connect(':memory:')
    try:
        connection.enable_load_extension(False)
        connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024*1024)
        connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 256)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 4096)
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.execute('PRAGMA temp_store=MEMORY')
        connection.deserialize(raw)
        allowed = frozenset(('id', 'project_id', 'parent_id', 'time_archived'))
        def authorize(action, arg1, arg2, database, trigger):
            if action == sqlite3.SQLITE_SELECT: return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ and arg1 == 'session_v2' and arg2 in allowed and database == 'main' and trigger is None:
                return sqlite3.SQLITE_OK
            if (request.get('messages') is True and action == sqlite3.SQLITE_READ and arg1 == 'session_message'
                    and arg2 == 'session_id' and database == 'main' and trigger is None): return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        connection.set_authorizer(authorize)
        deadline = time.monotonic()+1
        ticks = 0
        def progress():
            nonlocal ticks
            ticks += 1
            return ticks > 400 or time.monotonic() >= deadline
        connection.set_progress_handler(progress, 1000)
        rows = connection.execute('SELECT id, project_id, parent_id, time_archived FROM session_v2 '
            'WHERE id = ? AND project_id = ? LIMIT 2', (session, project)).fetchall()
        if len(rows) != 1 or rows[0][0] != session or rows[0][1] != project: raise ValueError('selection mismatch')
        parent, archived = rows[0][2:]
        if parent is not None and (not isinstance(parent, str) or not parent.startswith('ses') or len(parent) > 256 or
                any(c.isspace() for c in parent) or parent == session): raise ValueError('invalid parent')
        if archived is not None and (type(archived) not in (int, float) or abs(archived) > 8640000000000000 or
                not math.isfinite(archived)): raise ValueError('invalid archive')
        result = {'parent': parent, 'archived': archived}
        if request.get('messages') is True:
            count = 0
            for _ in connection.execute('SELECT 1 FROM session_message WHERE session_id = ? COLLATE BINARY LIMIT 100001', (session,)):
                count += 1
                if count > 100000: raise ValueError('message count bound exceeded')
            result['message_count'] = count
        sys.stdout.write(json.dumps(result, allow_nan=False))
    finally:
        connection.close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        sys.exit(1)
