"""Private pretrial declarations for resource quality; no decision authority."""
import hashlib
import json

from .plan import ExperimentSpec

PROFILE = 'resource-quality-policy-v1'


def validate(policy):
    if (not isinstance(policy,dict) or set(policy) != {'schema_version','profile','quality_criteria_sha256','required_status'}
            or type(policy['schema_version']) is not int or policy['schema_version'] != 1
            or policy['profile'] != PROFILE or policy['required_status'] != 'supported'):
        raise ValueError('invalid resource quality policy')
    criteria = policy['quality_criteria_sha256']
    if not isinstance(criteria,str) or len(criteria) != 64 or any(c not in '0123456789abcdef' for c in criteria):
        raise ValueError('quality criteria require an exact lowercase SHA-256')
    return policy


def bind(plan,policy,*,public=False):
    """Check a plan pin; caller-provided declarations are never authenticated."""
    if public is not False: raise ValueError('resource quality policy is private')
    if type(plan) is not ExperimentSpec: raise ValueError('experiment plan required')
    plan.validate(); validate(policy)
    pin = hashlib.sha256(json.dumps(policy,sort_keys=True,separators=(',', ':')).encode()).hexdigest()
    declared = plan.resource_quality_policy_sha256
    status = 'unbound' if declared is None else 'matched' if declared == pin else 'mismatch'
    return dict(profile='resource-quality-policy-binding-v1',status=status,plan_sha256=plan.digest,
                quality_policy_sha256=pin,policy_source='caller_supplied_unverified',
                quality_status='unknown',quality_authentication='not_established',
                root_grouping='not_checked',resource_decision='not_computed',resource_gate='not_evaluated',
                publication='private',publication_allowed=False,automatic_promotion=False)
