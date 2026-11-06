"""Codex project-doc budget over the AGENTS.md directory chain in the static audit; synthetic checkouts only."""
import json
import os

import pytest

from agent_harness_lab import audit
from agent_harness_lab.sources import harness_inventory, instructions

BUDGET = 32 * 1024
TRUNCATED = 'instruction_chain_truncated_at_default_project_doc_bytes'
SKIPPED = 'instruction_chain_skipped_at_default_project_doc_bytes'
SOURCE = 'https://github.com/openai/codex/tree/d786ff91/codex-rs/core/src/agents_md.rs'
SECRET = 'SYNTHETIC_BUDGET_TEXT'


def text(size, lead=b''):
    """Exactly size bytes: an optional leading run, then a marker and filler, so content is never blank."""
    body = lead + SECRET.encode() + b'\n'
    return body + b'x' * (size - len(body))


@pytest.fixture
def checkout(tmp_path):
    def build(files, *, git=True, links=()):
        root = tmp_path / 'checkout'
        root.mkdir()
        for path, raw in files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        for path, target in links:
            os.symlink(target, root / path)
        if git:
            (root / '.git').mkdir()
            (root / '.git/HEAD').write_text('ref: refs/heads/main\n')
        return root
    return build


def budget_findings(report):
    return {row.path: row for row in report.findings if row.reason in (TRUNCATED, SKIPPED)}


def instruction_row(report):
    return next(row for row in report.dimensions if row.dimension == 'instructions')


def test_a_chain_that_exactly_fills_the_default_budget_loses_nothing(checkout):
    root = checkout({'AGENTS.md': text(20000), 'src/AGENTS.md': text(BUDGET - 20000)})
    report = audit.run(root, 'codex', task_cwd='src')
    assert budget_findings(report) == {}
    assert instruction_row(report).status == 'conformant'


def test_the_file_that_crosses_the_budget_is_named_with_its_dropped_bytes(checkout):
    root = checkout({'AGENTS.md': text(20000), 'src/AGENTS.md': text(BUDGET - 20000 + 1)})
    report = audit.run(root, 'codex', task_cwd='src')
    finding = budget_findings(report)['src/AGENTS.md']
    assert (finding.status, finding.reason, finding.kind, finding.evidence_kind) == (
        'nonconformant', TRUNCATED, 'instruction', 'structural')
    assert dict(finding.measures) == {'budget_bytes': BUDGET, 'loaded_bytes': BUDGET - 20000, 'dropped_bytes': 1}
    assert (finding.cost_class, finding.remediation, finding.source) == (
        'content_edit', 'shorten_the_instruction_chain_or_raise_project_doc_max_bytes', SOURCE)
    assert 'AGENTS.md' not in budget_findings(report)  # the root file loads whole
    assert instruction_row(report).status == 'nonconformant'
    assert TRUNCATED in instruction_row(report).reasons


def test_files_after_the_budget_is_exhausted_are_skipped_whole(checkout):
    root = checkout({'AGENTS.md': text(BUDGET), 'a/AGENTS.md': text(300), 'a/b/AGENTS.md': text(500)})
    found = budget_findings(audit.run(root, 'codex', task_cwd='a/b'))
    assert {path: (row.reason, dict(row.measures)) for path, row in found.items()} == {
        'a/AGENTS.md': (SKIPPED, {'budget_bytes': BUDGET, 'loaded_bytes': 0, 'dropped_bytes': 300}),
        'a/b/AGENTS.md': (SKIPPED, {'budget_bytes': BUDGET, 'loaded_bytes': 0, 'dropped_bytes': 500}),
    }


def test_a_single_oversized_root_file_reports_its_truncation_not_only_its_size(checkout):
    report = audit.run(checkout({'AGENTS.md': text(BUDGET + 40)}), 'codex')
    finding = budget_findings(report)['AGENTS.md']
    assert (finding.reason, dict(finding.measures)['dropped_bytes']) == (TRUNCATED, 40)
    assert 'instruction_exceeds_project_doc_bytes' not in instruction_row(report).reasons


def test_files_outside_the_task_chain_use_no_budget(checkout):
    root = checkout({'AGENTS.md': text(30000), 'src/AGENTS.md': text(30000), 'docs/AGENTS.md': text(30000)})
    assert budget_findings(audit.run(root, 'codex')) == {}  # task_cwd '.' loads only the root file


