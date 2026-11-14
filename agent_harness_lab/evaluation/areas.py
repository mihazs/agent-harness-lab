"""Capability areas: a project-declared registry and the one to three area tags a bank task carries.

A tag that no registry entry declares is a typed manifest-contract error, never a silent new area, and a task
without tags is untagged: no report may read that as coverage. Errors name fields and positions, never the value.
"""
import re

from .manifest_contract import ContractError

ID = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
MAX_AREAS, MAX_PER_TASK, MAX_ID, MAX_LABEL, MAX_DESCRIPTION = 32, 3, 64, 80, 400
# Starting points a project may copy into ahl.json; nothing is declared on its behalf.
SUGGESTED = (
    dict(id='coding', label='Coding', description='Locate, change and verify behavior in source code.'),
    dict(id='ux-ui', label='UX/UI', description='Build and review user-interface behavior, layout and flows.'),
    dict(id='docs', label='Docs', description='Write and correct documentation and explanations.'),
    dict(id='review', label='Review', description='Find defects and risks in a change and report them.'),
)


def _valid_id(value):
    return isinstance(value, str) and len(value) <= MAX_ID and ID.fullmatch(value) is not None


def parse_registry(value):
    """Check the ahl.json areas declaration and return its ids in declared order."""
    def fail(reason, field): raise ContractError(reason, 'configuration', field, 'ahl.json')
    if not isinstance(value, list) or len(value) > MAX_AREAS: fail('area_registry_invalid', 'areas')
    seen = set()
    for index, entry in enumerate(value):
        where = f'areas[{index}]'
        if not isinstance(entry, dict) or set(entry) != {'id', 'label', 'description'}: fail('area_registry_invalid', where)
        if not _valid_id(entry['id']): fail('area_registry_invalid', where + '.id')
        if entry['id'] in seen: fail('area_duplicate', where + '.id')
        seen.add(entry['id'])
        for key, limit in (('label', MAX_LABEL), ('description', MAX_DESCRIPTION)):
            text = entry[key]
            if not isinstance(text, str) or not text.strip() or len(text) > limit or any(ord(c) < 32 for c in text):
                fail('area_registry_invalid', f'{where}.{key}')
    return tuple(entry['id'] for entry in value)


def task_areas(value, *, field='areas', file='bank.json'):
    """Check one task's areas field (one to three distinct ids) and return the tags."""
    def fail(reason, where): raise ContractError(reason, 'bank_validation', where, file)
    if not isinstance(value, list): fail('task_areas_invalid', field)
    if not value: fail('task_areas_empty', field)
    if len(value) > MAX_PER_TASK: fail('task_areas_too_many', field)
    for index, area in enumerate(value):
        if area == '': fail('task_areas_empty', f'{field}[{index}]')
        if not _valid_id(area): fail('task_areas_invalid', f'{field}[{index}]')
        if area in value[:index]: fail('area_duplicate', f'{field}[{index}]')
    return tuple(value)


def check_declared(tags, declared, *, field='areas', file='bank.json'):
    """Reject the first tag the registry does not declare."""
    for index, area in enumerate(tags):
        if area not in declared: raise ContractError('task_area_undeclared', 'bank_validation', f'{field}[{index}]', file)
