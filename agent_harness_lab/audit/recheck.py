"""Recheck a caller-selected static convention before an improvement run.

The report is an untrusted selection and parameters, not an authority or proof of
runtime loading. This bounded bridge only supports Codex instruction budgets.
"""
import json
import os
from pathlib import Path
import stat

from agent_harness_lab import audit
from agent_harness_lab.audit import chain, improvement_hint
from agent_harness_lab.evaluation.integrity import open_directory_nofollow, relative

REASONS=frozenset((chain.TRUNCATED, chain.SKIPPED, chain.DECLARED_TRUNCATED, chain.DECLARED_SKIPPED))


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
    if not isinstance(params,dict) or set(params)!={'profile','runtime','task_cwd','codex_project_doc_max_bytes','publication_allowed','authority'}:
        raise ValueError('missing audit recheck parameters')
    cwd=params['task_cwd']; limit=params['codex_project_doc_max_bytes']
    if (params['profile']!='ahl-audit-recheck-parameters-v1' or params['runtime']!='codex'
            or params['authority']!='none' or params['publication_allowed'] is not False
            or not isinstance(cwd,str) or limit is not None and (type(limit) is not int or limit<0)):
        raise ValueError('invalid audit recheck parameters')
    if cwd != '.':
        relative(cwd)
        if '.git' in Path(cwd).parts: raise ValueError('unsafe audit task directory')
        descriptor=open_directory_nofollow(project/cwd); os.close(descriptor)
    findings=report.get('findings')
    if not isinstance(findings,list): raise ValueError('invalid audit findings')
    selected={(row.get('id'),row.get('reason')) for row in findings if isinstance(row,dict)
              and row.get('dimension')=='instructions' and isinstance(row.get('reason'),str) and row.get('reason') in REASONS
              and row.get('status') in ('weak','missing') and isinstance(row.get('id'),str)}
    fresh=audit.run(project,'codex',task_cwd=cwd,codex_project_doc_max_bytes=limit)
    if fresh.status!='completed' or report.get('harness_inventory_sha256')!=fresh.harness_inventory_sha256:
        raise ValueError('audit inventory changed or is unavailable')
    finding=next((row for row in fresh.findings if (row.id,row.reason) in selected
                  and row.status=='nonconformant' and row.dimension=='instructions'),None)
    if finding is None: raise ValueError('no eligible current audit finding')
    return dict(profile='ahl-audit-hypothesis-v1',hypothesis_source='static_convention',
                finding_id=finding.id, reason=finding.reason, convention_source=finding.source,
                inventory_sha256=fresh.harness_inventory_sha256,
                focus=improvement_hint.HINTS[finding.reason], selection='caller_declared_freshly_rechecked',
                snapshot_relation='separate_observation', authority='none',publication_allowed=False)
