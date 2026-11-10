"""Synthetic scoped instruction rewrites and pruning (#285); every verdict is a declaration, never loading proof."""
import argparse
import hashlib
import json
import itertools
import shutil

import pytest

from agent_harness_lab.evaluation.trial_surfaces import stage_harness
from agent_harness_lab.one_call import check_scope
from agent_harness_lab.sources import instruction_validation as iv

CLAUDE = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='src')
CODEX = dict(schema_version=1, runtime='codex', version='0.166.0', task_cwd='src', fallback_filenames=[])
TASK = 'src/app/view.ts'
# Evaluation manifests the proposer does not author: tasks that run in src, and tasks that declare no cwd (root).
IN_SRC = dict(tasks=[dict(id='edit', cwd='src'), dict(id='control', cwd='src')])
AT_ROOT, MIXED = dict(tasks=[dict(id='edit')]), dict(tasks=[dict(id='edit', cwd='src'), dict(id='control')])
FILES = {
    'CLAUDE.md': 'Synthetic root guidance.\n',
    'src/CLAUDE.md': 'Synthetic source guidance.\nAlways run the synthetic tests.\n',
    '.claude/rules/ts.md': '---\npaths:\n  - "src/**/*.ts"\n---\nUse synthetic strict types.\n',
    '.claude/rules/docs.md': '---\npaths:\n  - "docs/**"\n---\nSynthetic docs rule.\n',
    'docs/CLAUDE.md': 'Synthetic unrelated docs guidance.\n',
}


def tree_digest(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*')) if p.is_file()}


class InstructionWorkspace:
    """A synthetic project with frozen baseline and candidate instruction trees."""

    def __init__(self, root):
        self.root = root; self.staged = itertools.count()
        self.project = self.root / 'project'
        (self.project / 'src/app').mkdir(parents=True)
        for name, text in FILES.items(): self.write(self.project, name, text)
        self.baseline = self.root / 'baseline'
        for name, text in FILES.items(): self.write(self.baseline, name, text)
        self.candidate = self.root / 'candidate'
        shutil.copytree(self.baseline, self.candidate)

    def write(self, root, name, text):
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text)

    def plan(self, profile=CLAUDE, **fields):
        return dict(dict(schema_version=1, profile=profile, task_paths=[TASK],
                         required=[dict(id='tests', text='Always run the synthetic tests.')]), **fields)

    def validate(self, plan=None, **options):
        return iv.validate(self.baseline, self.candidate, plan or self.plan(), project=self.project, **options)

    def trials(self, failed=(), surfaces=True):
        """I13 trial records whose workspace_surfaces come from the real stage_harness copy of each version."""
        paths = sorted(tree_digest(self.baseline).keys() | tree_digest(self.candidate).keys())
        records, base = [], self.root / f'trials-{next(self.staged)}'
        for version, root in (('baseline', self.baseline), ('candidate', self.candidate)):
            for split in ('development', 'regression'):
                workspace = base / version / split; workspace.mkdir(parents=True)
                record = dict(task=split, version=version, split=split,
                              status='failed' if (version, split) in failed else 'passed')
                if surfaces: record['workspace_surfaces'] = stage_harness(root, workspace, paths)
                records.append(record)
        return records


@pytest.fixture
def ws(tmp_path):
    return InstructionWorkspace(tmp_path)


def test_a_governing_scope_edit_is_valid_and_never_claims_loading(ws):
    ws.write(ws.candidate, 'src/CLAUDE.md', FILES['src/CLAUDE.md'] + 'Check the synthetic build.\n')
    document = ws.validate()
    assert (document['status'], document['reasons']) == ('valid', [])
    assert document['scope']['instruction_dirs'] == ['src']
    assert document['scope']['rules'] == ['.claude/rules/ts.md']
    assert document['scope']['source'] == 'proposer_declared_plan'  # provisional until bound
    bound = iv.bind(document, ws.plan(), MIXED, cwd_honored=True)
    assert (bound['status'], bound['reasons']) == ('valid', [])
    assert (bound['scope']['source'], bound['scope']['task_cwds']) == ('evaluation_manifest_task_cwds', ['.', 'src'])
    assert bound['scope']['instruction_dirs'] == document['scope']['instruction_dirs']
    # Only the declared task path makes the ts rule governing, so the bound scope drops it.
    assert (bound['scope']['rules'], bound['scope']['task_paths_source']) == ([], 'proposer_declared')
    assert [(o['path'], o['operation']) for o in document['operations']] == [('src/CLAUDE.md', 'update')]
    assert (document['observed_loading'], document['effective'], document['adoption_authority']) == ('unknown', 'unknown', False)
    assert document['removal']['disposition'] == 'not_applicable'
    assert 'Always run' not in json.dumps(document)


