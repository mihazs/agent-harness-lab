"""Reproducible CLI startup, diagnose throughput and diagnose memory measurements on synthetic traces.

Standard library only, POSIX only (per-child peak RSS comes from wait4). Results are indicative: they
describe one machine under whatever load it had, and are meant for before/after comparison on that machine.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
STARTUP_COMMANDS = ((), ('diagnose',), ('mine',), ('analyze',), ('doctor',), ('sources',), ('regression-review',), ('experiment-history',))
SIZES = (20_000, 80_000, 240_000)
SEED = 1
TOOLS = ('read_file', 'search', 'list_dir', 'run_tests', 'edit_file')
POOL = 6  # distinct fingerprints per tool; a small pool makes repeated identical reads occur


def case_events(case, rng):
    """One case: tool call/result pairs and assistant turns; about one call in five mutates state."""
    pick = lambda options: options[int(rng() * len(options))]
    revision, rows, calls = 0, [], 0
    for _ in range(2 + int(rng() * 18)):
        if rng() < 0.2:
            rows.append(dict(activity='assistant'))
            continue
        calls += 1
        tool, mutation = pick(TOOLS), rng() < 0.2
        base = dict(call_id=f'call-{calls}', tool_name=tool, state_revision=f'{case}-state-{revision}')
        rows.append(dict(base, activity='tool_call', tool_effect='mutation' if mutation else 'read_only'))
        revision += mutation
        rows.append(dict(base, activity='tool_result', state_revision=f'{case}-state-{revision}',
                         input_fingerprint=f'{tool}-input-{int(rng() * POOL)}',
                         result_fingerprint=f'{tool}-result-{int(rng() * POOL)}'))
    rows.append(dict(activity='turn_stop'))
    return [dict(row, case_id=case, event_id=str(number)) for number, row in enumerate(rows, 1)]


def synthetic_events(count, seed=SEED):
    """Exactly count normalized events; random() is the one generator method whose sequence Python guarantees."""
    rng, emitted, case = random.Random(seed).random, 0, 0
    while emitted < count:
        case += 1
        for row in case_events(f'case-{case}', rng)[:count - emitted]:
            emitted += 1
            yield row


def write_events(path, count, seed=SEED):
    with Path(path).open('w') as stream:
        for row in synthetic_events(count, seed):
            stream.write(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n')
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(argv):
    """Run one child process; return wall seconds, that child's peak RSS in bytes and its exit status."""
    started = time.perf_counter()
    child = subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _, status, usage = os.wait4(child.pid, 0)
    elapsed = time.perf_counter() - started
    child.returncode = os.waitstatus_to_exitcode(status)  # wait4 reaped it; keep Popen from warning about a live child
    return elapsed, usage.ru_maxrss * (1 if sys.platform == 'darwin' else 1024), child.returncode


def spread(values, scale=1.0, digits=1):
    ordered = sorted(values)
    return dict(median=round(statistics.median(ordered) * scale, digits), min=round(ordered[0] * scale, digits),
                max=round(ordered[-1] * scale, digits), runs=len(ordered))


def startup(runs):
    commands = {'(bare interpreter)': [sys.executable, '-c', 'pass']}
    commands.update({' '.join(command) or '(no subcommand)': [sys.executable, '-m', 'agent_harness_lab', *command, '--help']
                     for command in STARTUP_COMMANDS})
    result = {}
    for name, argv in commands.items():
        run(argv)  # untimed: compiles bytecode and warms the file cache
        timings = []
        for _ in range(runs):
            elapsed, _, status = run(argv)
            if status:
                raise RuntimeError(f'{name} exited with status {status}')
            timings.append(elapsed)
        result[name] = spread(timings, 1000)
    return result


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def diagnose(sizes, repeats, seed=SEED):
    result = {}
    with tempfile.TemporaryDirectory(prefix='ahl-perf-') as scratch:
        for count in sizes:
            events = Path(scratch) / f'events-{count}.jsonl'
            input_sha256 = write_events(events, count, seed)
            seconds, memory, outputs = [], [], set()
            for repeat in range(repeats):
                out = Path(scratch) / f'out-{count}-{repeat}'
                elapsed, peak, status = run([sys.executable, '-m', 'agent_harness_lab', 'diagnose', '--events', str(events), '--output', str(out)])
                if status:
                    raise RuntimeError(f'diagnose exited with status {status} at {count} events')
                seconds.append(elapsed)
                memory.append(peak)
                outputs.add((digest(out / 'findings.json'), digest(out / 'report.md')))
            size = events.stat().st_size
            result[str(count)] = dict(
                events=count, input_bytes=size, input_sha256=input_sha256, seconds=spread(seconds, 1, 3),
                peak_rss_mb=spread(memory, 1 / 1e6), input_mb_per_second=round(size / 1e6 / statistics.median(seconds), 2),
                outputs_identical_across_runs=len(outputs) == 1,
                findings_sha256=sorted(outputs)[0][0], report_sha256=sorted(outputs)[0][1])
    return result


