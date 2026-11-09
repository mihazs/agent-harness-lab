"""Skill metadata quality and routing-collision checks in the static audit; synthetic checkouts only."""
import json

import pytest

from agent_harness_lab import audit
from agent_harness_lab.audit import skills as skills_module
from agent_harness_lab.sources import harness_inventory, skill_headers

AGREEMENT = 'Extracts tables and text from PDF files. Use when the user mentions PDF forms, scans or extraction.'
SPEC = 'https://agentskills.io/specification'
PRACTICES = 'https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices'
CODEX_SKILLS = 'https://developers.openai.com/codex/skills'


def skill(name, description=AGREEMENT, body='Run the synthetic steps.\n', extra=''):
    return f'---\nname: {name}\ndescription: {description}\n{extra}---\n{body}'


@pytest.fixture
def checkout(tmp_path):
    def build(files):
        root = tmp_path / 'checkout'
        root.mkdir()
        for path, text in files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        (root / '.git').mkdir()
        (root / '.git/HEAD').write_text('ref: refs/heads/main\n')
        return root
    return build


def commands(report):
    return next(row for row in report.dimensions if row.dimension == 'commands')


def reasons(report):
    return {row.path: row.reason for row in report.findings if row.kind == 'skill'}


def test_a_valid_skill_with_supporting_files_conforms(checkout):
    root = checkout({'.claude/skills/pdf-tools/SKILL.md': skill('pdf-tools', body='See [guide](references/guide.md).\n'),
                     '.claude/skills/pdf-tools/references/guide.md': 'Guide.\n', '.claude/skills/pdf-tools/scripts/run.py': 'x = 1\n'})
    report = audit.run(root, 'claude-code')
    assert (commands(report).status, commands(report).surfaces, report.findings) == ('conformant', 1, ())


@pytest.mark.parametrize(('files', 'reason', 'source'), [
    ({'.claude/skills/a/SKILL.md': skill('a', 'Extracts tables and text from PDF files.')},
     'skill_description_lacks_trigger_phrasing', PRACTICES),
    ({'.claude/skills/claude-a/SKILL.md': skill('claude-a')}, 'skill_name_reserved_word', PRACTICES),
    ({'.claude/skills/a/SKILL.md': skill('b')}, 'skill_name_differs_from_directory', SPEC),
    ({'.claude/skills/Bad_Name/SKILL.md': skill('Bad_Name')}, 'skill_name_invalid', SPEC),
    ({'.claude/skills/a/SKILL.md': skill('a', 'x' * 1025 + ' use when asked')}, 'skill_description_too_long', SPEC),
    ({'.claude/skills/a/SKILL.md': '---\nname: a\n---\nBody\n'}, 'skill_description_missing', SPEC),
    ({'.claude/skills/a/SKILL.md': skill('a', 'true')}, 'skill_description_not_text', SPEC),
    ({'.claude/skills/a/SKILL.md': skill('a', body='line\n' * 600)}, 'skill_body_without_references', SPEC),
    ({'.claude/skills/a/SKILL.md': skill('a', body='See [gone](references/x.md).\n')}, 'skill_reference_missing', SPEC),
])
def test_a_metadata_violation_is_nonconformant_with_a_cited_rule(checkout, files, reason, source):
    report = audit.run(checkout(files), 'claude-code')
    (finding,) = report.findings
    assert (finding.status, finding.reason, finding.source, finding.dimension) == ('nonconformant', reason, source, 'commands')
    assert commands(report).status == 'nonconformant'
    assert (finding.cost_class, finding.remediation) == audit.REMEDIATIONS[reason]


def test_the_trigger_rule_is_lexical_and_cites_the_runtime_page(checkout):
    files = {'.agents/skills/a/SKILL.md': skill('a', 'Extracts tables and text from PDF files.')}
    (finding,) = audit.run(checkout(files), 'codex').findings
    assert (finding.evidence_kind, finding.source) == ('lexical', CODEX_SKILLS)


