"""Conventional Commits subjects are enforced for messages, PR titles and commit ranges."""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT/'scripts/check_commit_message.py'
spec = importlib.util.spec_from_file_location('check_commit_message', SCRIPT)
checker = importlib.util.module_from_spec(spec); spec.loader.exec_module(checker)


def run(*args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True)


def subjects(*values):
    """Readable ids: the subject itself, shortened when long."""
    def name(value):
        if not value: return 'empty'
        return value if len(value) <= 40 else f'{value[:20]}...({len(value)} chars)'
    return [pytest.param(*value, id=name(value[0])) if isinstance(value, tuple) else pytest.param(value, id=name(value))
            for value in values]


VALID = subjects('feat: add a flag', 'fix(cli): keep exit status', 'docs(skills/harness-self-improvement): clarify',
                 'refactor(a.b_c-d/e1)!: drop the legacy path', 'chore!: remove support',
                 'revert: undo the flag', 'ci: check titles', 'build: pin lefthook', 'perf: cache',
                 'style: wrap lines', 'test: cover ranges', 'feat: ' + 'x' * 94)
INVALID = subjects(*{
    'Add a flag': 'expected', 'feat add a flag': 'expected', 'feat:add': 'expected',
    'feature: add': 'unknown type', 'Feat: add': 'unknown type', 'wip: add': 'unknown type',
    'feat(CLI): add': 'invalid scope', 'feat(): add': 'invalid scope', 'feat(a b): add': 'invalid scope',
    'feat: add a flag.': 'period', 'feat: ': 'non-empty', 'feat!:': 'non-empty', 'feat:  add': 'non-empty',
    'feat: ' + 'x' * 95: 'limit is 100', '': 'empty', 'feat!(cli): add': 'expected',
    'Revert "feat: add a flag"': 'default revert',
}.items())
MERGES = subjects('Merge pull request #12 from someone/topic', "Merge branch 'main' into topic",
                  "Merge remote-tracking branch 'origin/main'", 'Merge 1234abc into 5678def')
AUTOSQUASH = subjects('fixup! feat: add a flag', 'squash! fix(cli): keep status', 'amend! docs: clarify',
                      'fixup! fixup! feat: add a flag')
BAD_AUTOSQUASH = subjects('fixup! Add a flag', 'squash! ', 'amend!feat: add', 'fixup!', 'Fixup! feat: add')


class TestSubject:
    @pytest.mark.parametrize('subject', VALID)
    def test_valid_subjects(self, subject):
        assert checker.violation(subject) is None

    @pytest.mark.parametrize('subject,reason', INVALID)
    def test_invalid_subjects_name_the_reason(self, subject, reason):
        assert reason in (checker.violation(subject) or '')

    @pytest.mark.parametrize('subject', MERGES)
    def test_default_merge_subjects_are_rejected(self, subject):
        assert 'default merge subject' in checker.violation(subject)

    def test_conventional_subject_mentioning_a_merge_is_accepted(self):
        assert checker.violation('chore: merge pull request #12 from someone/topic') is None

    @pytest.mark.parametrize('subject', AUTOSQUASH)
    def test_autosquash_prefixes_require_a_conventional_subject(self, subject):
        assert checker.violation(subject) is None

    @pytest.mark.parametrize('subject', BAD_AUTOSQUASH)
    def test_autosquash_prefixes_without_a_conventional_subject_are_rejected(self, subject):
        assert checker.violation(subject) is not None