def commit():
    try:
        return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def environment():
    return dict(python=platform.python_version(), implementation=platform.python_implementation(), platform=platform.platform(),
                cpu_count=os.cpu_count(), load_average_before=[round(value, 2) for value in os.getloadavg()], commit=commit(),
                generator_seed=SEED, indicative='one machine under its current load; compare runs on the same machine only')


def check(report, budgets):
    """Budget violations as readable lines. Startup is a ratio to the bare interpreter so machine speed mostly cancels."""
    problems = []
    control = report['startup']['(bare interpreter)']['median']
    for name, limit in budgets.get('startup_ratio_max', {}).items():
        ratio = report['startup'][name]['median'] / control
        if ratio > limit:
            problems.append(f'startup {name}: {ratio:.1f}x the bare interpreter exceeds {limit}x')
    for count, limits in budgets.get('diagnose', {}).items():
        measured = report['diagnose'].get(count)
        if measured is None:
            problems.append(f'diagnose {count} events was not measured')
            continue
        for key, label in (('seconds', 'seconds'), ('peak_rss_mb', 'MB')):
            if measured[key]['median'] > limits[key + '_max']:
                problems.append(f'diagnose {count} events: median {key} {measured[key]["median"]} exceeds {limits[key + "_max"]} {label}')
        if not measured['outputs_identical_across_runs']:
            problems.append(f'diagnose {count} events: outputs differ between runs')
    return problems


def render(report):
    lines = ['Startup (--help), ms: median / min / max']
    lines += [f'  {name:<22} {item["median"]:>7} {item["min"]:>7} {item["max"]:>7}' for name, item in report['startup'].items()]
    lines.append('diagnose: events, seconds (median), peak RSS MB (median), input MB/s, identical outputs')
    lines += [f'  {item["events"]:>8} {item["seconds"]["median"]:>8} {item["peak_rss_mb"]["median"]:>8} {item["input_mb_per_second"]:>8} '
              f'{item["outputs_identical_across_runs"]}' for item in report['diagnose'].values()]
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--runs', type=int, default=15, help='Timed runs per startup command (default: %(default)s)')
    parser.add_argument('--repeats', type=int, default=3, help='Runs per diagnose size (default: %(default)s)')
    parser.add_argument('--sizes', default=','.join(map(str, SIZES)), help='Comma-separated event counts (default: %(default)s)')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/perf-bench/report.json', help='JSON report path (default: %(default)s)')
    parser.add_argument('--check', type=Path, help='Budgets JSON; exit 1 when a result exceeds one')
    args = parser.parse_args(argv)
    sizes = tuple(int(value) for value in args.sizes.split(','))
    if min(args.runs, args.repeats, *sizes) < 1:
        parser.error('--runs, --repeats and --sizes must be positive')
    report = dict(environment=environment(), startup=startup(args.runs), diagnose=diagnose(sizes, args.repeats))
    report['environment']['load_average_after'] = [round(value, 2) for value in os.getloadavg()]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(render(report))
    print(f'report: {args.output}')
    problems = check(report, json.loads(args.check.read_text())) if args.check else []
    for problem in problems:
        print('BUDGET EXCEEDED: ' + problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
