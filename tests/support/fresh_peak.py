"""Peak traced memory of one call, measured in a fresh single-thread interpreter.

tracemalloc counts every thread of the process it runs in. A peak taken inside the pytest process therefore also holds
whatever another thread allocates meanwhile (a child process started with captured stdin, stdout and stderr costs three
default I/O buffers, 3 x 128 KiB on Python 3.14: 393,216 B, the excess of two CI failures), the one-off cost of a first
use, and the different modules loaded by the source and installed layouts. A fresh interpreter has one thread and has
imported the workload before tracing starts, so the number depends on the call alone.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple

import agent_harness_lab

REPOSITORY = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(agent_harness_lab.__file__).resolve().parent.parent  # the layout under test

# The workload builds its input untraced and returns the call to measure. The call runs twice under tracing: the first run
# warms lazy caches and its peak is reported as `first`; the second is the measurement, taken above what the first left.
CHILD = '''
import gc, importlib, json, sys, tracemalloc
module, name, args = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
run = getattr(importlib.import_module(module), name)(*args)
gc.collect()
tracemalloc.start()
run()
first = tracemalloc.get_traced_memory()[1]
gc.collect()
tracemalloc.reset_peak()
held = tracemalloc.get_traced_memory()[0]
value = run()
peak = tracemalloc.get_traced_memory()[1] - held
tracemalloc.stop()
print(json.dumps([value, peak, held, first]))
'''


class Peak(NamedTuple):
    value: Any
    peak: int
    held: int
    first: int

    def below(self, bound):
        """Fail naming the measured peak, the warm baseline it is taken above, the first-run peak and the bound."""
        assert self.peak < bound, f'peak {self.peak} B above a {self.held} B warm baseline (first run {self.first} B) is not below the {bound} B bound'


def fresh_peak(workload, *args):
    """Run workload(*args) in a fresh interpreter and measure the call it returns; its JSON value comes back with the peak.

    The workload is a top-level function of an importable module, and its arguments are JSON values.
    """
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(PACKAGE_ROOT), str(REPOSITORY)])}  # -P keeps cwd and the script directory out
    done = subprocess.run([sys.executable, '-P', '-c', CHILD, workload.__module__, workload.__qualname__, json.dumps(args)],
        capture_output=True, text=True, env=env, timeout=300, check=False)
    assert done.returncode == 0, done.stderr
    return Peak(*json.loads(done.stdout))
