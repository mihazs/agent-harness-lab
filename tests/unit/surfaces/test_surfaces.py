"""One classifier owns the harness-surface allowlist; its callers keep their verdicts."""
import base64
import dataclasses
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from unittest.mock import patch

import pytest

from agent_harness_lab import surfaces
from agent_harness_lab.evaluation import construction
from agent_harness_lab.harness_change_plan import _operation
from agent_harness_lab.promotion.journal import Journal
from agent_harness_lab.surfaces import (CAPABILITIES, PINS, RETRIEVED, RUNTIMES, SURFACES, VENDORED_FACET, Capability, Rule,
                                        adoption, classify, embeds_hooks)
from agent_harness_lab.telemetry import claude_native
from agent_harness_lab.workflow import candidate_states, harness_files, skill_roots, snapshot

I, S, H = 'instruction', 'skill', 'hook_contract_required'
A, P, C, R, U = 'adoptable', 'private_excluded', 'contract_required', 'recommendation_only', 'unsupported'
# path, staged writability without/with a root skills/ folder, plan surface, facets at 0o644, facets once 0o755.
# Staged means the caller observed checkout containment (candidate_states, journal.owned); UNCONFIRMED holds the
# lexical verdict every other caller still receives for the rows that need that fact.
GOLDEN = (
    ('AGENTS.md', A, A, 'instruction', (I,), (I, H)),
    ('CLAUDE.md', A, A, 'instruction', (I,), (I, H)),
    ('sub/AGENTS.md', A, A, 'instruction', (I,), (I, H)),
    ('sub/CLAUDE.md', A, A, 'instruction', (I,), (I, H)),
    ('app/CLAUDE.md', A, A, 'instruction', (I,), (I, H)),
    ('.claude/CLAUDE.md', A, A, 'instruction', (I,), (I, H)),
    ('CLAUDE.local.md', P, P, 'instruction', (I,), (I, H)),
    ('AGENTS.override.md', A, A, 'instruction', (I,), (I, H)),
    ('sub/AGENTS.override.md', A, A, 'instruction', (I,), (I, H)),
    # A same-named instruction file under a hook folder is a hook first and keeps the hook contract.
    ('.claude/hooks/CLAUDE.md', C, C, 'hook', (I, H), (I, H)),
    ('.agents/skills/demo/SKILL.md', A, A, 'skill', (S,), (S, H)),
    ('.claude/skills/demo/SKILL.md', A, A, 'skill', (S,), (S, H)),
    ('.claude/skills/demo/scripts/run.py', A, A, 'skill', (S, H), (S, H)),
    ('.claude/skills/demo/hooks/notes.md', A, A, 'skill', (S, H), (S, H)),
    ('.claude/skills/SKILL.md', U, U, 'skill', (S,), (S, H)),
    ('.agents/skills/demo', U, U, 'skill', (S,), (S, H)),
    ('skills/demo/SKILL.md', U, A, 'skill', (S,), (S, H)),
    ('skills/SKILL.md', U, U, 'skill', (S,), (S, H)),
    ('skills/demo/__pycache__/m.pyc', P, P, 'skill', (S,), (S, H)),
    ('.claude/skills/demo/.git/config', P, P, 'skill', (S,), (S, H)),
    ('plugin/skills/demo/SKILL.md', U, U, 'skill', (S,), (S, H)),
    ('pkg/.claude/skills/demo/SKILL.md', A, A, 'skill', (S,), (S, H)),
    ('pkg/.agents/skills/demo/SKILL.md', A, A, 'skill', (S,), (S, H)),
    ('pkg/.claude/skills/demo/scripts/run.py', C, C, 'skill', (S, H), (S, H)),
    ('pkg/.claude/skills/SKILL.md', U, U, 'skill', (S,), (S, H)),
    ('plugin/skills/demo/.claude/skills/x/SKILL.md', U, U, 'skill', (S,), (S, H)),
    ('my-skills/demo/SKILL.md', U, U, 'unsupported', (), (H,)),
    ('.claude/rules/style.md', A, A, 'path_rule', (), (H,)),
    ('.claude/rules/skills/style.md', A, A, 'path_rule', (S,), (S, H)),  # path_rule outranks a skills/ segment
    ('plugin/skills/demo/.claude/rules/style.md', A, A, 'path_rule', (S,), (S, H)),
    ('.claude/rules/run.sh', U, U, 'unsupported', (H,), (H,)),
    ('.codex/rules/default.rules', 'recommendation_only', 'recommendation_only', 'exec_policy', (), (H,)),
    ('rules/x.rules', U, U, 'unsupported', (), (H,)),  # a checkout's own rules/ is not Codex policy
    ('.claude/hooks/pre.sh', C, C, 'hook', (H,), (H,)),
    ('hooks/hooks.json', C, C, 'hook', (H,), (H,)),
    ('src/hook', C, C, 'hook', (H,), (H,)),
    ('.codex/hooks.json', C, C, 'hook', (H,), (H,)),
    ('.claude/workflows/release.js', C, C, 'workflow', (H,), (H,)),
    ('pkg/.claude/workflows/release.js', C, C, 'workflow', (H,), (H,)),
    ('.claude/workflows/lib/helper.js', U, U, 'unsupported', (H,), (H,)),
    ('.claude/workflows/notes.md', U, U, 'unsupported', (), (H,)),
    # Agent definitions can embed hooks, so they carry the hook contract and stay unresolved in plans.
    ('.claude/agents/reviewer.md', A, A, 'agent', (H,), (H,)),
    ('src/.claude/agents/team/reviewer.md', A, A, 'agent', (H,), (H,)),
    ('.codex/agents/reviewer.toml', A, A, 'agent', (H,), (H,)),
    ('agents/reviewer.md', C, C, 'agent', (H,), (H,)),
    # A hooks/ or skills/ folder inside an agents tree still holds agent definitions.
    ('.claude/agents/hooks/guard.md', A, A, 'agent', (H,), (H,)),
    ('.claude/agents/skills/helper.md', A, A, 'agent', (S, H), (S, H)),
    ('.codex/agents/hooks/guard.toml', A, A, 'agent', (H,), (H,)),
    ('agents/hook/guard.md', C, C, 'agent', (H,), (H,)),
    ('.claude/agents/notes.txt', U, U, 'unsupported', (), (H,)),
    ('.claude/agents.md', U, U, 'unsupported', (), (H,)),
    ('.claude/commands/ship.md', A, A, 'command', (), (H,)),
    ('.claude/commands/frontend/component.md', A, A, 'command', (), (H,)),
    ('.claude/skills/demo/.claude/commands/x.md', A, A, 'skill', (S,), (S, H)),
    ('packages/skills/.claude/commands/ship.md', A, A, 'command', (S,), (S, H)),
    ('apps/.claude/skills/demo/.claude/commands/x.md', A, A, 'skill', (S,), (S, H)),
    ('.claude/settings.json', C, C, 'hook', (H,), (H,)),
    ('.claude/settings.local.json', P, P, 'hook', (H,), (H,)),
    ('.codex/config.toml', C, C, 'hook', (H,), (H,)),
    ('src/.codex/config.toml', C, C, 'hook', (H,), (H,)),
    ('lefthook.yml', R, R, 'hook', (H,), (H,)),
    ('.husky/pre-commit', R, R, 'hook', (H,), (H,)),
    ('.claude-plugin/plugin.json', U, U, 'unsupported', (), (H,)),
    ('.claude/output-styles/terse.md', *('recommendation_only',) * 2, 'config', (), (H,)),
    ('.mcp.json', *('recommendation_only',) * 2, 'config', (), (H,)),
    # Runtime memory layouts of a declared home root never hide checkout folders that share their names.
    ('memories/AGENTS.md', A, A, 'instruction', (I,), (I, H)),
    ('memories/hooks/pre.sh', C, C, 'hook', (H,), (H,)),
    ('projects/api/memory/CLAUDE.md', A, A, 'instruction', (I,), (I, H)),
    ('.claude/agent-memory/hooks/pre.sh', P, P, 'hook', (H,), (H,)),
    ('.git/config', P, P, 'unsupported', (), (H,)),
    ('.git/hooks/pre-commit', P, P, 'hook', (H,), (H,)),
    ('__pycache__/m.pyc', P, P, 'unsupported', (), (H,)),
)
UNCONFIRMED = {
    **dict.fromkeys(('sub/AGENTS.md', 'sub/CLAUDE.md', 'app/CLAUDE.md', '.claude/CLAUDE.md', 'AGENTS.override.md',
                     'sub/AGENTS.override.md', 'memories/AGENTS.md', 'projects/api/memory/CLAUDE.md'), R),
    **dict.fromkeys(('pkg/.claude/skills/demo/SKILL.md', 'pkg/.agents/skills/demo/SKILL.md',
                     'pkg/.claude/skills/demo/scripts/run.py', '.claude/rules/style.md', '.claude/rules/skills/style.md',
                     'plugin/skills/demo/.claude/rules/style.md', '.claude/commands/ship.md',
                     '.claude/commands/frontend/component.md', 'packages/skills/.claude/commands/ship.md',
                     'apps/.claude/skills/demo/.claude/commands/x.md'), U),
    **dict.fromkeys(('.claude/agents/reviewer.md', 'src/.claude/agents/team/reviewer.md', '.codex/agents/reviewer.toml',
                     '.claude/agents/hooks/guard.md', '.claude/agents/skills/helper.md',
                     '.codex/agents/hooks/guard.toml'), C),
}
SHA = 'a' * 64


