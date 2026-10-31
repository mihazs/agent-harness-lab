"""Opt-in typed presentation adapter; never changes tool effects or authority."""
from dataclasses import dataclass
import copy
import hashlib
import json


@dataclass(frozen=True)
class Authorization:
    project_fingerprint: str
    tools: tuple
    adapter_ids: tuple
    version: str

    @property
    def digest(self):
        return hashlib.sha256(repr(self).encode()).hexdigest()


def normalize_error(envelope, authorization, adapter_id='structured-error-v1'):
    if adapter_id not in authorization.adapter_ids or envelope.get('tool') not in authorization.tools:
        raise ValueError('adapter/tool not explicitly authorized')
    if set(envelope)!={'call_id','tool','status','payload'} or envelope['status'] not in ('success','error'):
        raise ValueError('malformed tool envelope')
    encoded=json.dumps(envelope,sort_keys=True,allow_nan=False)
    if len(encoded.encode())>65536: raise ValueError('tool envelope exceeds bound')
    original=copy.deepcopy(envelope)
    result=dict(original=original,adapter_id=adapter_id,authorization_digest=authorization.digest)
    if envelope['status']=='error':
        payload=envelope['payload']
        code=payload.get('code') if isinstance(payload,dict) else None
        result['error_metadata']=dict(code=code if isinstance(code,str) and len(code)<=128 else 'unknown',
                                      status='error',retry_authorized=False)
    return result


def verify(original, transformed, authorization):
    return (transformed.get('original')==original and transformed.get('authorization_digest')==authorization.digest
            and transformed.get('adapter_id') in authorization.adapter_ids)


def runtime_capability(runtime,version):
    return dict(status='unsupported',runtime=runtime,version=version,
                reason='no_version_verified_tool_interception_or_controlled_proxy',automatic_promotion=False)
