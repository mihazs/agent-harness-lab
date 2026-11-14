"""`ahl improve --scope`: project by default, user-level skills only on explicit request. Synthetic homes only."""
import contextlib
import errno
import hashlib
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab import config
from agent_harness_lab.audit.precedence import LOAD_ORDER
from agent_harness_lab.cli import main
from agent_harness_lab.promotion.journal import Journal
from tests.support.one_call import one as one

BOM = b'\xef\xbb\xbf'
SCOPE_KEYS = {'scope', 'layers', 'layers_evaluated', 'layers_changed', 'changed_files', 'recommended_files', 'blocked', 'status', 'output'}
ROW_KEYS = {'layer', 'evaluated', 'status', 'changed_files', 'recommended_files', 'reason', 'root'}


def skill(name):
    return b'---\nname: %s\ndescription: Use for synthetic checks.\n---\nKeep the body intact.\n' % name.encode()


def tree(root, *, times=True):
    return {str(path.relative_to(root)): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode, path.stat().st_mtime_ns if times else None)
            for path in sorted(Path(root).rglob('*')) if path.is_file()}


class Home:
    """A synthetic user-level harness for both runtimes plus files no scope may ever edit."""
    def __init__(self, root):
        self.path = root / 'home'
        self.claude, self.codex = self.path / '.claude', self.path / '.agents'
        self.add(self.claude / 'skills/user-claude/SKILL.md', BOM + skill('user-claude'))
        self.add(self.codex / 'skills/user-codex/SKILL.md', BOM + skill('user-codex'))
        for decoy in ('.claude/CLAUDE.md', '.claude/settings.json', '.codex/AGENTS.md', '.codex/config.toml', '.claude/projects/p/memory/note.md'):
            self.add(self.path / decoy, b'synthetic user file\n')

    def add(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o640)


@pytest.fixture
def home(one, monkeypatch):
    synthetic = Home(one.root)
    monkeypatch.setenv('HOME', str(synthetic.path))
    monkeypatch.delenv('CLAUDE_CONFIG_DIR', raising=False)
    return synthetic


def fail(one, *args):
    with contextlib.redirect_stdout(io.StringIO()), pytest.raises(SystemExit) as raised:
        main(['improve', str(one.project), *args])
    return raised.value.code


def message(one, capsys, *args):
    capsys.readouterr()
    code = fail(one, *args)
    return code, capsys.readouterr().err


def test_default_scope_never_touches_user_level_files(one, home):
    before = tree(home.path)
    for flags in ((), ('--scope', 'project')):
        code, result = one.invoke('--repair-contracts', *flags)
        assert (code, result['status'], result['scope']) == (0, 'applied', 'project')
        assert (result['layers_evaluated'], result['layers_changed'], result['changed_files']) == (['project'], ['project'], ['.agents/skills/example/SKILL.md'])
        assert one.skill.read_bytes() == one.valid
        assert tree(home.path) == before  # contents, modes and modification times of every user-level file
        one.skill.write_bytes(BOM + one.valid)


@pytest.mark.parametrize('runtime,layer,decoy', [('claude', '.claude', '.agents'), ('codex', '.agents', '.claude')])
def test_global_scope_changes_only_the_named_location_and_rolls_back(one, home, runtime, layer, decoy):
    name = 'user-' + runtime
    target = home.path / layer / 'skills' / name / 'SKILL.md'
    before, project = tree(home.path), (one.skill.read_bytes(), one.skill.stat().st_mtime_ns)
    code, result = one.invoke('--repair-contracts', '--scope', 'global', '--agent', runtime)
    assert (code, result['status'], result['scope']) == (0, 'applied', 'global')
    assert (result['layers_evaluated'], result['layers_changed'], result['changed_files']) == (['global'], ['global'], [f'global:skills/{name}/SKILL.md'])
    assert target.read_bytes() == skill(name)
    assert (one.skill.read_bytes(), one.skill.stat().st_mtime_ns) == project  # project files are not edited
    after = tree(home.path)
    assert {path for path in after if after[path] != before[path]} == {f'{layer}/skills/{name}/SKILL.md'}
    # backup, content-hash receipt and verified rollback
    journal = Journal(home.path / layer, result['journal'])
    record = journal.load(result['deployment'])
    original = BOM + skill(name)
    assert record['status'] == 'committed'
    assert record['before'][f'skills/{name}/SKILL.md']['sha256'] == hashlib.sha256(original).hexdigest()
    assert record['after'][f'skills/{name}/SKILL.md']['sha256'] == hashlib.sha256(skill(name)).hexdigest()
    assert result['layers'][0]['root'] == str(home.path / layer) and result['rollback'].endswith(str(home.path / layer))
    with contextlib.redirect_stdout(io.StringIO()):
        assert main(['rollback', result['deployment'], '--project', str(home.path / layer)]) == 0
    assert target.read_bytes() == original
    assert tree(home.path, times=False) == {path: (digest, mode, None) for path, (digest, mode, _) in before.items()}