def golden_cases(*columns):
    """One pytest.param per GOLDEN row, id'd by path; columns picks which fields each case receives."""
    index = dict(name=0, off=1, on=2, surface=3, plain=4, executable=5)
    return [pytest.param(*(row[index[c]] for c in columns), id=row[0]) for row in GOLDEN]


def golden_modes(*columns):
    """One pytest.param per GOLDEN row and root-skills mode; columns picks the fields each case receives."""
    return [pytest.param(*(dict(name=name, root_skills=root_skills, verdict=verdict, surface=surface, plain=plain)[c] for c in columns),
                         id=f'{name}-{"root-skills" if root_skills else "no-root-skills"}')
            for name, off, on, surface, plain, _ in GOLDEN for root_skills, verdict in ((False, off), (True, on))]


@pytest.fixture
def project_root(tmp_path):
    # Hermetic: a git work tree enclosing the host's TMPDIR must not lend these projects its ignore rules.
    with patch('agent_harness_lab.promotion.journal.ignored', return_value=False):
        yield tmp_path


def candidate_admits(root, name, root_skills):
    folder = Path(tempfile.mkdtemp(dir=root))
    # A changed agent role is parsed, so a TOML path holds valid synthetic TOML.
    (folder / name).parent.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text('name = "synthetic"\n' if name.endswith('.toml') else 'synthetic')
    original = {'skills/other/SKILL.md': None} if root_skills else {}
    try: return list(candidate_states(folder, original)) == [name]
    except ValueError as error:
        assert 'outside project harness scope' in str(error); return False


