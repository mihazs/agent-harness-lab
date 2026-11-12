"""Matched runtime strata and provenance-based publication; no pooled model effect."""
from collections import Counter
from datetime import date
import html
import json
import math
from .history_report import summary as history_summary  # noqa: F401 - re-exported for existing importers

MATCH_FIELDS=('provider','runtime','model_revision','reasoning','task_bank_revision',
              'sandbox_image','permissions','controller_version')
PUBLIC_STATUSES=('unknown','not_run','not_executed','completed','passed','failed','skipped','inconclusive',
                 'blocked','incomplete','interrupted','budget_exhausted','execution_error','invalid_input',
                 'grader_error','timeout','agent_timeout','rejected','supported')
PUBLIC_EXECUTION_KINDS=('synthetic','replay','real_model','fake_agent')


def validate_pair(baseline,candidate,strict=True):
    for key in MATCH_FIELDS:
        if key not in baseline or key not in candidate or baseline[key]!=candidate[key]:
            raise ValueError('mismatched within-pair configuration: '+key)
    verified=baseline.get('effective_settings_verified') is True and candidate.get('effective_settings_verified') is True
    return dict(status='matched' if verified else 'blocked' if strict else 'unverified',
                effective_settings_verified=verified,stratum=tuple(baseline[k] for k in MATCH_FIELDS[:4]))


def public_export_allowed(records):
    if not isinstance(records,list) or not records: return False
    for record in records:
        if (not isinstance(record,dict)
                or not {'baseline_runtime','candidate_runtime','provenance'} <= record.keys()
                or record.keys() - {'baseline_runtime','candidate_runtime','provenance','status','execution_kind','publication_allowed'}):
            return False
        for name in ('baseline_runtime','candidate_runtime'):
            runtime=record[name]
            if (not isinstance(runtime,dict) or runtime.keys() - {*MATCH_FIELDS,'effective_settings_verified'}
                    or not all(isinstance(runtime.get(key),str) and 0 < len(runtime[key]) <= 4096 for key in MATCH_FIELDS)
                    or 'effective_settings_verified' in runtime and type(runtime['effective_settings_verified']) is not bool):
                return False
        provenance=record['provenance']
        if not isinstance(provenance,dict) or provenance.keys() - {'source_kind','approved_publication','source_ref','derived_from_private'}:
            return False
        if (record.get('status','unknown') not in PUBLIC_STATUSES
                or record.get('execution_kind','synthetic') not in PUBLIC_EXECUTION_KINDS
                or not isinstance(provenance.get('source_ref'),str) or not 0 < len(provenance['source_ref']) <= 4096):
            return False
        wrappers=(record,record.get('receipt'),record.get('analysis'),record.get('context'),record.get('binding'),record.get('execution'),record.get('process_mining'),record.get('incident_retrieval'),record.get('paired_history_analysis'))
        # Preparation contexts carry the private invocation as a known child.
        invocations=tuple(value.get('invocation') for value in wrappers if isinstance(value,dict))
        for metadata in (*wrappers,*invocations):
            if isinstance(metadata,dict) and (metadata.get('publication_allowed',True) is not True
                    or any(key in metadata for key in ('controller_run_id','cohort_manifest_sha256','preservation','receipt_sha256',
                        'cohort_binding','controller_qualification','qualification_sha256','controller_grade',
                        'sequential_profile','sequential_analysis','sampling_sha256','trajectory_profile','trajectory_analysis','precision_preflight','policy_sha256','permission_audit','isolation_qualification',
                        'controller_source_profile','source_capture','live_verification','verified_execution','controller_harness_input','harness_snapshot_binding','implementation_prerequisites','restricted_process_protocol','controlled_instruction_protocol','harness_change_plan','plan_sha256','configured_response_references_sha256',
                        'classifier_selection_allowed','review_event_sha256','protected_access','protected_access_sha256','import_sha256','foreign_claims','proposal_id',
                        'controller_derivation','derivation_id','derivation_binding_sha256','review_authentication','authentication','bank_qualification','construction_access','construction_id',
                        'closed_input','domain_sha256','qualification_event_sha256','delegation_sha256',
                        'skill_activation_receipt','audit_sha256','harness_inventory_sha256','instruction_precedence'))
                    or metadata.get('algorithm') in ('private-preservation-decision-v1','private-controller-history-report-v1',
                        'guarded-preservation-search-v1','corrected-cohort-qualification-v1')
                    or metadata.get('profile') in ('ahl-skill-activation-receipt-v1','ahl-harness-change-plan-v1','knowledge-temporal-v1','knowledge-links-v1','knowledge-origins-v1','proposer-mechanism-admission-v1','mechanism-route-dispositions-v1','mechanism-coverage-v1','ahl-controlled-agents-v1','conditional-finite-bank-sequential-v1','ahl-linux-restricted-process-v1','ahl-implementation-prerequisites-v1','controller-trajectory-v1','diagnostic-corpus-v1','diagnostic-corpus-benchmark-v1','ahl-controller-live-v1','permission-results-v1','protected-diagnostic-access-v1','external-evaluation-data-v1','one-call-improvement-v1','controlled-development-derivation-v1','fixed-root-precision-v1','fixed-root-power-simulation-v1','authenticated-artifact-review-v1','review-authority-policy-v1','conditional-final-bank-v1','owned-final-bank-controls-v1','canonical-construction-access-v1','cross-journal-commit-join-v1','cross-journal-pull-commit-join-v1','git-ref-movement-v1','ai-memory-okf-provenance-v1','ahl-harness-snapshot-binding-v1','precision-sample-size-v1','ci-test-results-v1','ci-evidence-commit-join-v1','claude-mem-export-v1','mem0-oss-export-v1','mem0-platform-export-v1','hindsight-bank-archive-v1','diagnostic-triage-policy-v2','diagnostic-triage-evaluation-v2','cognee-export-v1')

                    or metadata.get('format') in ('promptfoo-cases-json-v1','promptfoo-results-jsonl-v1','openai-evals-match-jsonl-v1')
                    or metadata.get('purpose')=='protected-diagnostic-access-v1'
                    or metadata.get('profile') in ('ahl-private-diagnosis-v1','ahl-analysis-process-graph-v1',
                        'ahl-analysis-execution-v1','ahl-improve-fixed-bank-v1','ahl-fresh-responses-comparison-v1',
                        'controller-request-reservation-v1','controller-realized-schedule-v1','ahl-skill-invocation-context-v1','ahl-live-construction-stage-v1',
                        'conditional-bank-requalification-v2','conditional-bank-closed-input-domain-v2','ahl-controlled-agents-project-inputs-v1',
                        'owned-evidence-views-v1','owned-closed-input-closure-v1','ahl-single-run-decision-disposition-v1',
                        'ahl-static-audit-v1','ahl-reviewed-comparison-evidence-v1','diagnostic-binary-triage-evaluation-v2',
                        'diagnostic-triage-evaluation-v4')
                    or any(key in metadata for key in ('request_sha256','workflow_request_sha256','responses_binding','responses_custody_sha256','request_binding','realized_schedule','paired_history_analysis'))
                    or metadata.get('report_kind')=='controller_trajectory'
                    or metadata.get('outcome_mapping')=='artifact_and_required_v1'
                    or metadata.get('evidence_source')=='controller_verified_development_only'):
                return False
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
    if isinstance(records,dict): return trajectory_summary(records)
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