def test_an_override_shadows_its_directorys_agents_md_from_the_budget(checkout):
    root = checkout({'AGENTS.md': text(30000), 'src/AGENTS.override.md': text(100), 'src/AGENTS.md': text(30000)})
    assert budget_findings(audit.run(root, 'codex', task_cwd='src')) == {}


def test_a_blank_file_after_exhaustion_loses_nothing(checkout):
    root = checkout({'AGENTS.md': text(BUDGET), 'a/AGENTS.md': b' \n\t\n', 'a/b/AGENTS.md': text(64)})
    found = budget_findings(audit.run(root, 'codex', task_cwd='a/b'))
    assert {path: row.reason for path, row in found.items()} == {'a/b/AGENTS.md': SKIPPED}


def test_a_blank_file_before_exhaustion_uses_no_budget(checkout):
    root = checkout({'AGENTS.md': text(BUDGET - 100), 'a/AGENTS.md': b' ' * 50, 'a/b/AGENTS.md': text(100)})
    assert budget_findings(audit.run(root, 'codex', task_cwd='a/b')) == {}


def test_a_blank_file_larger_than_the_remaining_budget_gets_no_finding(checkout):
    root = checkout({'AGENTS.md': text(BUDGET - 10), 'a/AGENTS.md': b' ' * 50, 'a/b/AGENTS.md': text(64)})
    assert budget_findings(audit.run(root, 'codex', task_cwd='a/b')) == {}  # a split character would settle a/b


def test_separator_characters_are_content_to_codex_and_use_the_budget(checkout):
    # Python's str.strip() removes U+001C, Rust's str::trim does not, so Codex loads this file and spends budget.
    root = checkout({'AGENTS.md': b'\x1c' * BUDGET, 'src/AGENTS.md': text(64)})
    report = audit.run(root, 'codex', task_cwd='src')
    assert {path: row.reason for path, row in budget_findings(report).items()} == {'src/AGENTS.md': SKIPPED}


def test_a_cut_inside_leading_whitespace_leaves_the_loaded_bytes_and_later_files_unsettled(checkout):
    root = checkout({'AGENTS.md': text(BUDGET - 10), 'a/AGENTS.md': text(400, lead=b' ' * 200), 'a/b/AGENTS.md': text(64)})
    found = budget_findings(audit.run(root, 'codex', task_cwd='a/b'))
    assert {path: (row.reason, dict(row.measures)) for path, row in found.items()} == {
        'a/AGENTS.md': (TRUNCATED, {'budget_bytes': BUDGET})}


def test_a_cut_at_the_end_of_leading_whitespace_loads_nothing_and_keeps_the_budget(checkout):
    root = checkout({'AGENTS.md': text(BUDGET - 30), 'a/AGENTS.md': text(400, lead=b' ' * 30), 'a/b/AGENTS.md': text(31)})
    found = budget_findings(audit.run(root, 'codex', task_cwd='a/b'))
    assert {path: (row.reason, dict(row.measures)) for path, row in found.items()} == {
        'a/AGENTS.md': (TRUNCATED, {'budget_bytes': BUDGET, 'loaded_bytes': 0, 'dropped_bytes': 400}),
        'a/b/AGENTS.md': (TRUNCATED, {'budget_bytes': BUDGET, 'loaded_bytes': 30, 'dropped_bytes': 1}),
    }


def test_without_a_root_marker_in_the_checkout_the_chain_is_not_judged(checkout):
    # Codex looks above the checkout for .git; what it finds there is never read, so no budget outcome is settled.
    report = audit.run(checkout({'AGENTS.md': text(30000), 'src/AGENTS.md': text(30000)}, git=False), 'codex', task_cwd='src')
    assert budget_findings(report) == {}
    assert instruction_row(report).status == 'conformant'  # the per-file checks alone; the chain adds no pass


def test_an_explicitly_untrusted_project_loads_no_project_docs(checkout):
    root = checkout({'AGENTS.md': text(30000), 'src/AGENTS.md': text(30000)})
    report = audit.run(root, 'codex', task_cwd='src', project_trust='declared_untrusted')
    assert budget_findings(report) == {}


