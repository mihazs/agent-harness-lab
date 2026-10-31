"""Bounded local lexical retrieval over explicitly approved redacted incidents."""
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
import re


@dataclass(frozen=True)
class Incident:
    incident_id: str
    root_id: str
    summary: str
    labels: tuple
    evidence_refs: tuple
    reviewer: str
    provenance: str
    sampling: str
    source_kind: str
    approved: bool

    def validate(self):
        if not self.approved or not self.reviewer or self.provenance != 'reviewed_annotation':
            raise ValueError('retrieval requires approved reviewed summaries')
        if self.source_kind not in ('synthetic','public','private') or self.sampling not in ('random','signal_enriched'):
            raise ValueError('unknown incident provenance/sampling')
        if not self.incident_id or not self.root_id or not 0 < len(self.summary) <= 4096 or not self.evidence_refs:
            raise ValueError('incident identity/evidence/summary missing or unbounded')


class LexicalIndex:
    def __init__(self, incidents, max_records=1000):
        if len(incidents)>max_records or len({i.incident_id for i in incidents})!=len(incidents):
            raise ValueError('retrieval corpus limit or duplicate IDs')
        self.incidents=tuple(incidents)
        for incident in incidents: incident.validate()
        self.tokens=[Counter(re.findall(r'[a-z0-9_]+',i.summary.lower())) for i in incidents]
        self.revision=hashlib.sha256(json.dumps([i.__dict__ for i in incidents],sort_keys=True).encode()).hexdigest()

    def query(self, text, top_k=5, max_context=8192):
        if not isinstance(text,str) or len(text)>4096 or not 1<=top_k<=20 or not 1<=max_context<=32768:
            raise ValueError('invalid bounded retrieval query')
        query=set(re.findall(r'[a-z0-9_]+',text.lower()))
        scores=[]
        for incident,tokens in zip(self.incidents,self.tokens):
            score=sum(math.log1p(tokens[token]) for token in query if token in tokens)
            if score: scores.append((score,incident.incident_id,incident))
        selected,total=[],0
        for score,_,incident in sorted(scores,key=lambda row:(-row[0],row[1]))[:top_k]:
            if total+len(incident.summary)>max_context: continue
            total+=len(incident.summary)
            selected.append(dict(incident_id=incident.incident_id,summary=incident.summary,
                                 evidence_refs=list(incident.evidence_refs),score=score,
                                 authority='untrusted_evidence_only'))
        return dict(revision=self.revision,results=selected,source='local_lexical',
                    calibration='not_a_correctness_oracle',private=any(i.source_kind=='private' for i in self.incidents))


def triage_without_calibration(label_scores, calibration=None):
    """No held-out reviewed labels means no calibrated diagnostic prediction."""
    return dict(status='abstained',reason='independent_calibration_not_implemented_or_unavailable',
                suggested_labels=sorted(label_scores),promotion_authority=False)
