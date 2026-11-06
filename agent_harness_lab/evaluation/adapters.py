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

    def __post_init__(self):
        if not self.project_fingerprint or not self.version or any(
                not isinstance(items,tuple) or any(not isinstance(v,str) or not 0<len(v)<=128 for v in items)
                for items in (self.tools,self.adapter_ids)):
            raise ValueError('authorization must be immutable bounded controller metadata')

    @property
    def digest(self):
        return hashlib.sha256(repr(self).encode()).hexdigest()


def normalize_error(envelope, authorization, adapter_id='structured-error-v1'):
    if adapter_id not in authorization.adapter_ids or envelope.get('tool') not in authorization.tools:
        raise ValueError('adapter/tool not explicitly authorized')
    if not isinstance(envelope.get('call_id'),str) or not 0<len(envelope['call_id'])<=128:
        raise ValueError('invalid call identity')
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
    try:
        expected=normalize_error(original,authorization,transformed.get('adapter_id'))
        return expected==transformed
    except (ValueError,TypeError,KeyError,AttributeError):
        return False


def runtime_capability(runtime,version):
    return dict(status='unsupported',runtime=runtime,version=version,
                reason='no_version_verified_tool_interception_or_controlled_proxy',automatic_promotion=False)


SPEC_SCHEMA = 'ahl.adapter-spec.v1'
SPEC_INPUT_CONTRACTS = ('tool-envelope-v1',)
SPEC_OUTPUT_CONTRACTS = ('presented-envelope-v1',)
SPEC_TRANSFORMATIONS = ('identity', 'structured-error-v1')
SPEC_SIDE_EFFECTS = ('none',)
SPEC_LIMIT_CEILINGS = dict(max_input_bytes=65536, max_output_bytes=131072, timeout_ms=10000)
SPEC_FIELDS = ('adapter_id', 'version', 'content_digest', 'tools', 'input_contract', 'output_contract',
               'transformations', 'side_effects', 'filesystem', 'network', 'credentials', 'nested_tools',
               'limits', 'previous_digest', 'runtime', 'runtime_version')
_AUTHORITY_FIELDS = ('filesystem', 'network', 'credentials', 'nested_tools')


def _bounded_text(value):
    return isinstance(value, str) and 0 < len(value) <= 128


