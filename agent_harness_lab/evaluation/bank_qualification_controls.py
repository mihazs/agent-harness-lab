"""Owned offline bank-control execution; serialized audit reports are not issuers."""
from copy import deepcopy
import hashlib
import math
import os
import stat
from pathlib import Path
import tempfile
import time
from weakref import WeakKeyDictionary

from agent_harness_lab.private_journal import encoded, record, sync_directory, write_bytes
from agent_harness_lab.sources.schema import SourceSpec
from . import controller_receipts
from .bank_tools import materialize_sources, regrade_artifacts
from .integrity import contained, copy_frozen, digest, tree
from .regression_controls import STATES, containment
from .regressions import fields
from .sandbox import Limits, SandboxBackend
from .task_bank import TaskSpec, _validate

PROFILE='owned-final-bank-controls-v1'
_ISSUED=WeakKeyDictionary()


class _Audit:
    pass


def hashed(value): return hashlib.sha256(encoded(value)).hexdigest()


def directory_modes(root,deadline=None):
    """Capture directory permissions under the bank's existing tree bounds."""
    root=Path(root).absolute()
    if '..' in root.parts or any(p.is_symlink() for p in (root,*root.parents)):
        raise ValueError('bank directories require canonical symlink-free paths')
    info=root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o7000:
        raise ValueError('bank root must be a directory without special permissions')
    modes={'.':stat.S_IMODE(info.st_mode)};files=0
    if deadline is not None:_check(deadline)
    for index,path in enumerate(root.rglob('*')):
        if deadline is not None:_check(deadline)
        if index>=5000 or len(path.relative_to(root).parts)>64:
            raise ValueError('bank directory entry/depth bound exceeded')
        info=path.lstat()
        if info.st_mode & 0o7000:raise ValueError('special bank permissions are unsupported')
        if stat.S_ISDIR(info.st_mode):modes[str(path.relative_to(root))]=stat.S_IMODE(info.st_mode)
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
            raise ValueError('bank contains symlink, hardlink or special entry')
        else:
            files+=1
            if files>4096:raise ValueError('bank file count bound exceeded')
    return dict(sorted(modes.items()))


def inventory(bank_path):
    bank_path=Path(bank_path).absolute()
    checked=_validate(bank_path)
    value,pin=record(bank_path,1024*1024)
    if checked['bank_sha256']!=pin:raise ValueError('bank changed during control inventory')
    tasks=[]
    for value in value['tasks']:
        task=TaskSpec.parse(value);bundle=contained(bank_path.parent,task.grader_bundle)
        controls,_=record_controls(bundle)
        selected=[]
        for control in controls:
            fixture=contained(bundle,control['fixture'])
            selected.append(dict(state=control['state'],fixture=control['fixture'],digest=digest(fixture)))
        tasks.append(dict(task=task.task_id,grader_bundle=task.grader_bundle,grader_sha256=task.grader_digest,
            grader_version=task.grader_version,artifacts=list(task.artifacts),controls=selected))
    return dict(bank_sha256=pin,tree_sha256=hashed(tree(bank_path.parent,max_files=4096,max_bytes=64*1024*1024)),
        directory_modes=directory_modes(bank_path.parent),tasks=tasks)


def record_controls(bundle):
    from .integrity import read_regular, unique_object, invalid_constant
    import json
    controls=json.loads(read_regular(bundle/'controls.json',256*1024),object_pairs_hook=unique_object,parse_constant=invalid_constant)
    if not isinstance(controls,list) or len(controls)!=len(STATES):raise ValueError('qualification needs all five frozen controls')
    for control in controls:
        fields(control,'state fixture')
        if control['state'] not in STATES:raise ValueError('unsupported qualification control')
    if {c['state'] for c in controls}!=set(STATES):raise ValueError('duplicate or missing qualification control')
    return controls,hashed(controls)


def _check(deadline):
    if time.monotonic()>=deadline:raise TimeoutError('bank control deadline exhausted')


