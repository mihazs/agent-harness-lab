"""Validation of private sealed resource artifacts; provider data remain unverified."""
from .comparison_custody import hashed
from .resource_decision import verify_plan_resource_contract

PROFILE = 'ahl-sealed-resource-evidence-v1'


def validate(value, spec, result, custody):
    if not isinstance(value,dict) or set(value) != {'schema_version','profile','candidate','publication_allowed','certification_authority','automatic_promotion'}:
        raise ValueError('invalid sealed resource artifact')
    if (type(value['schema_version']) is not int or value['schema_version'] != 1 or value['profile'] != PROFILE
            or value['publication_allowed'] is not False or value['certification_authority'] is not False or value['automatic_promotion'] is not False):
        raise ValueError('unsupported sealed resource artifact')
    candidate = value['candidate']
    keys = {'profile','plan_sha256','schedule_sha256','resource_contract_sha256','resource_contract',
            'result_canonical_sha256','custody_canonical_sha256','samples','state','durability','source','quality',
            'billing_completeness','publication','publication_allowed','certification_authority','automatic_promotion'}
    if not isinstance(candidate,dict) or set(candidate) != keys: raise ValueError('invalid resource candidate')
    contract = candidate['resource_contract']
    if (verify_plan_resource_contract(spec,contract)['status'] != 'matched'
            or candidate['resource_contract_sha256'] != spec.resource_contract_sha256
            or candidate['plan_sha256'] != spec.digest or candidate['schedule_sha256'] != hashed([row['custody']['scheduled'] for row in custody['rows']])
            or candidate['result_canonical_sha256'] != hashed(result) or candidate['custody_canonical_sha256'] != hashed(custody)):
        raise ValueError('resource artifact binding changed')
    flags = dict(profile='ahl-resource-evidence-candidate-v1',state='live_candidate_unsealed',durability='not_persisted',
                 source='provider_reported_unverified',quality='unverified',billing_completeness='unknown',publication='private',
                 publication_allowed=False,certification_authority=False,automatic_promotion=False)
    if any(candidate.get(key) != expected or type(expected) is bool and candidate.get(key) is not expected for key,expected in flags.items()):
        raise ValueError('resource artifact authority changed')
    samples = candidate['samples']
    if not isinstance(samples,list) or len(samples) != len(custody['rows']): raise ValueError('resource trial coverage changed')
    for sample,row in zip(samples,custody['rows']):
        if not isinstance(sample,dict) or set(sample) != {'scheduled_trial_sha256','usage'}:
            raise ValueError('invalid resource trial sample')
        trial = hashed(row['custody']['scheduled']); usage = sample['usage']
        usage_keys = {'profile','scheduled_trial_sha256','request_attempts','reported_responses','unreported_attempts',
                      'usage','cached_input_included_in_input','cache_write_tokens','source','trial_completion',
                      'billing_completeness','publication','publication_allowed','certification_authority','automatic_promotion',
                      'request_binding_coverage','durable'}
        if (sample['scheduled_trial_sha256'] != trial or not isinstance(usage,dict)
                or set(usage) != usage_keys
                or usage.get('scheduled_trial_sha256') != trial or usage.get('profile') != 'ahl-completed-trial-provider-usage-v1'
                or usage.get('trial_completion') != 'live_custody_accepted' or usage.get('request_binding_coverage') not in ('matched','unknown')
                or usage.get('source') != 'provider_reported_unverified' or usage.get('billing_completeness') != 'unknown'
                or usage.get('publication') != 'private' or usage.get('cached_input_included_in_input') is not True
                or usage.get('cache_write_tokens') is not None
                or usage.get('publication_allowed') is not False or usage.get('automatic_promotion') is not False
                or usage.get('certification_authority') is not False or usage.get('durable') is not False):
            raise ValueError('resource trial attribution changed')
        categories = usage.get('usage')
        if not isinstance(categories,dict) or set(categories) != {'input_tokens','output_tokens','total_tokens','cached_input_tokens'}:
            raise ValueError('resource usage categories changed')
        attempts,reported,missing = (usage.get(name) for name in ('request_attempts','reported_responses','unreported_attempts'))
        if (any(type(n) is not int or n < 0 or n > 1000 for n in (attempts,reported,missing))
                or reported + missing != attempts): raise ValueError('resource request coverage changed')
        if usage['request_binding_coverage'] == 'matched' and attempts != len(row['custody']['requests']):
            raise ValueError('resource request binding changed')
        for entry in categories.values():
            if not isinstance(entry,dict) or set(entry) != {'known_subtotal','known_requests','unknown_requests','total'}:
                raise ValueError('invalid resource usage category')
            known,unknown,subtotal = (entry[name] for name in ('known_requests','unknown_requests','known_subtotal'))
            if (type(known) is not int or type(unknown) is not int or known < 0 or known > reported or unknown < 0 or known + unknown != attempts
                    or entry['total'] is not None or (known == 0 and subtotal is not None)
                    or (known > 0 and (type(subtotal) is not int or not 0 <= subtotal <= 10**15))):
                raise ValueError('resource usage coverage changed')
    return value