class TestMessageFile:
    @pytest.fixture(autouse=True)
    def _folder(self, tmp_path):
        self.root = tmp_path

    def check(self, text):
        path = self.root/'COMMIT_EDITMSG'; path.write_text(text)
        return run(str(path))

    def test_comments_and_blank_lines_are_skipped(self):
        message = '\n# Please enter the commit message\n#\nfeat(hooks): check messages\n\nBody.\n# trailing comment\n'
        assert self.check(message).returncode == 0
        assert checker.subject_from_message(message) == 'feat(hooks): check messages'

    def test_body_and_trailers_do_not_change_the_subject(self):
        result = self.check('fix: keep status\n\nAdd body text.\n\nCo-Authored-By: Someone <someone@example.invalid>\n')
        assert result.returncode == 0, result.stderr

    def test_bad_subject_fails_and_reports(self):
        result = self.check('# comment\nUpdate things\n')
        assert result.returncode == 1
        assert 'commit message: expected' in result.stderr
        assert 'Update things' in result.stderr
        assert 'conventionalcommits.org' in result.stderr

    def test_scissors_line_ends_the_message(self):
        scissors = '# ------------------------ >8 ------------------------\n'
        verbose = '# Do not modify or remove the line above.\ndiff --git a/x b/x\n+feat: hidden\n'
        assert self.check('\n' + scissors + verbose).returncode == 1
        assert 'empty commit subject' in self.check(scissors + verbose).stderr
        assert self.check('docs: explain\n' + scissors + 'Not a subject\n').returncode == 0

    def test_comment_only_message_is_rejected(self):
        result = self.check('# only comments\n\n')
        assert result.returncode == 1
        assert 'empty commit subject' in result.stderr

    def test_missing_message_file_is_a_usage_error(self):
        result = run(str(self.root/'missing'))
        assert result.returncode == 2
        assert 'commit message check' in result.stderr


class TestCli:
    def test_subject_mode_exit_codes(self):
        assert run('--subject', 'ci: check pull request titles').returncode == 0
        result = run('--subject', 'Check pull request titles')
        assert result.returncode == 1
        assert 'subject: expected' in result.stderr

    @pytest.mark.parametrize('args', [pytest.param((), id='no-arguments'),
                                      pytest.param(('--subject', 'feat: a', '--range', 'a..b'), id='subject-and-range'),
                                      pytest.param(('--range', 'HEAD'), id='range-without-dots'),
                                      pytest.param(('message', '--subject', 'feat: a'), id='file-and-subject')])
    def test_usage_errors_exit_two(self, args):
        assert run(*args).returncode == 2

    def test_main_returns_codes_in_process(self):
        with contextlib.redirect_stderr(io.StringIO()):
            assert checker.main(['--subject', 'test: cover main']) == 0
            assert checker.main(['--subject', 'nope']) == 1


class TestRange:
    @pytest.fixture(autouse=True)
    def _repository(self, tmp_path):
        self.root = tmp_path
        self.git('init', '-q', '-b', 'main')
        self.commit('chore: start')
        self.git('switch', '-q', '-c', 'topic')

    def git(self, *args):
        result = subprocess.run(['git', '-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid',
                                 '-c', 'commit.gpgsign=false', *args], cwd=self.root, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return result.stdout

    def commit(self, subject):
        self.git('commit', '-q', '--allow-empty', '--no-verify', '-m', subject)

    def test_conventional_range_passes(self):
        self.commit('feat: one'); self.commit('fixup! feat: one')
        result = run('--range', 'main..topic', cwd=self.root)
        assert result.returncode == 0, result.stderr
        assert run('--range', 'topic..topic', cwd=self.root).returncode == 0

    def test_every_offending_subject_is_printed(self):
        self.commit('feat: good'); self.commit('Bad first'); self.commit('fix: also good'); self.commit('Bad second.')
        result = run('--range', 'main..HEAD', cwd=self.root)
        assert result.returncode == 1
        assert 'Bad first' in result.stderr
        assert 'Bad second.' in result.stderr
        assert 'feat: good' not in result.stderr
        assert '2 of 4 commits' in result.stderr

    def test_default_merge_commit_in_range_fails(self):
        self.commit('feat: topic work')
        self.git('switch', '-q', 'main'); self.commit('fix: main work')
        self.git('merge', '-q', '--no-ff', '--no-edit', 'topic')
        result = run('--range', 'HEAD~1..HEAD', cwd=self.root)
        assert result.returncode == 1
        assert "Merge branch 'topic'" in result.stderr

    def test_unknown_revision_is_a_usage_error(self):
        result = run('--range', 'missing..HEAD', cwd=self.root)
        assert result.returncode == 2
        assert 'could not read commit range' in result.stderr

