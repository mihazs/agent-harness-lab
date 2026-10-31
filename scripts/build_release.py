#!/usr/bin/env python3
"""Build an allowlisted release: application code and skills, never local data."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile


def build(root, output):
    root, output = Path(root).resolve(), Path(output)
    repository = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], cwd=root, text=True).strip()).resolve()
    if repository != root:
        raise ValueError('release input must be the repository root')
    tracked = set(subprocess.check_output(['git', 'ls-files', '--cached', '-z'], cwd=root).decode().split('\0'))
    version = json.loads((root / 'package.json').read_text())['version']
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?', version):
        raise ValueError('invalid version')
    files = [root / name for name in ('ahl', 'LICENSE', 'README.md', 'package.json')]
    files += sorted((root / 'agent_harness_lab').rglob('*.py'))
    files += sorted((root / 'tools').glob('*.py'))
    fixture_root = root / 'agent_harness_lab/evaluation/fixtures'
    files += sorted(p for p in fixture_root.rglob('*') if p.is_file() and p.suffix in ('.json', '.py', '.txt') and '__pycache__' not in p.parts)
    for path in (root / 'skills').rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts and not path.name.startswith('test_') and path.suffix in ('.md', '.py', '.yaml', '.json'):
            files.append(path)
    required = ('ahl', 'LICENSE', 'README.md', 'package.json', 'install.py', 'agent_harness_lab/cli.py')
    if any(name not in tracked or not (root / name).is_file() or (root / name).is_symlink() for name in required):
        raise ValueError('required release input missing, untracked, or symlinked')
    files = [path for path in files if str(path.relative_to(root)) in tracked]

    output.mkdir(parents=True, exist_ok=True)
    archive = output / f'agent-harness-lab-{version}.tar.gz'
    prefix = f'agent-harness-lab-{version}'
    with tarfile.open(archive, 'w:gz') as stream:
        for path in sorted(set(files)):
            if not path.is_file() or path.is_symlink():
                raise ValueError(f'missing or symlinked release input: {path.relative_to(root)}')
            info = stream.gettarinfo(str(path), f'{prefix}/{path.relative_to(root)}')
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ''
            info.mode = 0o755 if path.name == 'ahl' else 0o644
            with path.open('rb') as source:
                stream.addfile(info, source)
    installer = output / 'install.py'
    shutil.copyfile(root / 'install.py', installer)
    checksums = output / 'SHA256SUMS'
    checksums.write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in (archive, installer)))
    return archive, checksums, installer


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('dist'))
    args = parser.parse_args()
    for path in build(Path(__file__).resolve().parents[1], args.output):
        print(path)