def test_only_skill_headers_of_real_bundles_are_opened(one, home):
    bundle = home.claude / 'skills/user-claude'
    home.add(bundle / '.env', b'SYNTHETIC_SECRET=1\n')
    home.add(home.claude / 'skills/second/SKILL.md', BOM + skill('second'))
    home.add(one.root / 'external-bundle/SKILL.md', BOM + skill('external'))
    (home.claude / 'skills/linked').symlink_to(one.root / 'external-bundle', target_is_directory=True)
    code, result = one.invoke('--repair-contracts', '--scope', 'global', '--agent', 'claude')
    baseline = Path(result['output']) / 'baseline'
    assert code == 0 and result['changed_files'] == ['global:skills/second/SKILL.md', 'global:skills/user-claude/SKILL.md']
    assert {str(path.relative_to(baseline)) for path in baseline.rglob('*') if path.is_file()} == {'skills/second/SKILL.md', 'skills/user-claude/SKILL.md'}
    assert (one.root / 'external-bundle/SKILL.md').read_bytes() == BOM + skill('external')  # a linked bundle is never read or edited
    assert (bundle / '.env').read_bytes() == b'SYNTHETIC_SECRET=1\n'


def test_a_frozen_user_level_location_is_refused(one, home, capsys):
    (home.claude / '.ahl-read-only').write_text('')
    before = tree(home.path)
    code, error = message(one, capsys, '--repair-contracts', '--scope', 'global', '--agent', 'claude')
    assert code == 2 and 'read-only' in error and tree(home.path) == before


def test_project_configuration_cannot_reach_user_level_paths(one, home):
    for name in ('../home/.claude/skills/user-claude/SKILL.md', str(home.claude / 'skills/user-claude/SKILL.md')):
        with pytest.raises(ValueError):
            config.validate(config.defaults() | dict(harness_paths=[name]))


def test_claude_config_directory_names_the_claude_location(one, home, monkeypatch):
    other = one.root / 'claude-config'
    home.add(other / 'skills/elsewhere/SKILL.md', BOM + skill('elsewhere'))
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(other))
    before = tree(home.path)
    code, result = one.invoke('--repair-contracts', '--scope', 'global', '--agent', 'claude')
    assert (code, result['changed_files']) == (0, ['global:skills/elsewhere/SKILL.md'])
    assert tree(home.path) == before


