"""Versioned mechanism coverage, with hypotheses separate from validated repairs."""
from dataclasses import asdict, dataclass, field
import hashlib
import json

COMPONENTS = {'instruction', 'skill', 'tool_adapter', 'state_recovery',
              'runtime_or_tool_defect', 'evaluator', 'insufficient_evidence', 'no_change'}
DISPOSITIONS = {'proposed', 'evaluating', 'recommended', 'validated', 'rejected',
                'already_covered', 'outside_scope', 'deferred'}


@dataclass
class MechanismRecord:
    mechanism_id: str
    symptom: str
    evidence: list
    evidence_version: str
    component_version: str
    component: str = 'insufficient_evidence'
    exposure: int | None = None
    severity: str = 'normal'
    controls: list = field(default_factory=list)
    counterexamples: list = field(default_factory=list)
    competing_explanations: list = field(default_factory=list)
    boundary: str | None = None
    falsifier: str | None = None
    disposition: str = 'proposed'
    reason: str | None = None
    history: list = field(default_factory=list)

    def validate(self):
        if not all(isinstance(v, str) and v for v in
                   (self.mechanism_id, self.symptom, self.evidence_version, self.component_version)):
            raise ValueError('invalid mechanism identity/provenance')
        if self.component not in COMPONENTS or self.disposition not in DISPOSITIONS or self.severity not in ('critical','high','normal'):
            raise ValueError('invalid mechanism component/disposition/severity')
        if self.exposure is not None and (type(self.exposure) is not int or self.exposure < 0):
            raise ValueError('invalid exposure denominator')
        if not all(isinstance(v, list) for v in (self.evidence,self.controls,self.counterexamples,self.competing_explanations,self.history)):
            raise ValueError('mechanism evidence must be lists')
        return self

    @property
    def key(self):
        self.validate()
        return hashlib.sha256(json.dumps([self.mechanism_id,self.evidence_version,self.component_version],sort_keys=True).encode()).hexdigest()

    @property
    def actionable(self):
        return bool(self.evidence and self.boundary and self.falsifier and self.competing_explanations)

    def transition(self, disposition, reason):
        if disposition not in DISPOSITIONS or not isinstance(reason,str) or not reason:
            raise ValueError('disposition needs an explicit reason')
        self.history.append(dict(previous=self.disposition, disposition=disposition, reason=reason))
        self.disposition, self.reason = disposition, reason
        return self

    def as_dict(self):
        self.validate()
        return dict(schema_version=1, **asdict(self))



# Untrusted proposer output admission. Admitted records are hypotheses: the
# proposer cannot choose a reviewed route, a disposition, provenance versions or
# evidence the controller did not supply. Bounds are explicit and reported in the
# receipt; None means unlimited. Oversized or malformed entries are rejected with
# a reason, never truncated, upgraded or silently dropped.
ADMISSION_PROFILE = 'proposer-mechanism-admission-v1'
ADMITTED_STATUS = 'proposed_unverified'
SEVERITIES = ('critical', 'high', 'normal')
DEFAULT_PROPOSAL_LIMITS = dict(mechanisms=32, falsifiers=64, list_items=64, text=4096, identifier=128)
_MECHANISM_FIELDS = frozenset({'mechanism_id', 'symptom', 'evidence_refs', 'component', 'severity', 'exposure',
                               'controls', 'counterexamples', 'competing_explanations', 'boundary', 'falsifier'})
_AUTHORITY_FIELDS = frozenset({'disposition', 'status', 'reason', 'history', 'key', 'reviewed_component', 'route',
                               'validated', 'verified', 'diagnosed', 'evidence_version', 'component_version'})
_REF_LISTS = ('evidence_refs', 'controls', 'counterexamples')
_ID_CHARS = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-')


@dataclass(frozen=True)
class ProposalAdmission:
    """Admitted unverified records plus the JSON-safe private admission receipt."""
    records: tuple
    receipt: dict

    @property
    def status(self):
        return self.receipt['status']


def _limits(limits):
    merged = dict(DEFAULT_PROPOSAL_LIMITS)
    if limits is not None:
        if not isinstance(limits, dict) or set(limits) - set(merged):
            raise ValueError('unknown proposal limit')
        merged.update(limits)
    for value in merged.values():
        if value is not None and (type(value) is not int or value < 1):
            raise ValueError('proposal limits must be positive integers or None')
    return merged


def _within(value, bound):
    return bound is None or len(value) <= bound


