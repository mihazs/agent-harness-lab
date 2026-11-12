"""Typed evaluation-manifest contract: what the author writes, what the controller owns, and how violations read.

Every violation names the stage, file and field (names only, never content), says whether AHL or the
agent output is at fault, and gives the exact next step. No result text is a bare exception message.
"""
from contextlib import contextmanager

SPLITS = ('development', 'regression', 'heldout')
STATES = ('broken', 'partial', 'complete')
FILE = 'manifest.json'
AGENT, AHL = 'agent_output_defect', 'ahl_contract_mismatch'

# Fields the construction prompt asks the author to write, and the fields the controller writes itself.
AUTHOR_FIELDS = dict(manifest=('skills', 'harness_paths', 'tasks', 'grader_self_tests', 'grader_bundles'),
                     task=('id', 'split', 'fixture', 'prompt', 'artifacts'),
                     control=('task', 'state', 'fixture'), bundle=('path',))
CONTROLLER_FIELDS = dict(task=('grader',), control=('digest',), bundle=('digest',),
                         manifest=('max_trials', 'budget_seconds', 'timeout_seconds', 'no_nested_agents'))

# reason -> (origin, repairable by one authoring pass, generic remediation)
REASONS = {
    'evaluation_manifest_unreadable': (AGENT, True, 'Write manifest.json as one JSON object in the evaluation directory, then rerun.'),
    'evaluation_manifest_field_missing': (AGENT, True, 'Add the named manifest field with the documented shape, then rerun.'),
    'evaluation_manifest_field_invalid': (AGENT, True, 'Correct the named manifest field to the documented shape, then rerun.'),
    'evaluation_manifest_controller_field_supplied': (AGENT, True, 'Remove the named controller-owned field from manifest.json; the controller computes it. Then rerun.'),
    'authored_digest_not_accepted': (AGENT, True, 'Omit every digest field from manifest.json; the controller computes and freezes all digests. Then rerun.'),
    'evaluation_manifest_controls_incomplete': (AGENT, True, 'Give every task one broken, one partial and one complete grader self-test, then rerun.'),
    'grader_input_changed_after_freeze': (AGENT, False, 'A frozen grader bundle or control fixture changed after the controller froze it; find what wrote into it (a grader or helper must write nothing beside its inputs), keep it unchanged, then rerun.'),
    'evaluation_construction_timeout': (AGENT, True, 'The evaluation-construction agent overran its time box and the remaining budget could not cover another pass; raise the budget with --timeout or the construction share with --construction-fraction, or narrow the run with --focus so construction has less to build, then rerun.'),
    'evaluation_task_infeasible': (AGENT, False, 'The complete control of a task could not be graded within the per-trial timeout, so no trial could finish it in time; simplify the task or its grader so the reference completion finishes sooner, or raise --timeout when the budget bounds the per-trial timeout, then rerun.'),
    'internal_contract_error': (AHL, False, 'This is an AHL contract defect, not an input defect; report it with the named stage and field.'),
}


def contract_text(controller_grader=True):
    """The prompt sentence derived from the shared tables, so prompt and validators cannot drift apart."""
    a, c = AUTHOR_FIELDS, CONTROLLER_FIELDS
    task = a['task'] + (() if controller_grader else ('grader',))
    owned = [f'{k}.{v}' for k, vs in c.items() for v in vs if k != 'manifest' and (controller_grader or v != 'grader')]
    return ('Manifest contract: manifest.json holds ' + ', '.join(a['manifest']) + '; each task declares '
            + ', '.join(task) + '; each grader_self_tests entry has exactly ' + ', '.join(a['control'])
            + '; each grader_bundles entry has exactly ' + ', '.join(a['bundle'])
            + '. The controller owns ' + ', '.join(owned) + ' and the run limits; never write them. '
            'Omit every digest: the controller computes bundle and control digests itself, and an authored digest is never an integrity claim.')


class ContractError(ValueError):
    """A typed manifest or contract violation; str() is a composed diagnostic, never an exception message."""

    def __init__(self, reason, stage, field=None, file=FILE):
        self.reason, self.stage, self.field, self.file = reason, stage, field, file
        self.origin, self.repairable, self.remediation = REASONS[reason]
        super().__init__(self.render())

    @property
    def next_step(self):
        return f'{self.remediation} Field: {self.field}.' if self.field else self.remediation

    def render(self):
        return (f'{self.reason}: stage={self.stage} file={self.file} field={self.field or "-"} '
                f'origin={self.origin}. Next: {self.next_step}')

    def receipt(self):
        return dict(reason=self.reason, stage=self.stage, file=self.file, field=self.field, origin=self.origin,
                    next_step=self.next_step)

    def repair_instruction(self):
        return ('The controller rejected your manifest.json before any grading. Rejection: ' + self.render()
                + ' Fix only this defect in /work, keep everything else, and finish.')


@contextmanager
def boundary(stage, file=FILE):
    """Stage boundary: an unexpected KeyError/TypeError/AttributeError/IndexError never surfaces as exception text."""
    try:
        yield
    except ContractError:
        raise
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ContractError('internal_contract_error', stage, file=file) from exc