def _trajectory_assertion(assertion, depth, count, parent_id=None, child_index=None, *, include_v2=False):
    count[0] += 1
    if depth > 8 or count[0] > 128 or not isinstance(assertion, dict):
        raise ValueError('invalid or oversized trajectory assertion tree')
    item = {key:assertion.get(key) for key in ('assertion_id','assertion_sha256','status')}
    if (any(not isinstance(v,str) or not v for v in item.values())
            or item['status'] not in ('satisfied','violated','unknown')):
        raise ValueError('trajectory assertion identity/status required')
    if parent_id is not None:
        try: id_size = len(item['assertion_id'].encode('utf-8'))
        except UnicodeEncodeError: raise ValueError('invalid trajectory child identity') from None
        if (id_size > 160 or item['assertion_id'] != parent_id + '/' + str(child_index)
                or len(item['assertion_sha256']) != 64
                or any(char not in '0123456789abcdef' for char in item['assertion_sha256'])):
            raise ValueError('invalid trajectory child identity or digest')
    item['coverage_complete'] = assertion.get('coverage_complete') is True
    if include_v2:
        kind, stage = assertion.get('predicate_kind'), assertion.get('selected_stage')
        if (not {'predicate_kind', 'selected_stage'} <= assertion.keys()
                or kind not in ('required', 'forbidden', 'count', 'sequence', 'all_of', 'any_of')
                or (kind in ('all_of', 'any_of') and stage is not None)
                or (kind not in ('all_of', 'any_of') and stage not in ('request', 'denied', 'dispatch', 'result'))):
            raise ValueError('invalid trajectory predicate kind or stage')
        item.update(predicate_kind=kind, selected_stage=stage)
    children = assertion.get('children', [])
    if not isinstance(children, list) or len(children) > 32:
        raise ValueError('invalid trajectory assertion children')
    if children:
        item['children'] = [_trajectory_assertion(child, depth + 1, count, item['assertion_id'], index, include_v2=include_v2)
                            for index, child in enumerate(children)]
    return item