@pytest.mark.parametrize('runtime', ['claude', 'codex'])
def test_project_and_global_layers_are_attributed_in_the_runtimes_documented_order(one, home, runtime):
    layer = home.claude if runtime == 'claude' else home.codex
    for owner in (layer / 'skills', one.project / '.agents/skills'):
        home.add(owner / 'shared/SKILL.md', BOM + skill('shared'))
    code, result = one.invoke('--repair-contracts', '--scope', 'project+global', '--agent', runtime)
    assert (code, result['status'], result['scope']) == (0, 'applied', 'project+global')
    rows = {row['layer']: row for row in result['layers']}
    assert [row['layer'] for row in result['layers']] == result['precedence']['layer_order'] == ['global', 'project']
    assert result['precedence']['documented_load_order'] == LOAD_ORDER[runtime]
    assert result['precedence']['out_of_scope'] == (['managed', 'local'] if runtime == 'claude' else [])
    assert result['precedence']['shared_skill_names'] == ['shared'] and result['precedence']['same_name_skills'] == 'effective_layer_unknown'
    assert rows['global']['changed_files'] == sorted([f'skills/{runtime_skill}/SKILL.md' for runtime_skill in ('shared', 'user-' + runtime)])
    assert rows['project']['changed_files'] == ['.agents/skills/example/SKILL.md', '.agents/skills/shared/SKILL.md']
    assert result['layers_changed'] == ['global', 'project']
    assert result['changed_files'] == [*(f'global:{name}' for name in rows['global']['changed_files']), *rows['project']['changed_files']]
    for file in (layer / 'skills/shared/SKILL.md', one.project / '.agents/skills/shared/SKILL.md', one.skill):
        assert file.read_bytes() == skill(file.parent.name)
    assert rows['global']['rollback'].endswith(str(layer)) and rows['project']['rollback'].endswith(str(one.project))
    assert rows['global']['deployment'] != rows['project']['deployment']
    for row in rows.values():  # one run, one receipt, one plan per layer
        assert Path(row['plan']).parent.parent == Path(result['output'])
    assert json.loads((Path(result['output']) / 'workflow.json').read_text())['layers'] == result['layers']
    for row in rows.values():
        with contextlib.redirect_stdout(io.StringIO()):
            assert main(['rollback', row['deployment'], '--project', row['root']]) == 0
    assert one.skill.read_bytes() == BOM + one.valid


def test_project_configuration_cannot_select_global_scope(one, home, capsys):
    document = config.defaults() | dict(scope='global', harness_paths=['.agents/skills/example/SKILL.md'])
    (one.project / 'ahl.json').write_text(json.dumps(document))
    before, project = tree(home.path), tree(one.project)
    for flags in ((), ('--scope', 'global', '--agent', 'claude'), ('--scope', 'project')):
        code, error = message(one, capsys, '--repair-contracts', *flags)
        assert code == 2 and 'cannot select a harness scope' in error and '--scope' in error
    assert tree(home.path) == before and tree(one.project) == project
    assert not (one.root / 'state').exists()  # nothing was created, not even private run state


def test_user_level_plan_does_not_carry_the_projects_configuration(one, home):
    (one.project / 'ahl.json').write_text(json.dumps(config.defaults() | dict(harness_paths=['.agents/skills/example/SKILL.md'])))
    code, result = one.invoke('--repair-contracts', '--scope', 'project+global', '--agent', 'claude')
    assert [(row['layer'], row['status']) for row in result['layers']] == [('global', 'applied'), ('project', 'inconclusive')]
    plans = {row['layer']: json.loads(Path(row['plan']).read_text()) for row in result['layers']}
    references = {layer: dict(plan['harness_change_plan']['references']) for layer, plan in plans.items()}
    assert plans['global']['configuration_sha256'] is None and references['global']['configuration'] is None
    assert plans['project']['configuration_sha256'] and references['project']['configuration'] == plans['project']['configuration_sha256']
    assert references['global']['authorization'] != references['project']['authorization']


def test_user_level_authorization_is_independent_of_the_projects_settings(one, home):
    digests = []
    for name in ('example', 'other'):  # two project configurations that differ in what they govern
        (one.project / 'ahl.json').write_text(json.dumps(config.defaults() | dict(harness_paths=[f'.agents/skills/{name}/SKILL.md'])))
        (home.claude / 'skills/user-claude/SKILL.md').write_bytes(BOM + skill('user-claude'))
        code, result = one.invoke('--repair-contracts', '--scope', 'global', '--agent', 'claude')
        assert (code, result['layers_changed']) == (0, ['global'])
        plan = json.loads(Path(result['layers'][0]['plan']).read_text())
        digests.append(dict(plan['harness_change_plan']['references'])['authorization'])
    assert digests[0] == digests[1]


@pytest.mark.parametrize('flags,needle', [
    ((), '--agent codex or --agent claude'),
    (('--agent', 'auto'), '--agent codex or --agent claude'),
    (('--agent', 'responses'), 'responses runtime has no user-level harness'),
])
def test_unnamed_runtime_is_an_actionable_error_not_a_guess(one, home, capsys, flags, needle):
    before = tree(home.path)
    for scope in ('global', 'project+global'):
        code, error = message(one, capsys, '--repair-contracts', '--scope', scope, *flags)
        assert code == 2 and needle in error
    assert tree(home.path) == before and one.skill.read_bytes() == BOM + one.valid


