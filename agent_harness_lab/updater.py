"""Update archive installations using the bundled, checksum-verifying installer."""
import argparse
import json
from pathlib import Path
import runpy
import sys
import urllib.request

REPOSITORY = 'mihazs/agent-harness-lab'


def check(parser):
    """Read-only comparison against the latest release; works for any installation kind."""
    from .cli import version
    from .installation import FIX, key
    request = urllib.request.Request(f'https://api.github.com/repos/{REPOSITORY}/releases/latest', headers={'User-Agent': 'agent-harness-lab-update-check'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            latest = json.load(response)['tag_name'].removeprefix('v')
        newer = key(latest) > key(version())
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        parser.exit(2, f'update check error: {exc}\n')
    print(f'installed {version()}; latest published {latest}; ' + (f'update available, run: {FIX}' if newer else 'up to date'))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ahl update', description=__doc__,
        epilog='Example: ahl update --version 0.76.0; ahl update --check only reports whether a newer release is published (exit 0, nothing changed; 2 if the metadata cannot be fetched). Requires an active archive installation and its owned installation.json/launcher; source and pip installs are rejected. Downloads and verifies release assets, then updates the existing launcher/skill links and the managed region of the global instruction files (Claude CLAUDE.md, Codex AGENTS.md; nothing outside the markers is touched) through the bundled installer; --no-instructions opts out and is remembered, --instructions turns it back on. A release older than this feature updates the files only on the next ahl update. Exit 0 on success; 3 if updated but an instruction file was refused or not written (the warning gives the exact fix); invalid ownership or setup exits 2. ahl rollback install-NAME undoes an update from the backup path it prints.')
    parser.add_argument('--version', default='latest', help='Published release tag/version to download and verify, such as 0.76.0; latest resolves the latest release (default: %(default)s)')
    parser.add_argument('--instructions', action=argparse.BooleanOptionalAction, help='Write or refresh the managed region of the global instruction files; --no-instructions opts out and the choice is remembered for later updates (default: the remembered choice, otherwise on)')
    parser.add_argument('--check', action='store_true', help='Only compare the installed version with the latest published release (one HTTPS metadata request); download and change nothing')
    args = parser.parse_args(argv)
    if args.check:
        return check(parser)
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
            ['--version', args.version, '--root', str(root), '--bin-dir', state['bin_dir'],
             *([] if args.instructions is None else ['--instructions' if args.instructions else '--no-instructions'])],
            skill_dirs=state['skill_dirs'])
        return 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        parser.exit(2, f'update error: {exc}\n')


def rollback(name):
    """`ahl rollback install-NAME`: restore the launcher, skill links, receipt and instruction files an update saved."""
    from .cli import assets
    current = assets().resolve()
    try:
        installer = current / 'install.py'
        if current.parent.name != 'releases' or not (current / '.release-sha256').is_file() or installer.is_symlink() or not installer.is_file():
            raise ValueError('requires a release installation with its bundled installer')
        manifest = runpy.run_path(str(installer))['rollback'](current.parent.parent, name)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f'ahl rollback: {exc}', file=sys.stderr)
        return 2
    print(f'Restored {name}: {len(manifest["links"])} links, {len(manifest["files"])} instruction files, '
          f'{"the installation receipt" if manifest.get("receipt") else "no receipt change"}.')
    return 0