def test_useful_rule_deletion_fails_its_behavioral_control(ws):
    (ws.candidate / '.claude/rules/ts.md').unlink()
    document = ws.validate()
    assert document['status'] == 'valid'
    assert [(o['path'], o['operation']) for o in document['operations']] == [('.claude/rules/ts.md', 'remove')]
    assert document['removal']['disposition'] == 'requires_behavioral_controls'
    rejected = iv.removal_disposition(document, ws.trials(failed={('candidate', 'regression')}))
    assert (rejected['disposition'], rejected['reason']) == ('rejected', 'behavioral_control_failed')


def test_redundant_rule_removal_is_recommended_only_under_faithful_passing_controls(ws):
    (ws.candidate / '.claude/rules/ts.md').unlink()
    document = ws.validate()
    recommended = iv.removal_disposition(document, ws.trials())
    assert (recommended['disposition'], recommended['certification']) == ('recommended', 'required')
    assert iv.removal_disposition(document, ws.trials(surfaces=False))['reason'] == 'workspace_surfaces_unverified'
    assert iv.removal_disposition(document, [t for t in ws.trials() if t['split'] != 'regression'])['reason'] == 'behavioral_controls_missing'
    # A workspace that still held the removed rule did not test the removal.
    trials = ws.trials()
    for trial in trials:
        if trial['version'] == 'candidate': trial['workspace_surfaces'] = trials[0]['workspace_surfaces']
    assert iv.removal_disposition(document, trials)['reason'] == 'workspace_surfaces_unverified'


def test_an_unstaged_project_rule_edit_carries_its_baseline_hash(ws):
    # Only root and source instructions are staged; the rule the candidate guts lives only in the project.
    for root in (ws.baseline, ws.candidate): shutil.rmtree(root / '.claude')
    ws.write(ws.candidate, '.claude/rules/ts.md', '---\npaths:\n  - "src/**/*.ts"\n---\n')
    document = ws.validate()
    assert document['status'] == 'valid'
    baseline_hash = hashlib.sha256(FILES['.claude/rules/ts.md'].encode()).hexdigest()
    assert [(o['path'], o['operation'], o['before_sha256']) for o in document['operations']] == [('.claude/rules/ts.md', 'update', baseline_hash)]
    assert document['removal']['required']
    # The baseline trial workspace lacked the rule, so its controls never compared the guidance removed.
    removal = iv.removal_disposition(document, ws.trials())
    assert (removal['disposition'], removal['reason']) == ('inconclusive', 'workspace_surfaces_unverified')


def test_imported_project_documents_belong_to_the_closure(ws):
    ws.write(ws.project, 'src/guide.md', 'Always run the synthetic tests.\n')
    ws.write(ws.project, 'src/style.md', 'Synthetic style notes.\n')
    for root in (ws.project, ws.baseline, ws.candidate): ws.write(root, 'src/CLAUDE.md', 'Synthetic source guidance.\n@guide.md\n')
    document = ws.validate()
    assert (document['status'], document['reasons']) == ('valid', [])
    assert document['required'][0]['baseline_active'] == True
    ws.write(ws.candidate, 'src/CLAUDE.md', 'Synthetic source guidance.\n@guide.md\n@style.md\n')
    added = ws.validate()
    assert (added['status'], added['reasons']) == ('valid', [])
    assert added['measurements']['candidate_active_bytes'] > added['measurements']['baseline_active_bytes']
    ws.write(ws.candidate, 'src/CLAUDE.md', 'Synthetic source guidance.\n@guide.md\n@absent.md\n')
    assert ws.validate()['reasons'] == ['missing_import']
    # A personal file is never read into the closure, even when imported.
    ws.write(ws.project, 'src/CLAUDE.local.md', 'Synthetic personal notes.\n')
    ws.write(ws.candidate, 'src/CLAUDE.md', 'Synthetic source guidance.\n@guide.md\n@CLAUDE.local.md\n')
    assert 'Synthetic personal notes' not in json.dumps(ws.validate())


