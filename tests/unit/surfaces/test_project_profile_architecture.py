"""Recorded decisions and architectural patterns: detectors and the four findings they support (#1136).

Each finding fires on a synthetic repository and stays silent on its clean twin; an unclear record never fires.
"""
import pytest

from agent_harness_lab.project_profile import architecture, detect, report
from tests.support.profile_fixture import build, package

ADOPT = '# 2. Use pnpm\n\n## Status\n\nAccepted\n\n## Decision\n\nWe use pnpm.\n'
NPM_ONLY = {'package.json': package({'test': 'jest'})}


def arch(tmp_path, files):
    return detect.detect(build(tmp_path, files))['architecture']


def run(tmp_path, files):
    return report.build(build(tmp_path, files))['findings']


def kinds(found):
    return sorted((row['kind'], row['subject']) for row in found)


@pytest.mark.parametrize('path,text,status', [
    pytest.param('doc/adr/0001-use-x.md', '# 1. Use x\n\n## Status\n\nAccepted\n', 'accepted', id='adr-tools-section'),
    pytest.param('docs/decisions/0002-use-y.md', '---\nstatus: "accepted"\ndate: 2024-01-01\n---\n# Use y\n', 'accepted', id='madr-front-matter'),
    pytest.param('docs/adr/0003-use-z.md', '# Use z\n\nStatus: Accepted\n', 'accepted', id='status-line'),
    pytest.param('docs/adr/0004-old.md', '# 4. Old\n\n## Status\n\nSuperseded by [5. New](0005-new.md)\n', 'superseded', id='superseded-section'),
    pytest.param('docs/decisions/0006-old.md', '---\nstatus: superseded by ADR-0007\n---\n# Old\n', 'superseded', id='superseded-front-matter'),
])
def test_decision_record_layouts_are_read(tmp_path, path, text, status):
    [row] = arch(tmp_path, {path: text})['decisions']
    assert (row['path'], row['status']) == (path, status)


@pytest.mark.parametrize('path,text', [
    pytest.param('docs/adr/0001-x.md', '# X\n\n## Status\n\nProposed\n', id='proposed'),
    pytest.param('docs/adr/0001-x.md', '# X\n\n## Status\n\nDeprecated\n', id='deprecated'),
    pytest.param('docs/adr/0001-x.md', '# X\n\nNo status here.\n', id='no-status'),
    pytest.param('docs/adr/README.md', '# Index\n\nStatus: accepted\n', id='not-a-numbered-record'),
    pytest.param('docs/notes/0001-x.md', '# X\n\nStatus: accepted\n', id='not-a-decision-folder'),
    pytest.param('docs/adr/0001-x.md', '# X\n\n## Status\n\nAccepted, then rejected by the board\n' .replace('Accepted, then', 'Maybe accepted, then'), id='hedged'),
])
def test_a_record_without_a_plain_accepted_or_superseded_status_is_skipped(tmp_path, path, text):
    assert arch(tmp_path, {path: text})['decisions'] == []


def test_the_adr_dir_file_names_the_decision_folder(tmp_path):
    found = arch(tmp_path, {'.adr-dir': 'records/arch\n', 'records/arch/0001-x.md': '# X\n\nStatus: accepted\n'})
    assert [row['path'] for row in found['decisions']] == ['records/arch/0001-x.md']


def test_workspace_edges_and_generated_dirs_are_counted(tmp_path):
    found = arch(tmp_path, {'package.json': package(), 'packages/a/package.json': {'name': 'a', 'dependencies': {'b': '*', 'react': '1'}},
                            'packages/b/package.json': {'name': 'b'}, 'packages/b/src/generated/x.ts': '', 'node_modules/generated/x': ''})
    assert found['workspace'] == dict(packages=2, internal_edges=1) and found['generated_dirs'] == ['packages/b/src/generated']


