"""Synthetic Claude 2.1.316 captures with Skill calls and skill-file reads; no real sessions."""
import hashlib
import json

# A fixed, nonexistent project label keeps scoped identities deterministic; the reader only compares it to cwd.
PROJECT = '/synthetic-ahl/project'


def non_bundle_reads(project=PROJECT):
    """Reads that are not exact project skill-bundle entry points; each must leave the projection unchanged."""
    return (project + '/README.md', project + '/.claude/skills/alpha/README.md',
        project + '/.claude/skills/alpha/nested/SKILL.md', project + '/.claude/skills/SKILL.md',
        project + '/.claude/skills/alpha/../beta/SKILL.md', project + '//.claude/skills/alpha/SKILL.md',
        project + '/./.claude/skills/alpha/SKILL.md', project + '/.claude/skills/alpha/SKILL.md/',
        project + '/.claude/skills/alpha/skill.md', project + '/.codex/skills/alpha/SKILL.md',
        project + '-other/.claude/skills/alpha/SKILL.md', '/synthetic-ahl/home/.claude/skills/alpha/SKILL.md',
        '.claude/skills/alpha/SKILL.md')


# Projection digest of rows() on the pre-change adapter (commit 9a8efe3); a regression guard, not a grade.
BASELINE_SHA256 = '8bf237c1c83419edf958bd08cf065ac45666600ea931a23b2cdc24cafbbbe0dd'


def row(kind, ident, parent, message=None, project=PROJECT, **extra):
    value = dict(type=kind, uuid=ident, parentUuid=parent, timestamp='2026-01-01T00:00:00Z', sessionId='synthetic-session',
        cwd=project, version='2.1.316', isSidechain=False, **extra)
    if message is not None: value['message'] = message
    return value


def call(ident, parent, name, arguments, call_id, project=PROJECT):
    return row('assistant', ident, parent, dict(role='assistant', id='response-'+ident, stop_reason='tool_use',
        content=[dict(type='tool_use', id=call_id, name=name, input=arguments)]), project)


def result(ident, parent, call_id, error=False, project=PROJECT):
    return row('user', ident, parent, dict(role='user', content=[dict(type='tool_result', tool_use_id=call_id,
        is_error=error, content='SYNTHETIC_SECRET')]), project)


def rows(project=PROJECT, reads=None):
    """A capture without Skill calls or exact project skill-bundle reads."""
    reads = non_bundle_reads(project) if reads is None else reads
    out, parent = [row('user', 'u0', None, dict(role='user', content='SYNTHETIC_SECRET'), project)], 'u0'
    for index, path in enumerate(reads):
        out.append(call(f'a{index}', parent, 'Read', dict(file_path=path), f'call-{index}', project))
        out.append(result(f'r{index}', f'a{index}', f'call-{index}', project=project))
        parent = f'r{index}'
    out.append(call('bash', parent, 'Bash', dict(command='cat .claude/skills/alpha/SKILL.md'), 'call-bash', project))
    out.append(call('empty', 'bash', 'Read', {}, 'call-empty', project))
    out.append(call('odd', 'empty', 'Read', dict(file_path=7), 'call-odd', project))
    return out


def write(path, values): path.write_text(''.join(json.dumps(value)+'\n' for value in values))


def projection_sha256(events):
    return hashlib.sha256(json.dumps(list(events), sort_keys=True).encode()).hexdigest()
