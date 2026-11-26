"""Runtime version statements the manifests contradict (#1125): one synthetic repository per ecosystem, each with a clean twin."""
import pytest

from agent_harness_lab.project_profile import detect, report, versions
from tests.support.profile_fixture import build, package


def result(tmp_path, files, text):
    return report.build(build(tmp_path, {**files, 'AGENTS.md': text}))


def subjects(found):
    return [row['subject'] for row in found['findings'] if row['kind'] == 'version_contradicted']


PYTHON = {'pyproject.toml': '[project]\nname = "x"\nrequires-python = ">=3.10,<4"\n'}
NODE = {'package.json': package(engines={'node': '>=18'})}
RUST = {'Cargo.toml': '[package]\nname = "x"\nrust-version = "1.75"\n'}
GO = {'go.mod': 'module x\n\ngo 1.22\n'}


@pytest.mark.parametrize('files, text, subject, clean', [
    pytest.param(PYTHON, 'Written for Python 3.9.\n', 'python 3.9', 'Written for Python 3.12.\n', id='python-below-minimum'),
    pytest.param(PYTHON, 'Needs Python 3.8+.\n', 'python 3.8+', 'Needs Python 3.10+.\n', id='python-floor-below-minimum'),
    pytest.param(NODE, 'Use Node 16 locally.\n', 'node 16', 'Use Node 20 locally.\n', id='node-below-minimum'),
    pytest.param(NODE, 'Requires Node.js 16 or newer.\n', 'node 16+', 'Requires Node.js 18 or newer.\n', id='node-or-newer'),
    pytest.param(RUST, 'Builds with Rust 1.70.\n', 'rust 1.70', 'Builds with Rust 1.80.\n', id='rust-below-msrv'),
    pytest.param(GO, 'Install Go 1.20 first.\n', 'go 1.20', 'Install Go 1.22 first.\n', id='go-below-directive'),
    pytest.param({'pyproject.toml': '[project]\nname = "x"\n', '.python-version': '3.12.1\n'}, 'The interpreter is Python 3.11.\n', 'python 3.11', 'The interpreter is Python 3.12.\n', id='python-other-pin'),
    pytest.param({'package.json': package(), '.nvmrc': 'v20.11.0\n'}, 'Run on Node 18.\n', 'node 18', 'Run on Node 20.\n', id='node-other-pin'),
    pytest.param({'package.json': package(), '.node-version': '20\n'}, 'Run on Node 22+.\n', 'node 22+', 'Run on Node 18+.\n', id='node-floor-above-pin'),
])
def test_a_statement_the_manifest_contradicts_fires_and_its_twin_is_silent(tmp_path, files, text, subject, clean):
    found = result(tmp_path, files, text)
    row = next(row for row in found['findings'] if row['kind'] == 'version_contradicted')
    assert (row['subject'], row['finding_class'], row['where']) == (subject, 'contradiction', [dict(file='AGENTS.md', line=1)])
    assert row['id'].startswith('hp/version_contradicted/') and row['proposed_update']['action'] == 'correct_or_remove_line'
    assert row['evidence']['declared'] and row['status'] == 'candidate_claim'
    assert subjects(result(tmp_path / 'b', files, clean)) == []


def test_the_id_is_stable_across_line_moves(tmp_path):
    first = result(tmp_path, PYTHON, 'Written for Python 3.9.\n')['findings']
    second = result(tmp_path / 'b', PYTHON, 'Intro.\n\nWritten for Python 3.9.\n')['findings']
    assert [row['id'] for row in first] == [row['id'] for row in second]


@pytest.mark.parametrize('text', [
    pytest.param('Python 3.11+ is fine.\n', id='floor-the-manifest-satisfies'),
    pytest.param('Developed on Python 3.13.\n', id='exact-above-the-minimum'),
    pytest.param('Python 3.8 is no longer supported.\n', id='negated'),
    pytest.param('Tested on Python 3.9, 3.10 and 3.12.\n', id='list'),
    pytest.param('Supports Python 3.9 - 3.12.\n', id='range'),
    pytest.param('Supports Python 3.9 or 3.10.\n', id='alternatives'),
    pytest.param('```\npython 3.9\n```\n', id='fenced'),
    pytest.param('Python 3 is required.\n', id='no-minor'),
])
def test_statements_that_are_not_a_plain_contradiction_are_silent(tmp_path, text):
    assert subjects(result(tmp_path, PYTHON, text)) == []


