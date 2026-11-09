"""Commands, nested and ancestor skill roots and declared Codex prompts over synthetic checkouts only."""
from dataclasses import replace
import json
import os
import sys
from unittest.mock import patch

import pytest

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


# Valid YAML outside the reader's subset, an oversized block and a space-padded fence.
UNSUPPORTED_SKILLS = {'flow': '---\nname: flow\ndescription: d\nmetadata: {short-description: s}\n---\n',
                      'anchor': '---\nname: anchor\ndescription: &d text\n---\n',
                      'tag': '---\nname: tag\ndescription: !!str text\n---\n',
                      'big': '---\nname: big\ndescription: ' + 'x' * 70000 + '\n---\n',
                      'spaced': '--- \nname: spaced\ndescription: d\n---\n'}


class TestCommandsInventory:
    @pytest.fixture(autouse=True)
    def _base(self, tmp_path):
        self.base = tmp_path.resolve()

    def build(self, files, name='checkout'):
        return fixture.build(self.base / name, files, hostile=False)

    def entries(self, value, kind=None):
        assert value.status == 'declared'
        return {e.path: e for e in value.entries if kind is None or e.kind == kind}

    def test_a_command_with_a_same_name_skill_is_shadowed_and_frontmatter_name_and_paths_are_ignored(self):
        checkout = self.build({'.claude/commands/alpha.md': f'{SECRET}\n',
            '.claude/commands/frontend/component.md': f'---\nname: other\npaths: src/**\ndescription: d\n---\n{SECRET}\n',
            '.claude/commands/broken.md': 'placeholder\n', '.claude/commands/notes.txt': 'not markdown\n'})
        (checkout / '.claude/commands/broken.md').write_bytes(b'\xff\xfe')
        value = commands.capture(checkout, CLAUDE)
        found = self.entries(value, 'command')
        assert ({p: (e.id, e.load, e.flags) for p, e in found.items()} == {
            '.claude/commands/alpha.md': ('/alpha', 'startup', ('shadowed_by_skill',)),
            '.claude/commands/broken.md': ('/broken', 'startup', ('parse_failed',)),
            '.claude/commands/frontend/component.md': ('/frontend:component', 'startup',
                                                       ('frontmatter_name_ignored', 'frontmatter_paths_ignored'))})
        assert self.entries(value, 'skill')['.claude/skills/alpha/SKILL.md'].id == '/alpha'
        nodes = {n.path: n for n in inventory.capture(checkout, CLAUDE).nodes}
        component = nodes['.claude/commands/frontend/component.md']
        assert (component.kind, component.parse_status, component.capability_ref) == ('command', 'parsed', 'commands_skills:claude-code')
        assert dict(component.facts) == dict(frontmatter=True, name_key=True, paths_key=True, description_chars=1)
        assert nodes['.claude/skills/alpha/SKILL.md'].parse_status == 'not_parsed'
        # Commands are a Claude Code surface: a Codex inventory of the same checkout lists none.
        codex = commands.capture(checkout, dict(CODEX, task_cwd='.'))
        assert not self.entries(codex, 'command')
        assert SECRET not in json.dumps(value.document())

    def test_nested_subdirectory_skills_load_on_demand_with_a_subdirectory_qualified_id(self):
        checkout = self.build({'.claude/skills/deploy/SKILL.md': skill('deploy'),
                               'apps/web/.claude/skills/deploy/SKILL.md': f'{SECRET}\n',
                               'apps/web/.claude/commands/ship.md': f'{SECRET}\n'})
        found = self.entries(commands.capture(checkout, CLAUDE))
        assert (found['.claude/skills/deploy/SKILL.md'].id, found['.claude/skills/deploy/SKILL.md'].load) == ('/deploy', 'startup')
        nested = found['apps/web/.claude/skills/deploy/SKILL.md']
        assert (nested.id, nested.load, nested.flags, nested.openai_yaml) == ('/apps/web:deploy', 'on_demand', (), None)
        assert found['apps/web/.claude/commands/ship.md'].load == 'unknown'
        inside = self.entries(commands.capture(checkout, dict(CLAUDE, task_cwd='apps/web')))
        assert ((inside['apps/web/.claude/skills/deploy/SKILL.md'].id,
                          inside['apps/web/.claude/skills/deploy/SKILL.md'].load) == ('/deploy', 'startup'))

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
        assert rows['apps/web/.claude/skills/deploy/SKILL.md'] == ('/web:deploy', 'on_demand', ())
        assert rows['apps/web/.claude/skills/unique/SKILL.md'] == ('/unique', 'on_demand', ())
        assert rows['apps/.claude/commands/unique.md'] == ('/unique', 'startup', ('shadowed_by_skill',))
        assert rows['lib/.claude/skills/sibling/SKILL.md'] == (None, 'unknown', ())
        assert self.entries(commands.capture(checkout, CLAUDE))['apps/web/.claude/skills/deploy/SKILL.md'].id == '/apps/web:deploy'

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
        assert (found['.claude/skills/renamed/SKILL.md'].id, found['.claude/skills/renamed/SKILL.md'].flags) == (None, ('id_withheld',))
        assert found['.claude/skills/block/SKILL.md'].id == None
        # The shadowing check still resolves on the private name; only the emitted id is withheld.
        assert found['.claude/commands/PRIVATE_NAME_TEXT.md'].flags == ('shadowed_by_skill',)
        document = json.dumps(claude.document())
        for text in ('PRIVATE_BLOCK', 'line1'): assert text not in document
        assert document.count('PRIVATE_NAME_TEXT') == 2  # only the command's path and path-derived id
        codex = commands.capture(checkout, dict(CODEX, task_cwd='.'))
        rows = {p: (e.id, e.flags) for p, e in self.entries(codex, 'skill').items()}
        assert rows['.agents/skills/huge/SKILL.md'] == (None, ('name_too_long',))
        assert rows['.agents/skills/other/SKILL.md'] == (None, ('duplicate_name', 'id_withheld'))
        document = json.dumps(codex.document())
        for text in ('N' * 65, 'PRIVATE_CODEX_NAME'): assert text not in document

    @pytest.mark.parametrize('bad', [pytest.param('/a\nb', id='newline'), pytest.param('/' + 'x' * 5000, id='too_long')])
    def test_entry_ids_must_be_one_bounded_line(self, bad):
        checkout = self.build({'.agents/skills/other/SKILL.md': skill('PRIVATE_CODEX_NAME')})
        entry = commands.capture(checkout, dict(CODEX, task_cwd='.')).entries[0]
        with pytest.raises(ValueError): replace(entry, id=bad)

    @pytest.mark.parametrize('profile', [pytest.param(CLAUDE, id='claude-code'), pytest.param(dict(CODEX, task_cwd='.'), id='codex')])
    def test_private_excluded_command_and_skill_nodes_are_skipped_unread(self, profile):
        files = {'.claude/commands/__pycache__/x.md': f'{SECRET}\n', '.claude/skills/__pycache__/SKILL.md': skill('p'),
                 '.agents/skills/__pycache__/SKILL.md': skill('q')}
        checkout = self.build(files)
        assert inventory.capture(checkout, profile).status == 'declared'
        OPENED.append([])
        try: value = commands.capture(checkout, profile)
        finally: opened = OPENED.pop()
        assert not any(path.endswith(os.path.join('__pycache__', 'SKILL.md')) for path in opened)
        assert not set(files) & set(self.entries(value))

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
        assert rows['.agents/skills/beta/SKILL.md'] == ('$beta', 'startup', ('duplicate_name',), (), False)
        assert rows['src/task/.agents/skills/beta/SKILL.md'] == ('$beta', 'startup', ('duplicate_name',), (), False)
        assert rows['lib/.agents/skills/beta/SKILL.md'] == ('$beta', 'outside_scan', (), (), False)
        assert rows['src/.agents/skills/long/SKILL.md'][1:] == ('startup', ('description_too_long', 'name_too_long'), ('description_limit_unverified',), True)
        assert rows['.agents/skills/a/b/c/d/e/f/deep/SKILL.md'][2] == ('depth_exceeded',)
        # Pinned parser.rs: a missing name defaults to the directory; a missing frontmatter block or an empty
        # or missing description is rejected, so the skill has no key and joins no duplicate or config check.
        assert rows['.agents/skills/nameless/SKILL.md'][:3] == ('$nameless', 'startup', ())
        assert rows['.agents/skills/nofm/SKILL.md'][:3] == (None, 'startup', ('parse_failed',))
        for path in ('.agents/skills/blank/SKILL.md', '.agents/skills/undescribed/SKILL.md'):
            assert rows[path][:3] == (None, 'startup', ('description_missing',))
        assert '.claude/skills/alpha/SKILL.md' not in found  # Codex does not read .claude/skills
        # Telemetry skill prefixes come from the unchanged adoptable bundle rows.
        assert surfaces.skill_prefixes() == ('.agents/skills', '.claude/skills', 'skills')

    def test_skills_config_disables_apply_only_from_a_declared_config(self):
        checkout = self.build({'src/task/.agents/skills/gamma/SKILL.md': skill('gamma'),
                               '.agents/skills/beta/SKILL.md': skill('beta')})
        config = self.base / 'config.toml'
        config.write_text(f'[[skills.config]]\npath = "{checkout}/.agents/skills/beta/SKILL.md"\nenabled = false\n'
                          '[[skills.config]]\nname = "gamma"\nenabled = false\n'
                          '[[skills.config]]\nname = "gamma"\npath = "/synthetic/both"\nenabled = true\n')
        plain = self.entries(commands.capture(checkout, CODEX), 'skill')
        assert not any(e.flags for e in plain.values())
        value = commands.capture(checkout, CODEX, codex_config=str(config))
        found = self.entries(value, 'skill')
        assert value.skills_config == 'declared'
        assert ({p: e.flags for p, e in found.items()} == {'.agents/skills/beta/SKILL.md': ('disabled_by_declared_config',),
                          'src/task/.agents/skills/gamma/SKILL.md': ('disabled_by_declared_config',)})
        config.write_text(config.read_text() + '[[skills.config]]\nname = "gamma"\nenabled = true\n')
        found = self.entries(commands.capture(checkout, CODEX, codex_config=str(config)), 'skill')
        assert found['src/task/.agents/skills/gamma/SKILL.md'].flags == ('config_conflict',)
        with pytest.raises(ValueError): commands.capture(checkout, CLAUDE, codex_config=str(config))

    @pytest.mark.parametrize('config', ['relative.toml', 'missing.toml'])
    def test_an_undeclarable_codex_config_path_is_refused(self, config):
        checkout = self.build({'.agents/skills/beta/SKILL.md': skill('beta')})
        path = config if config == 'relative.toml' else str(self.base / config)
        with pytest.raises((ValueError, OSError)): commands.capture(checkout, CODEX, codex_config=path)

    def test_a_nameless_codex_skill_is_named_after_its_directory_for_duplicates_and_config(self):
        checkout = self.build({'.agents/skills/beta/SKILL.md': f'---\ndescription: d\n---\n{SECRET}\n',
                               'src/task/.agents/skills/beta/SKILL.md': skill('beta')})
        config = self.base / 'config.toml'
        config.write_text('[[skills.config]]\nname = "beta"\nenabled = false\n')
        found = self.entries(commands.capture(checkout, CODEX, codex_config=str(config)), 'skill')
        assert ({p: (e.id, e.flags) for p, e in found.items()} == {
            '.agents/skills/beta/SKILL.md': ('$beta', ('disabled_by_declared_config', 'duplicate_name')),
            'src/task/.agents/skills/beta/SKILL.md': ('$beta', ('disabled_by_declared_config', 'duplicate_name'))})

    def test_commands_below_an_ordinary_skills_directory_are_commands(self):
        checkout = self.build({'packages/skills/.claude/commands/ship.md': f'{SECRET}\n',
                               'skills/demo/.claude/commands/bundled.md': f'{SECRET}\n',
                               '.claude/skills/demo/.claude/commands/inner.md': f'{SECRET}\n'})
        found = self.entries(commands.capture(checkout, dict(CLAUDE, task_cwd='packages/skills')), 'command')
        assert {p: (e.id, e.load) for p, e in found.items()} == {'packages/skills/.claude/commands/ship.md': ('/ship', 'startup')}

    # A root skills/<dir> encloses its .claude/commands only when the caller owns a root skills/ tree.
    @pytest.mark.parametrize(('root_skills', 'kind'), [pytest.param(False, 'command', id='no_root_skills'), pytest.param(True, 'skill', id='root_skills')])
    def test_root_skills_decide_whether_a_bundled_command_directory_is_enclosed(self, root_skills, kind):
        assert surfaces.classify('skills/demo/.claude/commands/x.md', 'claude-code', root_skills=root_skills).kind == kind

    def test_prompts_and_skill_rereads_share_the_inventory_bounds(self):
        profile = dict(CODEX, task_cwd='.')
        checkout = self.build({'.agents/skills/big/SKILL.md': skill('big', 'd' * 600)})
        home = self.base / 'codex-home'
        (home / 'prompts').mkdir(parents=True)
        for index in range(3): (home / f'prompts/p{index}.md').write_text('x' * 400)
        assert commands.capture(checkout, profile, codex_home=str(home)).status == 'declared'
        walk = inventory._Walk(profile)
        assert inventory.capture(checkout, profile, walk=walk).status == 'declared'
        # Each budget alone is enough; one byte budget across inventory, re-reads and prompts blocks.
        with patch.object(inventory, 'MAX_BYTES', walk.bytes + 1000):
            assert inventory.capture(checkout, profile).status == 'declared'
            blocked = commands.capture(checkout, profile, codex_home=str(home))
        assert (blocked.status, blocked.blocked_reason) == ('blocked', 'byte_bound_exceeded')
        nodes = len(inventory.capture(checkout, profile).nodes)
        with patch.object(inventory, 'MAX_FILES', nodes + 2):
            blocked = commands.capture(checkout, profile, codex_home=str(home))
        assert (blocked.status, blocked.blocked_reason) == ('blocked', 'file_bound_exceeded')
        with patch.object(inventory, 'MAX_FILES', nodes + 3):
            assert len(self.entries(commands.capture(checkout, profile, codex_home=str(home)), 'prompt')) == 3

    def test_frontmatter_comments_and_quotes_follow_the_shared_reader(self):
        checkout = self.build({'.claude/skills/deploy/SKILL.md': '---\nname: deploy # primary\ndescription: d\n---\n',
                               '.claude/commands/deploy.md': f'{SECRET}\n',
                               '.claude/skills/open/SKILL.md': '---\nname: open\ndescription: "unclosed\n---\n'})
        found = self.entries(commands.capture(checkout, CLAUDE))
        assert (found['.claude/skills/deploy/SKILL.md'].id, found['.claude/skills/deploy/SKILL.md'].flags) == ('/deploy', ())
        assert found['.claude/commands/deploy.md'].flags == ('shadowed_by_skill',)
        assert found['.claude/skills/open/SKILL.md'].flags == ('parse_failed',)
        assert commands.frontmatter(b'---\nname: a # c\ntools: [x, y]\n---\n')['name'] == 'a'

    @pytest.mark.parametrize('raw', [
        pytest.param(b'---\ndescription: "unclosed\n---\n', id='unclosed_quote'), pytest.param(b'---\nname: a\n...\n', id='document_end'),
        pytest.param(b'---\n' + b''.join(b'k%d: v\n' % i for i in range(600)) + b'---\n', id='too_many_keys')])
    def test_malformed_frontmatter_is_rejected(self, raw):
        with pytest.raises(ValueError): commands.frontmatter(raw)

    def unsupported_skills(self):
        files = {f'.agents/skills/{folder}/SKILL.md': text for folder, text in UNSUPPORTED_SKILLS.items()}
        files.update({'src/task/.agents/skills/flow/SKILL.md': skill('flow'),
                      '.agents/skills/broken/SKILL.md': '---\nname: broken\nname: again\ndescription: d\n---\n'})
        checkout = self.build(files)
        config = self.base / 'config.toml'
        config.write_text(f'[[skills.config]]\nname = "anchor"\nenabled = false\n'
                          f'[[skills.config]]\npath = "{checkout}/.agents/skills/tag/SKILL.md"\nenabled = false\n')
        return checkout, self.entries(commands.capture(checkout, CODEX, codex_config=str(config)), 'skill')

    @pytest.mark.parametrize('folder', ['flow', 'anchor', 'big', 'spaced'])
    def test_frontmatter_the_reader_cannot_model_is_unknown_never_parse_failed(self, folder):
        # Valid YAML outside the reader's subset, an oversized block and a space-padded fence are unknowns: no
        # Codex rejection is claimed, so no description_missing, and no id, duplicate or name-selected config check.
        row = self.unsupported_skills()[1][f'.agents/skills/{folder}/SKILL.md']
        assert (row.id, row.load, row.flags, row.labels) == (None, 'startup', ('frontmatter_unsupported',), ())

    def test_unsupported_frontmatter_keeps_ids_config_and_duplicate_keys_definite(self):
        checkout, found = self.unsupported_skills()
        assert found['.agents/skills/tag/SKILL.md'].flags == ('disabled_by_declared_config', 'frontmatter_unsupported')
        assert (found['src/task/.agents/skills/flow/SKILL.md'].id, found['src/task/.agents/skills/flow/SKILL.md'].flags) == ('$flow', ())
        assert found['.agents/skills/broken/SKILL.md'].flags == ('parse_failed',)  # a duplicate key is definite
        claude = self.build({'.claude/commands/a.md': '---\nargument-hint: [pr-number] [priority]\n---\nbody\n',
                             '.claude/commands/flow.md': f'{SECRET}\n',
                             '.claude/skills/flow/SKILL.md': UNSUPPORTED_SKILLS['flow']}, name='claude')
        rows = {p: (e.id, e.flags) for p, e in self.entries(commands.capture(claude, CLAUDE)).items()}
        assert rows['.claude/commands/a.md'] == ('/a', ('frontmatter_unsupported',))
        assert rows['.claude/commands/flow.md'] == ('/flow', ())  # an unread skill name shadows nothing
        assert rows['.claude/skills/flow/SKILL.md'] == (None, ('frontmatter_unsupported',))
        node = next(n for n in inventory.capture(claude, CLAUDE).nodes if n.path == '.claude/commands/a.md')
        assert (node.parse_status, dict(node.facts)) == ('parsed', dict(frontmatter='unsupported'))
        assert commands.frontmatter(b'---\nname: a\n---\n--- \n') == dict(name='a')  # a body line is not a fence

    @pytest.mark.parametrize('raw', [pytest.param(b'--- \nname: a\n---\n', id='padded_opening_fence'),
                                     pytest.param(b'---\nname: a\n---\t\n', id='padded_closing_fence')])
    def test_space_padded_fences_are_unsupported_yaml(self, raw):
        with pytest.raises(ValueError) as caught: commands.frontmatter(raw)
        assert caught.value.reason == 'unsupported_yaml'

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
        assert not any('codex-home' in path for path in opened)
        assert (undeclared.prompts_root, self.entries(undeclared, 'prompt')) == ('not_declared', {})
        assert 'prompts/review.md' not in {n.path for n in inventory.capture(checkout, dict(CODEX, task_cwd='.')).nodes}
        value = commands.capture(checkout, dict(CODEX, task_cwd='.'), codex_home=str(home))
        prompts = self.entries(value, 'prompt')
        assert ({p: (e.id, e.root, e.load, e.labels) for p, e in prompts.items()} == {'prompts/review.md': ('/prompts:review', 'codex_home', 'unknown',
                                                ('deprecated', 'loader_unverified_at_head'))})
        assert {(r.path, r.root, r.reason) for r in value.refusals} == {('prompts/linked.md', 'codex_home', 'symlink')}
        document = json.dumps(value.document())
        for text in ('deprecated', 'loader_unverified_at_head'): assert text in document
        assert SECRET not in document
        with pytest.raises(ValueError): commands.capture(checkout, CLAUDE, codex_home=str(home))
        with pytest.raises(ValueError): commands.capture(checkout, CODEX, codex_home=str(checkout / 'src'))

    @pytest.mark.parametrize(('field', 'bad'), [
        ('observed_loading', 'observed'), ('effective', 'loaded'), ('flags', ('activated',)), ('labels', ('verified',)),
        ('load', 'always'), ('id', 'alpha'), ('openai_yaml', 'yes')])
    def test_entries_reject_loading_claims_and_unknown_vocabulary(self, field, bad):
        entry = commands.capture(self.build({'.claude/commands/alpha.md': f'{SECRET}\n'}), CLAUDE).entries[0]
        with pytest.raises(ValueError): replace(entry, **{field: bad})

    def test_entries_never_claim_loading_and_capture_is_read_only(self):
        checkout = self.build({'.claude/commands/alpha.md': f'{SECRET}\n'})
        before = fixture.tree_state(checkout)
        with patch('subprocess.Popen', denied), patch('os.system', denied), patch('os.posix_spawn', denied), \
                patch('os.fork', denied), patch('socket.socket', denied), patch('socket.create_connection', denied):
            value = commands.capture(checkout, CLAUDE)
        assert fixture.tree_state(checkout) == before
        with pytest.raises(ValueError): replace(value, effective='observed')
        assert value.summary()['observed_loading'] == 'unknown'
        assert not value.document()['publication_allowed']
        unpinned = commands.capture(checkout, dict(CLAUDE, version='2.1.300'))
        assert (unpinned.status, unpinned.entries) == ('unsupported', ())
        with patch.object(inventory, 'MAX_FILES', 1):
            blocked = commands.capture(checkout, CLAUDE)
        assert (blocked.status, blocked.blocked_reason, blocked.entries) == ('blocked', 'file_bound_exceeded', ())

    def test_a_skill_swapped_after_the_inventory_is_refused(self):
        checkout = self.build({})
        real = inventory.capture
        def swapped(*args, **kwargs):
            value = real(*args, **kwargs)
            (checkout / '.claude/skills/alpha/SKILL.md').write_text(skill('other'))
            return value
        with patch.object(inventory, 'capture', swapped), pytest.raises(ValueError, match='changed'):
            commands.capture(checkout, CLAUDE)

    def test_frontmatter_is_a_bounded_subset_never_a_yaml_load(self):
        assert commands.frontmatter(b'# no frontmatter\n') is None
        assert commands.frontmatter(b'---\nname: "q"\n# c\ndescription: |\n  one\n  two\n---\nbody') == dict(name='q', description='one\ntwo')
        assert commands.parse('AGENTS.md', b'', CODEX) == ('not_parsed', {})

    @pytest.mark.parametrize('raw', [
        pytest.param(b'---\nname: a\n', id='unterminated'), pytest.param(b'---\nname: a\nname: b\n---\n', id='duplicate_key'),
        pytest.param(b'---\n  orphan\n---\n', id='orphan_indent'), pytest.param(b'---\nbare\n---\n', id='bare_word'),
        pytest.param(b'---\nname: \xff\n---\n', id='invalid_utf8')])
    def test_frontmatter_subset_rejects_ambiguous_blocks(self, raw):
        with pytest.raises(ValueError): commands.frontmatter(raw)