def test_consolidation_preserves_scope(ws):
    ws.write(ws.candidate, 'src/CLAUDE.md', FILES['src/CLAUDE.md'] + 'Use synthetic strict types.\n')
    (ws.candidate / '.claude/rules/ts.md').unlink()
    document = ws.validate()
    assert document['status'] == 'valid'
    assert {o['path']: o['operation'] for o in document['operations']}['.claude/rules/ts.md'] == 'consolidate'
    assert not document['removal']['required']
    # Consolidating into a rule whose paths do not match the task path narrows the scope and is blocked.
    shutil.rmtree(ws.candidate); shutil.copytree(ws.baseline, ws.candidate)
    ws.write(ws.candidate, '.claude/rules/docs.md', FILES['.claude/rules/docs.md'] + 'Always run the synthetic tests.\n')
    ws.write(ws.candidate, 'src/CLAUDE.md', 'Synthetic source guidance.\n')
    blocked = ws.validate()
    assert blocked['status'] == 'blocked'
    assert 'rule_matches_no_task_path' in blocked['reasons']
    assert 'required_text_left_active_context' in blocked['reasons']


def test_byte_reduction_from_moving_text_out_of_context_does_not_count(ws):
    ws.write(ws.candidate, 'src/CLAUDE.md', 'Always run the synthetic tests.\n')
    ws.write(ws.candidate, '.claude/skills/build/SKILL.md', 'Synthetic source guidance.\n')
    moved = ws.validate()
    assert moved['status'] == 'valid'
    measured = moved['measurements']
    assert measured['candidate_active_bytes'] < measured['baseline_active_bytes']
    assert (measured['credited_reduction_bytes'], measured['selects_winner']) == (0, False)
    assert moved['removal']['moved_out_of_active_context_lines'] == 1
    assert moved['removal']['disposition'] == 'requires_behavioral_controls'
    # Moving required text out of the active context blocks the candidate.
    ws.write(ws.candidate, 'src/CLAUDE.md', 'Synthetic source guidance.\n')
    ws.write(ws.candidate, '.claude/skills/build/SKILL.md', 'Always run the synthetic tests.\n')
    assert ws.validate()['reasons'] == ['required_text_left_active_context']
    ws.write(ws.candidate, '.claude/skills/build/SKILL.md', 'Unrelated synthetic skill.\n')
    assert ws.validate()['reasons'] == ['required_text_removed']


def test_genuine_pruning_is_credited_but_still_needs_behavioral_controls(ws):
    ws.write(ws.candidate, 'src/CLAUDE.md', 'Always run the synthetic tests.\n')
    pruned = ws.validate()
    assert pruned['status'] == 'valid'
    assert pruned['measurements']['credited_reduction_bytes'] > 0
    assert pruned['removal']['disposition'] == 'requires_behavioral_controls'


def test_stale_baseline_or_changed_candidate_invalidates(ws):
    ws.write(ws.candidate, 'src/CLAUDE.md', FILES['src/CLAUDE.md'] + 'Check the synthetic build.\n')
    plan = ws.plan()
    document = iv.bind(ws.validate(plan), plan, IN_SRC, cwd_honored=True)
    assert iv.current(document, plan, ws.baseline, ws.candidate, project=ws.project) == []
    ws.write(ws.project, 'src/app/CLAUDE.md', 'Synthetic concurrent guidance.\n')
    assert iv.current(document, plan, ws.baseline, ws.candidate, project=ws.project) == ['stale_baseline']
    (ws.project / 'src/app/CLAUDE.md').unlink()
    ws.write(ws.project, 'src/CLAUDE.md', 'Synthetic concurrent edit.\n')
    assert 'stale_baseline' in ws.validate(plan)['reasons']
    ws.write(ws.project, 'src/CLAUDE.md', FILES['src/CLAUDE.md'])
    ws.write(ws.candidate, 'src/CLAUDE.md', 'Synthetic modified candidate.\nAlways run the synthetic tests.\n')
    assert iv.current(document, plan, ws.baseline, ws.candidate, project=ws.project) == ['candidate_changed']