def journal_owns(root, name, root_skills):
    project = Path(tempfile.mkdtemp(dir=root))
    if root_skills: (project / 'skills').mkdir()
    journal = Journal(project, Path(tempfile.mkdtemp(dir=root)) / 'journal')
    try: return journal.owned(name) == project.resolve() / name
    except ValueError as error:
        assert str(error) == 'transaction outside owned harness surface'; return False


# Pinned against the callers before and after they delegate to surfaces.classify.
@pytest.mark.parametrize('name, root_skills, verdict', golden_modes('name', 'root_skills', 'verdict'))
def test_candidate_and_journal_admit_exactly_the_golden_allowlist(project_root, name, root_skills, verdict):
    assert candidate_admits(project_root, name, root_skills) == (verdict == A)
    assert journal_owns(project_root, name, root_skills) == (verdict == A)


@pytest.mark.parametrize('name, surface, plain, executable', golden_cases('name', 'surface', 'plain', 'executable'))
def test_plan_surface_and_facets_follow_paths_and_either_mode(name, surface, plain, executable):
    created = _operation(name, None, (SHA, 0o644))
    assert (created.action, created.surface, created.facets) == ('create', surface, plain)
    for before, after in (((SHA, 0o644), (SHA, 0o755)), ((SHA, 0o750), None)):
        changed = _operation(name, before, after)
        assert (changed.surface, changed.facets) == (surface, executable)


def test_symlinks_are_refused_by_candidate_and_journal(project_root):
    folder = project_root / 'candidate'; (folder / '.claude/skills').mkdir(parents=True)
    target = project_root / 'outside'; (target / 'demo').mkdir(parents=True)
    (target / 'demo/SKILL.md').write_text('synthetic')
    os.symlink(target / 'demo', folder / '.claude/skills/demo')
    with pytest.raises(ValueError, match='symlink'): candidate_states(folder, {})
    journal = Journal(folder, project_root / 'journal')
    with pytest.raises(ValueError, match='symlink'): journal.owned('.claude/skills/demo/SKILL.md')


def test_discovery_keeps_root_files_prefixes_and_link_refusal(project_root):
    project = project_root / 'project'
    for name in ('AGENTS.md', 'sub/CLAUDE.md', '.agents/skills/a/SKILL.md', '.claude/skills/b/SKILL.md',
                 'skills/c/SKILL.md', 'skills/c/__pycache__/m.pyc', 'skills/c/.git/config', 'plugin/skills/d/SKILL.md',
                 '.claude/skills/nofile/notes.md', '.claude/rules/style.md'):
        (project / name).parent.mkdir(parents=True, exist_ok=True); (project / name).write_text('synthetic')
    (project_root / 'linked').mkdir(); (project_root / 'linked/SKILL.md').write_text('synthetic')
    os.symlink(project_root / 'linked', project / '.agents/skills/linked')
    assert [str(p.relative_to(project)) for p in skill_roots(project)] == \
                     ['.agents/skills/a', '.claude/skills/b', 'skills/c']
    assert [str(p.relative_to(project)) for p in harness_files(project)] == \
                     ['.agents/skills/a/SKILL.md', '.claude/skills/b/SKILL.md', 'AGENTS.md',
                      'skills/c/.git/config', 'skills/c/SKILL.md']


@pytest.mark.parametrize('name, root_skills, verdict, surface, plain',
                         golden_modes('name', 'root_skills', 'verdict', 'surface', 'plain'))
def test_classify_returns_todays_verdicts_for_every_golden_path(name, root_skills, verdict, surface, plain):
    staged = classify(name, None, 0o644, root_skills=root_skills, in_checkout=True)
    assert (staged.writability, staged.kind) == (verdict, surface)
    found = classify(name, None, 0o644, root_skills=root_skills)
    assert (found.writability, found.kind) == (UNCONFIRMED.get(name, verdict), surface)
    assert tuple(f for f in found.facets if f in (I, S, H)) == plain
    assert found.executable == name.endswith(('.py', '.sh', '.js', '.ts'))
    assert classify(name, None, 0o755, root_skills=root_skills).writability == \
                     UNCONFIRMED.get(name, verdict)


@pytest.mark.parametrize('name, scope', (('AGENTS.md', '.'), ('.claude/skills/demo/scripts/run.py', '.claude/skills/demo'),
                                         ('sub/CLAUDE.md', 'sub'), ('.agents/skills/demo/SKILL.md', '.agents/skills/demo')))
def test_scope_directory_follows_the_surface(name, scope):
    assert classify(name, None, None).scope_dir == scope


def test_root_skills_scope_is_the_skill_folder():
    assert classify('skills/demo/x/SKILL.md', None, None, root_skills=True).scope_dir == 'skills/demo'