def test_a_long_body_with_a_references_folder_and_fenced_or_external_links_conforms(checkout):
    body = 'line\n' * 600 + '[site](https://example.com/x.md) [up](../elsewhere.md) [top](#top)\n```\n[code](nope.md)\n```\n'
    root = checkout({'.claude/skills/a/SKILL.md': skill('a', body=body), '.claude/skills/a/references/more.md': 'More.\n'})
    assert audit.run(root, 'claude-code').findings == ()


def test_unsupported_frontmatter_is_unknown_never_a_pass(checkout):
    root = checkout({'.claude/skills/a/SKILL.md': '---\nname: a\ndescription: {flow: map}\n---\nBody\n'})
    report = audit.run(root, 'claude-code')
    assert (commands(report).status, commands(report).reasons) == ('unknown', ('skill_frontmatter_unsupported',))


def test_a_skill_the_runtime_does_not_load_is_unknown_not_conformant(checkout):
    files = {'.agents/skills/a/SKILL.md': skill('a'), 'skills/b/SKILL.md': skill('b'), '.claude/skills/c/deep/SKILL.md': skill('deep')}
    report = audit.run(checkout(files), 'claude-code')
    assert (commands(report).status, commands(report).reasons) == ('unknown', ('skill_not_loaded_by_runtime',))
    assert {row.path for row in report.findings} == set(reasons(report))


def test_a_codex_skill_outside_the_task_chain_is_unknown(checkout):
    files = {'.agents/skills/root/SKILL.md': skill('root', 'Builds rockets. Use when asked about rockets.'),
             'pkg/.agents/skills/near/SKILL.md': skill('near', 'Plants gardens. Use when asked about gardens.')}
    root = checkout(files)
    assert audit.run(root, 'codex', task_cwd='pkg').findings == ()
    elsewhere = audit.run(root, 'codex', task_cwd='.')
    assert reasons(elsewhere) == {'pkg/.agents/skills/near/SKILL.md': 'skill_not_loaded_by_runtime'}


def test_near_duplicate_descriptions_collide_and_name_both_skills_with_evidence(checkout):
    files = {'.claude/skills/pdf-tools/SKILL.md': skill('pdf-tools'),
             '.claude/skills/pdf-helper/SKILL.md': skill('pdf-helper', AGREEMENT.replace('scans', 'files')),
             '.claude/skills/deploy/SKILL.md': skill('deploy', 'Ships the service to staging. Use when asked to release builds.')}
    report = audit.run(checkout(files), 'claude-code')
    found = {row.path: row for row in report.findings}
    assert set(found) == {'.claude/skills/pdf-tools/SKILL.md', '.claude/skills/pdf-helper/SKILL.md'}
    row = found['.claude/skills/pdf-tools/SKILL.md']
    assert (row.reason, row.evidence_kind, row.related, row.source, row.cost_class) == (
        'skill_routing_collision', 'lexical', ('.claude/skills/pdf-helper/SKILL.md',), PRACTICES, 'behavioral_evaluation_required')
    measures = dict(row.measures)
    assert measures['threshold_permille'] == 500 and measures['similarity_permille'] >= 500
    assert measures['shared_tokens'] <= measures['union_tokens']
    assert found['.claude/skills/pdf-helper/SKILL.md'].related == ('.claude/skills/pdf-tools/SKILL.md',)
    assert (report.skill_collision_rule, report.skill_tokenizer) == ('ahl-skill-collision-jaccard-v1', 'ahl-skill-tokens-v1')
    assert json.loads(json.dumps(report.document()))['skill_collision_threshold_permille'] == 500


def test_a_stricter_threshold_clears_the_collision_and_an_invalid_one_is_refused(checkout):
    near = AGREEMENT.replace('scans', 'files')
    root = checkout({'.claude/skills/a/SKILL.md': skill('a'), '.claude/skills/b/SKILL.md': skill('b', near)})
    assert len(audit.run(root, 'claude-code').findings) == 2
    strict = audit.run(root, 'claude-code', collision_threshold=1.0)
    assert (strict.findings, strict.skill_collision_threshold_permille) == ((), 1000)
    for bad in (0, 1.5):
        with pytest.raises(ValueError):
            audit.run(root, 'claude-code', collision_threshold=bad)


