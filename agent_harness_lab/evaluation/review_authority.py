"""Optional offline key approvals under an explicitly installed host trust policy."""
import base64
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

from agent_harness_lab.monitoring.state import atomic_json
from agent_harness_lab.private_journal import encoded, record, record_bytes, scratch_dir, sync_directory, write_bytes
from .integrity import contained, read_regular
from .regressions import fields, sha256

PROFILE='authenticated-artifact-review-v1'
POLICY='review-authority-policy-v1'
PURPOSE='development_artifact_review'
NAMESPACE='ahl.artifact-review.v1'
VERIFIER=Path('/usr/bin/ssh-keygen')
MAX_BYTES=64*1024
BANK_PURPOSE='conditional_final_bank_review'
REQUALIFICATION_PURPOSE='conditional_final_bank_requalification'


def authority_names(purpose):
    if purpose==PURPOSE:return 'review-authority.json',NAMESPACE
    if purpose==BANK_PURPOSE:return 'bank-review-authority.json','ahl.final-bank-review.v1'
    if purpose==REQUALIFICATION_PURPOSE:return 'bank-requalification-authority.json','ahl.final-bank-requalification.v1'
    raise ValueError('unsupported review authority purpose')


class VerifierUnavailable(ValueError):
    pass


def _hash(raw): return hashlib.sha256(raw).hexdigest()


def _path(path):
    path=Path(path).absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('review policy requires a canonical host path')
    return path


def require_host():
    if os.name!='posix' or not callable(getattr(os,'geteuid',None)):
        raise VerifierUnavailable('openssh_review_verifier_unavailable')


def _private(path):
    require_host()
    info=path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.geteuid()
            or info.st_mode & 0o077):
        raise ValueError('review policy must remain an owner-only regular file')
    return read_regular(path,MAX_BYTES)