def test_project_configuration_and_environment_never_choose_the_runtime(one, home, capsys, monkeypatch):
    monkeypatch.setenv('CLAUDECODE', '1')  # an invoking host marker is not a choice either
    settings = config.prepare(dict(profile='cooperative', runtime=dict(name='claude', executable='/synthetic/claude'),
                                   harness_paths=['.agents/skills/example/SKILL.md']))
    config.create(one.project, settings)
    before = tree(home.path)
    code, error = message(one, capsys, '--repair-contracts', '--scope', 'global')
    assert code == 2 and '--agent codex or --agent claude' in error
    assert tree(home.path) == before


def test_missing_or_unsafe_location_is_an_actionable_error(one, home, capsys):
    before = tree(home.path)
    for path in (home.claude / 'skills',):
        for child in sorted(path.rglob('*'), reverse=True):
            child.unlink() if child.is_file() else child.rmdir()
        path.rmdir()
    code, error = message(one, capsys, '--repair-contracts', '--scope', 'global', '--agent', 'claude')
    assert code == 2 and 'skill directory' in error and 'does not exist' in error and '--scope project' in error
    real = one.root / 'real-agents'
    home.codex.rename(real)
    home.codex.symlink_to(real, target_is_directory=True)
    code, error = message(one, capsys, '--repair-contracts', '--scope', 'project+global', '--agent', 'codex')
    assert code == 2 and 'symlink' in error
    assert (real / 'skills/user-codex/SKILL.md').read_bytes() == BOM + skill('user-codex')
    assert one.skill.read_bytes() == BOM + one.valid and before.keys() >= {'.claude/CLAUDE.md'}


@pytest.mark.parametrize('where', ['home', 'claude', 'skills', 'config-dir'])
def test_default_scope_refuses_a_project_that_is_a_user_level_location(one, home, capsys, monkeypatch, where):
    if where == 'config-dir':
        monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(one.root / 'claude-config'))
        home.add(one.root / 'claude-config/skills/cfg/SKILL.md', BOM + skill('cfg'))
    project = dict(home=home.path, claude=home.claude, skills=home.claude / 'skills', **{'config-dir': one.root / 'claude-config'})[where]
    before = tree(home.path)
    with contextlib.redirect_stdout(io.StringIO()), pytest.raises(SystemExit) as raised:
        main(['improve', str(project), '--repair-contracts'])
    error = capsys.readouterr().err
    assert raised.value.code == 2 and 'overlaps a user-level harness location' in error and '--scope global' in error
    assert tree(home.path) == before and not (one.root / 'state/runs').exists() and not (one.root / 'state/promotions').exists()
    out = io.StringIO()  # the explicit scope is the only way in
    with contextlib.redirect_stdout(out):
        code = main(['improve', str(project), '--repair-contracts', '--scope', 'global', '--agent', 'claude'])
    assert code == 0 and json.loads(out.getvalue())['layers_changed'] == ['global']


@pytest.mark.parametrize('where', ['home', 'claude', 'skills', 'codex', 'codex-skills', 'config-dir'])
def test_default_model_driven_run_refuses_a_user_level_project_before_any_work(one, home, capsys, monkeypatch, where):
    if where == 'config-dir':
        monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(one.root / 'claude-config'))
        home.add(one.root / 'claude-config/skills/cfg/SKILL.md', BOM + skill('cfg'))
    project = dict(home=home.path, claude=home.claude, skills=home.claude / 'skills', codex=home.codex,
                   **{'codex-skills': home.codex / 'skills', 'config-dir': one.root / 'claude-config'})[where]
    before = tree(home.path)
    with contextlib.redirect_stdout(io.StringIO()), pytest.raises(SystemExit) as raised:
        main(['improve', str(project), '--agent', 'claude'])
    error = capsys.readouterr().err
    assert raised.value.code == 2 and 'overlaps a user-level harness location' in error and '--scope global' in error
    assert tree(home.path) == before and not (one.root / 'state/runs').exists() and not (one.root / 'state/promotions').exists()