def check_authored(manifest, *, stage, controller_grader=True, grading=True):
    """Shape-check an authored manifest before any other stage. Raises ContractError for the first defect."""
    def fail(reason, field): raise ContractError(reason, stage, field)
    if not isinstance(manifest, dict): fail('evaluation_manifest_unreadable', None)
    for name in AUTHOR_FIELDS['manifest']:
        if name not in manifest and (name != 'grader_bundles' or grading) and (controller_grader or name not in ('skills', 'harness_paths')):
            fail('evaluation_manifest_field_missing', name)
    for name in ('skills', 'harness_paths'):
        if name in manifest and (not isinstance(manifest[name], list) or not all(isinstance(v, str) for v in manifest[name])):
            fail('evaluation_manifest_field_invalid', name)
    tasks, controls, bundles = manifest['tasks'], manifest['grader_self_tests'], manifest.get('grader_bundles', {})
    if not isinstance(tasks, list) or not tasks: fail('evaluation_manifest_field_invalid', 'tasks')
    if not isinstance(controls, list): fail('evaluation_manifest_field_invalid', 'grader_self_tests')
    if not isinstance(bundles, dict): fail('evaluation_manifest_field_invalid', 'grader_bundles')
    required = [n for n in AUTHOR_FIELDS['task'] if grading or n != 'artifacts']
    if not controller_grader: required.append('grader')
    ids = []
    for index, task in enumerate(tasks):
        if not isinstance(task, dict): fail('evaluation_manifest_field_invalid', f'tasks[{index}]')
        for name in required:
            if name not in task: fail('evaluation_manifest_field_missing', f'tasks[{index}].{name}')
        if controller_grader and 'grader' in task: fail('evaluation_manifest_controller_field_supplied', f'tasks[{index}].grader')
        for name in ('id', 'fixture'):
            if not isinstance(task[name], str) or not task[name]: fail('evaluation_manifest_field_invalid', f'tasks[{index}].{name}')
        if task['split'] not in SPLITS: fail('evaluation_manifest_field_invalid', f'tasks[{index}].split')
        if 'prompt' in required and (not isinstance(task['prompt'], str) or not task['prompt']):
            fail('evaluation_manifest_field_invalid', f'tasks[{index}].prompt')
        if 'artifacts' in required and (not isinstance(task['artifacts'], list) or not task['artifacts']
                                        or not all(isinstance(a, str) and a for a in task['artifacts'])):
            fail('evaluation_manifest_field_invalid', f'tasks[{index}].artifacts')
        if not controller_grader and (not isinstance(task['grader'], list) or not task['grader']
                                      or not all(isinstance(a, str) for a in task['grader'])):
            fail('evaluation_manifest_field_invalid', f'tasks[{index}].grader')
        ids.append(task['id'])
    if len(set(ids)) != len(ids): fail('evaluation_manifest_field_invalid', 'tasks[*].id')
    if grading or bundles:
        for index, ident in enumerate(ids):
            if ident not in bundles: fail('evaluation_manifest_field_missing', f'grader_bundles[{index}]')
            declaration = bundles[ident]
            if not isinstance(declaration, dict): fail('evaluation_manifest_field_invalid', f'grader_bundles[{index}]')
            if 'path' not in declaration: fail('evaluation_manifest_field_missing', f'grader_bundles[{index}].path')
            if 'digest' in declaration: fail('authored_digest_not_accepted', f'grader_bundles[{index}].digest')
            if not isinstance(declaration['path'], str) or not declaration['path']:
                fail('evaluation_manifest_field_invalid', f'grader_bundles[{index}].path')
        if set(bundles) - set(ids): fail('evaluation_manifest_field_invalid', 'grader_bundles[*] (entry without a task)')
    for index, control in enumerate(controls):
        if not isinstance(control, dict): fail('evaluation_manifest_field_invalid', f'grader_self_tests[{index}]')
        for name in AUTHOR_FIELDS['control']:
            if name not in control: fail('evaluation_manifest_field_missing', f'grader_self_tests[{index}].{name}')
        if 'digest' in control: fail('authored_digest_not_accepted', f'grader_self_tests[{index}].digest')
        if not isinstance(control['fixture'], str) or not control['fixture'] or control['task'] not in ids:
            fail('evaluation_manifest_field_invalid', f'grader_self_tests[{index}].task' if control['task'] not in ids else f'grader_self_tests[{index}].fixture')
        if control['state'] not in STATES: fail('evaluation_manifest_field_invalid', f'grader_self_tests[{index}].state')
    for index, ident in enumerate(ids):
        if sorted(c['state'] for c in controls if c['task'] == ident) != sorted(STATES):
            fail('evaluation_manifest_controls_incomplete', f'grader_self_tests[task {index}]')
    return manifest


def strip_authored_digests(manifest):
    """Drop every authored bundle or control digest in place and return how many; the controller recomputes them."""
    if not isinstance(manifest, dict): return 0
    bundles, controls = manifest.get('grader_bundles'), manifest.get('grader_self_tests')
    entries = [*(bundles.values() if isinstance(bundles, dict) else []), *(controls if isinstance(controls, list) else [])]
    owned = [e for e in entries if isinstance(e, dict) and 'digest' in e]
    for entry in owned: del entry['digest']
    return len(owned)


def check_file(path, *, stage, strip_digests=False, **options):
    """Read and shape-check an authored manifest file; unreadable or non-object content is a typed defect.

    With strip_digests the authored digests are removed from the file first; returns (manifest, count stripped),
    and a ContractError carries the count as digests_stripped.
    """
    import json
    try:
        manifest = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ContractError('evaluation_manifest_unreadable', stage) from exc
    stripped = strip_authored_digests(manifest) if strip_digests else 0
    if stripped: path.write_text(json.dumps(manifest))
    try:
        return check_authored(manifest, stage=stage, **options), stripped
    except ContractError as error:
        error.digests_stripped = stripped  # the file already lost them: the caller must still record the count
        raise


def typed(exc, stage='improve'):
    """Last-resort rendering: bare lookup/type errors become internal_contract_error; everything else is unchanged."""
    if isinstance(exc, (KeyError, TypeError, AttributeError, IndexError)):
        return ContractError('internal_contract_error', stage, file=None)
    return exc
