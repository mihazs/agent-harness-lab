"""Immutable private review plans. Identity is never application authority."""
from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path, PurePosixPath
import re

from .private_journal import encoded
from .surfaces import HOOK_FACET, classify, unresolved_kinds

PROFILE = 'ahl-harness-change-plan-v1'
REFERENCE_NAMES=frozenset(('project','configuration','analysis','evidence','runtime','policy','authorization'))
# Kinds without an adoptable classifier row (hooks, workflows, exec policy, config) stay unresolved.
UNRESOLVED_KINDS=unresolved_kinds()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def _sha(value):
    if not isinstance(value,str) or re.fullmatch('[0-9a-f]{64}',value) is None:
        raise ValueError('plan reference must be an exact SHA-256')
    return value


@dataclass(frozen=True)
class FileRevision:
    path: str
    sha256: str
    mode: int

    def __post_init__(self):
        path=PurePosixPath(self.path)
        if not self.path or len(self.path)>4096 or self.path=='.' or '\\' in self.path or path.is_absolute() or str(path)!=self.path or '..' in path.parts or any(ord(c)<32 for c in self.path):
            raise ValueError('invalid plan-relative file path')
        _sha(self.sha256)
        if type(self.mode) is not int or not 0<=self.mode<=0o7777:raise ValueError('invalid plan mode')


def inventory(states):
    if len(states)>1000:raise ValueError('plan inventory exceeds file bound')
    return tuple(FileRevision(name,*state) for name,state in sorted(states.items()))


@dataclass(frozen=True)
class Operation:
    path: str
    action: str
    surface: str
    facets: tuple
    qualification: str = 'declared_not_activation_evidence'


def _operation(path,before,after):
    mode=0
    for state in (before,after):
        if state:mode|=state[1]
    surface=classify(path,None,mode)
    facets=tuple(name for name in ('instruction','skill',HOOK_FACET) if name in surface.facets)
    return Operation(path,'create' if before is None else 'remove' if after is None else 'update',surface.kind,facets)