def test_import_cycle_and_missing_import_block(ws):
    ws.write(ws.candidate, 'src/CLAUDE.md', FILES['src/CLAUDE.md'] + '@missing.md\n')
    assert ws.validate()['reasons'] == ['missing_import']
    ws.write(ws.baseline, 'src/app/CLAUDE.md', 'Synthetic app guidance.\n')
    ws.write(ws.project, 'src/app/CLAUDE.md', 'Synthetic app guidance.\n')
    ws.write(ws.candidate, 'src/CLAUDE.md', FILES['src/CLAUDE.md'] + '@app/CLAUDE.md\n')
    ws.write(ws.candidate, 'src/app/CLAUDE.md', 'Synthetic app guidance.\n@../CLAUDE.md\n')
    assert 'import_cycle' in ws.validate()['reasons']


def test_undeclared_shadowing_change_blocks_and_a_declared_one_passes(ws):
    for root in (ws.project, ws.baseline, ws.candidate):
        for name in FILES: (root / name).unlink()
        shutil.rmtree(root / '.claude')
        ws.write(root, 'AGENTS.md', 'Synthetic root agents.\n')
        ws.write(root, 'src/AGENTS.md', 'Synthetic source agents.\nAlways run the synthetic tests.\n')
    ws.write(ws.candidate, 'src/AGENTS.override.md', 'Synthetic override.\nAlways run the synthetic tests.\n')
    plan = ws.plan(CODEX)
    blocked = iv.validate(ws.baseline, ws.candidate, plan, runtime='codex', project=ws.project)
    assert blocked['reasons'] == ['undeclared_shadowing_change']
    declared = dict(plan, shadowing_changes=[dict(change='add', shadow='src/AGENTS.override.md', shadowed='src/AGENTS.md')])
    assert iv.validate(ws.baseline, ws.candidate, declared, runtime='codex', project=ws.project)['status'] == 'valid'


def test_claude_agents_md_shadowing_is_a_shadowing_change(ws):
    (ws.project / 'src/CLAUDE.md').unlink(); (ws.baseline / 'src/CLAUDE.md').unlink(); (ws.candidate / 'src/CLAUDE.md').unlink()
    for root in (ws.project, ws.baseline, ws.candidate):
        ws.write(root, 'src/app/AGENTS.md', 'Synthetic app agents.\nAlways run the synthetic tests.\n')
    ws.write(ws.candidate, 'src/app/CLAUDE.md', 'Synthetic app guidance.\nAlways run the synthetic tests.\n')
    assert 'undeclared_shadowing_change' in ws.validate()['reasons']


def test_rule_paths_edit_must_keep_the_declared_task_path_matched(ws):
    ws.write(ws.candidate, '.claude/rules/ts.md', '---\npaths:\n  - "lib/**/*.ts"\n---\nUse synthetic strict types.\n')
    assert 'rule_paths_dropped_task_path' in ws.validate()['reasons']
    removed = ws.validate(ws.plan(task_paths=[TASK, 'lib/x.ts'], removed_task_paths=[TASK]))
    assert 'rule_paths_dropped_task_path' not in removed['reasons']


def test_edits_outside_the_governing_scope_are_blocked(ws):
    ws.write(ws.candidate, 'CLAUDE.md', 'Synthetic root rewrite.\n')
    assert ws.validate()['reasons'] == ['outside_governing_scope']
    shutil.rmtree(ws.candidate); shutil.copytree(ws.baseline, ws.candidate)
    ws.write(ws.candidate, 'docs/CLAUDE.md', 'Synthetic unrelated rewrite.\n')
    assert ws.validate()['reasons'] == ['outside_governing_scope']
    shutil.rmtree(ws.candidate); shutil.copytree(ws.baseline, ws.candidate)
    ws.write(ws.candidate, '.claude/rules/docs.md', FILES['.claude/rules/docs.md'] + 'More synthetic docs.\n')
    assert ws.validate()['reasons'] == ['rule_matches_no_task_path']
    shutil.rmtree(ws.candidate); shutil.copytree(ws.baseline, ws.candidate)
    ws.write(ws.candidate, 'src/AGENTS.override.md', 'Synthetic Codex-only file.\n')
    assert ws.validate()['reasons'] == ['not_a_runtime_instruction_surface']