def test_a_refused_candidate_stops_the_chain_where_codex_would_follow_it(checkout, tmp_path):
    (tmp_path / 'elsewhere.md').write_bytes(text(100))
    root = checkout({'AGENTS.md': text(30000), 'a/b/AGENTS.md': text(30000)},
                    links=[('a/AGENTS.md', str(tmp_path / 'elsewhere.md'))])
    report = audit.run(root, 'codex', task_cwd='a/b')
    assert budget_findings(report) == {}  # the link's size is unknown, so a/b/AGENTS.md is not judged
    assert instruction_row(report).status == 'unknown'
    assert 'refused_symlink' in instruction_row(report).reasons


def test_audit_recovers_task_cwd_from_the_inventory_or_rejects_a_mismatch(checkout):
    root = checkout({'AGENTS.md': text(20000), 'src/AGENTS.md': text(20000)})
    inventory = harness_inventory.capture(root, dict(schema_version=1, runtime='codex', version='0.166.0', task_cwd='src'))
    assert set(budget_findings(audit.audit(inventory))) == {'src/AGENTS.md'}
    with pytest.raises(ValueError):
        audit.audit(inventory, task_cwd='.')


def test_a_same_named_file_in_an_external_root_uses_no_budget(checkout, tmp_path):
    root = checkout({'AGENTS.md': text(BUDGET + 100)})
    home = tmp_path / 'codex_home'
    home.mkdir()
    (home / 'AGENTS.md').write_bytes(text(100))
    inventory = harness_inventory.capture(root, dict(schema_version=1, runtime='codex', version='0.166.0', task_cwd='.'),
                                          external_roots={'codex_home': str(home)})
    assert {node.origin for node in inventory.nodes if node.path == 'AGENTS.md'} == {'checkout', 'caller_declared_external'}
    report = audit.audit(inventory, task_cwd='.')
    budget = [row for row in report.findings if row.reason in (TRUNCATED, SKIPPED)]
    assert [(row.path, dict(row.measures)['dropped_bytes']) for row in budget] == [('AGENTS.md', 100)]
    assert len({row.id for row in report.findings}) == len(report.findings)


def test_claude_code_has_no_codex_budget(checkout):
    root = checkout({'CLAUDE.md': text(BUDGET + 1), 'AGENTS.md': text(BUDGET + 1)})
    assert budget_findings(audit.run(root, 'claude-code')) == {}


def test_the_document_carries_counts_and_paths_but_never_the_text(checkout):
    root = checkout({'AGENTS.md': text(BUDGET), 'src/AGENTS.md': text(64)})
    encoded = json.dumps(audit.run(root, 'codex', task_cwd='src').document())
    assert SECRET not in encoded
    document = json.loads(encoded)
    assert document['publication_allowed'] is False
    finding = next(row for row in document['findings'] if row['reason'] == SKIPPED)
    assert (finding['path'], finding['measures']) == ('src/AGENTS.md', [['budget_bytes', BUDGET], ['dropped_bytes', 64],
                                                                         ['loaded_bytes', 0]])


@pytest.mark.parametrize('measures', [
    [('dropped_bytes', 1)], (('dropped_bytes', -1),), (('dropped_bytes', True),), (('b', 1), ('a', 1)),
    (('a', 1), ('a', 2)), (('bad label', 1),), (('a', 1), (2, 1)),
])
def test_a_finding_rejects_malformed_measures(measures):
    row = dict(id='0' * 64, dimension='instructions', status='nonconformant', reason=SKIPPED, path='AGENTS.md',
               path_sha256='0' * 64, kind='instruction', evidence_kind='structural', cost_class='content_edit',
               remediation='shorten_the_instruction_chain_or_raise_project_doc_max_bytes')
    audit.Finding(**row)
    with pytest.raises(ValueError):
        audit.Finding(**row, measures=measures)


@pytest.mark.parametrize(('raw', 'leading'), [
    (b'', 0), (b'  \n# x', 3), (b'\x1c x', 0), ('　x'.encode(), 3), (b' \t\n', 3),
])
def test_the_codex_parser_records_the_leading_rust_whitespace_run(raw, leading):
    status, facts = instructions.parse_surface('AGENTS.md', raw, dict(runtime='codex', task_cwd='.'))
    assert (status, facts['codex_leading_blank_bytes'], facts['bytes']) == ('parsed', leading, len(raw))
