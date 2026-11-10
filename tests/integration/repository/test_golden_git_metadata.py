"""Synthetic graph claims describe input records, not verified Git history."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources.execution import CoverageManifest
from agent_harness_lab.sources.git_evidence import capture
from agent_harness_lab.sources.graph import EvidenceGraph
from agent_harness_lab.sources.schema import EvidenceRecord, SourceSpec, digest
from tests.unit.evaluation.test_benchmark_contracts import record as benchmark_record

GOLDEN = Path(__file__).resolve().parents[2] / 'support/golden_profiles/git-evidence-v1-synthetic.json'


def synthetic_snapshot():
    source = SourceSpec('local_git', 'git', 'v2', 'a' * 64, 'b' * 64, 'c' * 64,
                        identity_provenance='source_declared_id', enabled=True)
    def record(kind, native, revision, content):
        return EvidenceRecord(source.source_key, digest(native), revision, content,
                              'development_artifact', kind, '2026-01-01T00:00:00Z')
    commit = 'd' * 64
    rows = [record('git_commit', commit, 'e' * 64, 'f' * 64)]
    summary = dict(commits=[dict(commit_sha256=commit, parents_sha256=[])], refs=[],
                   head_sha256=commit, harness_sha256='1' * 64, warnings=[])
    return source, capture(source, rows, summary, record, 20, 0)


def test_saved_git_graph_and_coverage_preserve_candidate_status(tmp_path):
    source, snapshot = synthetic_snapshot()
    saved = json.loads(GOLDEN.read_text())
    graph = EvidenceGraph.parse(saved['graph'])
    coverage = CoverageManifest.parse(saved['coverage'])
    assert json.loads(json.dumps(snapshot.graph.to_dict())) == saved['graph']
    assert json.loads(json.dumps(snapshot.metadata.coverage.to_dict())) == saved['coverage']
    assert graph == snapshot.graph and coverage == snapshot.metadata.coverage
    graph.validate(source, snapshot.records)
    assert graph.joins[0].resolver_version == 'git-claims-v1'
    assert graph.joins[0].status == 'candidate' and coverage.parser_version == 'git-frozen-view-v2'
    assert graph.summary()['verification_authorized'] is False
    with pytest.raises(ValueError, match='evidence graph exceeds its declared source scope/version'):
        graph.validate(replace(source, scope_sha256='0' * 64), snapshot.records)
    forged = tmp_path / 'forged.json'
    forged.write_text(json.dumps([saved | benchmark_record() | {'publication_allowed': True}]))
    public = tmp_path / 'public'
    with pytest.raises(SystemExit) as exc:
        main(['benchmark-report', '--input', str(forged), '--output', str(public), '--public'])
    assert exc.value.code == 2
    assert not public.exists()
