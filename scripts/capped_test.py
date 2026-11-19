"""Run the normal test task in an opt-in CPU and memory limited systemd scope."""
import argparse
import shutil
import subprocess
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--memory', required=True, help='systemd MemoryMax value, for example 4G')
    parser.add_argument('--cpu', default='40%', help='systemd CPUQuota value (default: %(default)s)')
    parser.add_argument('--workers', type=int, default=0, help='Number of pytest workers; zero runs sequentially (default: %(default)s)')
    parser.add_argument('--python', default=sys.executable, help='Python executable for task test')
    args = parser.parse_args(argv)
    if args.workers < 0: parser.error('--workers must be nonnegative')
    systemd_run = shutil.which('systemd-run')
    if systemd_run is None:
        print('Cannot run capped tests: systemd-run is unavailable.', file=sys.stderr)
        return 2
    return subprocess.run([systemd_run, '--user', '--scope', '--collect', '--quiet',
                           f'--property=MemoryMax={args.memory}', f'--property=CPUQuota={args.cpu}',
                           '--', 'task', 'test', f'PYTHON={args.python}', f'TEST_WORKERS={args.workers}'],
                          check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
