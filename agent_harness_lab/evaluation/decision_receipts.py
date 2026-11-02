"""Private controller-recomputed analysis evidence, never adoption authority."""
import hashlib
import os
from pathlib import Path
import platform
import secrets
import stat
import sys

from agent_harness_lab.private_journal import encoded,record,sync_directory,write_bytes
from .integrity import contained,read_regular
from .regressions import sha256

MAX_BYTES = 2*1024*1024
MAX_ANALYSIS_BYTES = 16*1024*1024
MAX_FILES = 16
MAX_TOTAL_BYTES = 16*1024*1024
ALGORITHM = 'private-preservation-decision-v1'


def hashed(raw): return hashlib.sha256(raw).hexdigest()


def engine():
    """Conservative replay identity; different engines require regeneration."""
    from agent_harness_lab.cli import evaluator
    # Authority validators are transitive dependencies of the numeric result.
    root=Path(__file__).parents[1]
    sources,total={},0
    for path in root.rglob('*.py'):
        if len(sources)>=512: raise ValueError('analysis engine source count bound exceeded')
        raw=read_regular(path,min(1024*1024,8*1024*1024-total))
        total+=len(raw); sources[str(path.relative_to(root))]=hashed(raw)
    sources['typed_grading_controller']=hashed(read_regular(evaluator().__file__,1024*1024))
    return dict(algorithm=ALGORITHM,code=sources,python=dict(implementation=sys.implementation.name,
        version=sys.version,platform=sys.platform,machine=platform.machine(),libc=list(platform.libc_ver())))


def build(store,run_id):
    checked=store.checked(run_id)
    case,events,inputs,spec,result,validity=checked
    if result is None or validity!='current':
        raise ValueError('decision evidence requires a sealed currently applicable controller run')
    implementation=engine()
    analysis=store._analyze_checked(checked)
    analysis_raw=encoded(analysis)
    if len(analysis_raw)>MAX_ANALYSIS_BYTES: raise ValueError('decision analysis byte bound exceeded')
    membership=None
    if spec.cohort_manifest_sha256 is not None:
        membership,binding_hash=record(case/'cohort-binding.json',1024*1024)
        raw=read_regular(case/'cohort-manifest.json',1024*1024)
        if binding_hash!=inputs['cohort_binding_sha256'] or hashed(raw)!=spec.cohort_manifest_sha256:
            raise ValueError('decision cohort identity changed')
    preservation=analysis.get('preservation',{})
    endpoints={name:dict(required=value['required'],disposition=value['disposition'])
        for name,value in preservation.get('cohorts',{}).items()}
    payload=dict(schema_version=1,algorithm=ALGORITHM,run_id=run_id,event_sha256=events[-1][1],
        inputs_sha256=record(case/'inputs.json')[1],evidence_sha256=record(case/'evidence.json')[1],
        results_sha256=record(case/'execution/results.json',8*1024*1024)[1],
        manifest_sha256=inputs['source_manifest_sha256'],plan_sha256=spec.digest,
        baseline_sha256=spec.baseline_hash,candidate_sha256=spec.candidate_hash,
        oracle_sha256=spec.grader_hash,runtime_sha256=spec.runtime_hash,
        oracle_dependencies=inputs['dependencies'],bank_authority=inputs.get('bank_authority'),
        bank_authority_status='current_eligible' if inputs.get('bank_tasks') is not None else 'unverified',
        cohort_manifest_sha256=spec.cohort_manifest_sha256,
        cohort_binding_sha256=inputs.get('cohort_binding_sha256'),
        qualification_dependencies=membership['qualification_bindings'] if membership else {},
        analysis_engine=implementation,analysis_sha256=hashed(analysis_raw),
        analysis_disposition=dict(status=analysis['status'],reason=analysis.get('reason'),required_endpoints=endpoints,
            coverage=preservation.get('descriptive',{}).get('coverage'),
            critical_veto=analysis.get('reason')=='critical_regression',
            behavior_required_observation_unknown=analysis.get('behavior',{}).get('required_observation_unknown')),
        evidence_scope='controller_recomputed_analysis_only',native_identity='unverified',
        reviewer_identity='operator_attested_unverified' if membership else 'unverified',
        independence='unverified',publication_allowed=False,automatic_promotion=False,adoption_authority=False)
    raw=encoded(payload)
    if len(raw)>MAX_BYTES: raise ValueError('decision receipt byte bound exceeded')
    # Numeric work and metadata reads must not leave stale captured eligibility.
    if store.checked(run_id)!=checked or engine()!=implementation:
        raise ValueError('decision authority or analysis engine changed during recomputation')
    return payload,raw,analysis


def private(path,directory=False):
    info=path.lstat()
    if (info.st_uid!=os.geteuid() or info.st_mode&0o077
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or not directory and info.st_nlink!=1):
        raise ValueError('decision evidence must remain private and regular')


def folder(store,run_id,*,create=False):
    path=contained(store.case(run_id),'decisions')
    if create and not path.exists():
        path.mkdir(mode=0o700); sync_directory(path.parent)
    private(path,directory=True)
    return path


def inventory(path):
    total=0; names=set()
    with os.scandir(path) as entries:
        for entry in entries:
            if len(names)>=MAX_FILES: raise ValueError('decision receipt count bound exceeded')
            name=entry.name
            if not name.endswith('.json'): raise ValueError('unexpected or interrupted decision evidence')
            sha256(name[:-5]); target=contained(path,name); private(target)
            raw=read_regular(target,MAX_BYTES)
            if hashed(raw)!=name[:-5]: raise ValueError('decision receipt content identity changed')
            total+=len(raw); names.add(name)
            if total>MAX_TOTAL_BYTES: raise ValueError('decision receipt total byte bound exceeded')
    return names,total


def view(payload,raw,analysis):
    return dict(receipt_sha256=hashed(raw),receipt=payload,analysis=analysis,
        verification='current_recomputed',publication_allowed=False,automatic_promotion=False,adoption_authority=False)


def create(store,run_id):
    payload,raw,analysis=build(store,run_id)
    path=folder(store,run_id,create=True); names,total=inventory(path)
    name=hashed(raw)+'.json'; target=contained(path,name)
    if name in names:
        if read_regular(target,MAX_BYTES)!=raw: raise ValueError('immutable decision receipt changed')
    else:
        if len(names)>=MAX_FILES or total+len(raw)>MAX_TOTAL_BYTES:
            raise ValueError('decision receipt retention bound exceeded')
        temporary=contained(path,'.pending-'+secrets.token_hex(12))
        try:
            write_bytes(temporary,raw)
            os.link(temporary,target,follow_symlinks=False)
            temporary.unlink(); sync_directory(path)
        finally:
            if temporary.exists(): temporary.unlink()
    return verify(store,run_id,hashed(raw))


def verify(store,run_id,receipt_sha256):
    sha256(receipt_sha256)
    path=folder(store,run_id); inventory(path)
    target=contained(path,receipt_sha256+'.json'); private(target)
    saved,saved_hash=record(target,MAX_BYTES)
    if saved_hash!=receipt_sha256: raise ValueError('decision receipt content identity changed')
    if saved.get('analysis_engine')!=engine():
        raise ValueError('decision analysis engine differs; explicitly regenerate the receipt')
    payload,raw,analysis=build(store,run_id)
    if raw!=read_regular(target,MAX_BYTES) or hashed(raw)!=receipt_sha256:
        raise ValueError('decision receipt differs from current recomputed analysis')
    return view(payload,raw,analysis)
