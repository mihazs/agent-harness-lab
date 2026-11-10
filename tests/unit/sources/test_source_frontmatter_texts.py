"""The command, skill and prompt inventory reads frontmatter through the shared texts() mode (#307); synthetic only."""
import pytest

from agent_harness_lab.sources import agents_inventory as agents
from agent_harness_lab.sources import claude_rules as rules
from agent_harness_lab.sources import commands_inventory as commands
from agent_harness_lab.sources import frontmatter
from tests.support import harness_surface_fixture as fixture

CLAUDE = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='.')
CODEX = dict(schema_version=1, runtime='codex', version='0.166.0', task_cwd='.')
CASES = ((b'---\nname: a\n', 'unterminated'), (b'---\nname: a\n...\n', 'unterminated'),
         (b'---\nname: a\nname: b\n---\n', 'bad_yaml'), (b'---\ndescription: use when: x\n---\n', 'bad_yaml'),
         (b'---\nname: &x a\n---\n', 'unsupported_yaml'), (b'---\nmeta: {a: b}\n---\n', 'unsupported_yaml'),
         (b'---\nname: \xff\n---\n', 'not_utf8'),
         (b'---\n' + b''.join(b'k%d: v\n' % i for i in range(600)) + b'---\n', 'too_large'),
         (b'---\nd: ' + b'x' * 70000 + b'\n---\n', 'too_large'))


def reason(function, raw):
    with pytest.raises(frontmatter.FrontmatterError) as caught: function(raw)
    return caught.value.reason


class TestTextsMode:
    def test_texts_projects_values_to_text_and_keeps_read_unchanged(self):
        assert frontmatter.texts(b'') is None
        assert frontmatter.texts(b'body only\n') is None
        raw = b'---\nname: "q" # c\nempty:\ntags: [a, b]\npaths:\n  - src/**\nmeta:\n  k: v\n---\nbody'
        assert frontmatter.texts(raw) == dict(name='q', empty='', tags=None, paths=None, meta=None)
        assert frontmatter.texts('﻿---\nname: a\n---\nbody'.encode()) == dict(name='a')
        assert frontmatter.texts(b'---\nname: a\n---\n--- \n') == dict(name='a')  # a body line is no fence
        # read() keeps its own contract: a BOM is not a fence, and a list or mapping stays a structured value.
        assert reason(frontmatter.read, b'\xef\xbb\xbf---\nname: a\n---\n') == 'no_leading_fence'
        assert frontmatter.read(b'---\ntags: [a, b]\n---\n') == dict(tags=['a', 'b'])

    @pytest.mark.parametrize('raw', [
        pytest.param(b'--- \nname: a\n---\n', id='trailing-space'),
        pytest.param(b'---\nname: a\n---\t\n', id='closing-tab'),
        pytest.param(b'\xef\xbb\xbf---  \nname: a\n---\n', id='bom-and-spaces'),
    ])
    def test_a_space_padded_fence_is_never_judged(self, raw):
        assert reason(frontmatter.texts, raw) == 'unsupported_yaml'

    # #312: a fence line ending in spaces or tabs is never judged in any mode, and never read as unfenced.
    @pytest.mark.parametrize('raw', [
        pytest.param(b'--- \nname: a\n---\n', id='trailing-space'),
        pytest.param(b'---\nname: a\n---\t\nbody', id='closing-tab'),
        pytest.param(b'---\t \r\nname: a\r\n---\r\n', id='tab-space-crlf'),
    ])
    def test_no_mode_reads_a_whitespace_ended_fence_as_no_fence(self, raw):
        assert reason(frontmatter.read, raw) == 'unsupported_yaml'
        assert reason(frontmatter.texts, raw) == 'unsupported_yaml'

    def test_only_a_carriage_return_is_dropped_from_a_fence_and_an_indented_fence_is_no_fence(self):
        assert frontmatter.read(b'---\r\nname: a\r\n---\r\n') == dict(name='a')  # only '\r' is dropped
        assert frontmatter.texts(b'---\nd: |\n  --- \n---\n') == dict(d='---')  # an indented '---' is no fence

    @pytest.mark.parametrize('text', [
        pytest.param('--- \npaths:\n  - a\n---\n', id='opening-fence'),
        pytest.param('---\npaths:\n  - a\n--- \n---\n', id='closing-fence'),
    ])
    def test_string_lists_reject_a_whitespace_ended_fence(self, text):
        with pytest.raises(frontmatter.Unsupported) as caught:
            frontmatter.string_lists(text, ('paths',))
        assert caught.value.reason == 'unsupported_yaml'

    def test_a_whitespace_ended_fence_makes_a_rule_unknown(self):
        rule = b'--- \npaths:\n  - src/**\n---\nbody'
        assert rules.declaration(rule) == ('frontmatter_unsupported', None)
        assert rules.classify('.', *rules.declaration(rule), ('.',), ['docs/a.md'], None) == 'unknown'

    @pytest.mark.parametrize('raw', [
        pytest.param(b'--- \nname: rev\ndescription: d\n---\nbody', id='opening-fence'),
        pytest.param(b'---\nname: rev\ndescription: d\n--- \n---\nbody', id='closing-fence'),
    ])
    def test_a_whitespace_ended_fence_makes_an_agent_unknown(self, raw):
        facts = agents.parse('.claude/agents/rev.md', raw, CLAUDE)[1]
        assert (facts['disposition'], facts['reason']) == ('unknown', 'frontmatter_unsupported_yaml')

    @pytest.mark.parametrize('raw, expected', [pytest.param(raw, expected, id=f'{expected}-{index}')
                                               for index, (raw, expected) in enumerate(CASES)])
    def test_texts_has_the_shared_bounds_and_error_reasons(self, raw, expected):
        assert reason(frontmatter.texts, raw) == expected
        assert reason(frontmatter.read, raw) == expected  # one reader, so verdicts cannot drift


