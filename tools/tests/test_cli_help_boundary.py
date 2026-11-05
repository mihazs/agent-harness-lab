"""Help and README state the improve/watch recommendation boundary without running anything."""
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

# Exact help snapshots: the lines that say what each command changes and never changes.
TOP_DESCRIPTION = ('Start with ahl analyze . for read-only analysis, or ahl improve . to stage, evaluate and recommend\n'
                   'harness candidates in one call. improve and watch never apply behavioral changes to the project.\n')
SUMMARIES = {
    'improve': 'Stage and evaluate harness candidates, then recommend; never applies behavioral changes '
               '(only --repair-contracts may apply bounded skill-header repairs)',
    'watch': 'Recheck evidence; rerun recommendation-only staging and evaluation only when evidence or '
             'configuration changes; never edits harness files',
}
BOUNDARIES = {
    'improve': 'Changes: private state only: run artifacts (staged baseline/candidate copies, evaluation results, change plan) and a run lock.\n'
               'Never changes: project instructions, skills or hooks; behavioral candidates stay recommendations.\n'
               'Exception: explicit --repair-contracts may apply a bounded skill-header repair with a rollback journal;\n'
               'a project ahl.json keeps that repair recommendation-only. No commit, publish, deploy or background service.\n',
    'watch': 'Changes: private state only: run artifacts, a run lock and the watch checkpoint.\n'
             'Never changes: project harness files; watch has no --repair-contracts and applies no candidate.\n'
             'Reruns: on changed evidence or execution configuration, or a classified transient retry.\n',
}
# Option snapshots guard against dropping options while rewording help.
COMMON = ('--help', '--events', '--source-root', '--session', '--max-captures', '--max-capture-scan', '--max-events',
          '--sequence-length')
RUNTIME = ('--format', '--source-version', '--output')
EXECUTION = ('--focus', '--agent', '--inference-settings', '--codex', '--claude', '--timeout', '--sandbox-graders',
             '--cgroup-root', '--image-root', '--image-sha256', '--permission-audit', '--evaluation-store', '--disk-bytes')
OPTIONS = {
    'improve': (*COMMON, '--incident-corpus', '--incident-query', *RUNTIME, '--experiment-request', *EXECUTION,
                '--repair-contracts'),
    'watch': (*COMMON, *RUNTIME, *EXECUTION, '--interval', '--max-attempts', '--retry-delay', '--watch-budget', '--once'),
}
SUBCOMMANDS = ('rollback', 'update', 'mine', 'diagnose', 'evaluate', 'benchmark', 'skills', 'bank', 'bank-interchange',
               'bank-review', 'experiment', 'experiment-history', 'grader-correction', 'regression', 'regression-review',
               'sandbox-check', 'research-simulate', 'incident-search', 'promotion', 'benchmark-report', 'doctor', 'init',
               'sources', 'analyze', 'analysis', 'improve', 'watch')
# Wording that would imply behavioral auto-adoption or continuous self-modification.
FORBIDDEN = ('adopt supported harness repairs', 'continuously improve', 'automatic improvement', 'automatically apply',
             'auto-adopt', 'adopts')
README_BOUNDARY = (
    '`ahl improve .` writes private state only: staged baseline and candidate copies, evaluation results, '
    'a change plan and a run lock. It never edits project instructions, skills or hooks.',
    'A candidate that passes evaluation is reported for independent certification; it is not applied.',
    '`ahl improve . --repair-contracts` is the one exception',
    'A project `ahl.json` keeps even that repair recommendation-only.',
    '`ahl watch .` reruns the same recommendation-only staging and evaluation when evidence or execution '
    'configuration changes, or after a classified transient failure.',
    'has no `--repair-contracts` option and never edits harness files.',
    'Neither command commits, publishes, deploys or starts a background service.',
)


def flat(text):
    return ' '.join(text.split())


class HelpBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.outputs = {}
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            work, home, bin_dir = base / 'work', base / 'home', base / 'bin'
            for path in (work, home, bin_dir):
                path.mkdir()
            marker = base / 'agent-started'
            # Fake agent runtimes record any invocation; help must start none.
            for name in ('codex', 'claude'):
                fake = bin_dir / name
                fake.write_text(f'#!/bin/sh\necho "$0" >> {marker}\n')
                fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            env = {**os.environ, 'PYTHONPATH': str(ROOT), 'HOME': str(home), 'XDG_STATE_HOME': str(home / 'state'),
                   'PATH': str(bin_dir) + os.pathsep + os.environ.get('PATH', ''), 'COLUMNS': '100'}
            for route in ((), ('improve',), ('watch',)):
                run = subprocess.run([sys.executable, '-m', 'agent_harness_lab', *route, '--help'], cwd=work, env=env,
                                     capture_output=True, text=True, timeout=30)
                cls.outputs[route[0] if route else 'top'] = run
            cls.side_effects = dict(agent=marker.exists(), work=os.listdir(work), home=os.listdir(home))

    def test_help_exits_zero_without_starting_an_agent_or_writing_state(self):
        for name, run in self.outputs.items():
            with self.subTest(command=name):
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(run.stderr, '')
        self.assertEqual(self.side_effects, dict(agent=False, work=[], home=[]))

    def test_top_level_help_snapshot_states_the_boundary_and_keeps_every_subcommand(self):
        text = self.outputs['top'].stdout
        self.assertIn(TOP_DESCRIPTION, text)
        for name, summary in SUMMARIES.items():
            self.assertRegex(flat(text), re.escape(name) + r' ' + re.escape(flat(summary)))
        choices = re.search(r'\{([a-z,-]+)\}', text).group(1).split(',')
        self.assertEqual(tuple(choices), SUBCOMMANDS)

    def test_command_help_snapshot_states_what_changes_and_never_changes(self):
        for name in ('improve', 'watch'):
            with self.subTest(command=name):
                text = self.outputs[name].stdout
                self.assertIn('\n' + SUMMARIES[name] + '\n', text)
                self.assertIn('\n\n' + BOUNDARIES[name] + 'Output: ', text)

    def test_command_help_keeps_every_option(self):
        for name, expected in OPTIONS.items():
            with self.subTest(command=name):
                options = re.findall(r'^  (?:-h, )?(--[a-z0-9-]+)', self.outputs[name].stdout, re.M)
                self.assertEqual(tuple(options), expected)

    def test_help_never_implies_behavioral_auto_adoption(self):
        for name, run in self.outputs.items():
            text = flat(run.stdout).lower()
            for phrase in FORBIDDEN:
                with self.subTest(command=name, phrase=phrase):
                    self.assertNotIn(phrase, text)

    def test_readme_states_the_same_boundary_for_improve_and_watch(self):
        readme = flat((ROOT / 'README.md').read_text(encoding='utf-8'))
        for claim in README_BOUNDARY:
            with self.subTest(claim=claim[:40]):
                self.assertIn(claim, readme)


if __name__ == '__main__':
    unittest.main()
