"""Private reports resolved from current controller receipts, never imported claims."""
import html
import json
import re

from .regressions import fields,sha256


def summary(store,receipt_refs,*,public=False):
    if public: raise ValueError('controller history reports are private, including synthetic-labelled aggregates')
    if not isinstance(receipt_refs,list) or not receipt_refs:
        raise ValueError('nonempty explicit controller receipt references required')
    from .oracle_history import HistoryStore
    history=HistoryStore(store); seen=set(); comparisons=[]
    for ref in receipt_refs:
        fields(ref,'run_id receipt_sha256')
        for value in ref.values(): sha256(value)
        key=(ref['run_id'],ref['receipt_sha256'])
        if key in seen: raise ValueError('duplicate controller receipt reference')
        seen.add(key)
        verified=history.verify_decision(*key)
        checked=history.checked(key[0])
        if checked[-1]!='current' or checked[4] is None:
            raise ValueError('sealed current trial evidence required')
        from agent_harness_lab.private_journal import record
        if record(checked[0]/'execution/results.json',8*1024*1024)[1]!=verified['receipt']['results_sha256']:
            raise ValueError('trial evidence differs from decision receipt')
        sampling=record(checked[0]/'sequential/binding.json',1024*1024)[0] if checked[3].sequential is not None else None
        records=history.trials(checked[2],checked[3],checked[4],sampling=sampling)
        entry=dict(receipt_sha256=key[1],receipt=verified['receipt'],analysis=verified['analysis'],
                   task_pairs=task_pairs(checked[3],checked[4],records,sampling=sampling))
        comparisons.append(entry)
    result=dict(schema_version=1,report_kind='controller_history',algorithm='private-controller-history-report-v1',
        history_store=str(history.root),comparisons=comparisons,publication='private',
        publication_allowed=False,automatic_promotion=False,adoption_authority=False,
        independence='unverified',native_identity='unverified',pooled_treatment_effect=None,
        limitations=['current recomputation is analysis evidence, not certification or adoption',
                     'root independence and native reviewer identity remain unverified'])
    for ref,entry in zip(receipt_refs,comparisons):
        current=history.verify_decision(ref['run_id'],ref['receipt_sha256'])
        if current['receipt']!=entry['receipt'] or current['analysis']!=entry['analysis']:
            raise ValueError('controller report authority changed during collection')
    return result


MAX_ACTION_ROWS=128


def action_timeline(report,trial_id):
    """Project bounded recorded metadata; these rows are not authenticated action custody."""
    unknown=dict(coverage='unknown',events=[],truncated=False)
    if (not isinstance(report,dict) or type(report.get('schema_version')) is not int or report.get('schema_version')!=1 or report.get('privacy')!='private'
            or report.get('trial_id')!=trial_id or report.get('trial_id_visibility')!='observed'
            or report.get('role')!='executor' or not isinstance(report.get('actions'),list)
            or not isinstance(report.get('coverage'),dict)):
        return unknown
    actions=report['actions']; scope=report.get('scope_id');seen=set();events=[]
    if not isinstance(scope,str) or not scope:return unknown
    for row in actions:
        if not isinstance(row,dict) or row.get('scope_id')!=scope:return unknown
        ordinal=row.get('observed_ordinal'); request=row.get('request_ordinal')
        if type(ordinal) is not int or ordinal<1 or ordinal in seen or type(request) is not int or request<0:
            return unknown
        seen.add(ordinal)
        if len(events)>=MAX_ACTION_ROWS:continue
        kind=row.get('kind');kind=kind if kind in ('model_request','tool_request','assistant_emission','message_observation') else 'other'
        decision=row.get('decision');decision=decision if decision in ('accepted','denied','pending_validation') else 'unknown'
        outcome=row.get('worker_outcome');outcome=outcome if outcome in ('pending','observed','unavailable','unobserved') else 'unknown'
        events.append(dict(ordinal=ordinal,request=request,kind=kind,decision=decision,worker_outcome=outcome))
    events.sort(key=lambda row:row['ordinal'])
    coverage=report['coverage']; complete=(coverage.get('records_complete') is True
        and type(coverage.get('omitted_records')) is int and coverage['omitted_records']==0
        and type(coverage.get('omitted_updates')) is int and coverage['omitted_updates']==0)
    truncated=len(actions)>MAX_ACTION_ROWS
    return dict(coverage='recorded_complete' if complete and not truncated else 'partial_or_unknown',
                events=events,truncated=truncated)