def test_every_declared_runtime_admits_a_claude_skill():
    for runtime in RUNTIMES:
        assert classify('.claude/skills/demo/SKILL.md', runtime, None).writability == A


def test_unsafe_paths_unknown_runtimes_and_boolean_modes_are_refused():
    for bad in ('', '/AGENTS.md', '../AGENTS.md', None):
        with pytest.raises(ValueError): classify(bad, None, None)
    with pytest.raises(ValueError): classify('AGENTS.md', 'unknown-runtime', None)
    with pytest.raises(ValueError): classify('AGENTS.md', None, True)


def test_the_executable_bit_comes_from_the_mode():
    assert classify('bin/tool', None, 0o100).executable
    assert not classify('bin/tool', None, 0o644).executable


def test_invalid_declared_roots_are_refused():
    for bad in ('', 7):
        with pytest.raises(ValueError): classify('AGENTS.md', None, None, declared_root=bad)


@pytest.mark.parametrize('path', ('memories/AGENTS.md', 'memories/hooks/pre.sh', 'projects/api/memory/CLAUDE.md'))
def test_home_memory_layouts_are_private_beneath_any_declared_root(path):
    assert classify(path, None, None).writability != P
    # Labels are free-form, so the memory layout never depends on a magic root name.
    for root in ('codex_home', 'claude_home', 'codex', 'claude', 'claude_user', 'plugins'):
        assert classify(path, None, None, declared_root=root).writability == P


def test_root_skills_condition_is_evaluated_lazily():
    calls = []
    probe = lambda: calls.append(1) or True
    assert classify('AGENTS.md', None, None, root_skills=probe).writability == A
    assert calls == []
    assert classify('skills/demo/SKILL.md', None, None, root_skills=probe).writability == A
    assert calls == [1]


def test_checkout_containment_is_probed_only_for_a_gated_row_that_names_the_path():
    calls = []
    probe = lambda: calls.append(1) or True
    for name, verdict in (('AGENTS.md', A), ('.claude/skills/demo/SKILL.md', A), ('.claude/hooks/CLAUDE.md', C)):
        assert classify(name, None, None, in_checkout=probe).writability == verdict
    assert calls == []
    assert classify('.claude/rules/x.md', None, None, in_checkout=probe).writability == A
    assert calls == [1]
    assert classify('.claude/rules/x.md', None, None, in_checkout=lambda: False).writability == U
    assert classify('app/CLAUDE.md', None, None, in_checkout=True, origin='external_repository').writability == R


def test_an_added_table_row_reaches_every_caller(project_root):
    row = surfaces.Rule('synthetic-notes', 'note', 1, A, lambda path, parts, ctx: parts[:2] == ('.claude', 'notes'),
                        label='synthetic notes')
    with patch.object(surfaces, 'RULES', (*surfaces.RULES, row)):
        assert surfaces.classify('.claude/notes/style.md', None, None).kind == 'note'
        assert candidate_admits(project_root, '.claude/notes/style.md', False)
        assert journal_owns(project_root, '.claude/notes/style.md', False)
        assert _operation('.claude/notes/style.md', None, (SHA, 0o644)).surface == 'note'
        assert surfaces.adoptable_surfaces().endswith('; synthetic notes')
    assert not candidate_admits(project_root, '.claude/notes/style.md', False)


def test_rows_reject_unknown_writability_and_runtime():
    with pytest.raises(ValueError): Rule('x', 'skill', 1, 'writable', lambda *a: True)
    with pytest.raises(ValueError): Rule('x', 'skill', 1, A, lambda *a: True, runtimes=frozenset({'other'}), label='x')


def test_adoptable_rows_need_a_label_and_gated_verdicts_must_not_be_adoptable():
    # Every adoptable row names itself for the proposer prompts; a gated verdict must be a non-adoptable one.
    with pytest.raises(ValueError, match='label'): Rule('x', 'skill', 1, A, lambda *a: True)
    with pytest.raises(ValueError, match='unconfirmed'): Rule('x', 'skill', 1, A, lambda *a: True, label='x', unconfirmed=A)
    with pytest.raises(ValueError, match='unconfirmed'): Rule('x', 'skill', 1, R, lambda *a: True, unconfirmed=U)


# Every item the 2026-11-05 surface research left unverified, keyed by matrix surface and runtime.
UNVERIFIED = {
    ('instructions', 'claude-code'), ('hooks', 'codex'), ('workflows', 'claude-code'), ('agents', 'codex'),
    ('commands_skills', 'codex'), ('plugins', 'claude-code'), ('plugins', 'codex'), ('memory', 'codex'),
    ('settings', 'claude-code'),
    ('path_rules', 'claude-code'),  # ancestor launch rules; glob dotfile, case, bracket and negation semantics
    # Trial isolation requests stay unverified until a native smoke run confirms them.
    ('mcp', 'claude-code'), ('hooks', 'claude-code'), ('settings', 'codex'),
}


def test_rows_are_frozen_pinned_and_cite_public_docs():
    assert isinstance(CAPABILITIES, tuple)
    assert {(r.surface, r.runtime) for r in CAPABILITIES if r.status == 'verified'} == \
                     {(s, r) for s in SURFACES for r in RUNTIMES}
    for row in CAPABILITIES:
        assert (row.pinned_version, row.retrieved) == (PINS[row.runtime], RETRIEVED)
        assert row.doc_url.startswith('https://')
        with pytest.raises(dataclasses.FrozenInstanceError): row.status = 'verified'
    assert len({(r.surface, r.runtime, r.item) for r in CAPABILITIES}) == len(CAPABILITIES)