def test_project_and_global_layers_must_not_overlap(one, home, capsys):
    inside = home.claude / 'skills/work'
    home.add(inside / 'SKILL.md', BOM + skill('work'))
    for project in (home.claude, home.claude / 'skills'):
        with contextlib.redirect_stdout(io.StringIO()), pytest.raises(SystemExit) as raised:
            main(['improve', str(project), '--repair-contracts', '--scope', 'project+global', '--agent', 'claude'])
        assert raised.value.code == 2 and 'overlap' in capsys.readouterr().err
    assert (inside / 'SKILL.md').read_bytes() == BOM + skill('work')


def test_receipt_has_one_shape_for_every_scope(one, home):
    shapes = []
    for flags in ((), ('--scope', 'global', '--agent', 'claude'), ('--scope', 'project+global', '--agent', 'codex')):
        for path in (one.skill, home.claude / 'skills/user-claude/SKILL.md', home.codex / 'skills/user-codex/SKILL.md'):
            path.write_bytes(BOM + skill(path.parent.name if path != one.skill else 'example'))
        code, result = one.invoke('--repair-contracts', *flags)
        assert code == 0 and SCOPE_KEYS <= set(result)
        assert all(set(row) >= ROW_KEYS for row in result['layers'])
        shapes.append((result['status'], {key: type(result[key]) for key in SCOPE_KEYS}, [set(row) & ROW_KEYS for row in result['layers']][0]))
    assert len({json.dumps(shape[1], default=str, sort_keys=True) for shape in shapes}) == 1
    assert len({shape[0] for shape in shapes}) == 1 and all(shape[2] == ROW_KEYS for shape in shapes)


def test_model_driven_evaluation_of_user_level_layers_is_blocked_not_successful(one, home):
    before = tree(home.path)
    for scope, layers in (('global', ['global']), ('project+global', ['global', 'project'])):
        with patch('agent_harness_lab.cli.evaluator', side_effect=AssertionError('no model')):
            code, result = one.invoke('--scope', scope, '--agent', 'claude')
        assert (code, result['status'], result['reason']) == (3, 'blocked', 'global_scope_model_evaluation_unavailable')
        assert result['layers_evaluated'] == [] and result['layers_changed'] == [] and result['changed_files'] == []
        assert [(row['layer'], row['status']) for row in result['layers']] == [(layer, 'not_attempted') for layer in layers]
        assert [item['layer'] for item in result['blocked']] == layers
        assert 'repair-contracts' in result['remediation']
    assert tree(home.path) == before and one.skill.read_bytes() == BOM + one.valid


def test_default_model_driven_path_reports_the_same_scope_fields(one, home):
    with patch('agent_harness_lab.one_call.shutil.which', return_value='/synthetic/runtime'):
        code, result = one.invoke()
    assert (code, result['status'], result['reason']) == (3, 'blocked', 'select_one_runtime_with_agent')
    assert SCOPE_KEYS <= set(result) and result['scope'] == 'project'
    assert [(row['layer'], row['status']) for row in result['layers']] == [('project', 'blocked')]
    assert json.loads((Path(result['output']) / 'workflow.json').read_text())['layers'] == result['layers']


def test_an_input_error_after_a_committed_layer_is_still_reported_with_that_layer(one, home):
    real = Journal.prepare
    def prepare(self, *args, **kwargs):
        if self.project == one.project.resolve():
            raise ValueError('synthetic project conflict')
        return real(self, *args, **kwargs)
    with patch.object(Journal, 'prepare', prepare):
        code, result = one.invoke('--repair-contracts', '--scope', 'project+global', '--agent', 'claude')
    assert (code, result['status']) == (2, 'invalid_input')
    assert [(row['layer'], row['status']) for row in result['layers']] == [('global', 'applied'), ('project', 'invalid_input')]
    assert result['layers'][1]['error'] == 'synthetic project conflict' and result['layers_changed'] == ['global']


