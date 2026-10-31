#!/usr/bin/env python3
"""Scan an index snapshot, leaving unstaged edits and the Git index untouched."""
from pathlib import Path, PurePosixPath
import os
import subprocess
import tempfile


def check(root):
    root = Path(root).resolve()
    scanner = root/'node_modules/.bin/jscpd'
    if not scanner.is_file():
        raise ValueError('jscpd is missing; run npm ci before committing')
    entries = subprocess.check_output(['git', 'ls-files', '--stage', '-z'], cwd=root).split(b'\0')
    with tempfile.TemporaryDirectory(prefix='ahl-jscpd-') as folder:
        snapshot = Path(folder)
        for entry in filter(None, entries):
            metadata, name = entry.split(b'\t', 1)
            mode, blob, stage = metadata.split()
            if stage != b'0':
                raise ValueError('resolve unmerged index entries before duplication checks')
            path = PurePosixPath(os.fsdecode(name))
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('unsafe index path')
            if mode not in (b'100644', b'100755'):
                continue
            if path.suffix != '.py' and str(path) != '.jscpd.json':
                continue
            target = snapshot/path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(subprocess.check_output(['git', 'cat-file', 'blob', blob.decode('ascii')], cwd=root))
        if not (snapshot/'.jscpd.json').is_file():
            raise ValueError('stage .jscpd.json before running the hook')
        return subprocess.run([str(scanner), '--config', '.jscpd.json', '--exit-code', '1', '--threshold', '0',
                               '--fail-on-empty', '--no-colors'],
                              cwd=snapshot, timeout=60).returncode


if __name__ == '__main__':
    try:
        raise SystemExit(check(Path(__file__).resolve().parents[1]))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f'duplication check: {exc}')