def test_every_researched_unverified_item_stays_unverified():
    unverified = [r for r in CAPABILITIES if r.status == 'unverified']
    assert {(r.surface, r.runtime) for r in unverified} == UNVERIFIED
    assert len(unverified) == 19
    items = ' '.join(r.item for r in unverified)
    for phrase in ('size limit', 'version gates', 'static validator', 'load timing', 'custom prompts', 'description limit',
                   'cache subpath', 'plugin agents', 'validate command', 'memories layout', 'managed settings',
                   'claudeMdExcludes', 'dotfile', 'between the root and the cwd',
                   '--setting-sources', '--strict-mcp-config', 'disableAllHooks', 'temporary CODEX_HOME',
                   '--dangerously-bypass-hook-trust'):
        assert phrase in items


@pytest.mark.parametrize('field', ('doc_url', 'pinned_version', 'status'))
@pytest.mark.parametrize('value', ('', None))
def test_a_row_without_doc_url_pin_or_status_is_rejected(field, value):
    with pytest.raises(ValueError):
        dataclasses.replace(CAPABILITIES[0], **{field: value})


@pytest.mark.parametrize('field, value', (('status', 'probably'), ('runtime', 'other'), ('doc_url', 'http://example.invalid/'),
                                          ('pinned_version', '9.9.9'), ('surface', 'unknown'), ('version_gate', '2.1')))
def test_a_row_with_an_unsupported_field_value_is_rejected(field, value):
    with pytest.raises(ValueError):
        dataclasses.replace(CAPABILITIES[0], **{field: value})


def test_a_row_must_state_its_doc_url_and_pin():
    with pytest.raises(TypeError):
        Capability(surface='hooks', runtime='codex', item='synthetic', retrieved='2026-11-05')


ORDER_PATHS = ('CLAUDE.md', '.claude/skills/demo/SKILL.md', '.claude/skills/demo/hooks/run.sh', 'apps/x/AGENTS.md',
               '.claude/rules/a.md', '.git/config', 'skills/demo/SKILL.md')


@pytest.mark.parametrize('path', ORDER_PATHS)
def test_classification_does_not_depend_on_row_order(path):
    expected = surfaces.classify(path, root_skills=True)
    with patch.object(surfaces, 'RULES', tuple(reversed(surfaces.RULES))):
        assert surfaces.classify(path, root_skills=True) == expected


def test_added_rows_that_tie_on_priority_name_the_same_kind_in_either_order():
    # Two added rows that tie on priority must name the same kind whichever branch put its row first.
    always = lambda path, parts, context: path == 'docs/tied.md'
    tied = (surfaces.Rule('zeta-added', 'zeta', 40, 'recommendation_only', always),
            surfaces.Rule('alpha-added', 'alpha', 40, 'recommendation_only', always))
    results = []
    for order in (tied, tuple(reversed(tied))):
        with patch.object(surfaces, 'RULES', surfaces.RULES + order):
            results.append(surfaces.classify('docs/tied.md'))
    assert results[0] == results[1]
    assert results[0].kind == 'alpha'


def test_construction_proposer_lists_adoptable_surfaces_from_the_classifier():
    listed = surfaces.adoptable_surfaces()
    for label in ('root AGENTS.md or CLAUDE.md', 'a nested CLAUDE.md, including .claude/CLAUDE.md',
                  'a nested AGENTS.md or an AGENTS.override.md', 'files in an existing skills/<name>/ bundle',
                  '.claude/rules/**/*.md path rules', '.claude/agents/**/*.md agent definitions',
                  '.codex/agents/**/*.toml agent roles', '.claude/commands/**/*.md commands'):
        assert label in listed
    for absent in ('settings', 'hook', 'workflow', 'mcp', '.rules', 'memory', 'local'):
        assert absent not in listed
    seen = []
    def capture(client, backend, workspace, inputs, role, instructions, *rest):
        seen.append(instructions); raise RuntimeError('captured')
    with patch.object(construction, 'run_role', capture), pytest.raises(RuntimeError, match='captured'):
        construction.propose(None, None, Path('/nonexistent'), Path('/nonexistent'), None, None, 0)
    assert f'Modify only these project-owned surfaces under candidate: {listed}.' in seen[0]
    assert 'Never modify settings, permissions, hooks or credentials.' in seen[0]


def test_telemetry_skill_prefixes_come_from_the_classifier():
    assert claude_native.SKILL_BUNDLE_PREFIXES == \
                     tuple(PurePosixPath(root).parts for root in surfaces.skill_prefixes())


