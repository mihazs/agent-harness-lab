"""Run the normal test task in an opt-in systemd memory scope."""
import argparse
import shutil
import subprocess
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--memory', required=True, help='systemd MemoryMax value, for example 4G')
    parser.add_argument('--python', default=sys.executable, help='Python executable for task test')
    args = parser.parse_args(argv)
    systemd_run = shutil.which('systemd-run')
    if systemd_run is None:
        print('Skipping capped tests: systemd-run is unavailable.', file=sys.stderr)
        return 0
    return subprocess.run([systemd_run, '--user', '--scope', '--collect', '--quiet',
                           f'--property=MemoryMax={args.memory}', '--', 'task', 'test', f'PYTHON={args.python}'],
                          check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
