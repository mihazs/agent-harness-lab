"""Versioned lexical exposure evidence; content matches never prove shared cause."""
from dataclasses import dataclass
import hashlib
import re
import unicodedata
from .execution import RecordRef, frozen, unpack
from .schema import Contract, digest, fingerprint

ALGORITHM = 'nfc-word-punctuation-v1'
FIXTURE_ALGORITHM = 'fixture-path-json-nfc-v1'
MAX_BYTES = 1024*1024
MAX_TOKENS = 100000


@dataclass(frozen=True)
class TextFingerprint(Contract):
    exact_sha256: str
    normalized_sha256: str
    tokens: int
    algorithm: str = ALGORITHM
    schema_version: int = 1

    def __post_init__(self):
        self.validate(('exact_sha256', 'normalized_sha256'), {'algorithm':(ALGORITHM, FIXTURE_ALGORITHM)})
        if type(self.tokens) is not int or not 0 <= self.tokens <= MAX_TOKENS:
            raise ValueError('invalid bounded text token count')


def text_fingerprint(value):
    if not isinstance(value, str): raise ValueError('content fingerprint requires explicit text')
    try: raw = value.encode('utf-8')
    except UnicodeError: raise ValueError('content fingerprint requires valid UTF-8 text') from None
    if len(raw) > MAX_BYTES: raise ValueError('content fingerprint byte bound exceeded')
    tokens = []
    for token in re.finditer(r'\w+|[^\w\s]', unicodedata.normalize('NFC', value)):
        if len(tokens) >= MAX_TOKENS: raise ValueError('content fingerprint token bound exceeded')
        tokens.append(token.group())
    return TextFingerprint(hashlib.sha256(raw).hexdigest(), digest(tokens), len(tokens))


@dataclass(frozen=True)
class GroupingReceipt(Contract):
    left: RecordRef
    right: RecordRef
    left_content: TextFingerprint
    right_content: TextFingerprint
    content_relation: str
    relation: str
    evidence_class: str
    lineage_group_sha256: str | None = None
    reviewer_decision: str = 'pending'
    unresolved_collisions: tuple = ()
    invalidation_history: tuple = ()
    schema_version: int = 1

    def __post_init__(self):
        self.validate((), {'content_relation':('exact', 'normalized', 'different'),
            'relation':('similar_content', 'unlinked'), 'evidence_class':('heuristic_candidate', 'unlinked'),
            'reviewer_decision':('pending',)})
        if not all(isinstance(r, RecordRef) for r in (self.left, self.right)) or self.left.key == self.right.key:
            raise ValueError('content grouping requires two distinct exact source revisions')
        if not all(isinstance(f, TextFingerprint) for f in (self.left_content, self.right_content)):
            raise ValueError('content grouping requires typed fingerprints')
        left, right = self.left_content, self.right_content
        if left.algorithm != right.algorithm: raise ValueError('fingerprint algorithms are not comparable')
        expected = 'exact' if left.exact_sha256 == right.exact_sha256 else ('normalized' if left.normalized_sha256 == right.normalized_sha256 else 'different')
        linked = expected != 'different'
        if (self.content_relation != expected or self.relation != ('similar_content' if linked else 'unlinked')
                or self.evidence_class != ('heuristic_candidate' if linked else 'unlinked') or self.lineage_group_sha256 is not None):
            raise ValueError('content similarity cannot assert a causal lineage group')
        for values in (self.unresolved_collisions, self.invalidation_history):
            frozen(values, str, 64)
            for value in values: fingerprint(value)
            if len(set(values)) != len(values): raise ValueError('duplicate grouping history/collision')

    @classmethod
    def parse(cls, value):
        value = unpack(cls, value)
        for name in ('left', 'right'): value[name] = RecordRef.parse(value[name])
        for name in ('left_content', 'right_content'): value[name] = TextFingerprint.parse(value[name])
        for name in ('unresolved_collisions', 'invalidation_history'):
            if not isinstance(value[name], (list, tuple)): raise ValueError('invalid grouping metadata collection')
            value[name] = tuple(value[name])
        return cls(**value)

    @property
    def sha256(self): return digest(self.to_dict())


def compare(left, right, left_content, right_content, *, invalidation_history=()):
    relation = 'exact' if left_content.exact_sha256 == right_content.exact_sha256 else (
        'normalized' if left_content.normalized_sha256 == right_content.normalized_sha256 else 'different')
    linked = relation != 'different'
    return GroupingReceipt(left, right, left_content, right_content, relation,
        'similar_content' if linked else 'unlinked', 'heuristic_candidate' if linked else 'unlinked',
        unresolved_collisions=(digest(left.to_dict()), digest(right.to_dict())) if linked else (),
        invalidation_history=invalidation_history)
