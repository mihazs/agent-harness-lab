"""Compare native source preview contracts against an explicitly frozen checkout."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.integration.sources import test_source_codex as fixtures
from tests.support.unittest_cleanups import run_cleanups


@pytest.mark.skipif(not os.environ.get('AHL_NATIVE_BASELINE_ROOT'), reason='frozen native baseline not selected')
class TestNativeCompatibility:
    @pytest.mark.parametrize('version', [pytest.param('0.166.0-v1', id='file-v1'), pytest.param('0.166.0-v2', id='root-v2')])
    def test_codex_file_and_root_preview_contracts_match_the_frozen_baseline(self, version):
        fixture = fixtures.NativeSourceTests(); fixture.setUp()
        try:
            baseline = Path(os.environ['AHL_NATIVE_BASELINE_ROOT']).resolve()
            candidate = Path(__file__).resolve().parents[3]
            if version.endswith('-v2'):
                captures = fixture.root/'captures'; captures.mkdir()
                (captures/'rollout-compat.jsonl').write_bytes(fixture.capture.read_bytes())
                fixture.selected = dict(schema_version=1, project='project', source_root='captures', session=None,
                    max_files=100, max_scan_files=2000, max_records=10000)
                fixture.declare(); fixture.source = replace(fixture.source, version=version)
                fixture.manifest.write_text(json.dumps(dict(schema_version=1, source=fixture.source.to_dict(), data='selection.json')))
            results = []
            for checkout in (baseline, candidate):
                env = dict(os.environ, PYTHONPATH=str(checkout), PYTHONDONTWRITEBYTECODE='1')
                completed = subprocess.run([sys.executable, '-m', 'agent_harness_lab', 'sources', 'preview',
                    '--manifest', str(fixture.manifest), '--project-sha256', fixture.source.project_sha256,
                    '--scope-sha256', fixture.source.scope_sha256], env=env, cwd=fixture.root,
                    capture_output=True, text=True, timeout=20, check=False)
                assert completed.returncode == 0, completed.stderr
                results.append(json.loads(completed.stdout))
            assert results[1] == results[0]
        finally:
            run_cleanups(fixture)
