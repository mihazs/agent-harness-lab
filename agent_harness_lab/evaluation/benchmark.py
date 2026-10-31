"""Matched runtime strata and provenance-based publication; no pooled model effect."""
from collections import Counter
from datetime import date
import html
import json
import math

MATCH_FIELDS=('provider','runtime','model_revision','reasoning','task_bank_revision',
              'sandbox_image','permissions','harness_version')


def validate_pair(baseline,candidate,strict=True):
    for key in MATCH_FIELDS:
        if key not in baseline or key not in candidate or baseline[key]!=candidate[key]:
            raise ValueError('mismatched within-pair configuration: '+key)
    verified=baseline.get('effective_settings_verified') is True and candidate.get('effective_settings_verified') is True
    return dict(status='matched' if verified else 'blocked' if strict else 'unverified',
                effective_settings_verified=verified,stratum=tuple(baseline[k] for k in MATCH_FIELDS[:4]))


def public_export_allowed(records):
    if not records: return False
    for record in records:
        provenance=record.get('provenance',{})
        if provenance.get('source_kind') not in ('synthetic','public') or provenance.get('approved_publication') is not True:
            return False
        if not provenance.get('source_ref') or provenance.get('derived_from_private') is not False:
            return False
    return True


def measured_cost(usage,pricing=None):
    if pricing is None: return None
    try: date.fromisoformat(pricing['as_of'])
    except (ValueError,TypeError,KeyError): raise ValueError('dated supplied pricing required')
    total=0
    for key,rate in (('input_tokens','input_per_token'),('output_tokens','output_per_token')):
        value=usage.get(key)
        if value is None: return None
        if type(value) not in (int,float) or not math.isfinite(value) or value<0: raise ValueError('invalid measured usage')
        price=pricing.get(rate)
        if type(price) not in (int,float) or not math.isfinite(price) or price<0: raise ValueError('invalid supplied pricing')
        total+=value*price
    return dict(amount=total,currency=pricing.get('currency','unspecified'),pricing_as_of=pricing['as_of'])


def summary(records,*,public=False):
    if public and not public_export_allowed(records):
        raise ValueError('private/mixed/unapproved results cannot be exported, including aggregates')
    strata={}
    for record in records:
        pair=validate_pair(record['baseline_runtime'],record['candidate_runtime'],strict=False)
        key=json.dumps(pair['stratum'])
        group=strata.setdefault(key,dict(runtime_stratum=list(pair['stratum']),statuses=Counter(),runs=0,
                                        effective_settings_verified=True,capability_claim='none'))
        status=record.get('status','unknown')
        group['statuses'][status]+=1; group['runs']+=1
        group['effective_settings_verified'] &= pair['effective_settings_verified']
        if record.get('execution_kind')!='real_model': group['capability_claim']='synthetic_or_replay_only'
    return dict(schema_version=1,strata=list(strata.values()),pooled_treatment_effect=None,
                dollar_cost='unknown_without_dated_supplied_pricing_and_measured_usage',
                publication='explicit_public_export' if public else 'private',
                limitations=['failed/skipped/inconclusive/not-run statuses are retained',
                             'green deterministic CI is not a model capability certificate'])


def render(result):
    lines=['# Matched benchmark summary','','| Runtime stratum | Runs | Statuses | Settings verified |',
           '| --- | ---: | --- | --- |']
    rows=[]
    for group in result['strata']:
        label=' / '.join(str(v) for v in group['runtime_stratum'])
        statuses=json.dumps(group['statuses'],sort_keys=True)
        lines.append(f"| {label.replace('|','/')} | {group['runs']} | {statuses.replace('|','/')} | {group['effective_settings_verified']} |")
        rows.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in
                    (label,group['runs'],statuses,group['effective_settings_verified']))+'</tr>')
    lines+=['','No pooled model effect. Costs remain unknown without measured usage and supplied dated pricing.']
    document='<!doctype html><html lang="en"><meta charset="utf-8"><title>Matched benchmark summary</title><body><h1>Matched benchmark summary</h1><table><tr><th>Runtime stratum</th><th>Runs</th><th>Statuses</th><th>Settings verified</th></tr>'+''.join(rows)+'</table><p>No pooled model effect. Synthetic checks are not capability evidence.</p></body></html>'
    return '\n'.join(lines)+'\n',document
