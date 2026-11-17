"""The second-wave memory matrix keeps its closed vocabulary, pinned citations and an SDK-free base install."""
from pathlib import Path
import re
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[3]
DOC = (ROOT / 'docs/memory-compatibility-matrix.md').read_text()
STATUSES = {'verified', 'unsupported', 'requires_authorized_export', 'not_researched'}
SYSTEMS = ('graphiti', 'zep-cloud', 'supermemory', 'letta', 'langgraph-store', 'langmem', 'basic-memory')
DIMENSIONS = ['Source identity and namespace', 'List and get', 'History and revisions', 'Official export or API',
              'Read-only semantics', 'Privacy scope', 'Lifecycle and TTL', 'Cost and inference side effects',
              'Freshness', 'Completeness']
SURVEYED = ('OpenViking', 'mcp-memory-service', 'A-MEM', 'agentmemory')
# Distribution names a provider ships: none may be a dependency of the base install or any extra.
PROVIDER_PACKAGES = {'graphiti-core', 'zep-cloud', 'zep-python', 'supermemory', 'letta', 'letta-client', 'langmem',
                     'langgraph', 'langgraph-checkpoint', 'langgraph-checkpoint-sqlite', 'langgraph-checkpoint-postgres',
                     'basic-memory'}
LINK = re.compile(r'https://github\.com/([^/\s)]+/[^/\s)]+)/(?:blob|tree)/([^/\s)]+)/')


def section(title, level='## '):
    found = re.search(r'^%s%s\n(.*?)(?=^%s|\Z)' % (level, re.escape(title), level), DOC, re.S | re.M)
    assert found, title
    return found.group(1)


def table(body):
    lines = [line for line in body.splitlines() if line.startswith('|')]
    return [[cell.strip() for cell in line.strip('|').split(' | ')] for line in lines[2:]]


def bare(cell):
    return cell.strip('`')


SUMMARY = {bare(row[0]): row for row in table(section('Summary'))}
FINDINGS = {found.group(1): table(found.group(2)) for found in re.finditer(
    r'^### [^\n]*\(`([a-z-]+)`\)\n(.*?)(?=^#{2,3} |\Z)', section('Findings per system'), re.S | re.M)}


def test_summary_lists_each_named_system_once():
    ids = [bare(row[0]) for row in table(section('Summary'))]
    assert ids == list(SYSTEMS)


@pytest.mark.parametrize('system', SYSTEMS)
def test_summary_pins_a_full_commit_and_reports_no_adapter(system):
    row = SUMMARY[system]
    assert re.fullmatch(r'[0-9a-f]{40}', bare(row[3]))
    assert row[6] == 'none'


@pytest.mark.parametrize('system', SYSTEMS)
def test_each_system_section_covers_every_dimension_once_in_order(system):
    assert [row[0] for row in FINDINGS[system]] == DIMENSIONS


def test_no_section_exists_beyond_the_named_systems():
    assert set(FINDINGS) == set(SYSTEMS)


@pytest.mark.parametrize('system', SYSTEMS)
def test_every_status_is_in_the_closed_vocabulary(system):
    assert {bare(row[1]) for row in FINDINGS[system]} <= STATUSES


@pytest.mark.parametrize('system', SYSTEMS)
def test_citations_name_the_summary_repository_at_the_pinned_commit(system):
    repository = re.search(r'https://github\.com/([^/\s)]+/[^/\s)]+)\)', SUMMARY[system][1]).group(1)
    pin = bare(SUMMARY[system][3])
    for _, _, finding in FINDINGS[system]:
        assert {(repo, ref) for repo, ref in LINK.findall(finding)} <= {(repository, pin)}, finding


@pytest.mark.parametrize('system', SYSTEMS)
def test_every_verified_row_cites_the_pinned_source(system):
    for dimension, status, finding in FINDINGS[system]:
        if bare(status) == 'verified':
            assert LINK.search(finding), (dimension, 'verified without a pinned citation')


@pytest.mark.parametrize('system', SYSTEMS)
def test_every_row_states_a_reason_beyond_its_status(system):
    for dimension, _, finding in FINDINGS[system]:
        assert len(finding) >= 40, dimension


def test_surveyed_systems_stay_not_researched_and_have_no_section():
    rows = {row[0]: bare(row[1]) for row in table(section('Surveyed but not researched'))}
    assert set(SURVEYED) <= set(rows)
    assert set(rows.values()) == {'not_researched'}
    assert not any(name in section('Summary') or name in section('Findings per system') for name in SURVEYED)


def test_base_install_and_extras_declare_no_provider_package():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
    requirements = list(project.get('dependencies', []))
    for extra in project.get('optional-dependencies', {}).values():
        requirements += extra
    names = {re.match(r'[A-Za-z0-9_.-]+', item).group(0).lower().replace('_', '-') for item in requirements}
    assert not names & PROVIDER_PACKAGES


def test_docs_index_links_the_matrix():
    assert '(memory-compatibility-matrix.md)' in (ROOT / 'docs/README.md').read_text()


def test_matrix_holds_no_private_data():
    assert not re.search(r'/(home|Users|root|tmp|var)/|[A-Za-z]:\\|sk-[A-Za-z0-9]{8}|gh[pousr]_[A-Za-z0-9]{8}|AKIA[0-9A-Z]{8}'
                         r'|BEGIN [A-Z ]*PRIVATE KEY', DOC)
