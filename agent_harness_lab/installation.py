"""Offline report of where the launcher and global skill links resolve, and whether they agree."""
import json
import os
from pathlib import Path
import re
import shutil

SKILLS = ('harness-evaluation', 'harness-self-improvement')
SKILL_DIRS = ('.agents/skills', '.claude/skills')
MINIMUM = re.compile(r'Requires `ahl` (\d+\.\d+\.\d+) or later')
FIX = 'ahl update'


def key(version):
    """Sortable release key; shortcut: a prerelease suffix is ignored, releases here are plain X.Y.Z."""
    return tuple(int(part) for part in version.split('-')[0].split('.'))


def release_of(path):
    """Version of the installer-owned release directory that `path` resolves into, else None."""
    resolved = Path(path).resolve()
    for folder in (resolved, *resolved.parents):
        if folder.parent.name == 'releases' and (folder / '.release-sha256').is_file():
            return folder.name
    return None


def inspect(running_version, assets, home=None):
    """Return the installation facts and warnings; reads only local link targets and the linked SKILL.md files."""
    home, assets = Path(home) if home else Path.home(), Path(assets)
    release = release_of(assets)
    launcher = shutil.which('ahl')
    launcher_path = Path(launcher).resolve() if launcher else None
    launcher_version = release_of(launcher_path) if launcher_path else release
    directories = [home / name for name in SKILL_DIRS]
    if release:
        try:
            state = json.loads((assets.parent.parent / 'installation.json').read_text())
            directories += [Path(p) for p in state.get('skill_dirs', []) if isinstance(p, str) and os.path.isabs(p)]
        except (OSError, ValueError, AttributeError):
            pass
    links, seen = [], set()
    for folder in directories:
        for name in SKILLS:
            link = folder / name
            if link in seen or not (link.is_symlink() or link.is_dir()):
                continue
            seen.add(link)
            try:
                minimum = MINIMUM.search((link / 'SKILL.md').read_text())
            except OSError:
                minimum = None
            links.append(dict(path=str(link), resolves_to=str(link.resolve()), release=release_of(link),
                              minimum_cli=minimum.group(1) if minimum else None))
    cli = launcher_version or (running_version if release else None)
    warnings = []
    for link in links:
        if cli and link['release'] and link['release'] != cli:
            warnings.append(dict(code='launcher_skill_version_mismatch', fix=FIX,
                message=f"launcher is {cli} but {link['path']} links to release {link['release']}"))
        if cli and link['minimum_cli'] and key(cli) < key(link['minimum_cli']):
            warnings.append(dict(code='cli_below_skill_minimum', fix=FIX,
                message=f"launcher {cli} is older than the {link['minimum_cli']} that {link['path']} requires"))
    return dict(version=running_version, release_installation=release is not None, launcher=launcher,
                launcher_resolves_to=str(launcher_path) if launcher_path else None, launcher_release=launcher_version,
                skill_links=links, warnings=warnings, network_used=False)
