"""Local lexical retrieval over explicitly approved redacted incidents."""
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
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
        if not self.incident_id or not self.root_id or not isinstance(self.summary,str) or not self.summary or not self.evidence_refs:
            raise ValueError('incident identity/evidence/summary missing')


class LexicalIndex:
    def __init__(self, incidents, max_records=None):
        if max_records is not None and (type(max_records) is not int or max_records<0):
            raise ValueError('nonnegative retrieval corpus limit required')
        if ((max_records is not None and len(incidents)>max_records)
                or len({i.incident_id for i in incidents})!=len(incidents)):
            raise ValueError('retrieval corpus limit or duplicate IDs')
        self.incidents=tuple(incidents)
        for incident in incidents: incident.validate()
        self.tokens=[Counter(re.findall(r'[a-z0-9_]+',i.summary.lower())) for i in incidents]
        self.revision=hashlib.sha256(json.dumps([i.__dict__ for i in incidents],sort_keys=True).encode()).hexdigest()

    def _ranked(self, text):
        query=set(re.findall(r'[a-z0-9_]+',text.lower()))
        scores=[]
        for incident,tokens in zip(self.incidents,self.tokens):
            score=sum(math.log1p(tokens[token]) for token in query if token in tokens)
            if score: scores.append((score,incident.incident_id,incident))
        return sorted(scores,key=lambda row:(-row[0],row[1]))

    def query(self, text, top_k=None, max_context=None):
        if (not isinstance(text,str)
                or any(value is not None and (type(value) is not int or value<1) for value in (top_k,max_context))):
            raise ValueError('invalid retrieval query or explicit limit')
        selected,total=[],0
        for score,_,incident in self._ranked(text)[:top_k]:
            if max_context is not None and total+len(incident.summary)>max_context: continue
            total+=len(incident.summary)
            selected.append(dict(incident_id=incident.incident_id,summary=incident.summary,
                                 evidence_refs=list(incident.evidence_refs),score=score,
                                 authority='untrusted_evidence_only'))
        return dict(revision=self.revision,results=selected,source='local_lexical',
                    calibration='not_a_correctness_oracle',private=any(i.source_kind=='private' for i in self.incidents))

    def contrast(self, text, hypothesis, control_labels=(), top_k=None):
        """Group lexical neighbours of one label hypothesis into evidence and counterexamples.

        ``top_k`` bounds each group separately, so supporting matches can never
        crowd out contradicting cases or controls. Private summaries are
        withheld; similarity is reported as a hypothesis, never a shared cause.
        """
        controls=tuple(control_labels)
        if (not isinstance(text,str) or not _label(hypothesis)
                or not all(_label(label) for label in controls) or hypothesis in controls
                or hypothesis in UNRESOLVED_LABELS or set(controls)&set(UNRESOLVED_LABELS)
                or (top_k is not None and (type(top_k) is not int or top_k<1))):
            raise ValueError('invalid contrast query, hypothesis, control labels or explicit limit')
        groups={name:[] for name in CONTRAST_GROUPS}
        for score,_,incident in self._ranked(text):
            labels=set(incident.labels)
            name=('unresolved' if not labels or labels&set(UNRESOLVED_LABELS)
                  else 'supporting' if hypothesis in labels
                  else 'controls' if labels&set(controls) else 'contradicting')
            groups[name].append((score,incident))
        matched={incident.incident_id for rows in groups.values() for _,incident in rows}
        labelled=[i for i in self.incidents if hypothesis in i.labels]
        report={}
        for name,rows in groups.items():
            report[name]=dict(matches=len(rows),distinct_roots=len({i.root_id for _,i in rows}),
                              results=[_contrast_row(score,incident) for score,incident in rows[:top_k]])
        support,against=groups['supporting'],groups['contradicting']
        shared={i.root_id for _,i in support}&{i.root_id for _,i in against}
        state=('no_lexical_support' if not support else 'mixed' if against else 'supporting_only')
        return dict(profile=CONTRAST_PROFILE,revision=self.revision,source='local_lexical',
                    hypothesis=hypothesis,control_labels=sorted(controls),evidence_state=state,
                    groups=report,shared_roots_supporting_and_contradicting=len(shared),
                    uncovered=dict(hypothesis_labelled=len(labelled),
                                   hypothesis_labelled_unmatched=sum(i.incident_id not in matched for i in labelled),
                                   unmatched_corpus=len(self.incidents)-len(matched)),
                    sampling=sorted({i.sampling for _,rows in groups.items() for _,i in rows}),
                    prevalence='not_estimable_from_lexical_neighbours',
                    diagnosis='hypothesis_only',causal_claim=False,
                    calibration='not_a_correctness_oracle',promotion_authority=False,
                    private=any(i.source_kind=='private' for i in self.incidents),
                    private_summaries='withheld')


CONTRAST_PROFILE='incident-contrast-v1'
CONTRAST_GROUPS=('supporting','contradicting','controls','unresolved')
UNRESOLVED_LABELS=('unknown','ambiguous')


def _label(value):
    return isinstance(value,str) and bool(value) and value==value.strip()


def _contrast_row(score, incident):
    """Provenance-bearing match; raw root IDs and private summaries never leave the index."""
    private=incident.source_kind=='private'
    return dict(incident_id=incident.incident_id,
                root_sha256=hashlib.sha256(incident.root_id.encode()).hexdigest(),
                labels=sorted(incident.labels),evidence_refs=list(incident.evidence_refs),score=score,
                summary=None if private else incident.summary,
                summary_withheld='private_source' if private else None,
                provenance=incident.provenance,sampling=incident.sampling,source_kind=incident.source_kind,
                authority='untrusted_evidence_only')


def triage_without_calibration(label_scores, calibration=None):
    """No held-out reviewed labels means no calibrated diagnostic prediction."""
    return dict(status='abstained',reason='independent_calibration_not_implemented_or_unavailable',
                suggested_labels=sorted(label_scores),promotion_authority=False)


def contextual_search(corpus, query):
    """Read only explicit inputs; reviewed approval remains a caller declaration."""
    if corpus is None and query is None:
        return None
    if corpus is None or not isinstance(query, str) or not query.strip():
        raise ValueError('--incident-corpus and nonempty --incident-query are required together')
    try:
        rows = json.loads(Path(corpus).read_text())
        if not isinstance(rows, list):
            raise ValueError()
        incidents = []
        for row in rows:
            if (not isinstance(row, dict) or row.get('approved') is not True
                    or any(not isinstance(row.get(key), str) or not row[key]
                           for key in ('incident_id','root_id','summary','reviewer','provenance','sampling','source_kind'))
                    or any(not isinstance(row.get(key), list)
                           or any(not isinstance(value, str) or not value for value in row[key])
                           for key in ('labels','evidence_refs'))):
                raise ValueError()
            incidents.append(Incident(**{**row, 'labels':tuple(row['labels']),
                                         'evidence_refs':tuple(row['evidence_refs'])}))
        result = LexicalIndex(incidents).query(query)
    except (TypeError, ValueError, UnicodeError):
        raise ValueError('incident corpus requires valid explicitly approved reviewed Incident records') from None
    marker = dict(profile='ahl-analysis-execution-v1', publication_allowed=False)
    return dict(result, **marker, input_records=len(incidents),
                query_sha256=hashlib.sha256(query.encode()).hexdigest(),
                approval='caller_declared_reviewed_approval_unverified',
                independence='unverified', promotion_authority=False,
                results=[dict(row, **marker) for row in result['results']])
