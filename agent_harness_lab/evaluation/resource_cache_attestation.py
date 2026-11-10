"""Private caller statements about cache state, bound to sealed request scope."""
from .comparison_custody import hashed
from .resource_history import validate

PROFILE = 'ahl-cache-state-attestation-v1'
STATES = ('uncached', 'provider_cache_used')


def bind(artifact, spec, result, custody, attestations):
    """Bind explicit statements to the current v8 scope, without authenticating them."""
    validate(artifact, spec, result, custody)
    if artifact['schema_version'] != 2: raise ValueError('v8 resource coverage required')
    if not isinstance(attestations, list): raise ValueError('cache attestations must be a list')
    expected = {}
    for sample, row in zip(artifact['candidate']['samples'], custody['rows']):
        trial = sample['scheduled_trial_sha256']
        requests = row['custody']['requests']
        for ordinal in requests:
            key = (trial, ordinal)
            if key in expected: raise ValueError('duplicate custody request')
            expected[key] = None
    supplied = {}
    for entry in attestations:
        if (not isinstance(entry, dict) or set(entry) != {'scheduled_trial_sha256', 'request_ordinal', 'cache_state'}
                or type(entry['scheduled_trial_sha256']) is not str or type(entry['request_ordinal']) is not int
                or type(entry['cache_state']) is not str or entry['cache_state'] not in STATES):
            raise ValueError('invalid cache attestation')
        key = (entry['scheduled_trial_sha256'], entry['request_ordinal'])
        if key not in expected or key in supplied: raise ValueError('unknown or duplicate cache attestation')
        supplied[key] = entry['cache_state']
    trials = []
    for sample, row in zip(artifact['candidate']['samples'], custody['rows']):
        trial = sample['scheduled_trial_sha256']; requests = row['custody']['requests']
        states = [supplied.get((trial, ordinal)) for ordinal in requests]
        complete = (bool(requests) and sample['usage']['request_binding_coverage'] == 'matched'
                    and len(requests) == sample['usage']['request_attempts'] and None not in states)
        state = states[0] if complete and len(set(states)) == 1 else 'unknown'
        cached = sample['usage']['usage']['cached_input_tokens']['total']
        if cached is not None and ((state == 'uncached' and cached > 0)
                                   or (state == 'provider_cache_used' and cached < len(requests))):
            state = 'unknown'
        trials.append(dict(scheduled_trial_sha256=trial, request_attempts=sample['usage']['request_attempts'],
                           attested_requests=sum(state is not None for state in states), cache_state=state))
    pairs = {}
    for index, row in enumerate(custody['rows']):
        scheduled = row['custody']['scheduled']
        pairs.setdefault((scheduled['task'], scheduled['repetition']), []).append(index)
    for indices in pairs.values():
        if (len(indices) != 2 or {custody['rows'][i]['custody']['scheduled']['version'] for i in indices} != {'baseline', 'candidate'}
                or any(trials[i]['cache_state'] == 'unknown' for i in indices)
                or len({trials[i]['cache_state'] for i in indices}) != 1):
            for index in indices: trials[index]['cache_state'] = 'unknown'
    return dict(profile=PROFILE, plan_sha256=spec.digest, resource_artifact_sha256=hashed(artifact),
                custody_sha256=hashed(custody), trials=trials, source='caller_attested_unverified',
                cache_state_authentication='not_established', quality='unverified', resource_decision='not_computed',
                durable=False, publication='private', publication_allowed=False, automatic_promotion=False)
