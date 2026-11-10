"""Recheck a caller-selected static convention before an improvement run.

The report is an untrusted selection and parameters, not an authority or proof of
runtime loading. This bounded bridge supports selected Codex conventions.
"""
import json
import os
from pathlib import Path
import stat
import time

from agent_harness_lab import audit, surfaces
from agent_harness_lab.audit import chain, improvement_hint, instruction_totals
from agent_harness_lab.evaluation.integrity import open_directory_nofollow, relative
from agent_harness_lab.sources import harness_inventory
from agent_harness_lab.sources.git_view import Folder

REASONS=frozenset((chain.TRUNCATED, chain.SKIPPED, chain.DECLARED_TRUNCATED, chain.DECLARED_SKIPPED))
ROUTING='skill_routing_collision'
ROUTING_FOCUS='Investigate overlapping skill descriptions and evaluate whether routing distinguishes the skills.'
PROCEDURE='instruction_procedure_section_detected'
SOFT_BUDGET='instruction_launch_lines_above_soft_budget'
PROCEDURE_SOURCE='https://code.claude.com/docs/en/features-overview'


def select(path, project, runtime):
    if runtime != 'codex': raise ValueError('audit recheck requires the Codex runtime')
    descriptor=os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode): raise ValueError('invalid audit report')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream: raw=stream.read(2_000_001)
    finally: os.close(descriptor)
    if len(raw)>2_000_000: raise ValueError('audit report exceeds size bound')
    try: report=json.loads(raw)
    except (ValueError, UnicodeError): raise ValueError('invalid audit report') from None
    if not isinstance(report,dict): raise ValueError('invalid audit report')
    params=report.get('improvement_recheck')
    keys={'profile','runtime','task_cwd','codex_project_doc_max_bytes','publication_allowed','authority'}
    if not isinstance(params,dict) or params.get('profile') not in ('ahl-audit-recheck-parameters-v1','ahl-audit-recheck-parameters-v2'):
        raise ValueError('missing audit recheck parameters')
    version2=params['profile']=='ahl-audit-recheck-parameters-v2'
    if set(params)!=(keys | {'instruction_soft_lines'} if version2 else keys):
        raise ValueError('invalid audit recheck parameters')
    cwd=params['task_cwd']; limit=params['codex_project_doc_max_bytes']
    soft=params.get('instruction_soft_lines')
    if (params['runtime']!='codex'
            or params['authority']!='none' or params['publication_allowed'] is not False
            or not isinstance(cwd,str) or limit is not None and (type(limit) is not int or limit<0)
            or version2 and (type(soft) is not int or soft<0)):
        raise ValueError('invalid audit recheck parameters')
    if cwd != '.':
        relative(cwd)
        if '.git' in Path(cwd).parts: raise ValueError('unsafe audit task directory')
        descriptor=open_directory_nofollow(project/cwd); os.close(descriptor)
    findings=report.get('findings')
    if not isinstance(findings,list): raise ValueError('invalid audit findings')
    selected={(row.get('id'),row.get('reason')) for row in findings if isinstance(row,dict)
              and isinstance(row.get('reason'),str)
              and ((row.get('dimension')=='instructions' and row.get('reason') in REASONS | {PROCEDURE} | ({SOFT_BUDGET} if version2 else set()))
                   or (row.get('dimension')=='skills' and row.get('reason')==ROUTING))
              and row.get('status') in ('weak','missing') and isinstance(row.get('id'),str)}
    profile=dict(schema_version=1,runtime='codex',version=surfaces.PINS['codex'],task_cwd=cwd)
    inventory=harness_inventory.capture(project,profile,schema_version=2)
    fresh=audit.audit(inventory,task_cwd=cwd,codex_project_doc_max_bytes=limit)
    if fresh.status!='completed' or report.get('harness_inventory_sha256')!=fresh.harness_inventory_sha256:
        raise ValueError('audit inventory changed or is unavailable')
    finding=next((row for row in fresh.findings if (row.id,row.reason) in selected
                  and row.status=='nonconformant'
                  and ((row.reason in REASONS and row.dimension=='instructions')
                       or (row.reason==ROUTING and row.dimension=='commands'))),None)
    shape_reasons=(PROCEDURE,SOFT_BUDGET) if version2 else (PROCEDURE,)
    if finding is None and any((audit.digest(['instructions','.','instruction',reason]),reason) in selected for reason in shape_reasons):
        with Folder(project.parent,time.monotonic()+30) as folder:
            totals=instruction_totals.codex(profile,folder,project.name,inventory,declared_limit=limit)
        for reason in shape_reasons:
            if ((audit.digest(['instructions','.','instruction',reason]),reason) not in selected or totals['status']!='declared'
                    or not totals['files']): continue
            if reason==PROCEDURE and not totals['shape']['procedure_sections']: continue
            if reason==SOFT_BUDGET and totals['lines']<=soft: continue
            result=dict(profile='ahl-audit-hypothesis-v1',hypothesis_source='static_convention',
                finding_id=audit.digest(['instructions','.','instruction',reason]),reason=reason,
                convention_source=PROCEDURE_SOURCE,inventory_sha256=fresh.harness_inventory_sha256,
                focus=improvement_hint.HINTS[reason],selection='caller_declared_freshly_rechecked',
                snapshot_relation='separate_observation',authority='none',publication_allowed=False)
            if reason==SOFT_BUDGET:
                result.update(instruction_soft_lines=soft,threshold_origin='caller_declared',
                              convention_source_role='general_reference_not_threshold')
            return result
    if finding is None: raise ValueError('no eligible current audit finding')
    return dict(profile='ahl-audit-hypothesis-v1',hypothesis_source='static_convention',
                finding_id=finding.id, reason=finding.reason, convention_source=finding.source,
                inventory_sha256=fresh.harness_inventory_sha256,
                focus=ROUTING_FOCUS if finding.reason==ROUTING else improvement_hint.HINTS[finding.reason],
                selection='caller_declared_freshly_rechecked',
                snapshot_relation='separate_observation', authority='none',publication_allowed=False)