def unrelated_docs_cwd(ws):
    # The proposer declares task_cwd docs, which no evaluated task runs in, to make docs/CLAUDE.md governing.
    ws.write(ws.candidate, 'docs/CLAUDE.md', 'Synthetic unrelated rewrite.\n')
    plan = ws.plan(dict(CLAUDE, task_cwd='docs'), task_paths=['docs/guide.md'], required=[])
    document = ws.validate(plan)
    assert (document['status'], document['scope']['source']) == ('valid', 'proposer_declared_plan')
    assert 'docs' in document['scope']['instruction_dirs']
    assert iv.current(document, plan, ws.baseline, ws.candidate, project=ws.project) == ['scope_unbound']
    return plan, document


@pytest.mark.parametrize('manifest', [pytest.param(IN_SRC, id='in-src'), pytest.param(AT_ROOT, id='at-root')])
def test_an_unrelated_declared_task_cwd_cannot_reach_a_sibling_instruction_or_rule(ws, manifest):
    plan, document = unrelated_docs_cwd(ws)
    refused = iv.bind(document, plan, manifest, cwd_honored=True)
    assert (refused['status'], refused['scope']['source']) == ('blocked', 'evaluation_manifest_task_cwds')
    assert refused['reasons'] == ['task_cwd_not_evaluated'] + (['task_path_not_evaluated'] if manifest is IN_SRC else [])
    assert dict(reason='task_cwd_not_evaluated', path='docs') in refused['findings']
    assert iv.current(refused, plan, ws.baseline, ws.candidate, project=ws.project) == ['not_valid']


def test_a_task_path_outside_every_evaluated_cwd_cannot_reach_a_sibling_rule(ws):
    # A sibling rule reached through a task path outside every evaluated cwd is refused as well.
    ws.write(ws.candidate, '.claude/rules/docs.md', FILES['.claude/rules/docs.md'] + 'More synthetic docs.\n')
    plan = ws.plan(task_paths=[TASK, 'docs/guide.md'])
    document = ws.validate(plan)
    assert document['status'] == 'valid'
    assert '.claude/rules/docs.md' in document['scope']['rules']
    refused = iv.bind(document, plan, IN_SRC, cwd_honored=True)
    assert (refused['status'], refused['reasons']) == ('blocked', ['task_path_not_evaluated', 'task_path_scope_unbound'])
    assert refused['findings'] == [dict(reason='task_path_not_evaluated', path='docs/guide.md'),
                                   dict(reason='task_path_scope_unbound', path='.claude/rules/docs.md')]
    # Binding never relaxes a blocked document, and a manifest without tasks binds nothing.
    blocked = dict(document, status='blocked', reasons=['import_cycle'])
    assert iv.bind(blocked, plan, IN_SRC) is blocked
    for manifest in ({}, dict(tasks=[]), dict(tasks=[dict(id='edit', cwd='../outside')])):
        with pytest.raises(ValueError): iv.bind(document, plan, manifest, cwd_honored=True)


def docs_task_path_at_root(ws):
    # Every evaluated task runs at the root, which contains every path, so the proposer declares a task path
    # under docs to make the on-demand docs/CLAUDE.md and the docs path rule governing.
    return ws.plan(dict(CLAUDE, task_cwd='.'), task_paths=['docs/guide.md'], required=[])


@pytest.mark.parametrize('manifest', [pytest.param(AT_ROOT, id='at-root'), pytest.param(MIXED, id='mixed')])
def test_a_declared_task_path_below_a_root_cwd_cannot_reach_a_sibling_instruction_or_rule(ws, manifest):
    ws.write(ws.candidate, 'docs/CLAUDE.md', 'Synthetic unrelated rewrite.\n')
    ws.write(ws.candidate, '.claude/rules/docs.md', FILES['.claude/rules/docs.md'] + 'More synthetic docs.\n')
    plan = docs_task_path_at_root(ws)
    document = ws.validate(plan)
    assert (document['status'], document['scope']['instruction_dirs'], document['scope']['rules']) == \
        ('valid', ['.', 'docs'], ['.claude/rules/docs.md'])
    assert document['scope']['task_path_scope'] == dict(instruction_dirs=['docs'], rules=['.claude/rules/docs.md'])
    refused = iv.bind(document, plan, manifest, cwd_honored=True)
    assert (refused['status'], refused['reasons']) == ('blocked', ['task_path_scope_unbound'])
    assert [f for f in refused['findings'] if f['reason'] == 'task_path_scope_unbound'] == \
        [dict(reason='task_path_scope_unbound', path='.claude/rules/docs.md'),
         dict(reason='task_path_scope_unbound', path='docs/CLAUDE.md')]
    assert (refused['scope']['instruction_dirs'], refused['scope']['rules'], refused['scope']['task_paths_source']) == \
        (['.'], [], 'proposer_declared')
    assert iv.current(refused, plan, ws.baseline, ws.candidate, project=ws.project) == ['not_valid']


