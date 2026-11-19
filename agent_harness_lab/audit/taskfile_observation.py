"""Bounded lexical Taskfile observations; no task execution or missing-target verdict."""
import re
import time

from agent_harness_lab.audit import observation_inputs
from agent_harness_lab.sources import frontmatter
from agent_harness_lab.sources.git_view import Folder

PROFILE = 'ahl-taskfile-observation-v1'
MAX_BYTES = 256 * 1024
MAX_LINES = 512
MAX_TARGETS = 2048
MAX_MENTIONS = 1024
_KEY = re.compile(r'''(?:"([^"\\]*)"|'([^']*)'|([A-Za-z_][A-Za-z0-9_.-]*(?::[A-Za-z0-9_.-]+)*))[ \t]*:(?:[ \t]+(.*))?$''')
_MENTION = re.compile(r'(?<![\w-])task[ \t]+([A-Za-z_][A-Za-z0-9_.-]*(?::[A-Za-z0-9_.-]+)*)')


def targets(raw):
    """Return literal root task keys in the modelled subset; this does not validate YAML or task loading."""
    if type(raw) is not bytes or len(raw) > MAX_BYTES: raise ValueError('taskfile exceeds its bound')
    text = raw.decode('utf-8')
    lines = text.splitlines()
    if len(lines) > MAX_LINES or any(line.strip() in ('---', '...') for line in lines):
        raise ValueError('unsupported taskfile document')
    fields = frontmatter._mapping(lines, False, _KEY)
    tasks = fields.get('tasks')
    if not isinstance(tasks, frontmatter.Mapping) or len(tasks.keys) > MAX_TARGETS:
        raise ValueError('unsupported taskfile tasks')
    return tasks.keys


def capture(project, inventory, task_cwd):
    """Compare root instruction mentions to lexical root task keys without executing either file."""
    result = dict(profile=PROFILE, status='unknown', scope='checkout_root_files_only', targets=[], mentions=[],
                  target_coverage='unknown', matching='lexical_only', observed_loading='unknown',
                  mention_coverage='unknown', runtime_execution='unknown',
                  snapshot_relation='separate_observation', publication_allowed=False)
    try:
        if task_cwd != '.': return result
        with Folder(project, time.monotonic() + 30) as folder:
            taskfile = folder.read('Taskfile.yml', MAX_BYTES)
            folder.verify()
        if taskfile is None: return result
        names = targets(taskfile)
        result.update(status='observed', targets=list(names))
        found = observation_inputs.read(project, inventory, task_cwd, 'Taskfile.yml', Folder, MAX_BYTES)
        if found is None or found[1] != taskfile: return result
        instruction, _ = found
        matches = []
        for number, line in enumerate(instruction.decode('utf-8').splitlines(), 1):
            for match in _MENTION.finditer(line):
                if len(matches) >= MAX_MENTIONS: raise ValueError('task mention bound exceeded')
                target = match.group(1)
                matches.append(dict(line=number, target=target,
                                    status='lexical_candidate' if target in names else 'unknown'))
        result.update(mentions=matches, mention_coverage='root_instruction_only')
    except (ValueError, OSError, TimeoutError, UnicodeError, RecursionError):
        pass
    return result
