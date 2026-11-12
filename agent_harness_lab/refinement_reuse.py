"""A refinement attempt reuses the frozen development and regression tasks and constructs only fresh held-out inputs (#739).

The controller copies the frozen parts beside the author's fresh held-out tasks and checks every copied byte against the
digest it took when the first attempt froze them. The held-out freshness rule is unchanged: validate_manifest still
rejects any held-out input an earlier attempt used.
"""
import hashlib
from pathlib import Path
import shutil

from agent_harness_lab.evaluation.integrity import contained, digest
from agent_harness_lab.evaluation.manifest_contract import ContractError

FRESH = 'heldout'
STAGE = 'evaluation_construction'


def _digest(path):
    return digest(path) if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()


def _programs(task, source):
    """Relative paths of the grader programs this task keeps inside the evaluation directory."""
    found = []
    for argument in task['grader']:
        program = Path(argument)
        if program.is_absolute() and program.resolve().is_relative_to(source):
            found.append(program.resolve().relative_to(source).as_posix())
    return found


def plan(source, manifest):
    """The reusable parts of a controller-validated manifest, each with the digest it has right now."""
    source = Path(source).resolve()
    tasks = [task for task in manifest['tasks'] if task['split'] != FRESH]
    ids = {task['id'] for task in tasks}
    controls = [control for control in manifest['grader_self_tests'] if control['task'] in ids]
    bundles = {ident: bundle for ident, bundle in manifest.get('grader_bundles', {}).items() if ident in ids}
    paths = {task['fixture'] for task in tasks} | {control['fixture'] for control in controls}
    paths |= {bundle['path'] for bundle in bundles.values()} | {name for task in tasks for name in _programs(task, source)}
    kept = sorted(name for name in paths if not any(other != name and name.startswith(other + '/') for other in paths))
    return dict(source=source, tasks=tasks, controls=controls, bundles=bundles,
                frozen={name: _digest(contained(source, name)) for name in kept})


def prompt_line(reuse):
    ids = ', '.join(sorted(task['id'] for task in reuse['tasks']))
    return (f'Refinement: the controller reuses the frozen development and regression tasks ({ids}) with their graders and controls; '
            f'do not recreate or edit them. Write manifest.json with only new tasks whose split is {FRESH}, with fresh fixtures, '
            'their own grader_bundles and grader_self_tests, and ids, file and directory names the reused tasks do not use '
            f'(reserved: {", ".join(sorted(reuse["frozen"]))}). The controller adds the reused tasks after you finish.\n')


def merge(reuse, destination, manifest):
    """Check that the author wrote only fresh held-out tasks, copy the frozen parts in and return (merged manifest, facts)."""
    destination, source = Path(destination).resolve(), reuse['source']
    reused = {task['id'] for task in reuse['tasks']}
    for index, task in enumerate(manifest['tasks']):
        if task['split'] != FRESH:
            raise ContractError('evaluation_manifest_field_invalid', STAGE, f'tasks[{index}].split')
        if task['id'] in reused:
            raise ContractError('evaluation_manifest_field_invalid', STAGE, f'tasks[{index}].id')
    if any((destination / name).exists() for name in reuse['frozen']):
        raise ContractError('evaluation_manifest_field_invalid', STAGE, 'tasks[*].fixture')  # a fresh path collides with a reused one
    for name in reuse['frozen']:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        (shutil.copytree if (source / name).is_dir() else shutil.copy2)(source / name, target)
    if any(_digest(destination / name) != expected for name, expected in reuse['frozen'].items()):
        raise ContractError('grader_input_changed_after_freeze', 'refinement_reuse', 'reused_tasks')
    tasks = [dict(task, grader=[str(destination / Path(argument).resolve().relative_to(source))
                                if Path(argument).is_absolute() and Path(argument).resolve().is_relative_to(source) else argument
                                for argument in task['grader']]) for task in reuse['tasks']]
    merged = dict(manifest, tasks=tasks + manifest['tasks'],
                  grader_self_tests=[{key: value for key, value in control.items() if key != 'digest'}
                                     for control in reuse['controls']] + manifest['grader_self_tests'])
    if reuse['bundles'] or manifest.get('grader_bundles'):
        merged['grader_bundles'] = {**{ident: dict(path=bundle['path']) for ident, bundle in reuse['bundles'].items()},
                                    **manifest.get('grader_bundles', {})}
    return merged, dict(reused_tasks=len(tasks), fresh_heldout_tasks=len(manifest['tasks']), frozen_digests_verified=True)