def test_a_decision_against_the_tool_the_harness_runs_is_a_contradiction(tmp_path):
    files = {**NPM_ONLY, 'docs/adr/0002-use-pnpm.md': ADOPT, 'AGENTS.md': 'Install with `npm install` and run `npm run test`. Decisions: see docs/adr.\n'}
    [row] = [row for row in run(tmp_path, files) if row['kind'] == 'decision_contradicted']
    assert (row['subject'], row['finding_class'], row['evidence']['decided'], row['evidence']['mentions']) == ('npm', 'contradiction', 'pnpm', 2)
    assert row['proposed_update'] == dict(action='correct_or_remove_line', record='docs/adr/0002-use-pnpm.md') and row['where'][0]['file'] == 'AGENTS.md'


@pytest.mark.parametrize('files', [
    pytest.param({'AGENTS.md': 'Use `pnpm install`. See docs/adr.\n'}, id='clean-twin'),
    pytest.param({'AGENTS.md': 'Use `npm install`. See docs/adr.\n', 'package-lock.json': ''}, id='repository-settles-it'),
    pytest.param({'AGENTS.md': 'Use `npm install`. See docs/adr.\n', 'docs/adr/0003-use-yarn.md': '# 3. Use yarn\n\nStatus: accepted\n'}, id='accepted-records-disagree'),
    pytest.param({'AGENTS.md': 'Never run `npm install`. See docs/adr.\n'}, id='negated-line'),
])
def test_unsettled_or_clean_cases_do_not_contradict(tmp_path, files):
    base = {'package.json': package(), 'docs/adr/0002-use-pnpm.md': ADOPT}
    assert not any(kind == 'decision_contradicted' for kind, _ in kinds(run(tmp_path, {**base, **files})))


def test_a_title_that_negates_or_names_two_tools_decides_nothing(tmp_path):
    for title in ('Do not use pnpm', 'Use pnpm and yarn', 'Evaluate pnpm'):
        assert arch(tmp_path / title.replace(' ', '-'), {'docs/adr/0001-x.md': f'# {title}\n\nStatus: accepted\n'})['decisions'][0]['tool'] is None


def test_a_superseded_decision_the_harness_still_runs_is_a_contradiction(tmp_path):
    files = {'package.json': package(),
             'docs/adr/0001-use-npm.md': '# 1. Use npm\n\n## Status\n\nSuperseded by [2. Use pnpm](0002-use-pnpm.md)\n',
             'docs/adr/0002-use-pnpm.md': ADOPT, 'AGENTS.md': 'Run `npm install`. See docs/adr.\n'}
    row = next(row for row in run(tmp_path, files) if row['kind'] == 'superseded_decision_stated')
    assert (row['subject'], row['evidence']['decided']) == ('npm', 'pnpm')


def test_citing_a_superseded_record_as_current_is_a_contradiction_but_history_is_not(tmp_path):
    old = {'docs/adr/0001-queue.md': '# 1. Queue\n\n## Status\n\nSuperseded by 2\n', 'docs/adr/0002-bus.md': '# 2. Bus\n\nStatus: accepted\n'}
    stated = run(tmp_path, {**old, 'AGENTS.md': 'Follow ADR-1 for queues. See docs/adr.\n'})
    assert [(row['subject'], row['where']) for row in stated if row['kind'] == 'superseded_decision_stated'] == [('docs/adr/0001-queue.md', [dict(file='AGENTS.md', line=1)])]
    for line in ('ADR-1 was superseded by ADR-2.', 'Do not follow docs/adr/0001-queue.md.', 'Follow ADR-12 for queues.'):
        assert not any(row['kind'] == 'superseded_decision_stated' for row in run(tmp_path / 'b', {**old, 'AGENTS.md': line + ' See docs/adr.\n'}))


