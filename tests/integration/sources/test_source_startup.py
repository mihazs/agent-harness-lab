"""Help and the empty source probe do not load provider implementations (#756)."""
import json
import subprocess
import sys

import pytest


@pytest.mark.parametrize('args', [['sources', '--help'], ['sources']])
def test_help_and_empty_probe_skip_the_provider_and_network_stack(args):
    script = '''
import json, sys
from agent_harness_lab.cli import main
try:
    status = main(json.loads(sys.argv[1]))
except SystemExit as exit:
    status = exit.code
assert status == 0
assert 'agent_harness_lab.sources.github_config' not in sys.modules
assert 'agent_harness_lab.sources.snapshot' not in sys.modules
assert 'http.client' not in sys.modules
'''
    result = subprocess.run([sys.executable, '-c', script, json.dumps(args)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    if len(args) == 1:
        assert json.loads(result.stdout) == dict(schema_version=1, enabled_sources=0, source_read=False, network_used=False)
    else:
        assert 'usage: ahl sources' in result.stdout
