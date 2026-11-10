"""Deterministic content-exposure candidates for explicit local banks."""
import hashlib
import json
from .integrity import contained, digest, read_regular, tree, unique_object
from agent_harness_lab.sources.execution import RecordRef
from agent_harness_lab.sources.grouping import ALGORITHM, FIXTURE_ALGORITHM, TextFingerprint, compare, text_fingerprint
from agent_harness_lab.sources.schema import digest as metadata_digest



def fixture_content(root, expected, remaining=None):
    limits = dict(max_files=None, max_bytes=None, max_entries=None, max_depth=None)
    files = tree(root, **limits); normalized = {}; tokens = 0; total = 0; prompt = None
    for name, identity in files.items():
        raw = read_regular(contained(root, name), None if remaining is None else remaining-total)
        total += len(raw)
        if hashlib.sha256(raw).hexdigest() != identity['sha256']:
            raise ValueError('fixture changed during content fingerprinting')
        try: text = raw.decode('utf-8')
        except UnicodeError:
            if name.endswith('.json'): raise ValueError('fixture JSON must be UTF-8') from None
            normalized[name] = ('binary-sha256-v1', identity['sha256']); continue
        task = None; kind = ALGORITHM
        if name.endswith('.json'):
            try:
                task = json.loads(text, object_pairs_hook=unique_object)
                text = json.dumps(task, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
                kind = 'json-'+ALGORITHM
            except (ValueError, RecursionError):
                raise ValueError('fixture JSON is invalid or ambiguous') from None
        content = text_fingerprint(text); tokens += content.tokens
        normalized[name] = (kind, content.normalized_sha256)
        if name == 'task.json':
            if not isinstance(task, dict) or not isinstance(task.get('prompt'), str):
                raise ValueError('task JSON requires an explicit prompt')
            prompt = text_fingerprint(task['prompt'])
            if not prompt.tokens: raise ValueError('task prompt is empty')
    if digest(root, **limits) != expected: raise ValueError('fixture changed during content fingerprinting')
    return TextFingerprint(expected, metadata_digest(normalized), tokens, algorithm=FIXTURE_ALGORITHM), prompt, total


def inspect_bank(root, tasks, bank_sha256, *, similarity_algorithm=None):
    from .root_similarity import ALGORITHM, ALGORITHMS, shingles
    algorithm = ALGORITHM if similarity_algorithm is None else similarity_algorithm
    if algorithm not in ALGORITHMS: raise ValueError('unsupported root similarity algorithm')
    buckets = {}; receipts = []; issues = []; identities = []; total = 0; overlaps = 0; prompts = []
    for task in sorted(tasks, key=lambda task:task.task_id):
        content, prompt, consumed = fixture_content(contained(root, task.fixture), task.fixture_digest, None)
        total += consumed
        if prompt is not None:
            fixture = contained(root, task.fixture)
            raw = read_regular(contained(fixture, 'task.json'), None)
            parsed = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object)
            if text_fingerprint(parsed['prompt']) != prompt or digest(fixture, max_files=None, max_bytes=None, max_entries=None, max_depth=None) != task.fixture_digest:
                raise ValueError('fixture changed during similarity inspection')
            if shingles(parsed['prompt'], algorithm=algorithm):
                prompts.append((task.root_id, parsed['prompt']))
        ref = RecordRef(bank_sha256, metadata_digest(task.task_id), task.fixture_digest)
        identities.append(dict(subject=ref.to_dict(), fixture=content.to_dict(), prompt=None if prompt is None else prompt.to_dict()))
        for kind, fingerprint in (('fixture', content), ('prompt', prompt)):
            if fingerprint is None: continue
            key = kind, fingerprint.normalized_sha256
            prior = buckets.setdefault(key, (task, ref, fingerprint))
            if prior[1] == ref: continue
            overlaps += 1
            receipt = compare(prior[1], ref, prior[2], fingerprint)
            receipts.append(dict(kind=kind, receipt=receipt.to_dict()))
            issues.append(dict(task=task.task_id, status='content_overlap_unreviewed',
                partition_boundary=prior[0].partition != task.partition, kind=kind,
                receipt_sha256=receipt.sha256))
    revision = metadata_digest(dict(algorithm=FIXTURE_ALGORITHM, bank_sha256=bank_sha256, fingerprints=identities))
    from .root_similarity import evidence as similarity_evidence, suspected_components
    roots_available = all(hasattr(t, 'root_id') for t in tasks)
    similarity = similarity_evidence(bank_sha256, revision, [(t.task_id, t.root_id) for t in tasks], prompts, algorithm=algorithm) if roots_available and len(prompts) == len(tasks) else None
    roots = {t.root_id for t in tasks} if roots_available else set()
    groups = suspected_components(prompts, algorithm=algorithm) if similarity is None else similarity['groups']
    if roots_available and similarity is None:
        issues.append(dict(status='independence_unverified', reason='prompt_similarity_incomplete'))
    flagged = {root for group in groups for root in group}
    issues.extend(dict(task=t.task_id, status='independence_unverified', reason='suspected_near_duplicate')
                  for t in tasks if roots_available and t.root_id in flagged)
    return dict(schema_version=1, algorithm=FIXTURE_ALGORITHM, revision_sha256=revision, checked_tasks=len(tasks),
        root_similarity=dict(status='complete' if similarity is not None else 'incomplete',
            uninspected_tasks=len(tasks)-len(prompts),
            nominal_roots=len(roots) if roots_available else None, effective_roots=None if similarity is None else len(roots) - sum(len(g)-1 for g in groups),
            evidence=similarity, evidence_sha256=None if similarity is None else metadata_digest(similarity),
            independence='unverified'),
        input_bytes=total, overlaps=overlaps, receipts=receipts, receipts_truncated=False,
        independent_roots=None, lineage_verified=False, publication_allowed=False), issues
