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
from agent_harness_lab.sources.claude_instructions import scope_of
from agent_harness_lab.sources.git_view import Folder

REASONS=frozenset((chain.TRUNCATED, chain.SKIPPED, chain.DECLARED_TRUNCATED, chain.DECLARED_SKIPPED))
ROUTING='skill_routing_collision'
ROUTING_FOCUS='Investigate overlapping skill descriptions and evaluate whether routing distinguishes the skills.'
PROCEDURE='instruction_procedure_section_detected'
SOFT_BUDGET='instruction_launch_lines_above_soft_budget'
PROCEDURE_SOURCE='https://code.claude.com/docs/en/features-overview'


class InventoryBlocked(ValueError):
    """The fresh inventory exceeded the entry bound; carries the typed reason and the largest directories."""
    def __init__(self, remedy):
        super().__init__('audit inventory blocked: ' + remedy['reason'])
        self.remedy = remedy


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
    remedy=harness_inventory.entry_bound_remedy(project,profile) if inventory.blocked_reason=='entry_bound_exceeded' else None
    if remedy: raise InventoryBlocked(remedy)
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


# Automatic discovery for a first run whose history has findings but no verified outcome (#332). It reuses the hypothesis
# shape of select(); the audit is fresh, read-only and bounded, and calls no model.
AUTOMATIC='automatic_no_trace_candidate'
SOFT_LINES=200  # the `ahl audit` default; a convention, not a validated threshold
RUNTIMES={'codex':'codex','claude':'claude-code'}
# The oversized-file conventions have their own focus text; select() and --from-audit do not accept these reasons.
OVERSIZED={'instruction_exceeds_launch_bytes':'Investigate whether the oversized project instruction file can be simplified safely.',
           'instruction_exceeds_project_doc_bytes':'Investigate whether the oversized project instruction file can be simplified safely.'}
# Most content-losing first: a chain the runtime cuts, then an oversized file, a routing collision, then shape conventions.
PRIORITY=(chain.DECLARED_SKIPPED,chain.SKIPPED,chain.DECLARED_TRUNCATED,chain.TRUNCATED,
          *OVERSIZED,ROUTING,SOFT_BUDGET,PROCEDURE)
# What discover() evaluates per runtime: it audits the project root with the default budget, so the declared-budget chain
# reasons never apply, and the chain and shape conventions need settled instruction totals (a project root, no unresolved import).
APPLICABLE={'codex':(chain.SKIPPED,chain.TRUNCATED,'instruction_exceeds_project_doc_bytes',ROUTING,SOFT_BUDGET,PROCEDURE),
            'claude':('instruction_exceeds_launch_bytes',ROUTING,SOFT_BUDGET,PROCEDURE)}
NEEDS_TOTALS=frozenset((chain.SKIPPED,chain.TRUNCATED,SOFT_BUDGET,PROCEDURE))


def discover(project, agent, *, soft_lines=SOFT_LINES):
    """Freshly audit the project and return every eligible static convention, best first, for runtime ``agent``.

    Every candidate carries ``hypothesis_source`` static_convention and no authority. ``checked`` lists the conventions
    actually evaluated and ``unsettled`` the applicable ones that could not be: a blocked inventory evaluates none, and
    unsettled instruction totals leave the chain, line and procedure conventions unknown, never negative."""
    runtime=RUNTIMES[agent]
    profile=dict(schema_version=1,runtime=runtime,version=surfaces.PINS[runtime],task_cwd='.')
    inventory=harness_inventory.capture(project,profile,schema_version=2)
    fresh=audit.audit(inventory,task_cwd='.')
    found=dict(runtime=runtime,inventory_status=fresh.status,inventory_sha256=fresh.harness_inventory_sha256,
               blocked_reason=fresh.blocked_reason,findings=len(fresh.findings),candidates=[],
               checked=[],unsettled=list(APPLICABLE[agent]),instruction_totals=None)
    if fresh.status!='completed': return found
    def hypothesis(finding_id, reason, source, focus, **more):
        return dict(profile='ahl-audit-hypothesis-v1',hypothesis_source='static_convention',finding_id=finding_id,reason=reason,
            convention_source=source,inventory_sha256=fresh.harness_inventory_sha256,focus=focus,selection=AUTOMATIC,
            snapshot_relation='separate_observation',authority='none',publication_allowed=False,**more)
    rows=[]
    for row in fresh.findings:
        if row.status!='nonconformant': continue
        if row.dimension=='instructions' and row.reason in REASONS | set(OVERSIZED):
            focus=OVERSIZED.get(row.reason) or improvement_hint.HINTS[row.reason]
        elif row.dimension=='commands' and row.reason==ROUTING: focus=ROUTING_FOCUS
        else: continue
        rows.append(hypothesis(row.id,row.reason,row.source,focus))
    try:
        with Folder(project.parent,time.monotonic()+30) as folder:
            if runtime=='codex': totals=instruction_totals.codex(profile,folder,project.name,inventory)
            else:
                expected={n.path_sha256:n.content_sha256 for n in inventory.nodes if n.origin=='checkout' and n.kind=='instruction'
                          and n.content_sha256 is not None and scope_of(n.path) is not None}
                totals=instruction_totals.claude(profile,folder,project.name,expected=expected)
    except (ValueError,OSError,TimeoutError): totals=dict(status='unknown',files=None,reasons=['instruction_totals_unavailable'])
    found['instruction_totals']=dict(status=totals['status'],reasons=totals['reasons'])
    settled=totals['status']=='declared'
    cut={row['reason'] for row in rows}  # a finding is a settled observation even when the totals are not
    found['checked']=[reason for reason in APPLICABLE[agent] if settled or reason not in NEEDS_TOTALS or reason in cut]
    found['unsettled']=[reason for reason in APPLICABLE[agent] if reason not in found['checked']]
    if settled and totals['files']:
        identity=lambda reason: audit.digest(['instructions','.','instruction',reason])
        if totals['lines']>soft_lines:
            rows.append(hypothesis(identity(SOFT_BUDGET),SOFT_BUDGET,PROCEDURE_SOURCE,improvement_hint.HINTS[SOFT_BUDGET],
                instruction_soft_lines=soft_lines,threshold_origin='assumed_default',convention_source_role='general_reference_not_threshold'))
        if totals['shape']['procedure_sections']:
            rows.append(hypothesis(identity(PROCEDURE),PROCEDURE,PROCEDURE_SOURCE,improvement_hint.HINTS[PROCEDURE]))
    found['candidates']=sorted(rows,key=lambda row:(PRIORITY.index(row['reason']),row['finding_id']))
    return found