def test_a_declared_task_path_below_a_root_cwd_still_binds_an_edit_at_the_evaluated_cwd(ws):
    # The same plan still binds an edit at the evaluated cwd, and check_scope enforces the narrowed scope.
    plan = docs_task_path_at_root(ws)
    ws.write(ws.candidate, 'CLAUDE.md', FILES['CLAUDE.md'] + 'Synthetic root addition.\n')
    bound = iv.bind(ws.validate(plan), plan, AT_ROOT)
    assert (bound['status'], bound['scope']['instruction_dirs']) == ('valid', ['.'])
    assert iv.current(bound, plan, ws.baseline, ws.candidate, project=ws.project) == []
    original = {name: (hashlib.sha256(text.encode()).hexdigest(), 0o644) for name, text in FILES.items()}
    args = argparse.Namespace(); args._instruction_scope = bound['scope']
    with pytest.raises(ValueError, match='governing scope'):
        check_scope(args, dict(original, **{'docs/CLAUDE.md': ('0' * 64, 0o644)}), original)


DOCS_LINE = 'Synthetic unrelated docs guidance.\n'
GIT_RULE = '.claude/rules/git.md'


# Root tasks lose guidance that only an on-demand docs file, or a rule that stops loading at launch, still
# holds; declaring a docs task path must not hide that loss from the removal gate.
@pytest.mark.parametrize(('name', 'baseline', 'candidate'), [
    pytest.param('root_line_kept_only_on_demand', {'CLAUDE.md': FILES['CLAUDE.md'] + DOCS_LINE}, {},
                 id='root_line_kept_only_on_demand'),
    pytest.param('launch_rule_made_conditional', {},
                 {GIT_RULE: '---\npaths:\n  - "docs/**"\n---\nSynthetic launch git rule.\n'},
                 id='launch_rule_made_conditional')])
def test_a_declared_task_path_cannot_turn_a_root_removal_into_not_applicable(ws, name, baseline, candidate):
    for root in (ws.project, ws.baseline, ws.candidate):
        ws.write(root, GIT_RULE, 'Synthetic launch git rule.\n')
    for path, text in baseline.items():
        for root in (ws.project, ws.baseline, ws.candidate): ws.write(root, path, text)
    if name == 'root_line_kept_only_on_demand': ws.write(ws.candidate, 'CLAUDE.md', FILES['CLAUDE.md'])
    for path, text in candidate.items(): ws.write(ws.candidate, path, text)
    for task_paths in ([], ['docs/guide.md']):
        plan = ws.plan(dict(CLAUDE, task_cwd='.'), task_paths=task_paths, required=[])
        bound = iv.bind(ws.validate(plan), plan, AT_ROOT)
        assert bound['status'] == 'valid'
        assert iv.current(bound, plan, ws.baseline, ws.candidate, project=ws.project) == []
        assert (bound['removal']['required'], bound['removal']['disposition']) == \
            (True, 'requires_behavioral_controls'), task_paths
        assert bound['measurements']['credited_reduction_bytes'] == 0, task_paths


def test_each_task_path_must_lie_below_the_plans_own_task_cwd(ws):
    # Tasks run in src and docs; a plan for src may not name a docs path to make docs guidance governing.
    ws.write(ws.candidate, 'src/CLAUDE.md', FILES['src/CLAUDE.md'] + 'Check the synthetic build.\n')
    plan = ws.plan(task_paths=[TASK, 'docs/guide.md'])
    document = ws.validate(plan)
    assert document['status'] == 'valid'
    refused = iv.bind(document, plan, dict(tasks=[dict(id='edit', cwd='src'), dict(id='docs', cwd='docs')]), cwd_honored=True)
    assert (refused['status'], refused['reasons']) == ('blocked', ['task_path_not_evaluated'])
    assert refused['findings'] == [dict(reason='task_path_not_evaluated', path='docs/guide.md')]


def cwd_edit(ws):
    # Outside the controlled Responses executor every trial runs at the root, so a manifest task cwd binds nothing.
    ws.write(ws.candidate, 'src/CLAUDE.md', FILES['src/CLAUDE.md'] + 'Check the synthetic build.\n')
    plan = ws.plan()
    return plan, ws.validate(plan)


