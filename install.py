#!/usr/bin/env python3
"""Install the release CLI and skills without Git, pip, or third-party packages."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import shlex
import stat
import sys
import tarfile
import tempfile
from urllib.request import Request, urlopen

REPOSITORY = 'mihazs/agent-harness-lab'
SKILLS = ('harness-evaluation', 'harness-self-improvement')
VERSION = re.compile(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?')


def fetch(url, target):
    request = Request(url, headers={'User-Agent': 'agent-harness-lab-installer'})
    with urlopen(request, timeout=60) as response, Path(target).open('wb') as stream:
        size = 0
        while block := response.read(1024 * 1024):
            size += len(block)
            if size > 100 * 1024 * 1024:
                raise ValueError('release download exceeds 100 MiB')
            stream.write(block)


def verify(archive, checksums):
    expected = None
    for line in Path(checksums).read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip('*') == Path(archive).name:
            if expected is not None or not re.fullmatch(r'[a-fA-F0-9]{64}', parts[0]):
                raise ValueError('invalid or duplicate checksum entry')
            expected = parts[0].lower()
    if Path(archive).stat().st_size > 100 * 1024 * 1024:
        raise ValueError('release archive exceeds 100 MiB')
    with Path(archive).open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if expected != actual:
        raise ValueError('release checksum mismatch or missing entry')
    return actual


def extract(archive, destination):
    """Accept one archive root and regular files only; reject links and traversal."""
    with tarfile.open(archive, 'r:gz') as stream:
        members = []
        roots, names = set(), set()
        expanded = 0
        for member in stream:
            expanded += member.size
            if len(members) >= 1000 or member.size < 0 or expanded > 100 * 1024 * 1024:
                raise ValueError('invalid release archive size')
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or '\\' in member.name or not path.parts:
                raise ValueError('unsafe release path')
            if member.name in names or not (member.isfile() or member.isdir()):
                raise ValueError('duplicate or non-regular release member')
            names.add(member.name)
            roots.add(path.parts[0])
            members.append(member)
        if not members or len(roots) != 1:
            raise ValueError('release archive must contain one root')
        for member in members:
            target = Path(destination).joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with stream.extractfile(member) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o755 if target.name == 'ahl' else 0o644)
        return Path(destination) / roots.pop()


def owned_link(link, root, relative):
    if not link.is_symlink():
        return False
    try:
        target = link.resolve()
        version = target.relative_to(root / 'releases').parts[0]
        return bool(VERSION.fullmatch(version) and target == root / 'releases' / version / relative
                    and (root / 'releases' / version / '.release-sha256').is_file())
    except (ValueError, OSError):
        return False


def install(archive, checksums, root, bin_dir, skill_dirs):
    root, bin_dir = Path(root).resolve(), Path(bin_dir).resolve()
    archive_hash = verify(archive, checksums)
    try:
        import fcntl
    except ImportError:
        raise ValueError('release installation requires POSIX advisory file locking') from None
    if not hasattr(os, 'O_NOFOLLOW'):
        raise ValueError('release installation requires no-follow file support')
    root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(root / '.install.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
            raise ValueError('installation lock must be an owned regular file')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('another installation or update is in progress for this root') from None
        return _install_locked(archive, archive_hash, root, bin_dir, skill_dirs)
    finally:
        os.close(descriptor)


def _install_locked(archive, archive_hash, root, bin_dir, skill_dirs):
    receipt = root / 'installation.json'
    if receipt.is_symlink() or (receipt.exists() and not receipt.is_file()):
        raise ValueError('installation receipt must be a regular file')
    skill_dirs = [str(Path(folder).expanduser().resolve()) for folder in skill_dirs]
    with tempfile.TemporaryDirectory(prefix='.stage-', dir=root) as temporary:
        unpacked = extract(archive, temporary)
        version = json.loads((unpacked / 'package.json').read_text())['version']
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            raise ValueError('invalid release version')
        for relative in ('ahl', 'agent_harness_lab/cli.py', *(f'skills/{name}/SKILL.md' for name in SKILLS)):
            if not (unpacked / relative).is_file():
                raise ValueError('release is missing required files')
        if unpacked.name != f'agent-harness-lab-{version}' or Path(archive).name != f'agent-harness-lab-{version}.tar.gz':
            raise ValueError('release filename, root and version must agree')
        destination = root / 'releases' / version
        links = [(bin_dir / 'ahl', Path('ahl'))]
        links += [(Path(folder).expanduser().resolve() / name, Path('skills') / name)
                  for folder in skill_dirs for name in SKILLS]
        links = list(dict(links).items())
        for link, relative in links:
            if link.is_symlink() and not owned_link(link, root, relative):
                raise ValueError(f'refusing to replace a foreign symlink: {link}')
            if link.exists() and not link.is_symlink() and (relative == Path('ahl') or not link.is_dir()):
                raise ValueError(f'refusing to replace a non-skill directory: {link}')
        created = False
        if destination.exists():
            marker = destination / '.release-sha256'
            if not marker.is_file() or marker.read_text().strip() != archive_hash:
                raise ValueError('installed version differs; refusing to overwrite it')
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            (unpacked / '.release-sha256').write_text(archive_hash + '\n')
            unpacked.rename(destination)
            created = True
        backup = None
        moved, installed = [], []
        try:
            for link, relative in links:
                target = destination / relative
                if link.is_symlink() and link.resolve() == target:
                    continue
                link.parent.mkdir(parents=True, exist_ok=True)
                if link.exists() or link.is_symlink():
                    if backup is None:
                        (root / 'backups').mkdir(exist_ok=True)
                        backup = Path(tempfile.mkdtemp(prefix='install-', dir=root / 'backups'))
                    saved = backup / str(len(moved))
                    link.rename(saved)
                    moved.append((saved, link))
                link.symlink_to(target, target_is_directory=relative != Path('ahl'))
                installed.append(link)
            # Keep update settings in the same rollback boundary as launcher links.
            state = Path(temporary) / 'installation.json'
            state.write_text(json.dumps(dict(schema_version=1, version=version,
                bin_dir=str(bin_dir), skill_dirs=skill_dirs)) + '\n')
            state.chmod(0o600)
            state.replace(receipt)
        except BaseException:
            for link in reversed(installed):
                link.unlink()
            for saved, link in reversed(moved):
                saved.rename(link)
            if created:
                shutil.rmtree(destination)
            raise
        return destination, backup


def main(argv=None, *, skill_dirs=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Requires Python 3.11+. Downloads release metadata/assets over HTTPS unless --archive and --checksums are supplied.\n'
        'Checks the archive SHA-256 against SHA256SUMS; obtain both files from a trusted release source.\n'
        'Writes a versioned installation, ahl launcher, selected global skill links and a private installation receipt.\n'
        'Replaced links/directories are backed up; prints installation paths and shell PATH guidance.\n'
        'Exit 0: installed; 2: invalid arguments, download or installation error.\n'
        'Examples: python install.py --agent codex; python install.py --archive release.tar.gz --checksums SHA256SUMS')
    parser.add_argument('--version', default='latest', help='Release version to download when --archive is omitted, with optional v prefix (default: %(default)s)')
    parser.add_argument('--root', type=Path, default=Path.home() / '.local/share/agent-harness-lab', help='Installation root for versioned releases, backups and receipt (default: ~/.local/share/agent-harness-lab)')
    parser.add_argument('--bin-dir', type=Path, default=Path.home() / '.local/bin', help='Directory for the ahl launcher symlink; add it to PATH (default: ~/.local/bin)')
    parser.add_argument('--agent', choices=('codex', 'claude', 'both'), default='both', help='Global skill-link targets: ~/.agents/skills for codex, ~/.claude/skills for claude (default: %(default)s)')
    parser.add_argument('--archive', type=Path, help='Existing local release .tar.gz for offline installation; requires matching --checksums and skips downloads')
    parser.add_argument('--checksums', type=Path, help='Existing SHA256SUMS containing the exact --archive filename and digest; required together with --archive')
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11):
        parser.error('Python 3.11+ is required')
    if bool(args.archive) != bool(args.checksums):
        parser.error('--archive and --checksums must be supplied together')
    if args.version != 'latest' and not VERSION.fullmatch(args.version.removeprefix('v')):
        parser.error('invalid release version')
    directories = []
    if args.agent in ('codex', 'both'):
        directories.append(Path.home() / '.agents/skills')
    if args.agent in ('claude', 'both'):
        directories.append(Path.home() / '.claude/skills')
    if skill_dirs is not None:
        directories = skill_dirs
    try:
        with tempfile.TemporaryDirectory(prefix='ahl-download-') as temporary:
            archive, checksums = args.archive, args.checksums
            if archive is None:
                endpoint = 'latest' if args.version == 'latest' else 'tags/v' + args.version.removeprefix('v')
                request = Request(f'https://api.github.com/repos/{REPOSITORY}/releases/{endpoint}', headers={'User-Agent': 'agent-harness-lab-installer'})
                with urlopen(request, timeout=60) as response:
                    release = json.load(response)
                version = release['tag_name'].removeprefix('v')
                if not VERSION.fullmatch(version):
                    raise ValueError('invalid release tag')
                base = f'https://github.com/{REPOSITORY}/releases/download/v{version}/'
                archive = Path(temporary) / f'agent-harness-lab-{version}.tar.gz'
                checksums = Path(temporary) / 'SHA256SUMS'
                fetch(base + archive.name, archive)
                fetch(base + 'SHA256SUMS', checksums)
            destination, backup = install(archive, checksums, args.root, args.bin_dir, directories)
        print(f'Installed {destination.name}: {args.bin_dir / "ahl"}')
        if backup:
            print(f'Previous installations backed up at {backup}')
        bin_dir = args.bin_dir.expanduser().resolve()
        if str(bin_dir) not in os.environ.get('PATH', '').split(os.pathsep):
            print('For this POSIX shell, run: export PATH=' + shlex.quote(str(bin_dir)) + ':"$PATH"')
        print(f'Check: {shlex.quote(str(bin_dir / "ahl"))} --help')
        print('Update later: ahl update (after a release containing this command is installed)')
    except (ValueError, OSError, KeyError, tarfile.TarError) as exc:
        parser.exit(2, f'installation error: {exc}\n')


if __name__ == '__main__':
    main()
