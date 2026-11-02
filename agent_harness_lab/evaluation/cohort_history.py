"""Freeze preservation declarations beside history; qualification is not a decision."""
from agent_harness_lab.private_journal import encoded,record,write_bytes
from . import cohorts
from .integrity import contained,read_regular,relative
from .plan import schedule
from .regressions import fields,sha256


def declaration(source,spec):
    value=source.get('cohort_manifest')
    if spec.cohort_manifest_sha256 is None:
        if value is not None: raise ValueError('cohort declarations require a frozen plan pin')
        return None
    fields(value,'path sha256'); sha256(value['sha256']); relative(value['path'])
    if value['sha256']!=spec.cohort_manifest_sha256:
        raise ValueError('cohort declaration differs from frozen plan')
    if spec.profile!='descriptive_smoke' or spec.partition!='development':
        raise ValueError('exposed preservation evidence cannot qualify a protected experiment')
    return value


def validate(binding,source,spec,bank_tasks,*,current=True):
    if not isinstance(binding,dict): raise ValueError('frozen preservation binding must be an object')
    selected=declaration(source,spec)
    if selected is None or not isinstance(bank_tasks,dict) or not bank_tasks:
        raise ValueError('preservation requires actual current bank task authority')
    if binding.get('manifest_sha256')!=selected['sha256']:
        raise ValueError('cohort evidence differs from frozen plan')
    (cohorts.revalidate if current else cohorts.validate_snapshot)(binding,spec.tasks,spec.baseline_hash)
    scheduled={row['trial_id'] for row in schedule(spec)}
    for evidence in binding['qualification_bindings'].values():
        if evidence['plan_sha256']==spec.digest or scheduled.intersection(evidence['trial_ids']):
            raise ValueError('current comparison outcomes cannot qualify preservation membership')
    for cohort in binding['manifest']['cohorts']:
        for member in cohort['members']:
            name=member['task_id']; expected=bank_tasks.get(name)
            if (not isinstance(expected,dict) or any(member[key]!=expected[key] for key in
                    ('task_revision_sha256','materialized_sha256','execution_fixture_sha256','grader_version'))
                    or member['bundle_sha256']!=source['grader_bundles'].get(name,{}).get('digest')):
                raise ValueError('preservation qualification differs from current task or oracle')
    return binding


def capture(root,source,spec,bank_tasks):
    selected=declaration(source,spec)
    if selected is None: return None
    binding=cohorts.capture(contained(root,selected['path']),selected['sha256'],spec.tasks,spec.baseline_hash)
    return validate(binding,source,spec,bank_tasks)


def freeze(case,binding,source,spec,bank_tasks):
    raw=read_regular(binding['manifest_path'],cohorts.MAX_BYTES)
    write_bytes(case/'cohort-manifest.json',raw)
    frozen=cohorts.capture(case/'cohort-manifest.json',binding['manifest_sha256'],spec.tasks,spec.baseline_hash)
    if {k:v for k,v in frozen.items() if k!='manifest_path'}!={k:v for k,v in binding.items() if k!='manifest_path'}:
        raise ValueError('preservation qualification changed before controller freeze')
    validate(frozen,source,spec,bank_tasks)
    write_bytes(case/'cohort-binding.json',encoded(frozen))
    return record(case/'cohort-binding.json',cohorts.MAX_BYTES)[1]