EMBEDDED_HOOK_CASES = (
    pytest.param('a.md', b'---\nname: a\ndescription: d\n---\nMention hooks in the body only.\n', False, id='md-body-mention'),
    pytest.param('a.md', b'No fence, so Claude skips the file.\nhooks: x\n', False, id='md-no-fence'),
    pytest.param('a.md', b'\xef\xbb\xbf---\nhooks: {}\n---\n', True, id='md-bom-fence'),
    pytest.param('a.md', b'---\nname: a\nHOOKS: {}\n', True, id='md-uppercase-key'),
    pytest.param('a.md', b'---\n"\\u0068ooks": {}\n---\n', True, id='md-escaped-key'),
    pytest.param('a.md', b'\xff\xfe', True, id='md-undecodable'),
    pytest.param('a.toml', b'name = "a"\n[nested]\nlist = [{hooks = 1}]\n', True, id='toml-nested-key'),
    pytest.param('a.toml', b'"\\u0068ooks" = 1\n', True, id='toml-escaped-key'),
    pytest.param('a.toml', b'name = "a"\ndeveloper_instructions = "hooks are fine in prose"\n', False, id='toml-prose'),
    pytest.param('a.toml', b'[unterminated\n', True, id='toml-unparseable'))


@pytest.mark.parametrize('name, raw, expected', EMBEDDED_HOOK_CASES)
def test_agent_hook_detection_fails_closed(name, raw, expected):
    assert embeds_hooks(name, raw) == expected


def test_agent_hook_detection_sets_the_adoption_verdict():
    hooked = b'---\nhooks: {}\n---\n'
    assert adoption('.claude/agents/a.md', 0o644, hooked, in_checkout=True).writability == C
    assert adoption('.claude/agents/a.md', 0o644, None, in_checkout=True).writability == A
    # Only agent definitions are read; an instruction file may discuss hooks freely.
    assert adoption('app/CLAUDE.md', 0o644, hooked, in_checkout=True).writability == A
    assert adoption('.claude/rules/x.md', 0o755, b'x', in_checkout=True).writability == C


FRONTMATTER_HOOKED = b'---\ndescription: x\nhooks:\n  PreToolUse:\n    - hooks:\n        - type: command\n' \
                     b'          command: ./synthetic.sh\n---\n'


@pytest.mark.parametrize('name', ('.claude/commands/deploy.md', 'pkg/.claude/skills/x/SKILL.md', '.claude/skills/x/SKILL.md',
                                  '.claude/rules/r.md', '.claude/agents/a.md'))
def test_every_claude_frontmatter_surface_refuses_hooks(name):
    assert adoption(name, 0o644, FRONTMATTER_HOOKED, in_checkout=True).writability == C


@pytest.mark.parametrize('key', (b'permissionMode: bypassPermissions', b'mcpServers:\n  s:\n    command: /bin/sh',
                                 b'PERMISSIONMODE: bypassPermissions', b'allowed-tools: Bash(*)'),
                         ids=('permission-mode', 'mcp-servers', 'uppercase-permission-mode', 'allowed-tools'))
def test_every_claude_frontmatter_surface_refuses_permissions_and_mcp(key):
    raw = b'---\nname: a\ndescription: b\n' + key + b'\n---\nbody\n'
    assert adoption('.claude/agents/a.md', 0o644, raw, in_checkout=True).writability == C
    assert adoption('.claude/commands/c.md', 0o644, raw, in_checkout=True).writability == C


@pytest.mark.parametrize('name', ('.claude/commands/c.md', 'pkg/.claude/skills/x/SKILL.md', '.claude/agents/a.md',
                                  'pkg/.claude/skills/x/reference.md'))
def test_frontmatter_surfaces_stay_adoptable_when_hooks_appear_only_in_the_body(name):
    plain = b'---\nname: a\ndescription: b\n---\nhooks in the body only\n'
    assert adoption(name, 0o644, plain, in_checkout=True).writability == A


def test_a_bundle_file_that_is_not_skill_md_is_not_parsed_as_frontmatter():
    assert adoption('pkg/.claude/skills/x/reference.md', 0o644, FRONTMATTER_HOOKED, in_checkout=True).writability == A


# Claude runs !`cmd` and ```! blocks in command and skill bodies on invocation, so they need the hook contract.
RENDERED = ('.claude/commands/deploy.md', '.claude/commands/frontend/c.md', '.claude/skills/x/SKILL.md',
            'pkg/.claude/skills/x/SKILL.md', '.agents/skills/x/SKILL.md')
HEAD = b'---\ndescription: synthetic\n---\n'


def shell_verdict(name, body, **context):
    return adoption(name, 0o644, HEAD + body, in_checkout=True, **context).writability


def rendered_with(*bodies):
    """One pytest.param per rendered surface and body."""
    return [pytest.param(name, body, id=f'{name}-{index}') for name in RENDERED for index, body in enumerate(bodies)]


DOCUMENTED_FORMS = (b'## Changes\n\n!`git diff HEAD`\n',            # a line that starts with !
                    b'- PR diff: !`gh pr diff`\n',                    # ! after whitespace
                    b'## Environment\n```!\nnode --version\n```\n')  # the multi-line fenced form


@pytest.mark.parametrize('name, body', rendered_with(*DOCUMENTED_FORMS))
def test_documented_forms_route_commands_and_skills_to_the_hook_contract(name, body):
    assert shell_verdict(name, body) == C


def test_documented_form_in_a_root_skills_bundle_routes_to_the_hook_contract():
    assert shell_verdict('skills/x/SKILL.md', DOCUMENTED_FORMS[0], root_skills=True) == C


@pytest.mark.parametrize('fence', (b'```\n!`date`\n```\n', b'```bash\necho\n!`date`\n```\n', b'~~~\n!`date`\n~~~\n'),
                         ids=('plain-fence', 'language-fence', 'tilde-fence'))
