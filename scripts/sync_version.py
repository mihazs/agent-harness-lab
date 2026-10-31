#!/usr/bin/env python3
"""Keep Python metadata aligned with the Changesets-managed version."""
import json
from pathlib import Path
import re


def sync(root):
    root = Path(root)
    version = json.loads((root / 'package.json').read_text())['version']
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?', version):
        raise ValueError('invalid release version')
    path = root / 'pyproject.toml'
    text, count = re.subn(r'^version = "[^"]+"$', f'version = "{version}"', path.read_text(), count=1, flags=re.M)
    if count != 1:
        raise ValueError('Python version field not found')
    path.write_text(text)
    lock = root / 'package-lock.json'
    if lock.exists():
        data = json.loads(lock.read_text())
        data['version'] = version
        data.get('packages', {}).get('', {})['version'] = version
        lock.write_text(json.dumps(data, indent=2) + '\n')
    return version


if __name__ == '__main__':
    print(sync(Path(__file__).resolve().parents[1]))
