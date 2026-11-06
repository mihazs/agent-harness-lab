"""Conventional Commits subjects are enforced for messages, PR titles and commit ranges."""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT/'scripts/check_commit_message.py'
spec = importlib.util.spec_from_file_location('check_commit_message', SCRIPT)
checker = importlib.util.module_from_spec(spec); spec.loader.exec_module(checker)


def run(*args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True)


class SubjectTests(unittest.TestCase):
    def test_valid_subjects(self):
        for subject in ('feat: add a flag', 'fix(cli): keep exit status', 'docs(skills/harness-evaluation): clarify',
                        'refactor(a.b_c-d/e1)!: drop the legacy path', 'chore!: remove support',
                        'revert: undo the flag', 'ci: check titles', 'build: pin lefthook', 'perf: cache',
                        'style: wrap lines', 'test: cover ranges', 'feat: ' + 'x' * 94):
            with self.subTest(subject=subject):
                self.assertIsNone(checker.violation(subject))

    def test_invalid_subjects_name_the_reason(self):
        cases = {
            'Add a flag': 'expected', 'feat add a flag': 'expected', 'feat:add': 'expected',
            'feature: add': 'unknown type', 'Feat: add': 'unknown type', 'wip: add': 'unknown type',
            'feat(CLI): add': 'invalid scope', 'feat(): add': 'invalid scope', 'feat(a b): add': 'invalid scope',
            'feat: add a flag.': 'period', 'feat: ': 'non-empty', 'feat!:': 'non-empty', 'feat:  add': 'non-empty',
            'feat: ' + 'x' * 95: 'limit is 100', '': 'empty', 'feat!(cli): add': 'expected',
            'Revert "feat: add a flag"': 'default revert',
        }
        for subject, reason in cases.items():
            with self.subTest(subject=subject):
                self.assertIn(reason, checker.violation(subject) or '')

    def test_default_merge_subjects_are_rejected(self):
        for subject in ('Merge pull request #12 from someone/topic', "Merge branch 'main' into topic",
                        "Merge remote-tracking branch 'origin/main'", 'Merge 1234abc into 5678def'):
            with self.subTest(subject=subject):
                self.assertIn('default merge subject', checker.violation(subject))
        self.assertIsNone(checker.violation('chore: merge pull request #12 from someone/topic'))

    def test_autosquash_prefixes_require_a_conventional_subject(self):
        for subject in ('fixup! feat: add a flag', 'squash! fix(cli): keep status', 'amend! docs: clarify',
                        'fixup! fixup! feat: add a flag'):
            with self.subTest(subject=subject):
                self.assertIsNone(checker.violation(subject))
        for subject in ('fixup! Add a flag', 'squash! ', 'amend!feat: add', 'fixup!', 'Fixup! feat: add'):
            with self.subTest(subject=subject):
                self.assertIsNotNone(checker.violation(subject))


class MessageFileTests(unittest.TestCase):
    def check(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'COMMIT_EDITMSG'; path.write_text(text)
            return run(str(path))

    def test_comments_and_blank_lines_are_skipped(self):
        message = '\n# Please enter the commit message\n#\nfeat(hooks): check messages\n\nBody.\n# trailing comment\n'
        self.assertEqual(self.check(message).returncode, 0)
        self.assertEqual(checker.subject_from_message(message), 'feat(hooks): check messages')

    def test_body_and_trailers_do_not_change_the_subject(self):
        result = self.check('fix: keep status\n\nAdd body text.\n\nCo-Authored-By: Someone <someone@example.invalid>\n')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bad_subject_fails_and_reports(self):
        result = self.check('# comment\nUpdate things\n')
        self.assertEqual(result.returncode, 1)
        self.assertIn('commit message: expected', result.stderr)
        self.assertIn('Update things', result.stderr)
        self.assertIn('conventionalcommits.org', result.stderr)

    def test_scissors_line_ends_the_message(self):
        scissors = '# ------------------------ >8 ------------------------\n'
        verbose = '# Do not modify or remove the line above.\ndiff --git a/x b/x\n+feat: hidden\n'
        self.assertEqual(self.check('\n' + scissors + verbose).returncode, 1)
        self.assertIn('empty commit subject', self.check(scissors + verbose).stderr)
        self.assertEqual(self.check('docs: explain\n' + scissors + 'Not a subject\n').returncode, 0)

    def test_comment_only_message_is_rejected(self):
        result = self.check('# only comments\n\n')
        self.assertEqual(result.returncode, 1)
        self.assertIn('empty commit subject', result.stderr)

    def test_missing_message_file_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as folder:
            result = run(str(Path(folder)/'missing'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('commit message check', result.stderr)


class CliTests(unittest.TestCase):
    def test_subject_mode_exit_codes(self):
        self.assertEqual(run('--subject', 'ci: check pull request titles').returncode, 0)
        result = run('--subject', 'Check pull request titles')
        self.assertEqual(result.returncode, 1)
        self.assertIn('subject: expected', result.stderr)

    def test_usage_errors_exit_two(self):
        for args in ((), ('--subject', 'feat: a', '--range', 'a..b'), ('--range', 'HEAD'),
                     ('message', '--subject', 'feat: a')):
            with self.subTest(args=args):
                self.assertEqual(run(*args).returncode, 2)

    def test_main_returns_codes_in_process(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(checker.main(['--subject', 'test: cover main']), 0)
            self.assertEqual(checker.main(['--subject', 'nope']), 1)


class RangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git('init', '-q', '-b', 'main')
        self.commit('chore: start')
        self.git('switch', '-q', '-c', 'topic')

    def git(self, *args):
        result = subprocess.run(['git', '-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid',
                                 '-c', 'commit.gpgsign=false', *args], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def commit(self, subject):
        self.git('commit', '-q', '--allow-empty', '--no-verify', '-m', subject)

    def test_conventional_range_passes(self):
        self.commit('feat: one'); self.commit('fixup! feat: one')
        result = run('--range', 'main..topic', cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(run('--range', 'topic..topic', cwd=self.root).returncode, 0)

    def test_every_offending_subject_is_printed(self):
        self.commit('feat: good'); self.commit('Bad first'); self.commit('fix: also good'); self.commit('Bad second.')
        result = run('--range', 'main..HEAD', cwd=self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Bad first', result.stderr)
        self.assertIn('Bad second.', result.stderr)
        self.assertNotIn('feat: good', result.stderr)
        self.assertIn('2 of 4 commits', result.stderr)

    def test_default_merge_commit_in_range_fails(self):
        self.commit('feat: topic work')
        self.git('switch', '-q', 'main'); self.commit('fix: main work')
        self.git('merge', '-q', '--no-ff', '--no-edit', 'topic')
        result = run('--range', 'HEAD~1..HEAD', cwd=self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Merge branch 'topic'", result.stderr)

    def test_unknown_revision_is_a_usage_error(self):
        result = run('--range', 'missing..HEAD', cwd=self.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn('could not read commit range', result.stderr)


if __name__ == '__main__':
    unittest.main()