@pytest.mark.parametrize('manifest', [pytest.param(IN_SRC, id='in-src'), pytest.param(MIXED, id='mixed')])
def test_a_task_cwd_the_trial_runner_does_not_honor_is_refused(ws, manifest):
    plan, document = cwd_edit(ws)
    with pytest.raises(ValueError, match='controlled Responses executor'):
        iv.bind(document, plan, manifest)


def test_a_task_cwd_binds_when_the_trial_runner_honors_it(ws):
    plan, document = cwd_edit(ws)
    assert iv.bind(document, plan, IN_SRC, cwd_honored=True)['status'] == 'valid'
    assert iv.task_cwds(AT_ROOT) == ['.']


def test_check_scope_rejects_an_unrelated_same_name_file(ws):
    original = {name: (hashlib.sha256(text.encode()).hexdigest(), 0o644) for name, text in FILES.items()}
    unrelated = dict(original, **{'docs/CLAUDE.md': ('0' * 64, 0o644)})
    created = dict(original, **{'vendor/CLAUDE.md': ('0' * 64, 0o644)})
    governing = dict(original, **{'src/CLAUDE.md': ('0' * 64, 0o644)})
    args = argparse.Namespace()
    check_scope(args, unrelated, original)  # the frozen baseline already holds it; validation decides
    with pytest.raises(ValueError, match='governing scope'): check_scope(args, created, original)
    ws.write(ws.candidate, 'docs/CLAUDE.md', 'Synthetic unrelated rewrite.\n')
    args._instruction_scope = ws.validate()['scope']
    with pytest.raises(ValueError, match='governing scope'): check_scope(args, unrelated, original)
    with pytest.raises(ValueError, match='governing scope'): check_scope(args, created, original)
    check_scope(args, governing, original)
    check_scope(args, unrelated)  # without the baseline inventory only the configured scope applies


def test_hook_requires_a_plan_for_nested_edits_only(ws):
    original = {'AGENTS.md': ('1' * 64, 0o644), 'src/CLAUDE.md': ('2' * 64, 0o644)}
    args = argparse.Namespace(project=ws.project)
    root_only = dict(original, **{'AGENTS.md': ('3' * 64, 0o644)})
    assert iv.hook(args, ws.baseline, ws.candidate, original, root_only, {'status': 'candidate'}, 'claude') is None
    nested = dict(original, **{'src/CLAUDE.md': ('3' * 64, 0o644)})
    document = iv.hook(args, ws.baseline, ws.candidate, original, nested, {'status': 'candidate'}, 'claude')
    assert (document['status'], document['reasons'], args._instruction_scope) == ('blocked', ['instruction_plan_required'], None)
    invalid = iv.hook(args, ws.baseline, ws.candidate, original, nested,
                      {'instruction_plan': dict(ws.plan(), extra=True)}, 'claude')
    assert invalid['reasons'] == ['instruction_plan_invalid']
    mismatch = iv.hook(args, ws.baseline, ws.candidate, original, nested, {'instruction_plan': ws.plan()}, 'codex')
    assert mismatch['reasons'] == ['plan_runtime_differs']
    unsupported = iv.validate(ws.baseline, ws.candidate, ws.plan(dict(CLAUDE, version='9.9.9')))
    assert unsupported['status'] == 'unsupported'
    missing_cwd = iv.validate(ws.baseline, ws.candidate, ws.plan(dict(CLAUDE, task_cwd='absent')), project=ws.project)
    assert missing_cwd['reasons'] == ['task_cwd_unavailable']


def test_validation_leaves_inputs_graders_and_heldout_untouched(ws):
    evaluation = ws.root / 'evaluation'
    ws.write(evaluation, 'heldout/original', 'synthetic held-out input')
    ws.write(evaluation, 'grader/grade.py.txt', 'synthetic grader')
    ws.write(ws.candidate, 'src/CLAUDE.md', 'Always run the synthetic tests.\n')
    before = {root: tree_digest(root) for root in (ws.project, ws.baseline, ws.candidate, evaluation)}
    document = ws.validate()
    trials = ws.trials()
    frozen = json.dumps(trials, sort_keys=True)
    iv.removal_disposition(document, trials)
    assert json.dumps(trials, sort_keys=True) == frozen
    for root, digest in before.items(): assert tree_digest(root) == digest
    assert str(ws.root) not in json.dumps({k: v for k, v in document.items() if k != 'task_cwd'})


