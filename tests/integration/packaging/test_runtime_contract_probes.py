"""Opt-in probes of the REAL `claude` and `codex` CLIs: flags and exit codes only, zero model calls (#685).

Skipped unless AHL_RUNTIME_CONTRACT_PROBES=1 (`task test:runtime-contracts`), and a runtime that is not on PATH is
skipped individually; a skip is reported as skipped and never counts as a pass. Only `--version` and `--help` are
run, with a temporary HOME so no real configuration or credential is read. Trace-format compatibility cannot be
probed without a model call: the pinned trace profiles are printed next to the installed versions for review.
"""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from agent_harness_lab import hooks_validation
from agent_harness_lab.telemetry.claude_native import VERSION as CLAUDE_TRACE_PROFILE
from agent_harness_lab.telemetry.codex_rollout import VERSION as CODEX_TRACE_PROFILE

pytestmark = pytest.mark.skipif(os.environ.get('AHL_RUNTIME_CONTRACT_PROBES') != '1',
                                reason='opt-in real-runtime contract probes: set AHL_RUNTIME_CONTRACT_PROBES=1')
RUNTIMES = {'claude-code': ('claude', [], CLAUDE_TRACE_PROFILE), 'codex': ('codex', ['exec'], CODEX_TRACE_PROFILE)}


def probe(binary, *args, home):
    env = {'PATH': os.environ['PATH'], 'HOME': str(home), 'XDG_CONFIG_HOME': str(home / 'config'), 'TMPDIR': str(home)}
    return subprocess.run([binary, *args], env=env, cwd=home, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)


@pytest.mark.parametrize('runtime', RUNTIMES)
def test_real_runtime_accepts_the_flags_ahl_passes_and_reports_a_version(runtime, tmp_path, record_property):
    name, subcommand, profile = RUNTIMES[runtime]
    binary = shutil.which(name)
    if binary is None:
        pytest.skip(f'{name} is not on PATH')
    version = probe(binary, '--version', home=tmp_path)
    assert version.returncode == 0 and version.stdout.strip(), version.stderr
    record_property('installed_version', version.stdout.strip())
    record_property('pinned_trace_profile', profile)
    help_text = probe(binary, *subcommand, '--help', home=tmp_path)
    assert help_text.returncode == 0, help_text.stderr
    flags = {part.split('=')[0] for part in hooks_validation.command(Path(tmp_path), runtime, binary) if part.startswith('--')}
    assert flags and not [flag for flag in sorted(flags) if flag not in help_text.stdout + help_text.stderr], flags
