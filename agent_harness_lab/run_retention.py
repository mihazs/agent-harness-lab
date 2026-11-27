"""Automatic deletion of old run outputs in AHL's own state directory.

Only `run-*` folders that `ahl improve` created under state/runs/<project> are candidates. The newest AHL_RUN_KEEP (default 10)
always stay, as does the run the incremental pointer or record names; of the rest, folders untouched for AHL_RUN_RETENTION_DAYS (default
30) are deleted. AHL_RUN_RETENTION_DAYS=off disables it. An explicit --output is never touched. Standard library only.
"""
import os
from pathlib import Path
import re
import shutil
import time

KEEP, DAYS = 10, 30
POINTER, RECORD = 'latest-run.json', 'harness-states.json'  # incremental.py's files: every run they name stays
_RUN = re.compile(r'run-[A-Za-z0-9_-]{1,80}')


def _setting(name, default):
    raw = os.environ.get(name, '').strip().lower()
    if raw in ('off', 'none', 'false', '0') and name == 'AHL_RUN_RETENTION_DAYS': return None
    try: return max(0, int(raw)) if raw else default
    except ValueError: return default


def prune(store, current=None, now=None):
    """Delete old run folders in `store`; return the names deleted. Never raises: retention must not fail a run."""
    days, keep = _setting('AHL_RUN_RETENTION_DAYS', DAYS), _setting('AHL_RUN_KEEP', KEEP)
    if days is None: return []
    store, now = Path(store), time.time() if now is None else now
    try:
        protected = {Path(current).name} if current else set()
        for name in (POINTER, RECORD):
            try: protected |= set(_RUN.findall((store / name).read_text()))
            except (OSError, UnicodeError): pass
        runs = sorted((entry for entry in store.iterdir() if _RUN.fullmatch(entry.name) and entry.is_dir() and not entry.is_symlink()),
                      key=lambda entry: entry.stat().st_mtime, reverse=True)
    except OSError:
        return []
    deleted = []
    for entry in runs[keep:]:
        try:
            if entry.name in protected or now - entry.stat().st_mtime < days * 86400: continue
            shutil.rmtree(entry)
            deleted.append(entry.name)
        except OSError:
            continue
    return deleted