SAMPLES = (b'', b'# body\n', b'---\nname: "q" # c\n---\n', b'---\ndescription:\n  - one\n---\n',
           b'\xef\xbb\xbf---\nname: a\n---\n', b'--- \nname: a\n---\n') + tuple(raw for raw, _ in CASES)


class TestCommandsInventoryReader:
    @pytest.fixture(autouse=True)
    def _base(self, tmp_path):
        self.base = tmp_path.resolve()

    @pytest.mark.parametrize('raw', [pytest.param(raw, id=f'sample-{index}') for index, raw in enumerate(SAMPLES)])
    def test_the_commands_inventory_reads_only_through_texts(self, raw):
        try: expected = ('ok', frontmatter.texts(raw))
        except frontmatter.FrontmatterError as error: expected = ('error', error.reason)
        try: actual = ('ok', commands.frontmatter(raw))
        except frontmatter.FrontmatterError as error: actual = ('error', error.reason)
        assert actual == expected

    def test_a_listed_description_is_parsed_without_a_description_length(self):
        listed = b'---\ndescription:\n  - one\n  - two\n---\nbody'
        assert commands.parse('.claude/commands/x.md', listed, CLAUDE) == ('parsed', dict(frontmatter=True, name_key=False, paths_key=False, description_chars=None))

    def test_a_skill_with_a_non_text_name_is_parse_failed_and_unsupported_yaml_is_unknown(self):
        checkout = fixture.build(self.base / 'checkout', {
            '.claude/skills/listed/SKILL.md': '---\nname:\n  - other\ndescription: d\n---\nbody\n',
            '.claude/skills/anchored/SKILL.md': '---\nname: &x other\ndescription: d\n---\nbody\n',
            '.claude/skills/plain/SKILL.md': '---\nname: plain\nallowed-tools:\n  - Read\ndescription: d\n---\nbody\n'},
            hostile=False)
        found = {e.path: (e.id, e.flags) for e in commands.capture(checkout, CLAUDE).entries if e.kind == 'skill'}
        assert found['.claude/skills/listed/SKILL.md'] == ('/listed', ('parse_failed',))
        assert found['.claude/skills/anchored/SKILL.md'] == (None, ('frontmatter_unsupported',))
        assert found['.claude/skills/plain/SKILL.md'] == ('/plain', ())

    def test_a_codex_skill_with_a_list_description_is_parse_failed_not_a_crash(self):
        checkout = fixture.build(self.base / 'codex', {
            '.agents/skills/tool/SKILL.md': '---\nname: tool\ndescription:\n  - a\n---\nbody\n'}, hostile=False)
        rows = {e.path: e.flags for e in commands.capture(checkout, CODEX).entries if e.kind == 'skill'}
        assert 'parse_failed' in rows['.agents/skills/tool/SKILL.md']

