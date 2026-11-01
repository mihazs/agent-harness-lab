"""Bounded, declarative reproduction proposals; never execute or admit a case."""
from dataclasses import dataclass
import hashlib
import json
import re
from .integrity import read_regular, unique_object
from .task_bank import identifier

DISPOSITIONS = {'reproduced', 'not_reproduced', 'environment_or_grader_defect',
                'unsupported', 'insufficient_evidence'}
OUTCOMES = {'task_failure', 'task_success', 'expected_rejection',
            'environment_or_grader_defect', 'unknown'}


def fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ValueError('reproduction fields must match the versioned schema')


def text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise ValueError('reproduction text must be nonempty, bounded and single-line')


def sha256(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('reproduction identity must be an exact lowercase SHA-256')


def references(value):
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError('reproduction reference list must be bounded')
    seen = set()
    for entry in value:
        fields(entry, 'ref sha256')
        text(entry['ref']); sha256(entry['sha256'])
        if entry['ref'] in seen:
            raise ValueError('duplicate reproduction reference')
        seen.add(entry['ref'])


def invalid_constant(value):
    raise ValueError('nonfinite reproduction JSON number')


@dataclass(frozen=True)
class RegressionProposal:
    """Parsed declarations are evidence to review, not verified reproduction."""
    value: dict

    @classmethod
    def parse(cls, value):
        fields(value, 'schema_version proposal_id root_id family mechanism source before after task_start environment expected_boundary reproduction')
        if type(value['schema_version']) is not int or value['schema_version'] != 1:
            raise ValueError('unsupported regression proposal version')
        for key in ('proposal_id', 'root_id', 'family'):
            identifier(value[key]); text(value[key])
        text(value['mechanism']); text(value['expected_boundary'])
        source = value['source']
        fields(source, 'kind permission evidence_refs trace_refs')
        if source['kind'] not in ('synthetic', 'public', 'private') or source['permission'] != 'approved':
            raise ValueError('regression proposals require explicitly approved source records')
        references(source['evidence_refs']); references(source['trace_refs'])
        for key in ('before', 'after'):
            snapshot = value[key]
            fields(snapshot, 'harness_sha256 config_sha256 memory_sha256 evidence_refs')
            references(snapshot['evidence_refs'])
            for name in ('harness_sha256', 'config_sha256', 'memory_sha256'):
                if snapshot[name] is not None:
                    sha256(snapshot[name])
                    if not snapshot['evidence_refs']:
                        raise ValueError('observed snapshot identities require evidence references')
        start = value['task_start']
        fields(start, 'ref sha256')
        if start != dict(ref=None, sha256=None):
            text(start['ref']); sha256(start['sha256'])
        environment = value['environment']
        fields(environment, 'image_sha256 runtime_ref requirements')
        if environment['image_sha256'] is not None: sha256(environment['image_sha256'])
        if environment['runtime_ref'] is not None: text(environment['runtime_ref'])
        if not isinstance(environment['requirements'], list) or not 0 < len(environment['requirements']) <= 32:
            raise ValueError('bounded environment requirements required')
        for requirement in environment['requirements']: text(requirement)
        reproduction = value['reproduction']
        fields(reproduction, 'disposition original_outcome replay_outcome run_refs boundary_refs reason')
        if (not isinstance(reproduction['disposition'], str) or reproduction['disposition'] not in DISPOSITIONS
                or any(not isinstance(reproduction[k], str) or reproduction[k] not in OUTCOMES
                       for k in ('original_outcome', 'replay_outcome'))):
            raise ValueError('unsupported reproduction disposition/outcome')
        references(reproduction['run_refs']); references(reproduction['boundary_refs'])
        text(reproduction['reason'])
        obj = cls(value)
        if reproduction['disposition'] == 'reproduced':
            if any(reproduction[k] != 'task_failure' for k in ('original_outcome', 'replay_outcome')):
                raise ValueError('recovered errors, expected rejections and unknown outcomes are not reproduced task failures')
            if obj.missing():
                raise ValueError('reproduced declaration requires recorded snapshots, runtime and boundary evidence')
        if (reproduction['disposition'] == 'environment_or_grader_defect'
                and 'environment_or_grader_defect' not in (reproduction['original_outcome'], reproduction['replay_outcome'])):
            raise ValueError('environment/grader disposition requires a matching outcome')
        # Detach caller-owned containers so later edits cannot change this receipt.
        return cls(json.loads(json.dumps(value, allow_nan=False)))

    def missing(self):
        value = self.value
        missing = [f'source.{key}' for key in ('evidence_refs', 'trace_refs') if not value['source'][key]]
        for role in ('before', 'after'):
            missing.extend(f'{role}.{key}' for key in ('harness_sha256', 'config_sha256', 'memory_sha256')
                           if value[role][key] is None)
        if value['task_start']['sha256'] is None: missing.append('task_start')
        missing.extend(f'environment.{key}' for key in ('image_sha256', 'runtime_ref') if value['environment'][key] is None)
        missing.extend(f'reproduction.{key}' for key in ('run_refs', 'boundary_refs') if not value['reproduction'][key])
        return missing

    def receipt(self, proposal_sha256):
        disposition = self.value['reproduction']['disposition']
        return dict(schema_version=1, proposal_sha256=proposal_sha256, lifecycle='reported',
                    declared_disposition=disposition, missing_evidence=self.missing(),
                    next_action='diagnostic_review' if disposition == 'environment_or_grader_defect' else 'reproduction_review',
                    privacy='private' if self.value['source']['kind'] == 'private' else 'public_or_synthetic_source',
                    evidence_verification='not_executed', task_failure_verified=False,
                    bank_admission=False, automatic_promotion=False, publication_allowed=False)


def load(path):
    raw = read_regular(path, 256 * 1024)
    try:
        value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except RecursionError:
        raise ValueError('reproduction JSON nesting limit exceeded') from None
    return RegressionProposal.parse(value), hashlib.sha256(raw).hexdigest()


def validate(path):
    proposal, identity = load(path)
    return proposal.receipt(identity)
