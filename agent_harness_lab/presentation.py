"""Bounded local presentation. Observations here never authorize execution."""
import json
import math
import sys

from .monitoring.state import EXIT_CODES

STAGES=frozenset(('preflight','evidence','proposal','evaluation','comparison','final evaluation','application','completion'))
NEXT_ACTIONS={
    'select_one_runtime_with_agent':'Select --agent codex or --agent claude and run improve again.',
    'agent_runtime_unavailable':'Install or configure the selected supported runtime, then run improve again.',
    'configured_profile_does_not_allow_cooperative_improvement':'Review the project profile; execution requires an explicitly supported cooperative configuration.',
    'configured_bank_digest_mismatch':'Review the selected bank and its expected digest in ahl.json.',
    'configured_project_bank_integration_pending':'Custom-bank improvement is unavailable; retain that bank for explicit reviewed evaluation or choose a supported workflow.',
    'trial_budget_requires_two_preparation_and_six_paired_trials':'Configure at least eight trial slots for preparation and paired evaluation.',
    'shared_trial_budget_exhausted':'Inspect completed work and review the next authorized design; the shared trial ceiling was reached.',
    'unresolved_operation_requires_hook_contract':'Review the whole batch; executable or hook facets need a supported validation contract before application.',
    'permission_qualification_not_met':'Restore complete permission-result capture and inspect the worker diagnostics before a new authorized run.',
    'final_comparison_incomplete':'Inspect final-comparison diagnostics in private artifacts; incomplete work cannot authorize application.',
    'conditional_application_requires_plain_controlled_improve':'Use plain improve with its signed controlled application configuration.',
    'inference_sandbox_unavailable':'Restore the configured Linux worker image and delegated resource controls before retrying.',
    'grader_sandbox_unavailable':'Restore the configured grader worker and delegated resource controls before retrying.',
    'precision_target_infeasible':'Review the predeclared design and allowed budget before a new authorized run.',
    'candidate_requires_independent_certification':'Inspect the evaluated change plan and its unmet prerequisites in the private artifacts.',
    'run_busy':'Another run or stale lock exists; verify its PID and let the active run finish before retrying.',
    'configuration_changed':'Review the changed project configuration and start a new authorized run with current inputs.',
    'runtime_references_changed':'Restore or explicitly review changed runtime references before a new authorized run.',
    'target_conflict':'Preserve intervening target changes and review the recorded deployment before retrying or rolling back.',
    'authorization_changed':'Review current authorization and retirement state; do not replay a consumed approval.',
    'invalid_bank':'Review the selected bank schema, content and expected digest before execution.',
    'invalid_runtime':'Review the configured supported runtime and explicit executable or Responses references.',
    'invalid_budget':'Provide a finite positive supported run budget within the authorized limits.',
    'grader_self_test_rejected':'Repair the grader controls before a new authorized evaluation.',
    'project_unavailable':'Select an existing authorized project directory.',
    'path_permission_unavailable':'Restore authorized local read access without elevation, then run doctor for diagnostics.',
    'local_reference_missing':'Restore the selected local references, then run doctor for metadata diagnostics.',
}
ERROR_REASONS={
    'project must be an existing directory':'project_unavailable',
    'project configuration changed during improvement':'configuration_changed',
    'project configuration changed during evaluation':'configuration_changed',
    'controlled Responses references changed during improvement':'runtime_references_changed',
    'comparison inference settings changed':'runtime_references_changed',
    'target changed before application':'target_conflict',
    'Project harness changed during evaluation; recommendation is stale':'target_conflict',
    'target inventory changed during planned application':'target_conflict',
    'target changed during transaction':'target_conflict',
    'newer deployment conflict':'target_conflict',
    'intervening edit/mode/path conflict':'target_conflict',
    'bank static qualification is stale or revoked':'authorization_changed',
    'review policy is no longer current':'authorization_changed',
    'invalid configured bank':'invalid_bank','invalid configured bank digest':'invalid_bank',
    'invalid configured runtime':'invalid_runtime','execution profiles require an explicit runtime':'invalid_runtime',
    'timeout must be positive':'invalid_budget',
}