def test_inline_form_inside_a_fenced_code_block_is_not_excluded(fence):
    # The docs never say a plain fence suppresses the inline form, so a fenced example fails closed.
    assert shell_verdict('.claude/commands/c.md', fence) == C


@pytest.mark.parametrize('body', (b'KEY=!`cmd`\n', b'!``cmd``\n', b'! `cmd`\n', b'  ```!\ncmd\n```\n', b'````!\ncmd\n````\n',
                                  b'~~~!\ncmd\n~~~\n', b'``` !\ncmd\n```\n', b'text\r\n!`cmd`\r\n', b'\xff\xfe!'),
                         ids=('assignment', 'double-backtick', 'spaced-backtick', 'indented-fence', 'long-fence',
                              'tilde-marker', 'spaced-fence', 'crlf', 'undecodable'))
def test_unverified_variants_fail_closed(body):
    assert shell_verdict('.claude/commands/c.md', body) == C


# Claude Code matches ```! anywhere, so a fence after other text on the line fails closed too.
FENCE_AFTER_TEXT = (b'> ```!\n> node -v\n> ```\n', b'>```!\nid\n```\n', b'- ```!\n  node -v\n  ```\n',
                    b'1. ```!\n   node -v\n   ```\n', b'Env: ```!\nnode -v\n```\n', b'Use ```!date``` here.\n',
                    b'|```!id```|\n', b'\xc2\xa0```!\nid\n```\n', b'text\r```!\rnode -v\r```\r',
                    b'x\r```!\nid\n```\n', 'intro\u2028```!\nid\n```\n'.encode())


@pytest.mark.parametrize('name, body', rendered_with(*FENCE_AFTER_TEXT))
def test_a_fence_after_other_text_on_the_line_fails_closed(name, body):
    assert shell_verdict(name, body) == C


def test_prose_quoting_the_marker_fails_closed():
    # Even prose that quotes the marker, as in `!`, holds ! before a backtick and therefore fails closed.
    assert shell_verdict('.claude/commands/c.md', b'Use the `!` prefix.\n') == C
    # Substitution runs over the original file, so the frontmatter is scanned as well.
    raw = b'---\ndescription: !`date`\n---\nbody\n'
    assert adoption('.claude/commands/c.md', 0o644, raw, in_checkout=True).writability == C


@pytest.mark.parametrize('name, body', rendered_with(b'Run `git diff HEAD` and summarize it. Done!\n', b'```bash\ngit status\n```\n',
                                                     b'Shout! Then `make test`.\n', b'A line\n!important\n', b'```bash\n!! echo\n```\n'))
def test_plain_bodies_stay_adoptable(name, body):
    assert shell_verdict(name, body) == A


@pytest.mark.parametrize('name', ('.claude/agents/a.md', '.claude/rules/r.md', 'CLAUDE.md', 'app/CLAUDE.md',
                                  '.claude/skills/x/reference.md'))
def test_surfaces_that_do_not_render_shell_keep_their_verdicts(name):
    assert shell_verdict(name, b'!`git diff HEAD`\n') == A


def test_a_command_without_content_keeps_its_verdict():
    assert adoption('.claude/commands/c.md', 0o644, None, in_checkout=True).writability == A


def test_both_writers_refuse_a_pre_executing_command(tmp_path):
    name, raw = '.claude/commands/ship.md', HEAD + b'!`git push`\n'
    folder = tmp_path / 'candidate'; (folder / name).parent.mkdir(parents=True)
    (folder / name).write_bytes(raw)
    with pytest.raises(ValueError, match='needs a hook contract'): candidate_states(folder, {})
    (folder / name).write_bytes(HEAD + b'Run `git push` after review.\n')
    assert list(candidate_states(folder, {})) == [name]
    with patch('agent_harness_lab.promotion.journal.ignored', return_value=False):
        journal = Journal(folder, tmp_path / 'journal')
        target = {'content': base64.b64encode(raw).decode(), 'mode': 0o644}
        with pytest.raises(ValueError, match='outside owned harness surface'): journal.owned(name, target)


def test_the_syntax_is_pinned_to_public_docs():
    row, = (r for r in CAPABILITIES if 'shell pre-execution' in r.item)
    assert (row.runtime, row.status, row.doc_url) == \
                     ('claude-code', 'verified', 'https://code.claude.com/docs/en/skills')


# Third-party and generated trees hold no .git of their own, yet they are never project-owned surfaces.
VENDORED = ('node_modules/pkg/CLAUDE.md', 'node_modules/pkg/.claude/rules/x.md', 'node_modules/AGENTS.md',
            '.venv/lib/python3/site-packages/pkg/AGENTS.md', '.venv/.claude/rules/x.md',
            'vendor/lib/.claude/skills/demo/SKILL.md', 'app/node_modules/pkg/.claude/agents/r.md')


@pytest.mark.parametrize('name', VENDORED)
def test_vendored_paths_keep_their_kind_but_are_never_adoptable(project_root, name):
    lexical = classify(name)
    found = classify(name, None, 0o644, in_checkout=True, root_skills=True)
    assert found.writability == U
    assert found.kind != 'unsupported'  # inventories still list it as its surface
    assert VENDORED_FACET in found.facets
    assert (lexical.writability, lexical.kind) == (U, found.kind)
    assert classify(name, origin='external_repository', in_checkout=True).writability == U
    assert not candidate_admits(project_root, name, False)
    assert not journal_owns(project_root, name, False)