def policy_value(raw,*,purpose=PURPOSE):
    value,pin=record_bytes(raw,MAX_BYTES)
    fields(value,'schema_version profile purpose reviewers')
    if type(value['schema_version']) is not int or value['schema_version']!=1 or value['profile']!=POLICY or value['purpose']!=purpose:
        raise ValueError('unsupported review authority policy')
    reviewers=value['reviewers']
    if not isinstance(reviewers,dict) or not 0<len(reviewers)<=32: raise ValueError('bounded reviewer keys required')
    for identity,key in reviewers.items():
        if not isinstance(identity,str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',identity) is None:
            raise ValueError('exact reviewer principal required')
        if not isinstance(key,str) or len(key)>128 or len(key.split())!=2 or key.split()[0]!='ssh-ed25519':
            raise ValueError('review authority supports raw Ed25519 public keys only')
        try: blob=base64.b64decode(key.split()[1],validate=True)
        except ValueError: raise ValueError('invalid reviewer public key') from None
        if len(blob)!=51 or blob[:19]!=b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20':
            raise ValueError('invalid Ed25519 key encoding')
    return value,pin


def _outside(path,roots):
    if any(path.is_relative_to(root) or root.is_relative_to(path) for root in roots):
        raise ValueError('trusted policy overlaps controller or case inputs')


def check_sources(store,roots):
    for name in ('review-authority.json','bank-review-authority.json','bank-requalification-authority.json'):
        marker=store.root/name
        if marker.exists():
            value,_=record(marker,4096)
            _outside(_path(value['source_path']),list(roots))


def install(store,path,roots=(),*,purpose=PURPOSE):
    marker,_=authority_names(purpose)
    path=_path(path); _outside(path,[store.root,*map(_path,roots)])
    raw=_private(path); _,pin=policy_value(raw,purpose=purpose)
    folder=contained(store.root,'review-policies');folder.mkdir(mode=0o700,exist_ok=True)
    target=folder/(pin+'.json')
    if target.exists():
        if _private(target)!=raw: raise ValueError('frozen reviewer policy changed')
    else:
        if len(list(folder.iterdir()))>=32:raise ValueError('reviewer policy capacity exhausted')
        write_bytes(target,raw);sync_directory(folder)
    if _private(path)!=raw:raise ValueError('review policy changed during installation')
    atomic_json(store.root/marker,dict(schema_version=1,profile=POLICY,source_path=str(path),policy_sha256=pin))
    return dict(profile=PROFILE,policy_sha256=pin,qualification='unverified',publication_allowed=False)


def active(store,*,purpose=PURPOSE):
    marker,_=authority_names(purpose)
    value,_=record(store.root/marker,4096)
    fields(value,'schema_version profile source_path policy_sha256')
    if type(value['schema_version']) is not int or value['schema_version']!=1 or value['profile']!=POLICY:
        raise ValueError('review authority is not installed')
    sha256(value['policy_sha256']); source=_path(value['source_path']); _outside(source,[store.root])
    raw=_private(source); policy,pin=policy_value(raw,purpose=purpose)
    if pin!=value['policy_sha256']: raise ValueError('installed reviewer policy changed or was revoked')
    if _private(store.root/'review-policies'/(pin+'.json'))!=raw:raise ValueError('frozen reviewer policy differs')
    return policy,pin


def request(store,review,*,purpose=PURPOSE):
    _,pin=active(store,purpose=purpose)
    value=dict(schema_version=1,profile=PROFILE,purpose=purpose,store_sha256=_hash(str(store.root).encode()),
        policy_sha256=pin,review=review)
    if len(encoded(value))>MAX_BYTES:raise ValueError('authenticated review request capacity exhausted')
    return value


def request_value(store,value,*,purpose=PURPOSE):
    if len(encoded(value))>MAX_BYTES:raise ValueError('authenticated review request capacity exhausted')
    fields(value,'schema_version profile purpose store_sha256 policy_sha256 review')
    if (type(value['schema_version']) is not int or value['schema_version']!=1 or value['profile']!=PROFILE
            or value['purpose']!=purpose or value['store_sha256']!=_hash(str(store.root).encode())):
        raise ValueError('authenticated review request scope changed')
    sha256(value['policy_sha256'])
    return value['review']


def verify(store,value,signature,*,require_current=True,deadline=None,purpose=PURPOSE):
    _,namespace=authority_names(purpose)
    review=request_value(store,value,purpose=purpose)
    raw=_private(store.root/'review-policies'/(value['policy_sha256']+'.json'))
    policy,pin=policy_value(raw,purpose=purpose)
    if pin!=value['policy_sha256']:raise ValueError('authenticated policy snapshot changed')
    identity=review['reviewer']['identity'];key=policy['reviewers'].get(identity)
    if key is None:raise ValueError('reviewer key is not authorized by policy')
    if not isinstance(signature,str) or len(signature.encode())>16*1024:
        raise ValueError('bounded detached signature required')
    if not VERIFIER.is_file() or not os.access(VERIFIER,os.X_OK):raise VerifierUnavailable('openssh_review_verifier_unavailable')
    deadline=time.monotonic()+10 if deadline is None else deadline
    remaining=min(10,deadline-time.monotonic())
    if remaining<=0:raise TimeoutError('review verification deadline exhausted')
    with tempfile.TemporaryDirectory(dir=scratch_dir(store.root),prefix='.review-verify-') as temporary:
        folder=Path(temporary);allowed=folder/'signers';detached=folder/'signature'
        write_bytes(allowed,(identity+' '+key+'\n').encode());write_bytes(detached,signature.encode())
        try:
            result=subprocess.run([str(VERIFIER),'-Y','verify','-f',str(allowed),'-I',identity,'-n',namespace,'-s',str(detached)],
                input=encoded(value),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=remaining,
                env={'PATH':'/usr/bin:/bin','LC_ALL':'C'},check=False)
        except subprocess.TimeoutExpired:raise TimeoutError('review verification deadline exhausted') from None
    if result.returncode!=0:raise ValueError('review signature is invalid or unsupported')
    if time.monotonic()>=deadline:raise TimeoutError('review verification deadline exhausted')
    if require_current and active(store,purpose=purpose)[1]!=pin:raise ValueError('review policy is no longer current')
    return dict(profile=PROFILE,policy_sha256=pin,key_sha256=_hash(key.encode()),
        signer=identity,namespace=namespace,request_sha256=_hash(encoded(value)))


def save(store,value,signature,proof):
    value=dict(schema_version=1,profile=PROFILE,request=value,signature=signature,proof=proof)
    raw=encoded(value); pin=_hash(raw)
    if len(raw)>2*MAX_BYTES:raise ValueError('authenticated review record capacity exhausted')
    folder=store.root/'review-auth';folder.mkdir(mode=0o700,exist_ok=True)
    target=folder/(pin+'.json')
    if target.exists():
        if read_regular(target,2*MAX_BYTES)!=raw:raise ValueError('review authentication artifact changed')
    else:write_bytes(target,raw);sync_directory(folder)
    return dict(profile=PROFILE,record_sha256=pin)


def validate_event(store,event,case_id,*,require_current=True,deadline=None):
    binding=event['payload'].get('authentication')
    if binding is None:return None
    fields(binding,'profile record_sha256');sha256(binding['record_sha256'])
    if binding['profile']!=PROFILE:raise ValueError('unsupported authenticated review event')
    value,pin=record(store.root/'review-auth'/(binding['record_sha256']+'.json'),2*MAX_BYTES)
    fields(value,'schema_version profile request signature proof')
    if type(value['schema_version']) is not int or value['schema_version']!=1 or value['profile']!=PROFILE or pin!=binding['record_sha256']:
        raise ValueError('authenticated review record changed')
    review=request_value(store,value['request'])
    if review!=event['payload']['review'] or review['case_id']!=case_id or review['expected_event_sha256']!=event['previous_sha256']:
        raise ValueError('authenticated review differs from its exact case event')
    proof=verify(store,value['request'],value['signature'],require_current=require_current,deadline=deadline)
    if proof!=value['proof']:raise ValueError('authenticated review proof changed')
    return proof


def current(store,event):
    binding=event['payload'].get('authentication')
    if binding is None:return True
    try:
        value,_=record(store.root/'review-auth'/(binding['record_sha256']+'.json'),2*MAX_BYTES)
        return active(store)[1]==value['request']['policy_sha256']
    except (ValueError,OSError,TypeError,KeyError):return False