def operation(path, decision, **fields):
    return dict(dict(path=path, decision=decision, mechanism_id='m-tests', evidence_refs=['ev-1'],
                     expected_change='Synthetic build check is added.', preserved=['tests'],
                     falsifier='Synthetic build still fails unchecked.'), **fields)


def test_operations_are_justified_one_to_one_and_recorded_as_hashes(ws):
    ws.write(ws.candidate, 'src/CLAUDE.md', FILES['src/CLAUDE.md'] + 'Check the synthetic build.\n')
    plan = ws.plan(operations=[operation('src/CLAUDE.md', 'update')])
    document = ws.validate(plan)
    assert (document['status'], document['reasons']) == ('valid', [])
    [record] = document['decisions']
    assert (record['path'], record['decision'], record['mechanism_id'], record['preserved']) == (
        'src/CLAUDE.md', 'update', 'm-tests', ['tests'])
    assert record['expected_change_sha256'] == hashlib.sha256(b'Synthetic build check is added.').hexdigest()
    assert record['after_sha256'] == document['operations'][0]['after_sha256']
    assert 'Synthetic build' not in json.dumps(document['decisions'])


def test_a_plan_without_operations_keeps_decisions_undeclared(ws):
    ws.write(ws.candidate, 'src/CLAUDE.md', FILES['src/CLAUDE.md'] + 'Check the synthetic build.\n')
    assert ws.validate()['decisions'] is None


@pytest.mark.parametrize('reason, operations', [
    ('operation_unjustified', []),
    ('justification_for_unchanged_path', [operation('src/CLAUDE.md', 'update'), operation('CLAUDE.md', 'update')]),
    ('justification_decision_mismatch', [operation('src/CLAUDE.md', 'remove')]),
])
def test_a_missing_extra_or_mismatched_justification_blocks(ws, reason, operations):
    ws.write(ws.candidate, 'src/CLAUDE.md', FILES['src/CLAUDE.md'] + 'Check the synthetic build.\n')
    document = ws.validate(ws.plan(operations=operations))
    assert (document['status'], document['reasons']) == ('blocked', [reason])


def test_a_consolidation_must_be_declared_as_one(ws):
    ws.write(ws.candidate, 'src/CLAUDE.md', FILES['src/CLAUDE.md'] + 'Use synthetic strict types.\n')
    (ws.candidate / '.claude/rules/ts.md').unlink()
    declared = [operation('src/CLAUDE.md', 'update'), operation('.claude/rules/ts.md', 'consolidate')]
    assert ws.validate(ws.plan(operations=declared))['status'] == 'valid'
    declared[1] = operation('.claude/rules/ts.md', 'remove')
    assert ws.validate(ws.plan(operations=declared))['reasons'] == ['justification_decision_mismatch']


@pytest.mark.parametrize('bad', [
    dict(evidence_refs=[]), dict(preserved=['unknown']), dict(falsifier=' '), dict(expected_change='a\nb'),
    dict(mechanism_id='bad id'), dict(decision='rewrite'),
])
def test_an_uncited_or_malformed_justification_is_an_invalid_plan(ws, bad):
    ws.write(ws.candidate, 'src/CLAUDE.md', FILES['src/CLAUDE.md'] + 'Check the synthetic build.\n')
    document = ws.validate(ws.plan(operations=[dict(operation('src/CLAUDE.md', 'update'), **bad)]))
    assert (document['status'], document['reasons']) == ('blocked', ['instruction_plan_invalid'])


@pytest.mark.parametrize('field', ['expected_change', 'falsifier', 'required'])
def test_a_lone_surrogate_in_plan_text_is_an_invalid_plan_not_a_crash(ws, field):
    ws.write(ws.candidate, 'src/CLAUDE.md', FILES['src/CLAUDE.md'] + 'Check the synthetic build.\n')
    bad = 'a\ud800'
    plan = ws.plan(operations=[dict(operation('src/CLAUDE.md', 'update'), **({} if field == 'required' else {field: bad}))])
    if field == 'required': plan['required'] = [dict(id='tests', text=bad)]
    document = ws.validate(plan)
    assert (document['status'], document['reasons']) == ('blocked', ['instruction_plan_invalid'])