def test_a_blocked_layer_stops_the_run_and_the_next_layer_is_not_attempted(one, home):
    executable = home.claude / 'skills/user-claude/SKILL.md'
    executable.chmod(0o750)  # an executable skill file needs a hook contract, so its repair batch is blocked
    before = tree(home.path)
    code, result = one.invoke('--repair-contracts', '--scope', 'project+global', '--agent', 'claude')
    assert (code, result['status'], result['reason']) == (3, 'blocked', 'unresolved_operation_requires_hook_contract')
    assert [(row['layer'], row['status']) for row in result['layers']] == [('global', 'blocked'), ('project', 'not_attempted')]
    assert result['layers'][1]['reason'] == 'earlier_layer_blocked'
    assert result['layers_evaluated'] == ['global'] and result['layers_changed'] == [] and result['changed_files'] == []
    assert result['layers'][0]['recommended_files'] == ['skills/user-claude/SKILL.md'] and result['recommended_files'] == ['global:skills/user-claude/SKILL.md']
    assert tree(home.path) == before and one.skill.read_bytes() == BOM + one.valid


def test_a_recommendation_only_project_does_not_block_or_report_as_applied(one, home):
    settings = config.defaults() | dict(harness_paths=['.agents/skills/example/SKILL.md'])
    config.create(one.project, settings)
    code, result = one.invoke('--repair-contracts', '--scope', 'project+global', '--agent', 'claude')
    rows = {row['layer']: row for row in result['layers']}
    assert (code, result['status'], result['reason']) == (3, 'inconclusive', 'configuration_requires_recommendation_only')
    assert (rows['global']['status'], rows['project']['status']) == ('applied', 'inconclusive')  # the project file never constrains user-level files
    assert result['layers_changed'] == ['global'] and result['recommended_files'] == ['.agents/skills/example/SKILL.md']
    assert one.skill.read_bytes() == BOM + one.valid and (home.claude / 'skills/user-claude/SKILL.md').read_bytes() == skill('user-claude')


def test_a_failure_after_the_first_layer_still_reports_what_was_changed(one, home):
    real = Journal.prepare
    def prepare(self, *args, **kwargs):
        if self.project == one.project.resolve():
            raise OSError(errno.EIO, 'synthetic journal failure', str(one.root / 'state'))
        return real(self, *args, **kwargs)
    with patch.object(Journal, 'prepare', prepare):
        code, result = one.invoke('--repair-contracts', '--scope', 'project+global', '--agent', 'claude')
    assert (code, result['status']) == (1, 'execution_error')
    assert [(row['layer'], row['status']) for row in result['layers']] == [('global', 'applied'), ('project', 'execution_error')]
    assert result['layers_changed'] == ['global'] and result['changed_files'] == ['global:skills/user-claude/SKILL.md']
    assert (home.claude / 'skills/user-claude/SKILL.md').read_bytes() == skill('user-claude')
    assert one.skill.read_bytes() == BOM + one.valid


def test_budget_is_one_total_across_layers(one, home, capsys):
    code, result = one.invoke('--repair-contracts', '--scope', 'project+global', '--agent', 'claude', '--timeout', '60')
    assert (code, result['status']) == (0, 'applied')
    summary = capsys.readouterr().err
    assert 'budget_spent_seconds=' in summary and 'budget_spent_seconds=unknown' not in summary and 'changed_files=2' in summary
    with patch('agent_harness_lab.one_call.time.monotonic', side_effect=iter([0.0] + [1000.0] * 400)):
        code, result = one.invoke('--repair-contracts', '--scope', 'project+global', '--agent', 'claude', '--timeout', '60')
    assert code == 1 and result['status'] == 'budget_exhausted' and result['layers_changed'] == []


@pytest.mark.parametrize('prefix', [['improve'], ['skills', 'invoke']])
def test_skill_invocation_is_an_alias_with_the_same_scope_contract(one, home, prefix):
    for path, name in ((one.skill, 'example'), (home.claude / 'skills/user-claude/SKILL.md', 'user-claude')):
        path.write_bytes(BOM + skill(name))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main([*prefix, str(one.project), '--repair-contracts', '--scope', 'global', '--agent', 'claude'])
    result = json.loads(out.getvalue())
    assert (code, result['scope'], result['layers_changed']) == (0, 'global', ['global'])
    invocation = Path(result['output']) / 'invocation.json'
    assert invocation.exists() == (prefix[0] == 'skills')
    assert not invocation.exists() or json.loads(invocation.read_text())['scope'] == 'global'