def test_vcs_metadata_and_hooks_inside_a_vendored_tree_keep_their_stricter_verdicts():
    # Stricter verdicts stay: VCS metadata inside a vendored tree is still private_excluded.
    assert classify('node_modules/pkg/.git/config').writability == P
    assert classify('node_modules/pkg/.claude/hooks/run.sh').writability == U


@pytest.mark.parametrize('name', ('vendor.md', 'my_node_modules/CLAUDE.md', 'src/vendored/CLAUDE.md'))
def test_only_exact_vendored_directory_names_count(name):
    # Only directories count, and only exact names: project folders and files keep their verdicts.
    assert VENDORED_FACET not in classify(name, in_checkout=True).facets


def test_a_project_folder_that_resembles_a_vendored_name_stays_adoptable():
    assert classify('src/vendored/CLAUDE.md', in_checkout=True).writability == A


@pytest.mark.parametrize('name', ('.claude/rules/x.md', '.claude/skills/demo/SKILL.md', '.claude/CLAUDE.md', '.agents/skills/a/SKILL.md',
                                  '.codex/agents/r.toml', '.claude/commands/ship.md'))
def test_root_harness_folders_stay_the_projects_own_even_when_ignored(name):
    assert classify(name, None, None, in_checkout=True, ignored=lambda: True).writability == A


def test_ignore_rules_are_probed_only_for_an_otherwise_adoptable_nested_path():
    calls = []
    probe = lambda: calls.append(1) or True
    for name, expected in (('AGENTS.md', A), ('CLAUDE.md', A), ('.claude/hooks/pre.sh', C), ('lefthook.yml', R),
                           ('CLAUDE.local.md', P), ('node_modules/pkg/CLAUDE.md', U)):
        assert classify(name, None, None, in_checkout=True, ignored=probe).writability == expected
    for name in ('.claude/rules/x.md', '.claude/skills/demo/SKILL.md', '.claude/CLAUDE.md', '.agents/skills/a/SKILL.md',
                 '.codex/agents/r.toml', '.claude/commands/ship.md'):
        assert classify(name, None, None, in_checkout=True, ignored=probe).writability == A
    assert classify('skills/demo/SKILL.md', None, None, root_skills=True, in_checkout=True,
                              ignored=probe).writability == A
    assert calls == []
    for name in ('dist/CLAUDE.md', 'generated/.claude/rules/x.md', 'app/.claude/skills/demo/SKILL.md'):
        found = classify(name, None, None, in_checkout=True, ignored=probe)
        assert (found.writability, VENDORED_FACET in found.facets) == (U, True)
    assert len(calls) == 3
    assert classify('dist/CLAUDE.md', None, None, in_checkout=True, ignored=lambda: False).writability == A


@pytest.mark.parametrize('name', ('.claude/skills/vendor/SKILL.md', '.claude/skills/venv/SKILL.md', '.agents/skills/vendor/SKILL.md',
                                  'skills/node_modules/SKILL.md', 'app/.claude/skills/vendor/SKILL.md', '.claude/agents/vendor/r.md'))
def test_a_skill_or_agent_named_like_a_vendored_tree_is_still_project_owned(name):
    found = classify(name, None, 0o644, root_skills=True, in_checkout=True)
    assert (found.writability, VENDORED_FACET in found.facets) == (A, False)


def test_a_plugin_skill_named_like_a_vendored_tree_stays_a_recommendation():
    assert classify('skills/vendor/SKILL.md', origin='external_repository').writability == R


@pytest.mark.parametrize('name', ('.claude/skills/vendor/node_modules/lib/README.md', '.claude/skills/tools/.venv/AGENTS.md',
                                  'vendor/skills/demo/SKILL.md', 'app/skills/x/node_modules/pkg/CLAUDE.md'))
def test_the_installed_dependencies_of_a_bundle_stay_vendored(name):
    # A plugin skill so named stays a recommendation; the bundle's own installed dependencies stay vendored.
    found = classify(name, None, 0o644, root_skills=True, in_checkout=True)
    assert (found.writability, VENDORED_FACET in found.facets) == (U, True)


def test_skill_bundles_named_vendor_or_holding_dependencies_stage_without_their_dependencies(tmp_path):
    root = tmp_path; project = root / 'project'
    for name in ('AGENTS.md', '.claude/skills/vendor/SKILL.md', '.claude/skills/venv/SKILL.md',
                 '.claude/skills/tools/SKILL.md', '.claude/skills/tools/node_modules/lib/index.js',
                 '.claude/skills/tools/.venv/lib/site.py'):
        (project / name).parent.mkdir(parents=True, exist_ok=True); (project / name).write_text('synthetic')
    assert [str(p.relative_to(project)) for p in harness_files(project)] == \
                     ['.claude/skills/tools/SKILL.md', '.claude/skills/vendor/SKILL.md',
                      '.claude/skills/venv/SKILL.md', 'AGENTS.md']
    original = snapshot(project, root / 'baseline')
    shutil.copytree(root / 'baseline', root / 'candidate')
    (root / 'candidate/.claude/skills/vendor/SKILL.md').write_text('after')
    assert sorted(candidate_states(root / 'candidate', original)) == sorted(original)