def _identifier(value, bound):
    return (isinstance(value, str) and bool(value) and _within(value, bound)
            and value[0].isascii() and value[0].isalnum() and set(value) <= _ID_CHARS)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _mechanism(item, refs, limits):
    """Return (normalized, None) or (None, reason) for one proposer mechanism."""
    if not isinstance(item, dict) or not all(isinstance(name, str) for name in item):
        return None, 'mechanism_not_object'
    authority = sorted(set(item) & _AUTHORITY_FIELDS)
    if authority:
        return None, 'authority_field_not_permitted:' + authority[0]
    if set(item) - _MECHANISM_FIELDS:
        return None, 'unknown_field'
    if not _identifier(item.get('mechanism_id'), limits['identifier']):
        return None, 'invalid_mechanism_id'
    value = dict(mechanism_id=item['mechanism_id'])
    for name, required in (('symptom', True), ('boundary', False), ('falsifier', False)):
        text = item.get(name)
        if text is None:
            if required:
                return None, 'missing_required_field:' + name
            value[name] = None
        elif not _text(text):
            return None, 'invalid_field_type:' + name
        elif not _within(text, limits['text']):
            return None, 'text_bound_exceeded:' + name
        else:
            value[name] = text
    for name in (*_REF_LISTS, 'competing_explanations'):
        rows = item.get(name, [])
        if not isinstance(rows, list):
            return None, 'invalid_field_type:' + name
        if not _within(rows, limits['list_items']):
            return None, 'list_bound_exceeded:' + name
        for row in rows:
            if name in _REF_LISTS:
                if not _identifier(row, limits['identifier']):
                    return None, 'invalid_reference:' + name
                if row not in refs:
                    return None, 'unknown_evidence_reference:' + name
            elif not _text(row):
                return None, 'invalid_field_type:' + name
            elif not _within(row, limits['text']):
                return None, 'text_bound_exceeded:' + name
        if len(set(rows)) != len(rows):
            return None, 'duplicate_list_item:' + name
        value[name] = list(rows)
    component, severity = item.get('component', 'insufficient_evidence'), item.get('severity', 'normal')
    if not isinstance(component, str) or component not in COMPONENTS:
        return None, 'unknown_component'
    if not isinstance(severity, str) or severity not in SEVERITIES:
        return None, 'unknown_severity'
    exposure = item.get('exposure')
    if exposure is not None and (type(exposure) is not int or exposure < 0):
        return None, 'invalid_exposure'
    value.update(component=component, severity=severity, exposure=exposure)
    return value, None


def _content(value):
    """Canonical content excluding identity; set-like lists compare unordered."""
    return json.dumps({k: sorted(v) if isinstance(v, list) else v for k, v in value.items()
                       if k != 'mechanism_id'}, sort_keys=True)


def _location_order(row):
    head, _, tail = row['location'].partition('[')
    return head, int(tail[:-1]) if tail else -1, row['reason']