def next_action(reason=None,status=None,*,has_artifacts=True):
    if isinstance(reason,str) and reason in NEXT_ACTIONS:return NEXT_ACTIONS[reason]
    if status=='applied':return 'Inspect the deployment receipt and recorded rollback in the private artifacts.'
    if status=='no_change':return 'Review the recorded no-change outcome; this run applied no target edit.'
    if status=='budget_exhausted':return 'Inspect recorded work before another authorized run; the shared run budget was exhausted.'
    return ('Inspect the private run artifacts for the next supported action.' if has_artifacts else
            'Use ahl doctor for local setup diagnostics and ahl improve --help for supported options.')


def error_reason(error):
    if isinstance(error,PermissionError):return 'path_permission_unavailable'
    if isinstance(error,FileNotFoundError):return 'local_reference_missing'
    message=str(error)
    if message in ERROR_REASONS:return ERROR_REASONS[message]
    for prefix,reason in (('Another run or stale lock exists: ','run_busy'),('Grader self-test rejected: ','grader_self_test_rejected')):
        if message.startswith(prefix):return reason
    return None


def _number(value):return type(value) in (int,float) and math.isfinite(value) and value>=0


def _text(value):
    return ''.join(c if c.isprintable() else '?' for c in str(value))[:4096]


class Presentation:
    def __init__(self,*,force_json=False,stdout=None,stderr=None):
        self.stdout=sys.stdout if stdout is None else stdout;self.stderr=sys.stderr if stderr is None else stderr
        try:self.human=not force_json and self.stdout.isatty()
        except OSError:self.human=False
        self.elapsed=self.remaining_seconds=self.budget_stage=None;self.requests=None;self.request_limit=None
        self.output_available=self.progress_available=True

    def _write(self,value,*,progress=False):
        if not (self.progress_available if progress else self.output_available):return
        try:print(value,file=self.stderr if progress else self.stdout,flush=True)
        except OSError:
            if progress:self.progress_available=False
            else:self.output_available=False

    def measure(self,name,remaining,limit):
        if not self.human or not isinstance(name,str) or name not in STAGES or not _number(limit):return
        value=remaining();elapsed=limit-value
        if type(value) in (int,float) and math.isfinite(value) and _number(elapsed):
            self.elapsed,self.remaining_seconds,self.budget_stage=elapsed,max(0,value),name

    def stage(self,name,client=None):
        if not self.human or not isinstance(name,str) or name not in STAGES:return
        if name=='preflight':self.elapsed=self.remaining_seconds=self.budget_stage=self.requests=self.request_limit=None
        if name=='completion':
            requests=getattr(client,'requests',None);settings=getattr(client,'settings',{})
            limit=settings.get('max_requests') if isinstance(settings,dict) else None
            self.requests=requests if type(requests) is int and requests>=0 else None
            self.request_limit=limit if type(limit) is int and limit>=0 else None
        self._write('AHL: '+name,progress=True)

    def finish(self,result,*,compact=False):
        try:
            if not self.human:
                self._write(json.dumps(result,indent=None if compact else 2));return
            status=result.get('status');status=status if isinstance(status,str) and (status in EXIT_CODES or status=='requires_intervention') else 'unknown'
            self._write('Outcome: '+status)
            changed=result.get('changed_files')
            self._write('Changed files: '+(str(len(changed)) if isinstance(changed,list) else 'unknown'))
            if self.elapsed is not None:
                self._write(f'Budget at {self.budget_stage}: elapsed {self.elapsed:.1f}s; remaining {self.remaining_seconds:.1f}s')
            if self.requests is not None:
                self._write('Request attempts: '+str(self.requests)+(('/'+str(self.request_limit)) if self.request_limit is not None else ''))
            self._write('Artifacts: '+(_text(result['output']) if isinstance(result.get('output'),str) else 'unavailable'))
            self._write('Next action: '+next_action(result.get('reason',result.get('recommendation')),status,has_artifacts=isinstance(result.get('output'),str)))
        finally:self.elapsed=self.remaining_seconds=self.budget_stage=None

    def error(self,error,output=None):
        reason=error_reason(error)
        if self.human:
            self.finish(dict(status='invalid_input',reason=reason,**({'output':str(output)} if output is not None else {})))
        else:
            action=next_action(reason,has_artifacts=output is not None)
            location=(' Artifacts: '+_text(output)) if output is not None else ''
            self._write('ahl: '+(reason+': ' if reason else '')+action+location,progress=True)


def stage(args,name,*,remaining=None,client=None):
    presentation=getattr(args,'_presentation',None)
    if presentation is None:return
    presentation.stage(name,client)
    if remaining is not None:presentation.measure(name,remaining,args.timeout)