def task_pairs(spec,result,records,*,sampling=None):
    """Display scheduled identities and recorded outcomes without changing analysis."""
    from .plan import schedule
    if spec.sequential is not None:
        from .sequential import schedule as sequential_schedule
        planned=sequential_schedule(spec,sampling)
    else:planned=schedule(spec)
    observed={}
    grades={row.trial_id:row for row in records}
    for trial in result['trials']:
        observed.setdefault(trial.get('trial_id'),[]).append(trial)
    pairs={}
    for trial in planned:
        key=tuple(trial[k] for k in ('task','root','family','repetition'))
        versions=pairs.setdefault(key,{})
        matches=observed.get(trial['trial_id'],[])
        side=dict(trial_id=trial['trial_id'],outcome='unknown',runtime='unknown',grader='unknown',
                  timeline=action_timeline(None,trial['trial_id']))
        if len(matches)==1 and all(matches[0].get(k)==v for k,v in trial.items()):
            value=matches[0]; grader=value.get('grader') or {}; execution=value.get('execution') or {}
            side['timeline']=action_timeline(execution.get('controller_actions'),trial['trial_id'])
            image=execution.get('attestation',{}).get('runtime_image',{})
            runtime=image.get('sha256') if image.get('verified_before') is True and image.get('verified_after') is True else None
            if isinstance(runtime,str) and runtime:side['runtime']=runtime
            identity=grader.get('grader_identity')
            if isinstance(identity,dict):side['grader']=identity
            grade=grades.get(trial['trial_id'])
            if (grade is not None and grade.status=='graded' and grade.provenance=='executable_grader'
                    and type(grade.outcome) is bool):
                side['outcome']='passed' if grade.outcome else 'failed'
        versions[trial['version']]=side
    rows=[]
    for key,versions in pairs.items():
        base=versions.get('baseline');candidate=versions.get('candidate')
        status='unknown'
        if base and candidate and base['outcome']!='unknown' and candidate['outcome']!='unknown':
            if (base['runtime']=='unknown' or candidate['runtime']=='unknown' or base['grader']=='unknown'
                    or candidate['grader']=='unknown' or base['runtime']!=candidate['runtime'] or base['grader']!=candidate['grader']):
                status='not comparable'
            elif base['outcome']==candidate['outcome']:status='unchanged'
            elif candidate['outcome']=='passed':status='improved'
            else:status='regressed'
        rows.append(dict(zip(('task','root','family','repetition'),key),baseline=base,candidate=candidate,status=status))
    return rows


def _cell(value):
    value=str(value).replace('\n',' ').replace('\r',' ').replace('\\','\\\\').replace('|','\\|')
    marker='`'*(1+max((len(run) for run in re.findall(r'`+',value)),default=0))
    return marker+' '+value+' '+marker