def trajectory_summary(results):
    """Saved JSON is descriptive input; it cannot recreate a live controller receipt."""
    from .trajectory_assertions import PROFILE, RECEIPT_V2_PROFILE
    if results.get('trajectory_profile') != PROFILE:
        raise ValueError('unsupported controller trajectory report')
    trials = results.get('trials')
    if not isinstance(trials,list) or not trials:
        raise ValueError('nonempty trajectory trials required')
    rows = []
    for trial in trials:
        if not isinstance(trial,dict): raise ValueError('trajectory trial object required')
        grade = trial.get('controller_grade',{})
        if not isinstance(grade,dict): raise ValueError('trajectory grade object required')
        receipt = grade.get('trajectory',{})
        if not isinstance(receipt,dict): raise ValueError('trajectory receipt object required')
        assertions = receipt.get('assertions',[])
        if not isinstance(assertions,list):
            raise ValueError('assertion report list required')
        row = {key:trial.get(key) for key in ('trial_id','task','root','family','version','status')}
        for value in row.values():
            if value is not None and not isinstance(value,str):
                raise ValueError('trajectory report identity/status text required')
        if not row['task']: raise ValueError('trajectory task identity required')
        reported_assertions = []
        for assertion in assertions:
            reported_assertions.append(_trajectory_assertion(assertion, 1, [0], include_v2=receipt.get('profile') == RECEIPT_V2_PROFILE))
        row.update(artifact_outcome=grade.get('artifact_outcome') if type(grade.get('artifact_outcome')) is bool else None,
            trajectory_status=receipt.get('status') if receipt.get('status') in ('satisfied','violated','unknown') else 'unknown',
            combined_outcome=grade.get('outcome') if type(grade.get('outcome')) is bool else None,
            critical_violation=grade.get('critical_violation') is True,
            required_observation_unknown=grade.get('required_observation_unknown') is not False,
            assertions=reported_assertions)
        rows.append(row)
    return dict(schema_version=1,report_kind='controller_trajectory',trials=rows,
        publication_allowed=False,automatic_promotion=False,history_authority='unverified',
        native_identity='unverified',oracle_validity='unverified',report_authority='imported_unverified',
        analysis=dict(status='unverified',reason='saved_json_cannot_recreate_controller_authority'),
        limitations=['Reported outcomes are unverified imported claims; no adoption or effect-safety certificate.',
            'Assertion counts are not independent samples. Hidden reads, transient writes and delivery remain unobserved.'])


def _markdown_cell(value):
    text = html.escape(' '.join(str(value).splitlines())).replace('|', '/')
    for character in ('\\', '`', '[', ']', '(', ')', '!', '*', '_'):
        text = text.replace(character, '\\' + character)
    return text


def render(result):
    if result.get('report_kind')=='controller_history':
        from .history_report import render as render_history
        return render_history(result)
    trajectory = result.get('report_kind')=='controller_trajectory'
    title = 'Private controller trajectory claims — unverified import' if trajectory else 'Matched benchmark summary'
    headers = ('Task / arm','Artifact outcome','Trajectory','Combined outcome','Critical / unknown required') if trajectory else ('Runtime stratum','Runs','Statuses','Settings verified')
    values = ([(r['task']+' / '+str(r['version']),r['artifact_outcome'],r['trajectory_status'],r['combined_outcome'],
                str(r['critical_violation'])+' / '+str(r['required_observation_unknown'])) for r in result['trials']]
              if trajectory else [(' / '.join(str(v) for v in g['runtime_stratum']),g['runs'],
                    json.dumps(g['statuses'],sort_keys=True),g['effective_settings_verified']) for g in result['strata']])
    notice = (' '.join(result['limitations']) if trajectory else
              'No pooled model effect. Costs remain unknown without measured usage and supplied dated pricing. Synthetic checks are not capability evidence.')
    lines=['# '+title,'','| '+' | '.join(headers)+' |','| '+' | '.join('---' for _ in headers)+' |']
    rows=[]
    for row in values:
        lines.append('| '+' | '.join(_markdown_cell(v) for v in row)+' |')
        rows.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in row)+'</tr>')
    lines+=['',notice]
    document='<!doctype html><html lang="en"><meta charset="utf-8"><title>'+html.escape(title)+'</title><body><h1>'+html.escape(title)+'</h1><table><tr>'+''.join('<th>'+h+'</th>' for h in headers)+'</tr>'+''.join(rows)+'</table><p>'+html.escape(notice)+'</p></body></html>'
    return '\n'.join(lines)+'\n',document