@pytest.mark.parametrize('files', [
    pytest.param({'pyproject.toml': '[project]\nrequires-python = ">=3.9 || <3.5"\n'}, id='alternatives'),
    pytest.param({'pyproject.toml': '[project]\nrequires-python = "!=3.9.*"\n'}, id='exclusion-only'),
    pytest.param({'pyproject.toml': '[project]\nrequires-python = "~> 3.9"\n'}, id='unknown-operator'),
    pytest.param({'pyproject.toml': '[project\nrequires-python = '}, id='unparsable-manifest'),
    pytest.param({'package.json': package(engines={'node': '>=14 || >=16'})}, id='node-alternatives'),
    pytest.param({'package.json': package(engines={'node': 'lts/*'})}, id='node-alias'),
    pytest.param({'package.json': package(), '.nvmrc': 'lts/*\n'}, id='nvm-alias'),
    pytest.param({'pyproject.toml': '[project]\n', '.python-version': 'pypy3.10\n'}, id='pyenv-name'),
    pytest.param({'pyproject.toml': '[project]\n', '.python-version': '3.8\n3.9\n'}, id='several-pins'),
    pytest.param({'package.json': package(engines={'node': 18})}, id='non-string'),
])
def test_an_unknown_or_unparsable_constraint_stays_silent(tmp_path, files):
    assert subjects(result(tmp_path, files, 'Use Python 3.7 and Node 12 and Rust 1.1 and Go 1.1.\n')) == []


def test_a_statement_one_manifest_satisfies_is_silent_when_the_manifests_disagree(tmp_path):
    files = {**PYTHON, '.python-version': '3.9\n'}
    assert subjects(result(tmp_path, files, 'Use Python 3.9.\n')) == []  # the pin matches, the minimum does not
    assert subjects(result(tmp_path / 'b', files, 'Use Python 3.7.\n')) == ['python 3.7']


def test_only_a_root_instruction_file_is_checked(tmp_path):
    files = {**PYTHON, 'sub/AGENTS.md': 'Use Python 3.9.\n', '.claude/skills/x/SKILL.md': 'Use Python 3.9.\n'}
    assert subjects(result(tmp_path, files, 'Nothing here.\n')) == []


def test_no_manifest_runtime_no_finding(tmp_path):
    assert subjects(result(tmp_path, {'Makefile': 'test:\n\ttrue\n'}, 'Use Python 3.9 and Node 12.\n')) == []


def test_the_profile_lists_the_declared_runtimes(tmp_path):
    root = build(tmp_path, {**PYTHON, **NODE, **RUST, **GO, '.nvmrc': 'v20\n'})
    rows = {(row['runtime'], row['source']): (row['kind'], row['version']) for row in detect.detect(root)['runtimes']}
    assert rows == {('python', 'pyproject.toml'): ('minimum', '3.10'), ('node', 'package.json'): ('minimum', '18'), ('node', '.nvmrc'): ('pin', '20'),
                    ('rust', 'Cargo.toml'): ('minimum', '1.75'), ('go', 'go.mod'): ('minimum', '1.22')}


@pytest.mark.parametrize('text, expected', [
    ('>=3.10', ('minimum', (3, 10))), ('>= 3.9, <4', ('minimum', (3, 9))), ('^18.2.0', ('minimum', (18, 2, 0))), ('~=3.8', ('minimum', (3, 8))),
    ('==3.11.*', ('pin', (3, 11))), ('18.x', ('pin', (18,))), ('v20.1.0', ('pin', (20, 1, 0))),
    ('>=3.9 || >=3.11', None), ('>3.9', None), ('<4', None), ('==3.9,==3.10', None), ('lts/*', None), ('', None),
])
def test_constraint_shapes(text, expected):
    assert versions.constraint(text) == expected


def test_several_runtimes_in_one_line_are_each_read():
    assert versions.stated_in('Go 1.20 and Rust 1.70+, Node 16') == [('go', '1.20', False), ('rust', '1.70', True), ('node', '16', False)]
