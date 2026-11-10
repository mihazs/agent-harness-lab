"""Bounded synthetic skill header observations; no command content is retained."""
import pytest

from agent_harness_lab import surfaces
from agent_harness_lab.sources import harness_inventory, skill_headers
from agent_harness_lab.sources.schema import digest


@pytest.mark.parametrize(('header', 'state'), [
    ('hooks:\n  PreToolUse:\n    - matcher: Edit\n      hooks:\n        - type: command\n          command: SYNTHETIC_SECRET\n', 'unknown'),
    ('hooks: {}\n', 'unknown'),
    ('hooks: arbitrary\n', 'unknown'),
    ('description: synthetic\n', 'not_declared'),
])
def test_skill_hook_frontmatter_is_private_and_bounded(header, state):
    raw = ('---\nname: sample\n' + header + '---\n').encode()
    status, facts = skill_headers.parse('.claude/skills/sample/SKILL.md', raw, {'runtime': 'claude-code'})
    assert status == 'parsed' and facts['embedded_hooks'] == state
    if 'embedded_hook_keys_unvalidated' in facts:
        assert facts['embedded_hook_keys_unvalidated'] == 1
        assert facts['embedded_hook_key_hashes_unvalidated'] == digest('PreToolUse')
    assert 'SYNTHETIC_SECRET' not in str(facts)


def test_unsupported_frontmatter_is_unknown():
    _, facts = skill_headers.parse('.claude/skills/sample/SKILL.md',
        b'---\nname: sample\nhooks: &anchor {}\n---\n', {'runtime': 'claude-code'})
    assert facts['embedded_hooks'] == 'unknown'


def test_malformed_nested_hook_content_never_becomes_declared():
    raw = (b'---\nname: sample\nhooks:\n  PreToolUse:\n    - hooks:\n'
           b'        - type: command\n          command: [\n---\n')
    _, facts = skill_headers.parse('.claude/skills/sample/SKILL.md', raw, {'runtime': 'claude-code'})
    assert facts['embedded_hooks'] == 'unknown'


def test_checkout_inventory_retains_only_private_hook_metadata(tmp_path):
    skill = tmp_path / '.claude/skills/sample/SKILL.md'
    skill.parent.mkdir(parents=True)
    skill.write_text('---\nname: sample\ndescription: synthetic\nhooks:\n  PreToolUse:\n'
                     '    - hooks:\n        - type: command\n          command: SYNTHETIC_SECRET\n---\n')
    profile = dict(schema_version=1, runtime='claude-code', version=surfaces.PINS['claude-code'], task_cwd='.')
    inventory = harness_inventory.capture(tmp_path, profile, schema_version=2)
    node = next(node for node in inventory.nodes if node.path == '.claude/skills/sample/SKILL.md')
    assert dict(node.facts)['embedded_hook_key_hashes_unvalidated'] == digest('PreToolUse')
    assert dict(node.facts)['embedded_hooks'] == 'unknown'
    assert 'SYNTHETIC_SECRET' not in str(inventory.document())
