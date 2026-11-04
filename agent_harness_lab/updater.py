"""Update archive installations using the bundled, checksum-verifying installer."""
import argparse
import json
from pathlib import Path
import runpy


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ahl update', description=__doc__,
        epilog='Example: ahl update --version 0.76.0. Requires an active archive installation and its owned installation.json/launcher; source and pip installs are rejected. Downloads and verifies release assets, then updates the existing launcher/skill links through the bundled installer. Exit 0 on success; invalid ownership or setup exits 2.')
    parser.add_argument('--version', default='latest', help='Published release tag/version to download and verify, such as 0.76.0; latest resolves the latest release (default: %(default)s)')
    args = parser.parse_args(argv)
    from .cli import assets
    current = assets().resolve()
    root = current.parent.parent
    try:
        if current.parent.name != 'releases' or not (current / '.release-sha256').is_file():
            raise ValueError('requires a release installation; use the release installer for source/pip installs')
        receipt = root / 'installation.json'
        if receipt.is_symlink() or not receipt.is_file() or receipt.stat().st_size > 65536:
            raise ValueError('missing or invalid installation receipt; rerun the release installer once')
        state = json.loads(receipt.read_text())
        if not isinstance(state, dict) or state.get('schema_version') != 1 or state.get('version') != current.name:
            raise ValueError('stale or invalid installation receipt; use the active installed launcher')
        paths = [state.get('bin_dir'), *(state.get('skill_dirs') or [])]
        if not isinstance(state.get('skill_dirs'), list) or len(paths) > 10 or any(
            not isinstance(p, str) or not Path(p).is_absolute() for p in paths):
            raise ValueError('invalid installation paths')
        launcher = Path(state['bin_dir']) / 'ahl'
        if not launcher.is_symlink() or launcher.resolve() != current / 'ahl':
            raise ValueError('launcher no longer belongs to this installation')
        installer = current / 'install.py'
        if installer.is_symlink() or not installer.is_file():
            raise ValueError('bundled installer unavailable; rerun the release installer once')
        runpy.run_path(str(installer))['main'](
            ['--version', args.version, '--root', str(root), '--bin-dir', state['bin_dir']],
            skill_dirs=state['skill_dirs'])
        return 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        parser.exit(2, f'update error: {exc}\n')