def audit(bank_path,output,backend,deadline):
    """Execute and seal actual exports plus grading; consume under the journal writer."""
    if (type(deadline) not in (int,float) or not 0<deadline<=time.monotonic()+300
            or not math.isfinite(deadline)):raise ValueError('bounded finite bank control deadline required')
    if (type(backend) is not SandboxBackend or backend.cgroup_root is None or backend.image is None
            or backend.disk_bytes is None):raise ValueError('actual fully pinned sandbox backend required')
    _check(deadline)
    bank_path,output=Path(bank_path).absolute(),Path(output).absolute()
    if ('..' in output.parts or output.is_relative_to(bank_path.parent) or bank_path.parent.is_relative_to(output)
            or any(p.is_symlink() for p in (output,*output.parents))):
        raise ValueError('bank control output and input must remain separate canonical trees')
    bound=inventory(bank_path)
    output.mkdir(mode=0o700,exist_ok=False)
    source=SourceSpec('controller','ahl-controller','v1',hashed(['bank-control-source',str(output)]),
        bound['bank_sha256'],hashed(bound),identity_provenance='source_declared_id',enabled=True)
    report=dict(profile=PROFILE,bank_binding=bound,image_sha256=backend.image.sha256,
        status='incomplete',checks=[],publication_allowed=False,completion_authority=False)
    with controller_receipts.session():
        for task in bound['tasks']:
            bundle=contained(bank_path.parent,task['grader_bundle'])
            for control in task['controls']:
                _check(deadline)
                if inventory(bank_path)!=bound:raise ValueError('frozen bank changed during control audit')
                with tempfile.TemporaryDirectory() as temporary:
                    workspace=Path(temporary)/'control'
                    copy_frozen(contained(bundle,control['fixture']),workspace,control['digest'])
                    materialize_sources(workspace)
                    before=digest(workspace)
                    capture=controller_receipts.begin(source,task['artifacts'],deadline)
                    try:
                        run=backend.run(['/usr/bin/true'],workspace,{},'executor',
                            Limits(seconds=min(5,deadline-time.monotonic())),artifact_allowlist=task['artifacts'])
                        if run['status']!='completed' or not containment(run,backend.image.sha256,'executor'):
                            raise ValueError('owned bank control export failed')
                        grade=regrade_artifacts(workspace,task['artifacts'],bundle,task['grader_sha256'],backend,
                            limits=Limits(seconds=min(5,deadline-time.monotonic())))
                        expected=control['state'] in ('complete','alternative_valid')
                        if (grade['outcome'] is not expected or not containment(grade['execution'],backend.image.sha256)
                                or grade['grader_identity']['grader_version']!=task['grader_version']):
                            raise ValueError('qualification grader controls failed')
                        unchanged=digest(workspace)==before and inventory(bank_path)==bound
                        capture.close(unchanged)
                        receipt=capture.read()
                        report['checks'].append(dict(task=task['task'],control=control['state'],expected=expected,
                            observed=grade['outcome'],controller=receipt))
                    finally:
                        capture.cancel()
    _check(deadline)
    if inventory(bank_path)!=bound:raise ValueError('frozen bank changed after control audit')
    report['status']='owned_controls_passed'
    raw=encoded(report)
    if len(raw)>8*1024*1024:raise ValueError('bank control report exceeds capacity')
    write_bytes(output/'audit.json',raw);sync_directory(output)
    handle=_Audit()
    _ISSUED[handle]=(os.getpid(),deadline,str(bank_path),str(output),bound,raw)
    return handle,deepcopy(report)


def consume(handle,bank_path):
    """No live audit survives consumption, another process, input mutation or expiry."""
    if type(handle) is not _Audit:raise ValueError('actual owned bank control completion required')
    value=_ISSUED.pop(handle,None)
    if value is None:raise ValueError('fresh owned bank control completion required')
    pid,deadline,path,output,bound,raw=value
    _check(deadline)
    if pid!=os.getpid() or str(Path(bank_path).absolute())!=path or inventory(bank_path)!=bound:
        raise ValueError('owned bank control completion scope changed')
    from .integrity import read_regular
    from .decision_receipts import private
    private(Path(output),directory=True);private(Path(output)/'audit.json')
    if read_regular(Path(output)/'audit.json',8*1024*1024)!=raw:raise ValueError('owned bank control report changed')
    return record(Path(output)/'audit.json',8*1024*1024)[0]
