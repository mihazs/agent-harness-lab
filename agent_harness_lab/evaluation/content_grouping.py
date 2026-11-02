"""Deterministic bounded content-exposure candidates for explicit local banks."""
import hashlib
import json
from .integrity import contained, digest, read_regular, tree, unique_object
from agent_harness_lab.sources.execution import RecordRef
from agent_harness_lab.sources.grouping import ALGORITHM, FIXTURE_ALGORITHM, MAX_BYTES, MAX_TOKENS, TextFingerprint, compare, text_fingerprint
from agent_harness_lab.sources.schema import digest as metadata_digest

MAX_BANK_BYTES = 32*1024*1024
MAX_RECEIPTS = 128


def fixture_content(root, expected, remaining):
    files = tree(root); normalized = {}; tokens = 0; total = 0; prompt = None
    for name, identity in files.items():
        raw = read_regular(contained(root, name), min(MAX_BYTES, remaining-total))
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
        if tokens > MAX_TOKENS: raise ValueError('fixture content token bound exceeded')
        normalized[name] = (kind, content.normalized_sha256)
        if name == 'task.json':
            if not isinstance(task, dict) or not isinstance(task.get('prompt'), str):
                raise ValueError('task JSON requires an explicit prompt')
            prompt = text_fingerprint(task['prompt'])
            if not prompt.tokens: raise ValueError('task prompt is empty')
    if digest(root) != expected: raise ValueError('fixture changed during content fingerprinting')
    return TextFingerprint(expected, metadata_digest(normalized), tokens, algorithm=FIXTURE_ALGORITHM), prompt, total


def inspect_bank(root, tasks, bank_sha256):
    buckets = {}; receipts = []; issues = []; identities = []; total = 0; overlaps = 0
    for task in sorted(tasks, key=lambda task:task.task_id):
        content, prompt, consumed = fixture_content(contained(root, task.fixture), task.fixture_digest, MAX_BANK_BYTES-total)
        total += consumed
        ref = RecordRef(bank_sha256, metadata_digest(task.task_id), task.fixture_digest)
        identities.append(dict(subject=ref.to_dict(), fixture=content.to_dict(), prompt=None if prompt is None else prompt.to_dict()))
        for kind, fingerprint in (('fixture', content), ('prompt', prompt)):
            if fingerprint is None: continue
            key = kind, fingerprint.normalized_sha256
            prior = buckets.setdefault(key, (task, ref, fingerprint))
            if prior[1] == ref: continue
            overlaps += 1
            receipt = compare(prior[1], ref, prior[2], fingerprint)
            if len(receipts) < MAX_RECEIPTS: receipts.append(dict(kind=kind, receipt=receipt.to_dict()))
            issues.append(dict(task=task.task_id, status='content_overlap_unreviewed',
                partition_boundary=prior[0].partition != task.partition, kind=kind,
                receipt_sha256=receipt.sha256))
    revision = metadata_digest(dict(algorithm=FIXTURE_ALGORITHM, bank_sha256=bank_sha256, fingerprints=identities))
    return dict(schema_version=1, algorithm=FIXTURE_ALGORITHM, revision_sha256=revision, checked_tasks=len(tasks),
        input_bytes=total, overlaps=overlaps, receipts=receipts, receipts_truncated=overlaps > MAX_RECEIPTS,
        independent_roots=None, lineage_verified=False, publication_allowed=False), issues
