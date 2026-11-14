"""Blind lineage adjudication: a `reviewed` label is a claim, independent judges give evidence."""
from collections import Counter
import hashlib
from itertools import combinations
import json
from pathlib import Path
import re
from agent_harness_lab.private_journal import encoded, record, write_bytes
from .integrity import contained, digest, read_regular, tree, unique_object
from .task_bank import TaskSpec, inspect

PROFILE = 'blind-lineage-adjudication-v1'
MIN_ADJUDICATORS = 2
MAX_ADJUDICATORS = 16
MAX_RELATED_ALIASES = 256  # shortcut: bounds pairwise comparison, split larger families into reviewed batches
MAX_BYTES = 1024 * 1024
UNBOUNDED = dict(max_files=None, max_bytes=None, max_entries=None, max_depth=None)
SEVERITY = ('cross_partition_relation', 'undeclared_relation', 'disputed', 'unsure', 'adjudicated_independent')
LIMITATIONS = (
    'adjudicator identities are operator-declared, not authenticated',
    'collusion and shared model or training lineage between adjudicators are not excluded',
    'blindness covers declared labels and other verdicts, not provenance a prompt or file name reveals',
    'no relation found by the declared adjudicators is not verified independence',
)


def _alias(bank_sha256, task_id):
    return hashlib.sha256(f'{bank_sha256}\0{task_id}'.encode()).hexdigest()[:16]


def _tasks(bank_path):
    bank_path = Path(bank_path)
    report = inspect(bank_path)  # verifies fixture and grader digests and the declared leakage rules
    raw = read_regular(bank_path, None)
    if hashlib.sha256(raw).hexdigest() != report['bank_sha256']: raise ValueError('bank changed during adjudication')
    tasks = [TaskSpec.parse(t, analysis_only=True) for t in json.loads(raw.decode('utf-8'))['tasks']]
    return report['bank_sha256'], tasks


def packet(bank_path):
    """The only view an adjudicator receives: opaque aliases, prompts and fixture digests, no task labels."""
    bank_sha256, tasks = _tasks(bank_path); items = []
    for task in tasks:
        fixture = contained(Path(bank_path).parent, task.fixture)
        files = {name: identity['sha256'] for name, identity in tree(fixture, **UNBOUNDED).items()}
        task_json = json.loads(read_regular(contained(fixture, 'task.json'), None).decode('utf-8'), object_pairs_hook=unique_object)
        if not isinstance(task_json, dict) or not isinstance(task_json.get('prompt'), str):
            raise ValueError('task JSON requires an explicit prompt')
        if digest(fixture, **UNBOUNDED) != task.fixture_digest: raise ValueError('fixture changed during adjudication packet')
        items.append(dict(alias=_alias(bank_sha256, task.task_id), prompt=task_json['prompt'],
                          fixture_sha256=task.fixture_digest, fixture_files=files))
    if len({item['alias'] for item in items}) != len(items): raise ValueError('adjudication alias collision')
    value = dict(schema_version=1, profile=PROFILE, bank_sha256=bank_sha256, items=sorted(items, key=lambda i: i['alias']))
    return value, hashlib.sha256(encoded(value)).hexdigest(), tasks


def write_packet(bank_path, output):
    output = Path(output).absolute()
    if '..' in output.parts or any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError('adjudication packet output must be a canonical host path')
    source = Path(bank_path).parent.resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('adjudication packet and bank must not overlap')
    value, pin, _ = packet(bank_path)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_bytes(output, encoded(value))
    return dict(profile=PROFILE, path=str(output), packet_sha256=pin, items=len(value['items']), publication_allowed=False)


def _submission(path, pin, aliases, reviewers):
    value, _ = record(path, MAX_BYTES)
    if set(value) != {'schema_version', 'profile', 'packet_sha256', 'adjudicator', 'related_groups', 'unsure'}:
        raise ValueError('adjudication fields must match the versioned schema')
    if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['profile'] != PROFILE:
        raise ValueError('unsupported adjudication profile')
    if value['packet_sha256'] != pin: raise ValueError('adjudication was made on a different packet')
    name = value['adjudicator']
    if not isinstance(name, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', name) is None:
        raise ValueError('exact adjudicator identity required')
    if name.casefold() in reviewers: raise ValueError('adjudicator is also a reviewer named in the bank')
    groups, unsure = value['related_groups'], value['unsure']
    if not isinstance(groups, list) or not isinstance(unsure, list) or any(not isinstance(g, list) or len(g) < 2 for g in groups):
        raise ValueError('adjudication needs related groups of two or more aliases and an unsure list')
    related = [a for g in groups for a in g]; everyone = related + unsure
    if (any(not isinstance(a, str) for a in everyone) or len(set(everyone)) != len(everyone)
            or not set(everyone) <= set(aliases) or len(related) > MAX_RELATED_ALIASES):
        raise ValueError('adjudication aliases must be known, unique and bounded')
    return name.casefold(), {frozenset(p) for g in groups for p in combinations(sorted(g), 2)}, set(unsure)


def evaluate(bank_path, submissions):
    """Compare independent blind verdicts with the bank's declared roots and partitions."""
    value, pin, tasks = packet(bank_path)
    if not isinstance(submissions, (list, tuple)) or not 0 < len(submissions) <= MAX_ADJUDICATORS:
        raise ValueError(f'one to {MAX_ADJUDICATORS} adjudication files required')
    bank_sha256 = value['bank_sha256']
    aliases = {_alias(bank_sha256, t.task_id): t for t in tasks}
    reviewers = {t.reviewer.casefold() for t in tasks}
    judged = [_submission(path, pin, aliases, reviewers) for path in submissions]
    if len({name for name, _, _ in judged}) != len(judged): raise ValueError('adjudicators must be distinct')
    unsure = set().union(*(u for _, _, u in judged))
    votes = Counter(pair for _, pairs, _ in judged for pair in pairs if not pair & unsure)
    status = {alias: 'adjudicated_independent' for alias in aliases}
    for alias in unsure: status[alias] = 'unsure'
    for pair, count in votes.items():
        first, second = (aliases[a] for a in sorted(pair))
        if first.partition == second.partition and first.root_id == second.root_id: continue  # the declared grouping
        kind = ('disputed' if count < len(judged) else 'cross_partition_relation' if first.partition != second.partition
                else 'undeclared_relation')
        for alias in pair:
            if SEVERITY.index(kind) < SEVERITY.index(status[alias]): status[alias] = kind
    by_task = {aliases[a].task_id: s for a, s in sorted(status.items(), key=lambda i: aliases[i[0]].task_id)}
    counts = Counter(by_task.values()); quorum = len(judged) >= MIN_ADJUDICATORS
    outcome = ('insufficient_adjudicators' if not quorum else 'relations_found' if counts['cross_partition_relation'] or counts['undeclared_relation']
               else 'inconclusive' if counts['disputed'] or counts['unsure'] else 'no_relation_found')
    return dict(schema_version=1, profile=PROFILE, packet_sha256=pin, bank_sha256=bank_sha256, outcome=outcome,
                adjudicators=len(judged), minimum_adjudicators=MIN_ADJUDICATORS, quorum_met=quorum, tasks=by_task,
                counts={s: counts[s] for s in SEVERITY if counts[s]},
                declared_reviewed_without_adjudicated_independence=sorted(
                    t.task_id for t in tasks if t.independence == 'reviewed' and (not quorum or by_task[t.task_id] != 'adjudicated_independent')),
                authenticated_adjudicators=False, independence_verified=False, certification_eligible=False,
                publication_allowed=False, limitations=list(LIMITATIONS))