def test_accepted_records_the_harness_never_mentions_are_a_gap(tmp_path):
    files = {'docs/decisions/0001-x.md': '# X\n\nStatus: accepted\n', 'AGENTS.md': 'Run `make test`.\n'}
    [row] = [row for row in run(tmp_path, files) if row['kind'] == 'decision_records_unstated']
    assert (row['finding_class'], row['evidence']) == ('gap', dict(accepted=1, records=1, folders=1)) and row['proposed_update']['folders'] == ['docs/decisions']
    for line in ('Decisions live in docs/decisions.', 'Read the ADRs first.', 'See the architecture decision records.'):
        assert not any(row['kind'] == 'decision_records_unstated' for row in run(tmp_path / 'b', {**files, 'AGENTS.md': line + '\n'}))


def test_only_superseded_records_leave_no_gap(tmp_path):
    files = {'docs/adr/0001-x.md': '# X\n\nStatus: superseded by 2\n', 'AGENTS.md': 'Run `make test`.\n'}
    assert not any(row['kind'] == 'decision_records_unstated' for row in run(tmp_path, files))


PATTERNS = {'package.json': package(), 'packages/a/package.json': {'name': 'a', 'dependencies': {'b': '*'}}, 'packages/b/package.json': {'name': 'b'},
            'src/generated/x.ts': '', 'db/migrations/001.sql': ''}


def test_each_detected_pattern_the_harness_never_states_is_a_gap(tmp_path):
    found = {row['subject']: row for row in run(tmp_path, {**PATTERNS, 'AGENTS.md': 'Run `make test`.\n'}) if row['kind'] == 'pattern_not_stated'}
    assert sorted(found) == ['generated_code', 'migrations'] or sorted(found) == ['generated_code', 'migrations', 'workspace_dependencies']
    assert found['generated_code']['proposed_update'] == dict(action='state_pattern', pattern='generated_code', paths=['src/generated'])
    assert found['migrations']['evidence'] == dict(directories=1)


@pytest.mark.parametrize('line', ['Never edit `src/generated`.', 'Generated code is rebuilt by `make gen`.', 'Migrations live in db/migrations.'])
def test_a_stated_pattern_is_not_a_gap(tmp_path, line):
    subjects = {row['subject'] for row in run(tmp_path, {**PATTERNS, 'AGENTS.md': line + '\n'}) if row['kind'] == 'pattern_not_stated'}
    assert not subjects & {'generated_code' if 'enerated' in line else 'migrations'}


def test_workspace_edges_without_a_word_are_a_gap_and_a_word_clears_it(tmp_path):
    files = {k: v for k, v in PATTERNS.items() if k.startswith(('package', 'packages'))}
    assert ('pattern_not_stated', 'workspace_dependencies') in kinds(run(tmp_path, {**files, 'AGENTS.md': 'Run `make test`.\n'}))
    assert ('pattern_not_stated', 'workspace_dependencies') not in kinds(run(tmp_path / 'b', {**files, 'AGENTS.md': 'Package `a` depends on workspace package `b`.\n'}))


def test_a_repository_without_decisions_or_patterns_yields_nothing(tmp_path):
    assert not [row for row in run(tmp_path, {'package.json': package({'test': 'jest'}), 'AGENTS.md': 'Run `npm test`.\n'})
                if row['kind'] in ('decision_contradicted', 'superseded_decision_stated', 'decision_records_unstated', 'pattern_not_stated')]


def test_finding_ids_are_stable_across_line_moves(tmp_path):
    files = {**PATTERNS, 'AGENTS.md': 'Run `make test`.\n'}
    first = {row['id'] for row in run(tmp_path / 'a', files)}
    assert first == {row['id'] for row in run(tmp_path / 'b', {**files, 'AGENTS.md': '\n\n' + files['AGENTS.md']})}


def test_the_record_bound_and_hostile_files_never_raise(tmp_path):
    files = {f'docs/adr/{i:04d}-x.md': '# X\n\nStatus: accepted\n' for i in range(architecture.MAX_RECORDS + 5)}
    assert len(arch(tmp_path, {**files, 'docs/adr/0999-bin.md': '\x00\xff'})['decisions']) <= architecture.MAX_RECORDS
