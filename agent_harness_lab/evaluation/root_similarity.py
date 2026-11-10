"""Local lexical candidates; absence of a match never establishes independence."""
import re
import unicodedata
from collections import defaultdict

ALGORITHM = 'root-prompt-shingle-jaccard-v1'
THRESHOLD = 0.7


def shingles(prompt):
    """Build three-token shingles, abstracting code-like identifier names."""
    text = unicodedata.normalize('NFC', prompt).casefold()
    variables = set(re.findall(r'\bset\s+([a-z]\w*)\s+from\b', text))
    variables.update(re.findall(r'\b([a-z]\w*)\s*=(?!=)', text))
    tokens = re.findall(r'\w+|[^\w\s]', text)
    tokens = [
        '<identifier>' if token in variables or '_' in token or len(token) == 1 and token.isalpha() and token not in ('a', 'i') else token
        for token in tokens
    ]
    return frozenset(zip(tokens, tokens[1:], tokens[2:]))


def suspected_components(prompts):
    """Group root IDs linked by at least one lexical candidate, including transitive links.

    ``prompts`` is an iterable of (root ID, prompt). Comparisons are local; no
    original prompt or shingle is returned. Identical root IDs remain one unit.
    """
    roots = sorted({root for root, _ in prompts})
    fingerprints = defaultdict(set)
    for root, prompt in prompts:
        fingerprint = shingles(prompt)
        if fingerprint:
            fingerprints[fingerprint].add(root)
    parent = {root: root for root in roots}

    def find(root):
        while parent[root] != root:
            parent[root] = parent[parent[root]]
            root = parent[root]
        return root

    def join(left, right):
        left, right = find(left), find(right)
        if left != right:
            parent[max(left, right)] = min(left, right)

    unique = list(fingerprints)
    for members in fingerprints.values():
        ordered = sorted(members)
        for root in ordered[1:]:
            join(ordered[0], root)
    inverted = defaultdict(list)
    for index, first in enumerate(unique):
        candidates = set()
        for shingle in first:
            candidates.update(inverted[shingle])
        for previous in candidates:
            second = unique[previous]
            if len(first & second) / len(first | second) >= THRESHOLD:
                join(next(iter(fingerprints[first])), next(iter(fingerprints[second])))
        for shingle in first:
            inverted[shingle].append(index)
    groups = defaultdict(list)
    for root in roots:
        groups[find(root)].append(root)
    return tuple(tuple(group) for group in groups.values() if len(group) > 1)


def evidence(bank_sha256, revision_sha256, task_roots, prompts):
    """Create an input-bound, descriptive snapshot for the supplied task roots."""
    rows = sorted([list(row) for row in task_roots])
    if len(prompts) != len(rows):
        raise ValueError('all selected task prompts are required for similarity evidence')
    return dict(schema_version=1, algorithm=ALGORITHM, bank_sha256=bank_sha256,
                grouping_revision_sha256=revision_sha256, task_roots=rows,
                groups=[list(group) for group in suspected_components(prompts)])


def project_evidence(full, task_roots):
    """Project inspected bank candidates to selected tasks without exposing other IDs."""
    rows = sorted([list(row) for row in task_roots])
    indexed = dict(full['task_roots'])
    if len({task for task, _ in rows}) != len(rows) or any(indexed.get(task) != root for task, root in rows):
        raise ValueError('selected tasks do not match bank task roots')
    selected = {root for _, root in rows}
    groups = [sorted(selected.intersection(group)) for group in full['groups']]
    return dict(full, task_roots=rows, groups=sorted(group for group in groups if len(group) > 1))


