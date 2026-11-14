"""Bind a bank task to the interaction contract and scripted-reply oracle its grader bundle holds.

Both files are private grader inputs, beside the grader and never in the agent-visible fixture. The task
declares only their identities; a declared identity that no longer matches the bytes, a file written for
another task or root, or a hidden reply that appears in the fixture is rejected. Nothing here grades an
episode or delivers a reply.
"""
import json

from .integrity import contained, read_regular, unique_object

CONTRACT = 'interaction-contract.json'
ORACLE = 'interaction-oracle.json'
MAX_BYTES = 65536
FIXTURE_FILE_BYTES = 10 * 1024 * 1024  # the fixture digest already bounds the whole tree to this


def _json(raw, what):
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError(f'invalid interaction {what}') from exc


def check(task, fixture, grader, *, analysis_only):
    """Raise ValueError unless the task's declared identities describe the files it ships with."""
    bound, scan = (None, None) if analysis_only else (MAX_BYTES, FIXTURE_FILE_BYTES)
    if task.interaction_contract_sha256:
        from .interaction_contract import contract_sha256
        contract = _json(read_regular(contained(grader, CONTRACT), bound), 'contract')
        if not isinstance(contract, dict) or contract_sha256(contract) != task.interaction_contract_sha256:
            raise ValueError('interaction contract changed; comparisons must be invalidated')
        if contract['task_id'] != task.task_id:
            raise ValueError('interaction contract names another task')
    if task.interaction_oracle_sha256:
        from .scripted_interaction import load
        oracle = load(read_regular(contained(grader, ORACLE), bound), task.interaction_oracle_sha256)
        if (oracle['task_id'], oracle['root_id']) != (task.task_id, task.root_id):
            raise ValueError('interaction oracle task or root differs')
        needle = oracle['reply'].encode('utf-8')
        for path in sorted(fixture.rglob('*')):
            if path.is_file() and needle in read_regular(path, scan):
                raise ValueError('interaction hidden reply appears in the fixture')


def counts(tasks):
    return dict(contracts=sum(bool(t.interaction_contract_sha256) for t in tasks),
                oracles=sum(bool(t.interaction_oracle_sha256) for t in tasks))
