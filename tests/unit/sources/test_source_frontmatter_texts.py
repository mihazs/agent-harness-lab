"""The command, skill and prompt inventory reads frontmatter through the shared texts() mode (#307); synthetic only."""
from pathlib import Path
import tempfile
import unittest

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
    with unittest.TestCase().assertRaises(frontmatter.FrontmatterError) as caught: function(raw)
    return caught.exception.reason


class TextsModeTests(unittest.TestCase):
    def test_texts_projects_values_to_text_and_keeps_read_unchanged(self):
        self.assertIsNone(frontmatter.texts(b''))
        self.assertIsNone(frontmatter.texts(b'body only\n'))
        raw = b'---\nname: "q" # c\nempty:\ntags: [a, b]\npaths:\n  - src/**\nmeta:\n  k: v\n---\nbody'
        self.assertEqual(frontmatter.texts(raw), dict(name='q', empty='', tags=None, paths=None, meta=None))
        self.assertEqual(frontmatter.texts('﻿---\nname: a\n---\nbody'.encode()), dict(name='a'))
        self.assertEqual(frontmatter.texts(b'---\nname: a\n---\n--- \n'), dict(name='a'))  # a body line is no fence
        # read() keeps its own contract: a BOM is not a fence, and a list or mapping stays a structured value.
        self.assertEqual(reason(frontmatter.read, b'\xef\xbb\xbf---\nname: a\n---\n'), 'no_leading_fence')
        self.assertEqual(frontmatter.read(b'---\ntags: [a, b]\n---\n'), dict(tags=['a', 'b']))

    def test_a_space_padded_fence_is_never_judged(self):
        for raw in (b'--- \nname: a\n---\n', b'---\nname: a\n---\t\n', b'\xef\xbb\xbf---  \nname: a\n---\n'):
            with self.subTest(raw=raw):
                self.assertEqual(reason(frontmatter.texts, raw), 'unsupported_yaml')

    def test_no_mode_reads_a_whitespace_ended_fence_as_no_fence(self):
        # #312: a fence line ending in spaces or tabs is never judged in any mode, and never read as unfenced.
        for raw in (b'--- \nname: a\n---\n', b'---\nname: a\n---\t\nbody', b'---\t \r\nname: a\r\n---\r\n'):
            with self.subTest(raw=raw):
                self.assertEqual(reason(frontmatter.read, raw), 'unsupported_yaml')
                self.assertEqual(reason(frontmatter.texts, raw), 'unsupported_yaml')
        self.assertEqual(frontmatter.read(b'---\r\nname: a\r\n---\r\n'), dict(name='a'))  # only '\r' is dropped
        self.assertEqual(frontmatter.texts(b'---\nd: |\n  --- \n---\n'), dict(d='---'))  # an indented '---' is no fence
        for text in ('--- \npaths:\n  - a\n---\n', '---\npaths:\n  - a\n--- \n---\n'):
            with self.subTest(text=text), self.assertRaises(frontmatter.Unsupported) as caught:
                frontmatter.string_lists(text, ('paths',))
            self.assertEqual(caught.exception.reason, 'unsupported_yaml')
        rule = b'--- \npaths:\n  - src/**\n---\nbody'
        self.assertEqual(rules.declaration(rule), ('frontmatter_unsupported', None))
        self.assertEqual(rules.classify('.', *rules.declaration(rule), ('.',), ['docs/a.md'], None), 'unknown')
        for raw in (b'--- \nname: rev\ndescription: d\n---\nbody', b'---\nname: rev\ndescription: d\n--- \n---\nbody'):
            with self.subTest(raw=raw):
                facts = agents.parse('.claude/agents/rev.md', raw, CLAUDE)[1]
                self.assertEqual((facts['disposition'], facts['reason']), ('unknown', 'frontmatter_unsupported_yaml'))

    def test_texts_has_the_shared_bounds_and_error_reasons(self):
        for raw, expected in CASES:
            with self.subTest(reason=expected, raw=raw[:24]):
                self.assertEqual(reason(frontmatter.texts, raw), expected)
                self.assertEqual(reason(frontmatter.read, raw), expected)  # one reader, so verdicts cannot drift


class CommandsInventoryReaderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='ahl-frontmatter-'); self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()

    def test_the_commands_inventory_reads_only_through_texts(self):
        samples = (b'', b'# body\n', b'---\nname: "q" # c\n---\n', b'---\ndescription:\n  - one\n---\n',
                   b'\xef\xbb\xbf---\nname: a\n---\n', b'--- \nname: a\n---\n') + tuple(raw for raw, _ in CASES)
        for raw in samples:
            with self.subTest(raw=raw[:24]):
                try: expected = ('ok', frontmatter.texts(raw))
                except frontmatter.FrontmatterError as error: expected = ('error', error.reason)
                try: actual = ('ok', commands.frontmatter(raw))
                except frontmatter.FrontmatterError as error: actual = ('error', error.reason)
                self.assertEqual(actual, expected)
        listed = b'---\ndescription:\n  - one\n  - two\n---\nbody'
        self.assertEqual(commands.parse('.claude/commands/x.md', listed, CLAUDE),
                         ('parsed', dict(frontmatter=True, name_key=False, paths_key=False, description_chars=None)))

    def test_a_skill_with_a_non_text_name_is_parse_failed_and_unsupported_yaml_is_unknown(self):
        checkout = fixture.build(self.base / 'checkout', {
            '.claude/skills/listed/SKILL.md': '---\nname:\n  - other\ndescription: d\n---\nbody\n',
            '.claude/skills/anchored/SKILL.md': '---\nname: &x other\ndescription: d\n---\nbody\n',
            '.claude/skills/plain/SKILL.md': '---\nname: plain\nallowed-tools:\n  - Read\ndescription: d\n---\nbody\n'},
            hostile=False)
        found = {e.path: (e.id, e.flags) for e in commands.capture(checkout, CLAUDE).entries if e.kind == 'skill'}
        self.assertEqual(found['.claude/skills/listed/SKILL.md'], ('/listed', ('parse_failed',)))
        self.assertEqual(found['.claude/skills/anchored/SKILL.md'], (None, ('frontmatter_unsupported',)))
        self.assertEqual(found['.claude/skills/plain/SKILL.md'], ('/plain', ()))

    def test_a_codex_skill_with_a_list_description_is_parse_failed_not_a_crash(self):
        checkout = fixture.build(self.base / 'codex', {
            '.agents/skills/tool/SKILL.md': '---\nname: tool\ndescription:\n  - a\n---\nbody\n'}, hostile=False)
        rows = {e.path: e.flags for e in commands.capture(checkout, CODEX).entries if e.kind == 'skill'}
        self.assertIn('parse_failed', rows['.agents/skills/tool/SKILL.md'])


if __name__ == '__main__':
    unittest.main()
