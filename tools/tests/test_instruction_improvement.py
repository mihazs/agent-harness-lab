"""Synthetic scoped instruction rewrites and pruning (#285); every verdict is a declaration, never loading proof."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from agent_harness_lab.evaluation.trial_surfaces import stage_harness
from agent_harness_lab.one_call import check_scope
from agent_harness_lab.sources import instruction_validation as iv

CLAUDE = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='src')
CODEX = dict(schema_version=1, runtime='codex', version='0.166.0', task_cwd='src', fallback_filenames=[])
TASK = 'src/app/view.ts'
FILES = {
    'CLAUDE.md': 'Synthetic root guidance.\n',
    'src/CLAUDE.md': 'Synthetic source guidance.\nAlways run the synthetic tests.\n',
    '.claude/rules/ts.md': '---\npaths:\n  - "src/**/*.ts"\n---\nUse synthetic strict types.\n',
    '.claude/rules/docs.md': '---\npaths:\n  - "docs/**"\n---\nSynthetic docs rule.\n',
    'docs/CLAUDE.md': 'Synthetic unrelated docs guidance.\n',
}


def tree_digest(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*')) if p.is_file()}


class InstructionImprovementTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='ahl-i14-'); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
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
        records, base = [], Path(tempfile.mkdtemp(dir=self.root))
        for version, root in (('baseline', self.baseline), ('candidate', self.candidate)):
            for split in ('development', 'regression'):
                workspace = base / version / split; workspace.mkdir(parents=True)
                record = dict(task=split, version=version, split=split,
                              status='failed' if (version, split) in failed else 'passed')
                if surfaces: record['workspace_surfaces'] = stage_harness(root, workspace, paths)
                records.append(record)
        return records

    def test_a_governing_scope_edit_is_valid_and_never_claims_loading(self):
        self.write(self.candidate, 'src/CLAUDE.md', FILES['src/CLAUDE.md'] + 'Check the synthetic build.\n')
        document = self.validate()
        self.assertEqual((document['status'], document['reasons']), ('valid', []))
        self.assertEqual(document['scope']['instruction_dirs'], ['src'])
        self.assertEqual(document['scope']['rules'], ['.claude/rules/ts.md'])
        self.assertEqual([(o['path'], o['operation']) for o in document['operations']], [('src/CLAUDE.md', 'update')])
        self.assertEqual((document['observed_loading'], document['effective'], document['adoption_authority']),
                         ('unknown', 'unknown', False))
        self.assertEqual(document['removal']['disposition'], 'not_applicable')
        self.assertNotIn('Always run', json.dumps(document))

    def test_useful_rule_deletion_fails_its_behavioral_control(self):
        (self.candidate / '.claude/rules/ts.md').unlink()
        document = self.validate()
        self.assertEqual(document['status'], 'valid')
        self.assertEqual([(o['path'], o['operation']) for o in document['operations']], [('.claude/rules/ts.md', 'remove')])
        self.assertEqual(document['removal']['disposition'], 'requires_behavioral_controls')
        rejected = iv.removal_disposition(document, self.trials(failed={('candidate', 'regression')}))
        self.assertEqual((rejected['disposition'], rejected['reason']), ('rejected', 'behavioral_control_failed'))

    def test_redundant_rule_removal_is_recommended_only_under_faithful_passing_controls(self):
        (self.candidate / '.claude/rules/ts.md').unlink()
        document = self.validate()
        recommended = iv.removal_disposition(document, self.trials())
        self.assertEqual((recommended['disposition'], recommended['certification']), ('recommended', 'required'))
        self.assertEqual(iv.removal_disposition(document, self.trials(surfaces=False))['reason'], 'workspace_surfaces_unverified')
        self.assertEqual(iv.removal_disposition(document, [t for t in self.trials() if t['split'] != 'regression'])['reason'],
                         'behavioral_controls_missing')
        # A workspace that still held the removed rule did not test the removal.
        trials = self.trials()
        for trial in trials:
            if trial['version'] == 'candidate': trial['workspace_surfaces'] = trials[0]['workspace_surfaces']
        self.assertEqual(iv.removal_disposition(document, trials)['reason'], 'workspace_surfaces_unverified')

    def test_consolidation_preserves_scope(self):
        self.write(self.candidate, 'src/CLAUDE.md', FILES['src/CLAUDE.md'] + 'Use synthetic strict types.\n')
        (self.candidate / '.claude/rules/ts.md').unlink()
        document = self.validate()
        self.assertEqual(document['status'], 'valid')
        self.assertEqual({o['path']: o['operation'] for o in document['operations']}['.claude/rules/ts.md'], 'consolidate')
        self.assertFalse(document['removal']['required'])
        # Consolidating into a rule whose paths do not match the task path narrows the scope and is blocked.
        shutil.rmtree(self.candidate); shutil.copytree(self.baseline, self.candidate)
        self.write(self.candidate, '.claude/rules/docs.md', FILES['.claude/rules/docs.md'] + 'Always run the synthetic tests.\n')
        self.write(self.candidate, 'src/CLAUDE.md', 'Synthetic source guidance.\n')
        blocked = self.validate()
        self.assertEqual(blocked['status'], 'blocked')
        self.assertIn('rule_matches_no_task_path', blocked['reasons'])
        self.assertIn('required_text_left_active_context', blocked['reasons'])

    def test_byte_reduction_from_moving_text_out_of_context_does_not_count(self):
        self.write(self.candidate, 'src/CLAUDE.md', 'Always run the synthetic tests.\n')
        self.write(self.candidate, '.claude/skills/build/SKILL.md', 'Synthetic source guidance.\n')
        moved = self.validate()
        self.assertEqual(moved['status'], 'valid')
        measured = moved['measurements']
        self.assertLess(measured['candidate_active_bytes'], measured['baseline_active_bytes'])
        self.assertEqual((measured['credited_reduction_bytes'], measured['selects_winner']), (0, False))
        self.assertEqual(moved['removal']['moved_out_of_active_context_lines'], 1)
        self.assertEqual(moved['removal']['disposition'], 'requires_behavioral_controls')
        # Moving required text out of the active context blocks the candidate.
        self.write(self.candidate, 'src/CLAUDE.md', 'Synthetic source guidance.\n')
        self.write(self.candidate, '.claude/skills/build/SKILL.md', 'Always run the synthetic tests.\n')
        self.assertEqual(self.validate()['reasons'], ['required_text_left_active_context'])
        self.write(self.candidate, '.claude/skills/build/SKILL.md', 'Unrelated synthetic skill.\n')
        self.assertEqual(self.validate()['reasons'], ['required_text_removed'])

    def test_genuine_pruning_is_credited_but_still_needs_behavioral_controls(self):
        self.write(self.candidate, 'src/CLAUDE.md', 'Always run the synthetic tests.\n')
        pruned = self.validate()
        self.assertEqual(pruned['status'], 'valid')
        self.assertGreater(pruned['measurements']['credited_reduction_bytes'], 0)
        self.assertEqual(pruned['removal']['disposition'], 'requires_behavioral_controls')

    def test_stale_baseline_or_changed_candidate_invalidates(self):
        self.write(self.candidate, 'src/CLAUDE.md', FILES['src/CLAUDE.md'] + 'Check the synthetic build.\n')
        plan = self.plan()
        document = self.validate(plan)
        self.assertEqual(iv.current(document, plan, self.baseline, self.candidate, project=self.project), [])
        self.write(self.project, 'src/app/CLAUDE.md', 'Synthetic concurrent guidance.\n')
        self.assertEqual(iv.current(document, plan, self.baseline, self.candidate, project=self.project), ['stale_baseline'])
        (self.project / 'src/app/CLAUDE.md').unlink()
        self.write(self.project, 'src/CLAUDE.md', 'Synthetic concurrent edit.\n')
        self.assertIn('stale_baseline', self.validate(plan)['reasons'])
        self.write(self.project, 'src/CLAUDE.md', FILES['src/CLAUDE.md'])
        self.write(self.candidate, 'src/CLAUDE.md', 'Synthetic modified candidate.\nAlways run the synthetic tests.\n')
        self.assertEqual(iv.current(document, plan, self.baseline, self.candidate, project=self.project), ['candidate_changed'])

    def test_import_cycle_and_missing_import_block(self):
        self.write(self.candidate, 'src/CLAUDE.md', FILES['src/CLAUDE.md'] + '@missing.md\n')
        self.assertEqual(self.validate()['reasons'], ['missing_import'])
        self.write(self.baseline, 'src/app/CLAUDE.md', 'Synthetic app guidance.\n')
        self.write(self.project, 'src/app/CLAUDE.md', 'Synthetic app guidance.\n')
        self.write(self.candidate, 'src/CLAUDE.md', FILES['src/CLAUDE.md'] + '@app/CLAUDE.md\n')
        self.write(self.candidate, 'src/app/CLAUDE.md', 'Synthetic app guidance.\n@../CLAUDE.md\n')
        self.assertIn('import_cycle', self.validate()['reasons'])

    def test_undeclared_shadowing_change_blocks_and_a_declared_one_passes(self):
        for root in (self.project, self.baseline, self.candidate):
            for name in FILES: (root / name).unlink()
            shutil.rmtree(root / '.claude')
            self.write(root, 'AGENTS.md', 'Synthetic root agents.\n')
            self.write(root, 'src/AGENTS.md', 'Synthetic source agents.\nAlways run the synthetic tests.\n')
        self.write(self.candidate, 'src/AGENTS.override.md', 'Synthetic override.\nAlways run the synthetic tests.\n')
        plan = self.plan(CODEX)
        blocked = iv.validate(self.baseline, self.candidate, plan, runtime='codex', project=self.project)
        self.assertEqual(blocked['reasons'], ['undeclared_shadowing_change'])
        declared = dict(plan, shadowing_changes=[dict(change='add', shadow='src/AGENTS.override.md', shadowed='src/AGENTS.md')])
        self.assertEqual(iv.validate(self.baseline, self.candidate, declared, runtime='codex', project=self.project)['status'], 'valid')

    def test_claude_agents_md_shadowing_is_a_shadowing_change(self):
        (self.project / 'src/CLAUDE.md').unlink(); (self.baseline / 'src/CLAUDE.md').unlink(); (self.candidate / 'src/CLAUDE.md').unlink()
        for root in (self.project, self.baseline, self.candidate):
            self.write(root, 'src/app/AGENTS.md', 'Synthetic app agents.\nAlways run the synthetic tests.\n')
        self.write(self.candidate, 'src/app/CLAUDE.md', 'Synthetic app guidance.\nAlways run the synthetic tests.\n')
        self.assertIn('undeclared_shadowing_change', self.validate()['reasons'])

    def test_rule_paths_edit_must_keep_the_declared_task_path_matched(self):
        self.write(self.candidate, '.claude/rules/ts.md', '---\npaths:\n  - "lib/**/*.ts"\n---\nUse synthetic strict types.\n')
        self.assertIn('rule_paths_dropped_task_path', self.validate()['reasons'])
        removed = self.validate(self.plan(task_paths=[TASK, 'lib/x.ts'], removed_task_paths=[TASK]))
        self.assertNotIn('rule_paths_dropped_task_path', removed['reasons'])

    def test_edits_outside_the_governing_scope_are_blocked(self):
        self.write(self.candidate, 'CLAUDE.md', 'Synthetic root rewrite.\n')
        self.assertEqual(self.validate()['reasons'], ['outside_governing_scope'])
        shutil.rmtree(self.candidate); shutil.copytree(self.baseline, self.candidate)
        self.write(self.candidate, 'docs/CLAUDE.md', 'Synthetic unrelated rewrite.\n')
        self.assertEqual(self.validate()['reasons'], ['outside_governing_scope'])
        shutil.rmtree(self.candidate); shutil.copytree(self.baseline, self.candidate)
        self.write(self.candidate, '.claude/rules/docs.md', FILES['.claude/rules/docs.md'] + 'More synthetic docs.\n')
        self.assertEqual(self.validate()['reasons'], ['rule_matches_no_task_path'])
        shutil.rmtree(self.candidate); shutil.copytree(self.baseline, self.candidate)
        self.write(self.candidate, 'src/AGENTS.override.md', 'Synthetic Codex-only file.\n')
        self.assertEqual(self.validate()['reasons'], ['not_a_runtime_instruction_surface'])

    def test_check_scope_rejects_an_unrelated_same_name_file(self):
        original = {name: (hashlib.sha256(text.encode()).hexdigest(), 0o644) for name, text in FILES.items()}
        unrelated = dict(original, **{'docs/CLAUDE.md': ('0' * 64, 0o644)})
        created = dict(original, **{'vendor/CLAUDE.md': ('0' * 64, 0o644)})
        governing = dict(original, **{'src/CLAUDE.md': ('0' * 64, 0o644)})
        args = argparse.Namespace()
        check_scope(args, unrelated, original)  # the frozen baseline already holds it; validation decides
        with self.assertRaisesRegex(ValueError, 'governing scope'): check_scope(args, created, original)
        self.write(self.candidate, 'docs/CLAUDE.md', 'Synthetic unrelated rewrite.\n')
        args._instruction_scope = self.validate()['scope']
        with self.assertRaisesRegex(ValueError, 'governing scope'): check_scope(args, unrelated, original)
        with self.assertRaisesRegex(ValueError, 'governing scope'): check_scope(args, created, original)
        check_scope(args, governing, original)
        check_scope(args, unrelated)  # without the baseline inventory only the configured scope applies

    def test_hook_requires_a_plan_for_nested_edits_only(self):
        original = {'AGENTS.md': ('1' * 64, 0o644), 'src/CLAUDE.md': ('2' * 64, 0o644)}
        args = argparse.Namespace(project=self.project)
        root_only = dict(original, **{'AGENTS.md': ('3' * 64, 0o644)})
        self.assertIsNone(iv.hook(args, self.baseline, self.candidate, original, root_only, {'status': 'candidate'}, 'claude'))
        nested = dict(original, **{'src/CLAUDE.md': ('3' * 64, 0o644)})
        document = iv.hook(args, self.baseline, self.candidate, original, nested, {'status': 'candidate'}, 'claude')
        self.assertEqual((document['status'], document['reasons'], args._instruction_scope),
                         ('blocked', ['instruction_plan_required'], None))
        invalid = iv.hook(args, self.baseline, self.candidate, original, nested,
                          {'instruction_plan': dict(self.plan(), extra=True)}, 'claude')
        self.assertEqual(invalid['reasons'], ['instruction_plan_invalid'])
        mismatch = iv.hook(args, self.baseline, self.candidate, original, nested, {'instruction_plan': self.plan()}, 'codex')
        self.assertEqual(mismatch['reasons'], ['plan_runtime_differs'])
        unsupported = iv.validate(self.baseline, self.candidate, self.plan(dict(CLAUDE, version='9.9.9')))
        self.assertEqual(unsupported['status'], 'unsupported')
        missing_cwd = iv.validate(self.baseline, self.candidate, self.plan(dict(CLAUDE, task_cwd='absent')), project=self.project)
        self.assertEqual(missing_cwd['reasons'], ['task_cwd_unavailable'])

    def test_validation_leaves_inputs_graders_and_heldout_untouched(self):
        evaluation = self.root / 'evaluation'
        self.write(evaluation, 'heldout/original', 'synthetic held-out input')
        self.write(evaluation, 'grader/grade.py.txt', 'synthetic grader')
        self.write(self.candidate, 'src/CLAUDE.md', 'Always run the synthetic tests.\n')
        before = {root: tree_digest(root) for root in (self.project, self.baseline, self.candidate, evaluation)}
        document = self.validate()
        trials = self.trials()
        frozen = json.dumps(trials, sort_keys=True)
        iv.removal_disposition(document, trials)
        self.assertEqual(json.dumps(trials, sort_keys=True), frozen)
        for root, digest in before.items(): self.assertEqual(tree_digest(root), digest)
        self.assertNotIn(str(self.root), json.dumps({k: v for k, v in document.items() if k != 'task_cwd'}))


if __name__ == '__main__':
    unittest.main()