def render(result):
    if result.get('report_kind')!='controller_history' or result.get('publication_allowed') is not False:
        raise ValueError('private controller report required')
    lines=['# Private controller history report','',
           'Current analysis evidence; no certification, automatic adoption or verified root independence.','']
    sections=[]
    for entry in result['comparisons']:
        receipt=entry['receipt']; analysis=entry['analysis']
        lines+=['## Comparison '+_cell(receipt['run_id']),'',
            'Receipt '+_cell(entry['receipt_sha256'])+'; plan '+_cell(receipt['plan_sha256'])+'.','',
            'Disposition: '+_cell(analysis['status'])+' / '+_cell(analysis.get('reason','unknown'))+'.','',
            '| Cohort | Role / required | Disposition | Effect [low, high] | Roots | Recovered | Introduced | Missing / invalid / unknown |',
            '| --- | --- | --- | --- | ---: | ---: | ---: | --- |']
        rows=[]
        for name,cohort in analysis.get('preservation',{}).get('cohorts',{}).items():
            endpoint=cohort['endpoint']; transitions=cohort['descriptive']['transitions']
            effect=([endpoint[key] for key in ('mean','low','high')] if endpoint else 'unknown')
            row=(name,str(cohort['role'])+' / '+str(cohort['required']),cohort['disposition'],effect,
                 endpoint['independent_roots'] if endpoint else 'unknown',transitions['fail_pass'],
                 transitions['pass_fail'],[transitions[key] for key in ('missing','invalid','unknown')])
            lines.append('| '+' | '.join(_cell(value) for value in row)+' |')
            rows.append('<tr>'+''.join('<td>'+html.escape(str(value))+'</td>' for value in row)+'</tr>')
        pair_headings=('Task','Root','Family','Repeat','Baseline outcome / trial','Candidate outcome / trial','Observed comparison')
        lines+=['','### Task observations','','Statuses describe recorded results, not causation or a changed analysis population.','',
                '| '+' | '.join(pair_headings)+' |','| '+' | '.join('---' for _ in pair_headings)+' |']
        pair_html=[]
        for pair in entry['task_pairs']:
            def label(side):
                return 'unknown' if side is None else side['outcome']+' / '+side['trial_id']
            values=(pair['task'],pair['root'],pair['family'],pair['repetition'],label(pair['baseline']),label(pair['candidate']),pair['status'])
            lines.append('| '+' | '.join(_cell(value) for value in values)+' |')
            pair_html.append('<tr>'+''.join('<td>'+html.escape(str(value))+'</td>' for value in values)+'</tr>')
        timeline_html=[]
        lines+=['','### Recorded action metadata','','Rows are per trial, unaligned across arms, and do not authenticate action custody.','']
        for pair in entry['task_pairs']:
            for arm in ('baseline','candidate'):
                side=pair[arm]
                if side is None:continue
                timeline=side['timeline'];label=arm+' / '+side['trial_id']+' / coverage: '+timeline['coverage']
                lines+=['#### '+_cell(label),'']
                timeline_html.append('<details><summary>'+html.escape(label)+'</summary><ol>')
                for event in timeline['events']:
                    text='ordinal {ordinal}; request {request}; {kind}; decision {decision}; worker {worker_outcome}'.format(**event)
                    lines.append('- '+_cell(text));timeline_html.append('<li>'+html.escape(text)+'</li>')
                timeline_html.append('</ol></details>');lines.append('')
        lines+=['','Full current qualification provenance, assumptions, coverage, per-task/root rates and observations:','']
        raw=json.dumps(entry,sort_keys=True,indent=2,allow_nan=False)
        lines.extend('    '+line for line in raw.splitlines())
        lines.append('')
        headings=('Cohort','Role / required','Disposition','Effect [low, high]','Roots','Recovered','Introduced','Missing / invalid / unknown')
        sections.append('<section><h2>Comparison '+html.escape(receipt['run_id'])+'</h2><p>Disposition: '
            +html.escape(analysis['status'])+' / '+html.escape(str(analysis.get('reason','unknown')))
            +'</p><table><thead><tr>'+''.join('<th scope="col">'+html.escape(value)+'</th>' for value in headings)
            +'</tr></thead><tbody>'+''.join(rows)+'</tbody></table><h3>Task observations</h3><p>Recorded outcomes, not causation.</p><table><thead><tr>'
            +''.join('<th scope="col">'+html.escape(value)+'</th>' for value in pair_headings)
            +'</tr></thead><tbody>'+''.join(pair_html)+'</tbody></table><h3>Recorded action metadata</h3><p>Per trial; unaligned across arms and not authenticated action custody.</p>'
            +''.join(timeline_html)+'<details><summary>Current qualification and analysis</summary><pre>'
            +html.escape(raw)+'</pre></details></section>')
    markdown='\n'.join(lines)+'\n'
    document=('<!doctype html><html lang="en"><meta charset="utf-8"><title>Private controller history report</title>'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; base-uri \'none\'; form-action \'none\'">'
        '<body><h1>Private controller history report</h1><p>Current analysis evidence; no certification or adoption authority.</p>'
        +''.join(sections)+'</body></html>')
    return markdown,document