def manifest_evidence(source):
    """Derive private candidates from the exact task prompts in a workflow manifest."""
    import hashlib
    from agent_harness_lab.sources.schema import digest
    tasks = source.get('tasks') if isinstance(source, dict) else None
    if not isinstance(tasks, list) or not 0 < len(tasks) <= 1000:
        raise ValueError('workflow similarity needs a bounded set of explicit task prompts')
    items = []
    for task in tasks:
        if not isinstance(task, dict) or any(not isinstance(task.get(key), str) or not task[key]
                                             for key in ('id', 'root', 'prompt')):
            raise ValueError('workflow similarity needs explicit task identities and prompts')
        items.append((task['id'], task['root'], task['prompt']))
    if len({task for task, _, _ in items}) != len(items):
        raise ValueError('workflow similarity task identities must be unique')
    identities = sorted([task, root, hashlib.sha256(prompt.encode('utf-8')).hexdigest()]
                        for task, root, prompt in items)
    bank = digest(dict(profile='workflow-prompt-similarity-v1', tasks=identities))
    value = evidence(bank, digest(dict(algorithm=ALGORITHM, bank_sha256=bank)),
                     [(task, root) for task, root, _ in items], [(root, prompt) for _, root, prompt in items])
    return dict(value, source_kind='workflow-prompt-similarity-v1')


def binding(spec, supplied, bank_path=None):
    """Validate the plan-pinned snapshot; it is evidence of candidates, not independence."""
    from agent_harness_lab.sources.schema import digest
    pin = spec.root_similarity_sha256
    if pin is None:
        if supplied is not None or bank_path is not None:
            raise ValueError('similarity evidence requires a frozen plan pin')
        return {}
    if bank_path is None:
        raise ValueError('current bank required for frozen root similarity evidence')
    if not isinstance(supplied, dict) or digest(supplied) != pin:
        raise ValueError('missing or changed frozen root similarity evidence')
    manifest_kind = supplied.get('source_kind')
    allowed = {'schema_version', 'algorithm', 'bank_sha256', 'grouping_revision_sha256', 'task_roots', 'groups'}
    if manifest_kind is not None:
        allowed.add('source_kind')
        if manifest_kind != 'workflow-prompt-similarity-v1': raise ValueError('unsupported similarity evidence source')
    if set(supplied) != allowed:
        raise ValueError('invalid root similarity evidence fields')
    if supplied['schema_version'] != 1 or supplied['algorithm'] != ALGORITHM:
        raise ValueError('unsupported root similarity evidence')
    for key in ('bank_sha256', 'grouping_revision_sha256'):
        value = supplied[key]
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('root similarity evidence requires SHA-256 identities')
    if supplied['task_roots'] != sorted([[task, root] for task, root, _ in spec.tasks]):
        raise ValueError('root similarity evidence does not match frozen tasks and roots')
    known = {root for _, root, _ in spec.tasks}
    groups = supplied['groups']
    if not isinstance(groups, list) or any(not isinstance(g, list) or len(g) < 2 or
            g != sorted(set(g)) or not set(g) <= known for g in groups):
        raise ValueError('invalid root similarity groups')
    if groups != sorted(groups) or sum(map(len, groups)) != len({root for group in groups for root in group}):
        raise ValueError('root similarity groups must be disjoint and ordered')
    if manifest_kind is not None:
        from agent_harness_lab.private_journal import record
        actual = manifest_evidence(record(bank_path, 1024 * 1024)[0])
    else:
        from .task_bank import inspect
        checked = inspect(bank_path)
        actual = checked['content_grouping']['root_similarity']['evidence']
        if actual is None:
            raise ValueError('current bank lacks complete root similarity evidence')
        actual = project_evidence(actual, [(task, root) for task, root, _ in spec.tasks])
    if actual != supplied:
        raise ValueError('current bank differs from frozen root similarity evidence')
    return {root: group[0] for group in groups for root in group}


def grouped_weights(roots, weights, groups):
    """Sum weights within a candidate component, preserving endpoint total weight."""
    combined = defaultdict(float)
    for root, weight in zip(roots, weights):
        combined[groups.get(root, root)] += weight
    return list(combined.values())
