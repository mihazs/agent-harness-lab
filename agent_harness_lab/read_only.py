"""Repository-owner opt-out for operations that can change a target project."""
from pathlib import Path


MARKER = '.ahl-read-only'


class FrozenProjectError(ValueError):
    pass


def frozen_root(project):
    project = Path(project).resolve()
    marked = None
    for root in (project, *project.parents):
        try:
            (root / MARKER).lstat()
        except (FileNotFoundError, NotADirectoryError):
            continue
        marked = root
    return marked


def require_writable(project):
    if frozen_root(project) is not None:
        raise FrozenProjectError(f'project is read-only because {MARKER} exists')


def require_external_output(project, output):
    root = frozen_root(project)
    if root is not None and Path(output).resolve().is_relative_to(root):
        raise FrozenProjectError(f'{MARKER} requires output outside the frozen directory')


def require_safe_cli_destinations(argv):
    # These options select mutable outputs or stores across the CLI dispatchers.
    options = {'--output', '--destination', '--export', '--store', '--cache', '--history-store',
               '--evaluation-store', '--backup-root'}
    for index, argument in enumerate(argv):
        if argument == '--': break
        option, separator, value = argument.partition('=')
        if option.startswith('--') and any(name.startswith(option) for name in options):
            if not separator:
                value = argv[index + 1] if index + 1 < len(argv) else ''
            if value and not value.startswith('--'):
                require_writable(Path(value))
