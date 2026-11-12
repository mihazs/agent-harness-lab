"""Injectable budget clocks for the workflow, which reads time through its module-level ``time`` name."""
import random
import time
from unittest.mock import patch

from agent_harness_lab import workflow


class _Time:
    """The real ``time`` module with ``monotonic`` replaced."""
    def __init__(self, monotonic): self.monotonic = monotonic
    def __getattr__(self, name): return getattr(time, name)


def frozen():
    """Every measured duration is exactly zero, so two routes agree by construction.

    Held at the real reading when created: the CLI progress line reads the real clock against the same start.
    """
    now = time.monotonic()
    return patch.object(workflow, 'time', _Time(lambda: now))


def jittered(seed, spread=0.3):
    """The real clock plus a random delay added on every read: measured durations differ run to run, by up to ``spread``."""
    rng, delay = random.Random(seed), [0.0]
    def monotonic():
        delay[0] += rng.uniform(0, spread)
        return time.monotonic() + delay[0]
    return patch.object(workflow, 'time', _Time(monotonic))