def test_skills_that_never_load_together_are_not_paired(checkout):
    siblings = {'a/.claude/skills/x/SKILL.md': skill('x'), 'b/.claude/skills/y/SKILL.md': skill('y')}
    assert audit.run(checkout(siblings), 'claude-code').findings == ()  # both load on demand, never from one another's scope


def test_a_skill_in_a_root_the_runtime_ignores_is_not_paired_with_a_loaded_one(checkout):
    other = {'.claude/skills/x/SKILL.md': skill('x'), '.agents/skills/y/SKILL.md': skill('y'), 'skills/w/SKILL.md': skill('w')}
    report = audit.run(checkout(other), 'claude-code')
    assert {row.reason for row in report.findings} == {'skill_not_loaded_by_runtime'}


def test_an_enclosing_scope_pairs_with_the_skill_nested_below_it(checkout):
    files = {'.claude/skills/x/SKILL.md': skill('x'), 'pkg/.claude/skills/y/SKILL.md': skill('y')}
    report = audit.run(checkout(files), 'claude-code')
    assert {row.reason for row in report.findings} == {'skill_routing_collision'}


def test_a_violation_is_reported_before_a_collision_and_unread_descriptions_never_collide(checkout):
    files = {'.claude/skills/a/SKILL.md': skill('a'), '.claude/skills/b/SKILL.md': skill('b', AGREEMENT, 'See [x](gone.md).\n'),
             '.claude/skills/c/SKILL.md': '---\nname: c\ndescription: {flow: map}\n---\n'}
    assert reasons(audit.run(checkout(files), 'claude-code')) == {
        '.claude/skills/a/SKILL.md': 'skill_routing_collision', '.claude/skills/b/SKILL.md': 'skill_reference_missing',
        '.claude/skills/c/SKILL.md': 'skill_frontmatter_unsupported'}


def test_the_inventory_keeps_description_words_out_of_its_facts(checkout):
    root = checkout({'.claude/skills/a/SKILL.md': skill('a', 'Zanzibar quokka telemetry. Use when asked.')})
    profile = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='.')
    text = json.dumps(harness_inventory.capture(root, profile).document())
    assert 'quokka' not in text.lower() and 'ahl-skill-tokens-v1' in text


def test_link_targets_never_reach_the_inventory_but_a_missing_one_is_still_found(checkout):
    root = checkout({'.claude/skills/a/SKILL.md': skill('a', body='[k](sk-live-ABCDEF123456SECRET) [ok](references/ok.md)\n'),
                     '.claude/skills/a/references/ok.md': 'Ok.\n'})
    profile = dict(schema_version=1, runtime='claude-code', version='2.1.317', task_cwd='.')
    text = json.dumps(harness_inventory.capture(root, profile).document())
    assert 'SECRET' not in text and 'ok.md' not in text.replace('.claude/skills/a/references/ok.md', '')
    assert reasons(audit.run(root, 'claude-code')) == {'.claude/skills/a/SKILL.md': 'skill_reference_missing'}


def test_collision_findings_cap_related_paths_and_keep_the_full_count(checkout):
    count = skills_module.MAX_RELATED + 4
    root = checkout({f'.claude/skills/s{n:02}/SKILL.md': skill(f's{n:02}') for n in range(count)})
    report = audit.run(root, 'claude-code')
    assert len(report.findings) == count
    for row in report.findings:
        assert len(row.related) == skills_module.MAX_RELATED
        assert dict(row.measures)['related_total'] == count - 1
        assert list(row.related) == sorted(row.related) and row.path not in row.related


def test_descriptions_under_the_minimum_token_count_never_collide(checkout):
    short = 'Builds rockets. Use when asked.'
    assert len(skill_headers.tokens(short)) < skills_module.MIN_TOKENS
    root = checkout({'.claude/skills/a/SKILL.md': skill('a', short), '.claude/skills/b/SKILL.md': skill('b', short)})
    assert audit.run(root, 'claude-code').findings == ()
