"""The conservative ignore-rule subset the harness inventory uses to skip ignored directories (#821)."""
import pytest

from agent_harness_lab.sources import ignore_rules as rules


def verdict(text, path, base=()):
    parsed = rules.parse(text, base)
    assert parsed is not rules.UNKNOWN
    return rules.ignored(parsed, tuple(path.split('/')))


@pytest.mark.parametrize('text,path,expected', [
    pytest.param('node_modules/', 'node_modules', True, id='directory-name-at-root'),
    pytest.param('node_modules/', 'a/b/node_modules', True, id='directory-name-at-any-depth'),
    pytest.param('node_modules', 'a/node_modules', True, id='name-without-slash'),
    pytest.param('node_modules/', 'node_modules_old', False, id='whole-name-only'),
    pytest.param('/dist', 'dist', True, id='anchored-at-root'),
    pytest.param('/dist', 'a/dist', False, id='anchored-not-below'),
    pytest.param('a/dist', 'a/dist', True, id='slash-in-the-middle-anchors'),
    pytest.param('a/dist', 'b/a/dist', False, id='middle-slash-not-any-depth'),
    pytest.param('build*/', 'build-x', True, id='star-glob'),
    pytest.param('b?ild/', 'build', True, id='question-glob'),
    pytest.param('[Bb]in/', 'Bin', True, id='character-class'),
    pytest.param('[!a]x/', 'bx', True, id='negated-class'),
    pytest.param('*/', 'anything', True, id='star-matches-any-directory-name'),
    pytest.param('a/*', 'a/b', True, id='star-stays-in-one-segment'),
    pytest.param('a/*', 'a/b/c', False, id='star-does-not-cross-a-slash'),
    pytest.param('**/cache', 'cache', True, id='leading-double-star-zero-levels'),
    pytest.param('**/cache', 'x/y/cache', True, id='leading-double-star-many-levels'),
    pytest.param('a/**/b', 'a/b', True, id='middle-double-star-zero-levels'),
    pytest.param('a/**/b', 'a/x/y/b', True, id='middle-double-star-many-levels'),
    pytest.param('a/**', 'a/b', True, id='trailing-double-star-matches-inside'),
    pytest.param('a/**', 'a', False, id='trailing-double-star-not-the-directory-itself'),
    pytest.param('# node_modules/', 'node_modules', False, id='comment'),
    pytest.param('', 'x', False, id='empty-file'),
    pytest.param('   \n\n', 'x', False, id='blank-lines'),
    pytest.param('node_modules/\r\n', 'node_modules', True, id='crlf-line-ending'),
    pytest.param('node_modules/   ', 'node_modules', True, id='trailing-spaces-dropped'),
    pytest.param('Build/', 'build', False, id='case-sensitive'),
])
def test_directory_is_ignored_only_when_a_modelled_pattern_matches(text, path, expected):
    assert verdict(text, path) is expected


@pytest.mark.parametrize('text,expected', [
    pytest.param('build/\n!build/', False, id='negation-reincludes'),
    pytest.param('!build/\nbuild/', True, id='later-rule-wins-over-earlier-negation'),
    pytest.param('*/\n!keep/', False, id='negated-name-after-star'),
    pytest.param('build/\nbuild/\n!build/\nbuild/', True, id='last-match-decides'),
])
def test_last_matching_rule_decides(text, expected):
    assert verdict(text, 'build' if 'keep' not in text else 'keep') is expected


def test_rule_applies_only_below_the_directory_of_its_ignore_file():
    text = 'out/'
    assert verdict(text, 'pkg/out', base=('pkg',)) is True
    assert verdict(text, 'out', base=('pkg',)) is False
    assert verdict(text, 'other/out', base=('pkg',)) is False
    assert verdict('/out', 'pkg/out', base=('pkg',)) is True
    assert verdict('/out', 'pkg/deep/out', base=('pkg',)) is False


def test_deeper_ignore_file_overrides_a_shallower_one_when_rules_are_concatenated():
    shallow = rules.parse('out/')
    deep = rules.parse('!out/', ('pkg',))
    assert rules.ignored(shallow + deep, ('pkg', 'out')) is False
    assert rules.ignored(shallow + deep, ('out',)) is True


@pytest.mark.parametrize('text', [
    pytest.param('\\#literal', id='backslash-escape'),
    pytest.param('a\\ b/', id='escaped-space'),
    pytest.param('[[:alpha:]]x/', id='posix-class'),
    pytest.param('[^a]x/', id='caret-negated-class'),
    pytest.param('a[b/', id='unbalanced-bracket'),
    pytest.param('a//b/', id='empty-segment'),
    pytest.param('/', id='lone-slash'),
    pytest.param('!', id='lone-negation'),
    pytest.param('/'.join(['a'] * 17), id='too-many-segments'),
    pytest.param('a/**/b/**/c/**/d/**/e', id='too-many-double-stars'),
])
def test_pattern_outside_the_modelled_subset_makes_the_file_unknown(text):
    assert rules.parse(text + '\nnode_modules/\n') is rules.UNKNOWN


def test_comment_with_a_backslash_is_not_a_pattern():
    assert rules.parse('# a \\ comment\nnode_modules/\n') is not rules.UNKNOWN


def test_more_rules_than_the_bound_make_the_file_unknown(monkeypatch):
    monkeypatch.setattr(rules, 'MAX_RULES', 3)
    assert rules.parse('a/\nb/\nc/\n') is not rules.UNKNOWN
    assert rules.parse('a/\nb/\nc/\nd/\n') is rules.UNKNOWN


def test_unknown_marker_anywhere_in_the_rules_ignores_nothing():
    known = rules.parse('node_modules/')
    assert rules.ignored(known, ('node_modules',)) is True
    assert rules.ignored(known + (rules.UNKNOWN,), ('node_modules',)) is False
    assert rules.ignored((rules.UNKNOWN,), ('node_modules',)) is False


def test_no_rules_ignore_nothing():
    assert rules.ignored((), ('node_modules',)) is False
