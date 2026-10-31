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
        if not all(isinstance(v, str) and 0 < len(v) <= 512 for v in
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