def from_proposal(proposal, *, evidence_refs, evidence_version, component_version, limits=None):
    """Admit untrusted proposer JSON as unverified MechanismRecords.

    Pure and total over ``proposal``: any value yields a ProposalAdmission whose
    receipt lists admitted entries (status ``proposed_unverified``) and every
    rejected location with a reason. Admitted records keep disposition
    ``proposed`` and the unreviewed ``insufficient_evidence`` component; the
    proposer's claim is only ``proposed_component``. ``evidence_refs`` is the
    controller's allowlist of citable evidence/case identifiers. Invalid
    controller arguments raise ValueError.
    """
    refs = frozenset(evidence_refs)
    if not all(isinstance(ref, str) and ref for ref in refs):
        raise ValueError('evidence references must be non-empty strings')
    if not all(isinstance(v, str) and v for v in (evidence_version, component_version)):
        raise ValueError('controller evidence/component versions are required')
    limits = _limits(limits)
    rejected, entries, records = [], [], []

    def reject(location, reason, mechanism_id=None):
        rejected.append(dict(location=location, mechanism_id=mechanism_id, reason=reason))

    def finish(status):
        rejected.sort(key=_location_order)
        body = dict(schema_version=1, profile=ADMISSION_PROFILE, publication_allowed=False,
                    authority='none', diagnosis_established=False, validation_established=False,
                    status=status, evidence_version=evidence_version, component_version=component_version,
                    limits=limits, entries=entries, rejected=rejected,
                    mechanisms=[record.as_dict() for record in records])
        body['sha256'] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        return ProposalAdmission(tuple(records), body)

    if not isinstance(proposal, dict) or not all(isinstance(name, str) for name in proposal):
        reject('proposal', 'proposal_not_object')
        return finish('rejected')
    status = proposal.get('status')
    mechanisms, falsifiers = proposal.get('mechanisms', []), proposal.get('falsifiers', [])
    if status == 'no_change':
        if mechanisms or falsifiers:
            reject('proposal', 'no_change_with_mechanisms')
            return finish('rejected')
        return finish('no_change')
    if status != 'candidate':
        reject('proposal.status', 'unsupported_proposal_status')
        return finish('rejected')
    for _ in sorted(set(proposal) - {'status', 'mechanisms', 'falsifiers'}):
        reject('proposal', 'unknown_field')
    for name, rows in (('mechanisms', mechanisms), ('falsifiers', falsifiers)):
        if not isinstance(rows, list):
            reject('proposal.' + name, 'invalid_field_type:' + name)
            return finish('rejected')
        if not _within(rows, limits[name]):
            # Never admit a prefix: the whole proposal is outside the declared bound.
            reject('proposal.' + name, 'count_bound_exceeded:' + name)
            return finish('rejected')
    if not mechanisms:
        reject('proposal.mechanisms', 'candidate_without_mechanisms')
        return finish('rejected')

    parsed, named = [], set()
    for index, item in enumerate(mechanisms):
        candidate = item.get('mechanism_id') if isinstance(item, dict) else None
        candidate = candidate if _identifier(candidate, limits['identifier']) else None
        if candidate is not None:
            named.add(candidate)
        value, reason = _mechanism(item, refs, limits)
        if reason:
            reject(f'mechanisms[{index}]', reason, candidate)
            named.add(('malformed', candidate))
        else:
            parsed.append((index, value))
    variants = {}
    for _, value in parsed:
        variants.setdefault(value['mechanism_id'], set()).add(_content(value))
    # Differing content, or a malformed variant, under one identity is contradictory.
    contradictory = {name for name, contents in variants.items()
                     if len(contents) > 1 or ('malformed', name) in named}
    seen_content, admitted = {}, {}
    for index, value in parsed:
        name, content = value['mechanism_id'], _content(value)
        if name in contradictory:
            reject(f'mechanisms[{index}]', 'contradictory_mechanism', name)
        elif name in admitted:
            reject(f'mechanisms[{index}]', 'duplicate_mechanism', name)
        elif content in seen_content:
            reject(f'mechanisms[{index}]', 'duplicate_mechanism_content:' + seen_content[content], name)
        else:
            seen_content[content] = name
            admitted[name] = dict(index=index, value=value, falsifiers={value['falsifier']} - {None},
                                  sources=['inline'] if value['falsifier'] else [])
    for index, item in enumerate(falsifiers):
        location = f'falsifiers[{index}]'
        owner = item.get('mechanism_id') if isinstance(item, dict) else None
        owner = owner if _identifier(owner, limits['identifier']) else None
        if not isinstance(item, dict) or not all(isinstance(k, str) for k in item):
            reject(location, 'falsifier_not_object')
        elif set(item) != {'mechanism_id', 'falsifier'}:
            reject(location, 'invalid_falsifier_fields', owner)
        elif owner is None:
            reject(location, 'invalid_mechanism_id')
        elif not _text(item['falsifier']):
            reject(location, 'invalid_field_type:falsifier', owner)
        elif not _within(item['falsifier'], limits['text']):
            reject(location, 'text_bound_exceeded:falsifier', owner)
        elif owner not in named:
            reject(location, 'falsifier_for_unknown_mechanism', owner)
        elif owner not in admitted:
            reject(location, 'falsifier_for_unadmitted_mechanism', owner)
        else:
            admitted[owner]['falsifiers'].add(item['falsifier'])
            admitted[owner]['sources'].append(location)
    for name, row in admitted.items():
        value, location = row['value'], f"mechanisms[{row['index']}]"
        if len(row['falsifiers']) > 1:
            # A record carries one falsifier; choosing among several would be silent selection.
            reject(location, 'multiple_distinct_falsifiers', name)
            for source in row['sources']:
                if source != 'inline':
                    reject(source, 'falsifier_for_unadmitted_mechanism', name)
            continue
        record = MechanismRecord(
            name, value['symptom'], list(value['evidence_refs']), evidence_version, component_version,
            component='insufficient_evidence', exposure=value['exposure'], severity=value['severity'],
            controls=list(value['controls']), counterexamples=list(value['counterexamples']),
            competing_explanations=list(value['competing_explanations']), boundary=value['boundary'],
            falsifier=next(iter(row['falsifiers']), None)).validate()
        records.append(record)
        entries.append(dict(mechanism_id=name, location=location, status=ADMITTED_STATUS,
                            proposed_component=value['component'], falsifier_sources=row['sources'],
                            actionable_if_reviewed=record.actionable))
    return finish('rejected' if not records else 'partially_admitted' if rejected else 'admitted')