def _sha256_hex(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


@dataclass(frozen=True)
class AdapterSpec:
    """Declared capability contract for one presentation adapter; it is data and never executes."""
    adapter_id: str
    version: str
    content_digest: str
    tools: tuple
    input_contract: str
    output_contract: str
    transformations: tuple
    side_effects: str
    filesystem: tuple
    network: tuple
    credentials: tuple
    nested_tools: tuple
    limits: tuple
    previous_digest: object
    runtime: str
    runtime_version: str

    def __post_init__(self):
        problem = _spec_problem(self)
        if problem:
            raise ValueError(f'adapter spec rejected: {problem}')

    @classmethod
    def from_mapping(cls, data):
        """Parse an untrusted JSON-like mapping; unknown or missing fields are rejected, never ignored."""
        if not isinstance(data, dict) or set(data) != set(SPEC_FIELDS):
            raise ValueError('adapter spec rejected: fields must be exactly the declared contract')
        values = dict(data)
        for name in ('tools', 'transformations') + _AUTHORITY_FIELDS:
            if not isinstance(values[name], list):
                raise ValueError(f'adapter spec rejected: {name} must be a list')
            values[name] = tuple(values[name])
        if not isinstance(values['limits'], dict):
            raise ValueError('adapter spec rejected: limits must be an object')
        values['limits'] = tuple(sorted(values['limits'].items(), key=lambda item: str(item[0])))
        return cls(**values)

    def as_mapping(self):
        data = {name: getattr(self, name) for name in SPEC_FIELDS}
        for name in ('tools', 'transformations') + _AUTHORITY_FIELDS:
            data[name] = list(data[name])
        data['limits'] = dict(self.limits)
        return data

    @property
    def digest(self):
        encoded = json.dumps(dict(schema=SPEC_SCHEMA, spec=self.as_mapping()), sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(encoded.encode()).hexdigest()


def _spec_problem(spec):
    for name in ('adapter_id', 'version', 'runtime', 'runtime_version'):
        if not _bounded_text(getattr(spec, name)):
            return f'{name} must be bounded text'
    if not _sha256_hex(spec.content_digest):
        return 'content_digest must be a lowercase sha256 hex digest'
    if spec.previous_digest is not None and not _sha256_hex(spec.previous_digest):
        return 'previous_digest must be null or a lowercase sha256 hex digest'
    for name in ('tools', 'transformations') + _AUTHORITY_FIELDS:
        items = getattr(spec, name)
        if not isinstance(items, tuple) or any(not _bounded_text(v) for v in items) or len(set(items)) != len(items):
            return f'{name} must be a tuple of distinct bounded text'
    if not spec.tools:
        return 'tools must name at least one tool'
    if spec.input_contract not in SPEC_INPUT_CONTRACTS or spec.output_contract not in SPEC_OUTPUT_CONTRACTS:
        return 'unsupported input/output contract'
    if not spec.transformations or not set(spec.transformations) <= set(SPEC_TRANSFORMATIONS):
        return 'transformations must be supported presentation-only transforms'
    if spec.side_effects not in SPEC_SIDE_EFFECTS:
        return 'side effects are not permitted'
    granted = [name for name in _AUTHORITY_FIELDS if getattr(spec, name)]
    if granted:
        return 'presentation adapters cannot hold ' + ', '.join(granted)
    if not isinstance(spec.limits, tuple) or any(not isinstance(p, tuple) or len(p) != 2 or not isinstance(p[0], str) for p in spec.limits) \
            or len(dict(spec.limits)) != len(spec.limits) or dict(spec.limits).keys() != SPEC_LIMIT_CEILINGS.keys():
        return 'limits must declare exactly ' + ', '.join(sorted(SPEC_LIMIT_CEILINGS))
    for name, value in spec.limits:
        if type(value) is not int or not 0 < value <= SPEC_LIMIT_CEILINGS[name]:
            return f'limit {name} must be a positive integer within its ceiling'
    return None


def verify_adapter_bytes(spec, data):
    """Revalidate implementation bytes against the declared digest before any sandboxed use."""
    return isinstance(data, bytes) and hashlib.sha256(data).hexdigest() == spec.content_digest


def review_candidate_spec(authorized, candidate, authorization):
    """Reject a candidate spec that expands the controller-authorized contract; no model judgment, no execution."""
    if not isinstance(authorized, AdapterSpec) or not isinstance(authorization, Authorization):
        raise TypeError('authorized spec and authorization must be typed controller objects')
    if authorized.adapter_id not in authorization.adapter_ids or not set(authorized.tools) <= set(authorization.tools):
        raise ValueError('authorized spec exceeds the project authorization')
    result = dict(schema=SPEC_SCHEMA, authorized_digest=authorized.digest,
                  authorization_digest=authorization.digest, executes_adapter_code=False,
                  automatic_promotion=False,
                  runtime=runtime_capability(authorized.runtime, authorized.runtime_version))
    try:
        spec = candidate if isinstance(candidate, AdapterSpec) else AdapterSpec.from_mapping(candidate)
    except (ValueError, TypeError) as error:
        return dict(result, status='rejected', reasons=[f'malformed_candidate: {error}'], candidate_digest=None)
    reasons = []
    for name in ('adapter_id', 'input_contract', 'output_contract', 'side_effects', 'runtime', 'runtime_version'):
        if getattr(spec, name) != getattr(authorized, name):
            reasons.append(f'changed_{name}')
    for name in ('tools', 'transformations') + _AUTHORITY_FIELDS:
        if not set(getattr(spec, name)) <= set(getattr(authorized, name)):
            reasons.append(f'expanded_{name}')
    ceilings = dict(authorized.limits)
    reasons += [f'expanded_limit_{name}' for name, value in spec.limits if value > ceilings[name]]
    if spec.content_digest != authorized.content_digest and spec.previous_digest != authorized.content_digest:
        reasons.append('missing_rollback_lineage')
    return dict(result, status='rejected' if reasons else 'within_authorized_capabilities',
                reasons=reasons, candidate_digest=spec.digest)