@dataclass(frozen=True)
class HarnessChangePlan:
    before: tuple
    after: tuple
    references: tuple
    diagnosis_sha256: str
    falsifiers_sha256: str
    dependencies_sha256: str
    affected_controls_sha256: str
    deferred_surfaces: tuple
    # (sha256, status, members) of each hook_contract_v1 verdict; a passed one resolves its members' operations.
    hook_contracts: tuple = ()

    def __post_init__(self):
        for rows in (self.before,self.after):
            if len(rows)>1000:raise ValueError('plan inventory exceeds file bound')
            if type(rows) is not tuple or any(type(row) is not FileRevision for row in rows) or rows!=tuple(sorted(set(rows),key=lambda row:row.path)) or len({r.path for r in rows})!=len(rows):
                raise ValueError('plan inventory must be unique ordered immutable revisions')
        if type(self.references) is not tuple or any(type(row) is not tuple or len(row)!=2 or not isinstance(row[0],str) or not row[0] for row in self.references):
            raise ValueError('invalid immutable plan references')
        if self.references!=tuple(sorted(set(self.references))) or len(dict(self.references))!=len(self.references):raise ValueError('duplicate plan reference')
        if set(dict(self.references))!=REFERENCE_NAMES:raise ValueError('missing or unknown plan references')
        for _,value in self.references:
            if value is not None:_sha(value)
        for value in (self.diagnosis_sha256,self.falsifiers_sha256,self.dependencies_sha256,self.affected_controls_sha256):_sha(value)
        if type(self.deferred_surfaces) is not tuple or any(not isinstance(v,str) or not v or len(v)>128 for v in self.deferred_surfaces):raise ValueError('invalid deferred surfaces')
        from .hooks_contract import STATUSES
        if type(self.hook_contracts) is not tuple or self.hook_contracts!=tuple(sorted(set(self.hook_contracts))) or any(
                type(row) is not tuple or len(row)!=3 or _sha(row[0]) is None or row[1] not in STATUSES or type(row[2]) is not tuple
                or not row[2] or row[2]!=tuple(sorted(set(row[2]))) or any(not isinstance(v,str) for v in row[2]) for row in self.hook_contracts):
            raise ValueError('invalid hook contract references')

    @classmethod
    def create(cls,before,after,*,references,diagnosis,falsifiers,dependencies,controls,deferred,contracts=()):
        return cls(inventory(before),inventory(after),tuple(sorted(references.items())),
                   *(digest(value) for value in (diagnosis,falsifiers,dependencies,controls)),tuple(deferred),
                   tuple(sorted({(row.sha256,row.status,tuple(row.members)) for row in contracts})))

    @property
    def operations(self):
        before={r.path:(r.sha256,r.mode) for r in self.before};after={r.path:(r.sha256,r.mode) for r in self.after}
        return tuple(_operation(name,before.get(name),after.get(name)) for name in sorted(before.keys()|after.keys()) if before.get(name)!=after.get(name))

    @property
    def unresolved_operations(self):
        # A kind no classifier row adopts, or any hook facet (agent definitions, executables), stays unresolved unless
        # a passed hook contract names it: contract_required resolves only through hook_contract_v1.
        resolved={path for _,status,members in self.hook_contracts if status=='passed' for path in members}
        return tuple(row.path for row in self.operations if row.path not in resolved
                     and (row.surface in UNRESOLVED_KINDS or 'hook_contract_required' in row.facets))

    def document(self):
        value=asdict(self)
        for name in ('before','after'):value[name]=[asdict(row) for row in getattr(self,name)]
        value.update(schema_version=1,profile=PROFILE,publication_allowed=False,adoption_authority=False,
            reference_status='declared_inputs_not_authority',operations=[asdict(row) for row in self.operations],
            unresolved_operations=self.unresolved_operations,
            disposition='no_change' if not self.operations else 'review_required',
            before_inventory_sha256=digest(value['before']),after_inventory_sha256=digest(value['after']))
        if not self.hook_contracts:value.pop('hook_contracts')  # plans without a contract keep their identity
        # JSON-compatible values only, detached from the immutable object.
        import json
        return json.loads(encoded(value))

    @property
    def sha256(self):return digest(self.document())

    @classmethod
    def parse(cls,value):
        try:
            plan=cls(tuple(FileRevision(**row) for row in value['before']),tuple(FileRevision(**row) for row in value['after']),
                tuple(tuple(row) for row in value['references']),*(value[name] for name in ('diagnosis_sha256','falsifiers_sha256','dependencies_sha256','affected_controls_sha256')),tuple(value['deferred_surfaces']),
                tuple((row[0],row[1],tuple(row[2])) for row in value.get('hook_contracts',())))
            if plan.document()!=value:raise ValueError('plan derived fields changed')
            return plan
        except (KeyError,TypeError,AttributeError):raise ValueError('invalid harness change plan') from None

    def validate_inventories(self,before,after):
        if inventory(before)!=self.before or inventory(after)!=self.after:raise ValueError('frozen plan inventory changed')


def produce(args,before,after,*,analysis,evidence,diagnosis,falsifiers,controls,deferred,contracts=()):
    """Bind actual producer revisions; these references confer no authority."""
    from .promotion import policy
    runtime={name:str(getattr(args,name,None)) for name in ('agent','codex','claude','inference_settings','image_sha256','disk_bytes','permission_audit')}
    runtime['configuration_references']=getattr(args,'_response_references',None)
    inference=getattr(args,'inference_settings',None)
    if inference is not None:
        from .evaluation.responses import read_settings
        runtime['inference_settings_sha256']=read_settings(inference)[1]
    references=dict(project=digest(str(args.project)),configuration=getattr(args,'configuration_sha256',None),
        analysis=digest(analysis),evidence=digest(evidence),runtime=digest(runtime),
        policy=hashlib.sha256(Path(policy.__file__).read_bytes()).hexdigest(),
        authorization=digest(dict(scope=getattr(args,'project_settings',None),repair_contracts=getattr(args,'repair_contracts',False))))
    return HarnessChangePlan.create(before,after,references=references,diagnosis=diagnosis,falsifiers=falsifiers,
        dependencies=['current_configuration','frozen_inventories','existing_application_policy'],controls=controls,deferred=deferred,
        contracts=contracts)


def attach(document,plan):
    document.update(harness_change_plan=plan.document(),plan_sha256=plan.sha256)
    return document
