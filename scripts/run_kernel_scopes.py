"""Run every collected kernel canary in bounded, disposable systemd scopes."""
from pathlib import Path
import re
import subprocess
import sys
import uuid


def selections(output, test_file):
    """Group collected tests by class, splitting the slow regression class by method."""
    summary = re.search(r'^(\d+) tests? collected\b', output, re.MULTILINE)
    nodes = [line for line in output.splitlines() if line.startswith('tests/integration/sandbox/test_sandbox_kernel.py::')]
    if summary is None or len(nodes) != int(summary.group(1)) or len(set(nodes)) != len(nodes):
        raise ValueError('kernel test collection is incomplete')
    groups = []
    for node in nodes:
        parts = node.split('::')
        if len(parts) != 3 or not re.fullmatch(r'Test\w+', parts[1]) or not re.match(r'^test_\w+(?:\[|$)', parts[2]):
            raise ValueError('unsupported kernel test collection')
        selection = f'{test_file}::{parts[1]}'
        # The regression-draft class exceeds the 90-second cap as a group.
        if parts[1] == 'TestRegressionDraftKernel':
            selection += '::' + parts[2].split('[', 1)[0]
        if selection not in groups:
            groups.append(selection)
    if not groups:
        raise ValueError('kernel test collection is empty')
    return groups


def run(root, python, test_filter=''):
    root = Path(root)
    test_file = root / 'tests/integration/sandbox/test_sandbox_kernel.py'
    pytest = [python, '-m', 'pytest', '-c', str(root / 'pyproject.toml')]
    def scoped(args):
        return ['systemd-run', '--user', '--scope', '--collect', '--quiet', '--expand-environment=no',
                f'--unit=ahl-sandbox-test-{uuid.uuid4()}', '--property=Delegate=cpu memory pids',
                '--property=TasksMax=64', '--property=MemoryMax=512M', '--property=RuntimeMaxSec=90',
                '--', *pytest, *args]
    if test_filter:
        groups = [[str(test_file), '-k', test_filter]]
    else:
        collected = subprocess.run(scoped([str(test_file), '--collect-only', '-q']), text=True, capture_output=True, check=False)
        if collected.returncode:
            print(collected.stdout + collected.stderr, file=sys.stderr)
            return collected.returncode
        try:
            groups = [[selection] for selection in selections(collected.stdout, test_file)]
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 2
    for group in groups:
        command = scoped([*group, '-v'])
        result = subprocess.run(command, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(run(sys.argv[1], sys.argv[2], sys.argv[3]))
