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
        entry=dict(receipt_sha256=key[1],receipt=verified['receipt'],analysis=verified['analysis'])
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
        lines+=['','Full current qualification provenance, assumptions, coverage, per-task/root rates and observations:','']
        raw=json.dumps(entry,sort_keys=True,indent=2,allow_nan=False)
        lines.extend('    '+line for line in raw.splitlines())
        lines.append('')
        headings=('Cohort','Role / required','Disposition','Effect [low, high]','Roots','Recovered','Introduced','Missing / invalid / unknown')
        sections.append('<section><h2>Comparison '+html.escape(receipt['run_id'])+'</h2><p>Disposition: '
            +html.escape(analysis['status'])+' / '+html.escape(str(analysis.get('reason','unknown')))
            +'</p><table><thead><tr>'+''.join('<th scope="col">'+html.escape(value)+'</th>' for value in headings)
            +'</tr></thead><tbody>'+''.join(rows)+'</tbody></table><details><summary>Current qualification and analysis</summary><pre>'
            +html.escape(raw)+'</pre></details></section>')
    markdown='\n'.join(lines)+'\n'
    document=('<!doctype html><html lang="en"><meta charset="utf-8"><title>Private controller history report</title>'
        '<body><h1>Private controller history report</h1><p>Current analysis evidence; no certification or adoption authority.</p>'
        +''.join(sections)+'</body></html>')
    return markdown,document
