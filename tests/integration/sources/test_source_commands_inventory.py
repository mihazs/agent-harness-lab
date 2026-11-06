"""Commands, nested and ancestor skill roots and declared Codex prompts over synthetic checkouts only."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab import surfaces
from agent_harness_lab.sources import commands_inventory as commands
from agent_harness_lab.sources import harness_inventory as inventory
from tests.support import harness_surface_fixture as fixture

CLAUDE = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='.')
CODEX = dict(schema_version=1, runtime='codex', version='0.166.0', task_cwd='src/task')
SECRET = fixture.SECRET
OPENED = []


def _audit(event, args):
    if OPENED and event == 'open' and isinstance(args[0], str): OPENED[-1].append(args[0])


sys.addaudithook(_audit)


def skill(name, description='synthetic skill'):
    return f'---\nname: {name}\ndescription: {description}\n---\n{SECRET}\n'


def denied(*args, **kwargs): raise AssertionError('commands inventory must not spawn processes or use the network')


class CommandsInventoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='ahl-commands-'); self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()

    def build(self, files, name='checkout'):
        return fixture.build(self.base / name, files, hostile=False)

    def entries(self, value, kind=None):
        self.assertEqual(value.status, 'declared')
        return {e.path: e for e in value.entries if kind is None or e.kind == kind}

    def test_a_command_with_a_same_name_skill_is_shadowed_and_frontmatter_name_and_paths_are_ignored(self):
        checkout = self.build({'.claude/commands/alpha.md': f'{SECRET}\n',
            '.claude/commands/frontend/component.md': f'---\nname: other\npaths: src/**\ndescription: d\n---\n{SECRET}\n',
            '.claude/commands/broken.md': 'placeholder\n', '.claude/commands/notes.txt': 'not markdown\n'})
        (checkout / '.claude/commands/broken.md').write_bytes(b'\xff\xfe')
        value = commands.capture(checkout, CLAUDE)
        found = self.entries(value, 'command')
        self.assertEqual({p: (e.id, e.load, e.flags) for p, e in found.items()}, {
            '.claude/commands/alpha.md': ('/alpha', 'startup', ('shadowed_by_skill',)),
            '.claude/commands/broken.md': ('/broken', 'startup', ('parse_failed',)),
            '.claude/commands/frontend/component.md': ('/frontend:component', 'startup',
                                                       ('frontmatter_name_ignored', 'frontmatter_paths_ignored'))})
        self.assertEqual(self.entries(value, 'skill')['.claude/skills/alpha/SKILL.md'].id, '/alpha')
        nodes = {n.path: n for n in inventory.capture(checkout, CLAUDE).nodes}
        component = nodes['.claude/commands/frontend/component.md']
        self.assertEqual((component.kind, component.parse_status, component.capability_ref),
                         ('command', 'parsed', 'commands_skills:claude-code'))
        self.assertEqual(dict(component.facts), dict(frontmatter=True, name_key=True, paths_key=True, description_chars=1))
        self.assertEqual(nodes['.claude/skills/alpha/SKILL.md'].parse_status, 'not_parsed')
        # Commands are a Claude Code surface: a Codex inventory of the same checkout lists none.
        codex = commands.capture(checkout, dict(CODEX, task_cwd='.'))
        self.assertFalse(self.entries(codex, 'command'))
        self.assertNotIn(SECRET, json.dumps(value.document()))

    def test_nested_subdirectory_skills_load_on_demand_with_a_subdirectory_qualified_id(self):
        checkout = self.build({'.claude/skills/deploy/SKILL.md': skill('deploy'),
                               'apps/web/.claude/skills/deploy/SKILL.md': f'{SECRET}\n',
                               'apps/web/.claude/commands/ship.md': f'{SECRET}\n'})
        found = self.entries(commands.capture(checkout, CLAUDE))
        self.assertEqual((found['.claude/skills/deploy/SKILL.md'].id, found['.claude/skills/deploy/SKILL.md'].load),
                         ('/deploy', 'startup'))
        nested = found['apps/web/.claude/skills/deploy/SKILL.md']
        self.assertEqual((nested.id, nested.load, nested.flags, nested.openai_yaml), ('/apps/web:deploy', 'on_demand', (), None))
        self.assertEqual(found['apps/web/.claude/commands/ship.md'].load, 'unknown')
        inside = self.entries(commands.capture(checkout, dict(CLAUDE, task_cwd='apps/web')))
        self.assertEqual((inside['apps/web/.claude/skills/deploy/SKILL.md'].id,
                          inside['apps/web/.claude/skills/deploy/SKILL.md'].load), ('/deploy', 'startup'))

    def test_nested_skills_are_qualified_only_on_a_directory_clash_relative_to_task_cwd(self):
        # code.claude.com/docs/en/skills (2.1.317): a nested skill is qualified only when its directory name
        # clashes, as the subdirectory path relative to the working directory, then the skill directory name.
        checkout = self.build({'.claude/skills/deploy/SKILL.md': skill('deploy'),
                               'apps/web/.claude/skills/deploy/SKILL.md': skill('renamed'),
                               'apps/web/.claude/skills/unique/SKILL.md': f'{SECRET}\n',
                               'apps/.claude/commands/unique.md': f'{SECRET}\n',
                               'lib/.claude/skills/sibling/SKILL.md': f'{SECRET}\n'})
        found = self.entries(commands.capture(checkout, dict(CLAUDE, task_cwd='apps')))
        rows = {p: (e.id, e.load, e.flags) for p, e in found.items()}
        self.assertEqual(rows['apps/web/.claude/skills/deploy/SKILL.md'], ('/web:deploy', 'on_demand', ()))
        self.assertEqual(rows['apps/web/.claude/skills/unique/SKILL.md'], ('/unique', 'on_demand', ()))
        self.assertEqual(rows['apps/.claude/commands/unique.md'], ('/unique', 'startup', ('shadowed_by_skill',)))
        self.assertEqual(rows['lib/.claude/skills/sibling/SKILL.md'], (None, 'unknown', ()))
        self.assertEqual(self.entries(commands.capture(checkout, CLAUDE))['apps/web/.claude/skills/deploy/SKILL.md'].id,
                         '/apps/web:deploy')

    def test_frontmatter_names_never_reach_the_document_and_ids_are_bounded(self):
        block = '---\nname: |\n  line1\n  line2 PRIVATE_BLOCK\n---\n'
        checkout = self.build({'.claude/skills/renamed/SKILL.md': skill('PRIVATE_NAME_TEXT'),
                               '.claude/skills/block/SKILL.md': block,
                               '.claude/commands/PRIVATE_NAME_TEXT.md': f'{SECRET}\n',
                               '.agents/skills/huge/SKILL.md': skill('N' * 5000),
                               '.agents/skills/other/SKILL.md': skill('PRIVATE_CODEX_NAME'),
                               '.agents/skills/other2/SKILL.md': skill('PRIVATE_CODEX_NAME')})
        claude = commands.capture(checkout, CLAUDE)
        found = self.entries(claude)
        self.assertEqual((found['.claude/skills/renamed/SKILL.md'].id, found['.claude/skills/renamed/SKILL.md'].flags),
                         (None, ('id_withheld',)))
        self.assertEqual(found['.claude/skills/block/SKILL.md'].id, None)
        # The shadowing check still resolves on the private name; only the emitted id is withheld.
        self.assertEqual(found['.claude/commands/PRIVATE_NAME_TEXT.md'].flags, ('shadowed_by_skill',))
        document = json.dumps(claude.document())
        for text in ('PRIVATE_BLOCK', 'line1'): self.assertNotIn(text, document)
        self.assertEqual(document.count('PRIVATE_NAME_TEXT'), 2)  # only the command's path and path-derived id
        codex = commands.capture(checkout, dict(CODEX, task_cwd='.'))
        rows = {p: (e.id, e.flags) for p, e in self.entries(codex, 'skill').items()}
        self.assertEqual(rows['.agents/skills/huge/SKILL.md'], (None, ('name_too_long',)))
        self.assertEqual(rows['.agents/skills/other/SKILL.md'], (None, ('duplicate_name', 'id_withheld')))
        document = json.dumps(codex.document())
        for text in ('N' * 65, 'PRIVATE_CODEX_NAME'): self.assertNotIn(text, document)
        entry = codex.entries[0]
        for bad in ('/a\nb', '/' + 'x' * 5000):
            with self.subTest(bad=bad[:8]), self.assertRaises(ValueError): replace(entry, id=bad)

    def test_private_excluded_command_and_skill_nodes_are_skipped_unread(self):
        files = {'.claude/commands/__pycache__/x.md': f'{SECRET}\n', '.claude/skills/__pycache__/SKILL.md': skill('p'),
                 '.agents/skills/__pycache__/SKILL.md': skill('q')}
        checkout = self.build(files)
        for profile in (CLAUDE, dict(CODEX, task_cwd='.')):
            with self.subTest(runtime=profile['runtime']):
                self.assertEqual(inventory.capture(checkout, profile).status, 'declared')
                OPENED.append([])
                try: value = commands.capture(checkout, profile)
                finally: opened = OPENED.pop()
                self.assertFalse(any(path.endswith(os.path.join('__pycache__', 'SKILL.md')) for path in opened))
                self.assertFalse(set(files) & set(self.entries(value)))

    def test_codex_ancestor_skill_roots_are_all_listed_and_bounds_are_flagged(self):
        checkout = self.build({'src/task/.agents/skills/beta/SKILL.md': skill('beta'),
            '.agents/skills/beta/SKILL.md': skill('beta'),
            'src/.agents/skills/long/SKILL.md': skill('n' * 65, 'd' * 1025),
            'src/.agents/skills/long/agents/openai.yaml': 'interface: {}\n',
            'lib/.agents/skills/beta/SKILL.md': skill('beta'),
            '.agents/skills/a/b/c/d/e/f/deep/SKILL.md': skill('deep'),
            '.agents/skills/nameless/SKILL.md': f'---\ndescription: x\n---\n{SECRET}\n',
            '.agents/skills/nofm/SKILL.md': f'{SECRET}\n',
            '.agents/skills/blank/SKILL.md': '---\nname: blank\ndescription: ""\n---\n',
            '.agents/skills/undescribed/SKILL.md': '---\nname: undescribed\n---\n'})
        found = self.entries(commands.capture(checkout, CODEX), 'skill')
        rows = {p: (e.id, e.load, e.flags, e.labels, e.openai_yaml) for p, e in found.items()}
        self.assertEqual(rows['.agents/skills/beta/SKILL.md'], ('$beta', 'startup', ('duplicate_name',), (), False))
        self.assertEqual(rows['src/task/.agents/skills/beta/SKILL.md'], ('$beta', 'startup', ('duplicate_name',), (), False))
        self.assertEqual(rows['lib/.agents/skills/beta/SKILL.md'], ('$beta', 'outside_scan', (), (), False))
        self.assertEqual(rows['src/.agents/skills/long/SKILL.md'][1:],
                         ('startup', ('description_too_long', 'name_too_long'), ('description_limit_unverified',), True))
        self.assertEqual(rows['.agents/skills/a/b/c/d/e/f/deep/SKILL.md'][2], ('depth_exceeded',))
        # Pinned parser.rs: a missing name defaults to the directory; a missing frontmatter block or an empty
        # or missing description is rejected, so the skill has no key and joins no duplicate or config check.
        self.assertEqual(rows['.agents/skills/nameless/SKILL.md'][:3], ('$nameless', 'startup', ()))
        self.assertEqual(rows['.agents/skills/nofm/SKILL.md'][:3], (None, 'startup', ('parse_failed',)))
        for path in ('.agents/skills/blank/SKILL.md', '.agents/skills/undescribed/SKILL.md'):
            self.assertEqual(rows[path][:3], (None, 'startup', ('description_missing',)))
        self.assertNotIn('.claude/skills/alpha/SKILL.md', found)  # Codex does not read .claude/skills
        # Telemetry skill prefixes come from the unchanged adoptable bundle rows.
        self.assertEqual(surfaces.skill_prefixes(), ('.agents/skills', '.claude/skills', 'skills'))

    def test_skills_config_disables_apply_only_from_a_declared_config(self):
        checkout = self.build({'src/task/.agents/skills/gamma/SKILL.md': skill('gamma'),
                               '.agents/skills/beta/SKILL.md': skill('beta')})
        config = self.base / 'config.toml'
        config.write_text(f'[[skills.config]]\npath = "{checkout}/.agents/skills/beta/SKILL.md"\nenabled = false\n'
                          '[[skills.config]]\nname = "gamma"\nenabled = false\n'
                          '[[skills.config]]\nname = "gamma"\npath = "/synthetic/both"\nenabled = true\n')
        plain = self.entries(commands.capture(checkout, CODEX), 'skill')
        self.assertFalse(any(e.flags for e in plain.values()))
        value = commands.capture(checkout, CODEX, codex_config=str(config))
        found = self.entries(value, 'skill')
        self.assertEqual(value.skills_config, 'declared')
        self.assertEqual({p: e.flags for p, e in found.items()},
                         {'.agents/skills/beta/SKILL.md': ('disabled_by_declared_config',),
                          'src/task/.agents/skills/gamma/SKILL.md': ('disabled_by_declared_config',)})
        config.write_text(config.read_text() + '[[skills.config]]\nname = "gamma"\nenabled = true\n')
        found = self.entries(commands.capture(checkout, CODEX, codex_config=str(config)), 'skill')
        self.assertEqual(found['src/task/.agents/skills/gamma/SKILL.md'].flags, ('config_conflict',))
        for bad in (dict(codex_config='relative.toml'), dict(codex_config=str(self.base / 'missing.toml'))):
            with self.subTest(bad=bad), self.assertRaises((ValueError, OSError)): commands.capture(checkout, CODEX, **bad)
        with self.assertRaises(ValueError): commands.capture(checkout, CLAUDE, codex_config=str(config))

    def test_a_nameless_codex_skill_is_named_after_its_directory_for_duplicates_and_config(self):
        checkout = self.build({'.agents/skills/beta/SKILL.md': f'---\ndescription: d\n---\n{SECRET}\n',
                               'src/task/.agents/skills/beta/SKILL.md': skill('beta')})
        config = self.base / 'config.toml'
        config.write_text('[[skills.config]]\nname = "beta"\nenabled = false\n')
        found = self.entries(commands.capture(checkout, CODEX, codex_config=str(config)), 'skill')
        self.assertEqual({p: (e.id, e.flags) for p, e in found.items()}, {
            '.agents/skills/beta/SKILL.md': ('$beta', ('disabled_by_declared_config', 'duplicate_name')),
            'src/task/.agents/skills/beta/SKILL.md': ('$beta', ('disabled_by_declared_config', 'duplicate_name'))})

    def test_commands_below_an_ordinary_skills_directory_are_commands(self):
        checkout = self.build({'packages/skills/.claude/commands/ship.md': f'{SECRET}\n',
                               'skills/demo/.claude/commands/bundled.md': f'{SECRET}\n',
                               '.claude/skills/demo/.claude/commands/inner.md': f'{SECRET}\n'})
        found = self.entries(commands.capture(checkout, dict(CLAUDE, task_cwd='packages/skills')), 'command')
        self.assertEqual({p: (e.id, e.load) for p, e in found.items()},
                         {'packages/skills/.claude/commands/ship.md': ('/ship', 'startup')})
        # A root skills/<dir> encloses its .claude/commands only when the caller owns a root skills/ tree.
        for root_skills, kind in ((False, 'command'), (True, 'skill')):
            with self.subTest(root_skills=root_skills):
                self.assertEqual(surfaces.classify('skills/demo/.claude/commands/x.md', 'claude-code',
                                                   root_skills=root_skills).kind, kind)

    def test_prompts_and_skill_rereads_share_the_inventory_bounds(self):
        profile = dict(CODEX, task_cwd='.')
        checkout = self.build({'.agents/skills/big/SKILL.md': skill('big', 'd' * 600)})
        home = self.base / 'codex-home'
        (home / 'prompts').mkdir(parents=True)
        for index in range(3): (home / f'prompts/p{index}.md').write_text('x' * 400)
        self.assertEqual(commands.capture(checkout, profile, codex_home=str(home)).status, 'declared')
        walk = inventory._Walk(profile)
        self.assertEqual(inventory.capture(checkout, profile, walk=walk).status, 'declared')
        # Each budget alone is enough; one byte budget across inventory, re-reads and prompts blocks.
        with patch.object(inventory, 'MAX_BYTES', walk.bytes + 1000):
            self.assertEqual(inventory.capture(checkout, profile).status, 'declared')
            blocked = commands.capture(checkout, profile, codex_home=str(home))
        self.assertEqual((blocked.status, blocked.blocked_reason), ('blocked', 'byte_bound_exceeded'))
        nodes = len(inventory.capture(checkout, profile).nodes)
        with patch.object(inventory, 'MAX_FILES', nodes + 2):
            blocked = commands.capture(checkout, profile, codex_home=str(home))
        self.assertEqual((blocked.status, blocked.blocked_reason), ('blocked', 'file_bound_exceeded'))
        with patch.object(inventory, 'MAX_FILES', nodes + 3):
            self.assertEqual(len(self.entries(commands.capture(checkout, profile, codex_home=str(home)), 'prompt')), 3)

    def test_frontmatter_comments_and_quotes_follow_the_shared_reader(self):
        checkout = self.build({'.claude/skills/deploy/SKILL.md': '---\nname: deploy # primary\ndescription: d\n---\n',
                               '.claude/commands/deploy.md': f'{SECRET}\n',
                               '.claude/skills/open/SKILL.md': '---\nname: open\ndescription: "unclosed\n---\n'})
        found = self.entries(commands.capture(checkout, CLAUDE))
        self.assertEqual((found['.claude/skills/deploy/SKILL.md'].id, found['.claude/skills/deploy/SKILL.md'].flags),
                         ('/deploy', ()))
        self.assertEqual(found['.claude/commands/deploy.md'].flags, ('shadowed_by_skill',))
        self.assertEqual(found['.claude/skills/open/SKILL.md'].flags, ('parse_failed',))
        self.assertEqual(commands.frontmatter(b'---\nname: a # c\ntools: [x, y]\n---\n')['name'], 'a')
        for raw in (b'---\ndescription: "unclosed\n---\n', b'---\nname: a\n...\n', b'---\n' + b''.join(b'k%d: v\n' % i for i in range(600)) + b'---\n'):
            with self.subTest(raw=raw[:20]), self.assertRaises(ValueError): commands.frontmatter(raw)

    def test_frontmatter_the_reader_cannot_model_is_unknown_never_parse_failed(self):
        # Valid YAML outside the reader's subset, an oversized block and a space-padded fence are unknowns: no
        # Codex rejection is claimed, so no description_missing, and no id, duplicate or name-selected config check.
        unsupported = {'flow': '---\nname: flow\ndescription: d\nmetadata: {short-description: s}\n---\n',
                       'anchor': '---\nname: anchor\ndescription: &d text\n---\n',
                       'tag': '---\nname: tag\ndescription: !!str text\n---\n',
                       'big': '---\nname: big\ndescription: ' + 'x' * 70000 + '\n---\n',
                       'spaced': '--- \nname: spaced\ndescription: d\n---\n'}
        files = {f'.agents/skills/{folder}/SKILL.md': text for folder, text in unsupported.items()}
        files.update({'src/task/.agents/skills/flow/SKILL.md': skill('flow'),
                      '.agents/skills/broken/SKILL.md': '---\nname: broken\nname: again\ndescription: d\n---\n'})
        checkout = self.build(files)
        config = self.base / 'config.toml'
        config.write_text(f'[[skills.config]]\nname = "anchor"\nenabled = false\n'
                          f'[[skills.config]]\npath = "{checkout}/.agents/skills/tag/SKILL.md"\nenabled = false\n')
        found = self.entries(commands.capture(checkout, CODEX, codex_config=str(config)), 'skill')
        for folder in ('flow', 'anchor', 'big', 'spaced'):
            with self.subTest(folder=folder):
                row = found[f'.agents/skills/{folder}/SKILL.md']
                self.assertEqual((row.id, row.load, row.flags, row.labels), (None, 'startup', ('frontmatter_unsupported',), ()))
        self.assertEqual(found['.agents/skills/tag/SKILL.md'].flags, ('disabled_by_declared_config', 'frontmatter_unsupported'))
        self.assertEqual((found['src/task/.agents/skills/flow/SKILL.md'].id, found['src/task/.agents/skills/flow/SKILL.md'].flags),
                         ('$flow', ()))
        self.assertEqual(found['.agents/skills/broken/SKILL.md'].flags, ('parse_failed',))  # a duplicate key is definite
        claude = self.build({'.claude/commands/a.md': '---\nargument-hint: [pr-number] [priority]\n---\nbody\n',
                             '.claude/commands/flow.md': f'{SECRET}\n',
                             '.claude/skills/flow/SKILL.md': unsupported['flow']}, name='claude')
        rows = {p: (e.id, e.flags) for p, e in self.entries(commands.capture(claude, CLAUDE)).items()}
        self.assertEqual(rows['.claude/commands/a.md'], ('/a', ('frontmatter_unsupported',)))
        self.assertEqual(rows['.claude/commands/flow.md'], ('/flow', ()))  # an unread skill name shadows nothing
        self.assertEqual(rows['.claude/skills/flow/SKILL.md'], (None, ('frontmatter_unsupported',)))
        node = next(n for n in inventory.capture(claude, CLAUDE).nodes if n.path == '.claude/commands/a.md')
        self.assertEqual((node.parse_status, dict(node.facts)), ('parsed', dict(frontmatter='unsupported')))
        for raw in (b'--- \nname: a\n---\n', b'---\nname: a\n---\t\n'):
            with self.subTest(raw=raw), self.assertRaises(ValueError) as caught: commands.frontmatter(raw)
            self.assertEqual(caught.exception.reason, 'unsupported_yaml')
        self.assertEqual(commands.frontmatter(b'---\nname: a\n---\n--- \n'), dict(name='a'))  # a body line is not a fence

    def test_codex_prompts_are_read_only_from_a_declared_codex_home_and_stay_labelled(self):
        checkout = self.build({'prompts/review.md': f'{SECRET}\n'})
        home = self.base / 'codex-home'
        (home / 'prompts/sub').mkdir(parents=True)
        (home / 'prompts/review.md').write_text(f'---\ndescription: d\n---\n{SECRET}\n')
        (home / 'prompts/sub/nested.md').write_text(SECRET)
        (home / 'prompts/notes.txt').write_text(SECRET)
        (home / 'prompts/linked.md').symlink_to(home / 'prompts/review.md')
        OPENED.append([])
        try: undeclared = commands.capture(checkout, dict(CODEX, task_cwd='.'))
        finally: opened = OPENED.pop()
        self.assertFalse(any('codex-home' in path for path in opened))
        self.assertEqual((undeclared.prompts_root, self.entries(undeclared, 'prompt')), ('not_declared', {}))
        self.assertNotIn('prompts/review.md', {n.path for n in inventory.capture(checkout, dict(CODEX, task_cwd='.')).nodes})
        value = commands.capture(checkout, dict(CODEX, task_cwd='.'), codex_home=str(home))
        prompts = self.entries(value, 'prompt')
        self.assertEqual({p: (e.id, e.root, e.load, e.labels) for p, e in prompts.items()},
                         {'prompts/review.md': ('/prompts:review', 'codex_home', 'unknown',
                                                ('deprecated', 'loader_unverified_at_head'))})
        self.assertEqual({(r.path, r.root, r.reason) for r in value.refusals}, {('prompts/linked.md', 'codex_home', 'symlink')})
        document = json.dumps(value.document())
        for text in ('deprecated', 'loader_unverified_at_head'): self.assertIn(text, document)
        self.assertNotIn(SECRET, document)
        with self.assertRaises(ValueError): commands.capture(checkout, CLAUDE, codex_home=str(home))
        with self.assertRaises(ValueError): commands.capture(checkout, CODEX, codex_home=str(checkout / 'src'))

    def test_entries_never_claim_loading_and_capture_is_read_only(self):
        checkout = self.build({'.claude/commands/alpha.md': f'{SECRET}\n'})
        before = fixture.tree_state(checkout)
        with patch('subprocess.Popen', denied), patch('os.system', denied), patch('os.posix_spawn', denied), \
                patch('os.fork', denied), patch('socket.socket', denied), patch('socket.create_connection', denied):
            value = commands.capture(checkout, CLAUDE)
        self.assertEqual(fixture.tree_state(checkout), before)
        entry = value.entries[0]
        for field, bad in (('observed_loading', 'observed'), ('effective', 'loaded'), ('flags', ('activated',)),
                           ('labels', ('verified',)), ('load', 'always'), ('id', 'alpha'), ('openai_yaml', 'yes')):
            with self.subTest(field=field), self.assertRaises(ValueError): replace(entry, **{field: bad})
        with self.assertRaises(ValueError): replace(value, effective='observed')
        self.assertEqual(value.summary()['observed_loading'], 'unknown')
        self.assertFalse(value.document()['publication_allowed'])
        unpinned = commands.capture(checkout, dict(CLAUDE, version='2.1.300'))
        self.assertEqual((unpinned.status, unpinned.entries), ('unsupported', ()))
        with patch.object(inventory, 'MAX_FILES', 1):
            blocked = commands.capture(checkout, CLAUDE)
        self.assertEqual((blocked.status, blocked.blocked_reason, blocked.entries), ('blocked', 'file_bound_exceeded', ()))

    def test_a_skill_swapped_after_the_inventory_is_refused(self):
        checkout = self.build({})
        real = inventory.capture
        def swapped(*args, **kwargs):
            value = real(*args, **kwargs)
            (checkout / '.claude/skills/alpha/SKILL.md').write_text(skill('other'))
            return value
        with patch.object(inventory, 'capture', swapped), self.assertRaisesRegex(ValueError, 'changed'):
            commands.capture(checkout, CLAUDE)

    def test_frontmatter_is_a_bounded_subset_never_a_yaml_load(self):
        self.assertIsNone(commands.frontmatter(b'# no frontmatter\n'))
        self.assertEqual(commands.frontmatter(b'---\nname: "q"\n# c\ndescription: |\n  one\n  two\n---\nbody'),
                         dict(name='q', description='one\ntwo'))
        for raw in (b'---\nname: a\n', b'---\nname: a\nname: b\n---\n', b'---\n  orphan\n---\n', b'---\nbare\n---\n',
                    b'---\nname: \xff\n---\n'):
            with self.subTest(raw=raw), self.assertRaises(ValueError): commands.frontmatter(raw)
        self.assertEqual(commands.parse('AGENTS.md', b'', CODEX), ('not_parsed', {}))


if __name__ == '__main__':
    unittest.main()
