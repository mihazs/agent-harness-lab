"""Opt-in fixed repeated-bank improvement, private and recommendation-only."""
from dataclasses import replace
from pathlib import Path
import time

from agent_harness_lab.private_journal import encoded,record,write_bytes
from .integrity import contained,copy_frozen,digest,read_regular
from .oracle import identity
from .plan import ExperimentSpec
from .precision import preflight
from .regressions import fields,sha256,text

PROFILE='ahl-improve-fixed-bank-v1'
REQUEST_FIELDS='schema_version profile manifest manifest_sha256 bank_sha256 experiment_id repeats seed max_trials min_roots effect_margin family_margin alpha family_weights precision_half_width operator_review cache_policy'


def disjoint(paths,inputs):
    for path in paths:
        for root in inputs:
            if path.is_relative_to(root) or root.is_relative_to(path):
                raise ValueError('private comparison inputs must remain outside every model input view')


def canonical(value):
    path=Path(value).absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('canonical symlink-free comparison paths required')
    return path


def request_fields(value):
    fields(value, REQUEST_FIELDS + (' root_similarity' if 'root_similarity' in value else ''))
    if 'root_similarity' in value and type(value['root_similarity']) is not bool:
        raise ValueError('root similarity option must be boolean')


def selected_store(args,output=None):
    value=getattr(args,'evaluation_store',None)
    if value is None:return None,None
    path=canonical(value);request=canonical(args.experiment_request)
    declaration,pin=record(request,64*1024);request_fields(declaration)
    if type(declaration['schema_version']) is not int or declaration['schema_version']!=1 or declaration['profile']!=PROFILE:
        raise ValueError('unsupported repeated workflow request')
    previous=getattr(args,'_evaluation_request_sha256',None)
    if previous is not None and previous!=pin:raise ValueError('fixed request changed after selected-store preflight')
    source=canonical(declaration['manifest']);manifest,source_pin=record(source,1024*1024)
    if source_pin!=declaration['manifest_sha256']:raise ValueError('selected-store source manifest changed')
    inputs=[request,source.parent,canonical(args.project),canonical(args.image_root),canonical(args.inference_settings)]
    authority=manifest.get('bank_authority')
    if authority is not None:
        if not isinstance(authority,dict) or not isinstance(authority.get('bank_path'),str):
            raise ValueError('invalid selected-store bank authority locator')
        bank=canonical(authority['bank_path'])
        if len(bank.parents)<3:raise ValueError('bounded bank owner locator required')
        inputs.append(bank.parents[2])
    if output is not None:inputs.append(canonical(output))
    disjoint([path],inputs)
    return path,pin


class Request:
    def __init__(self,args,output,deadline):
        if args.agent!='responses' or getattr(args,'command','improve')!='improve':
            raise ValueError('fixed-bank requests require explicit controlled improve')
        self.permission_profile=getattr(args,'permission_audit',None)
        self.baseline_bundle=None
        self.path=canonical(args.experiment_request);self.deadline=deadline
        self.value,self.request_hash=record(self.path,64*1024)
        request_fields(self.value)
        selected,pin=selected_store(args,output)
        if pin is not None and pin!=self.request_hash:raise ValueError('selected-store request changed')
        self.store=selected if selected is not None else output/'private-history'
        value=self.value
        if type(value['schema_version']) is not int or value['schema_version']!=1 or value['profile']!=PROFILE:
            raise ValueError('unsupported repeated workflow request')
        if value['cache_policy']!='fresh_controller_calls_provider_cache_unknown':
            raise ValueError('unsupported cache policy; provider cache behavior remains unknown')
        text(value['operator_review']);text(value['experiment_id'])
        self.source=canonical(value['manifest']);sha256(value['manifest_sha256']);sha256(value['bank_sha256'])
        self.private=[self.path,self.source.parent,canonical(args.inference_settings)]
        for name in ('source_cache',):
            if getattr(args,name,None) is not None:self.private.append(canonical(getattr(args,name)))
        sink=getattr(args,'source_sink',None)
        if sink is not None:self.private.append(canonical(sink.store.path))
        self.views=[canonical(args.project),canonical(args.image_root)]
        disjoint([*self.private,canonical(output)],self.views)
        disjoint([self.source.parent],[canonical(output)])
        from .responses import read_settings
        settings,_=read_settings(args.inference_settings)
        disjoint([canonical(settings['credential_file']),*self.private[2:]],[self.source.parent])
        disjoint([canonical(settings['credential_file'])],self.views)
        self.check()
        self.root=output/'fixed-bank';copy_frozen(self.source.parent,self.root,value['bank_sha256'])
        self.manifest=self.root/self.source.name
        source,pin=record(self.manifest,1024*1024)
        if pin!=value['manifest_sha256']:raise ValueError('fixed bank manifest identity differs')
        if source.get('skills') or source.get('harness_paths') or 'experiment' in source or 'protected_access' in source:
            raise ValueError('fixed-bank request needs exposed artifact tasks and a newly controller-bound plan')
        tasks=source.get('tasks')
        if not isinstance(tasks,list) or not 2<=len(tasks)<=1000 or any(not isinstance(t,dict) for t in tasks):
            raise ValueError('bounded repeated task bank required')
        if any(t.get('split') not in ('development','regression') for t in tasks):
            raise ValueError('improvement bank must expose development/regression tasks only')
        from .bank_tools import frozen_grader
        if not isinstance(source.get('grader_bundles'),dict):raise ValueError('fixed bank grader bundles must be an object')
        for task in tasks:
            declaration=source.get('grader_bundles',{}).get(task['id'])
            folder=contained(self.root,declaration['path'])
            task['grader']=['/usr/bin/python3',str(folder/'grade.py.txt'),'{workspace}',str(folder/'expected.json')]
            frozen_grader(self.root,task,declaration)
            fixture=contained(self.root,task['fixture'])
            disjoint([folder], [fixture])
        if set(source['grader_bundles'])!={t['id'] for t in tasks}:raise ValueError('exact selected grader set required')
        from .oracle_history import bank_authority
        bank_authority(source,export_root=self.root)
        if source.get('bank_authority') is not None:
            bank_path=canonical(source['bank_authority']['bank_path'])
            self.private.append(bank_path.parents[2])
            disjoint(self.private,self.views)
        self.source_value=source
        weights=value['family_weights']
        if not isinstance(weights,dict):raise ValueError('frozen family weights required')
        similarity = None
        if value.get('root_similarity', False):
            from .root_similarity import manifest_evidence
            similarity = manifest_evidence(source)
        from agent_harness_lab.sources.schema import digest as metadata_digest
        self.template=ExperimentSpec(value['experiment_id'],tuple((t['id'],t['root'],t['family']) for t in tasks),
            value['repeats'],value['seed'],value['max_trials'],value['min_roots'],value['effect_margin'],
            value['family_margin'],value['alpha'],tuple(sorted(weights.items())),'unbound-baseline','unbound-candidate',
            identity(self.root,tasks,source['grader_bundles']),'unbound-runtime',
            precision_half_width=value['precision_half_width'],
            root_similarity_sha256=metadata_digest(similarity) if similarity is not None else None)
        self.template.validate()
        trials=2*len(tasks)*self.template.repeats
        self.blocked_reason=('fixed_comparison_inference_budget_infeasible'
            if 2+2*trials>settings['max_requests'] or 1+trials>settings['max_tool_calls'] else None)
        if getattr(args,'project_settings',None) is not None and 1+trials>args.trial_limit:
            self.blocked_reason='configured_shared_trial_budget_infeasible'
        remaining=self.check()
        self.precision=preflight(self.template,trial_limit=value['max_trials'],budget_seconds=remaining,
            root_similarity_evidence=similarity,root_similarity_bank=self.manifest if similarity is not None else None)
        write_bytes(output/'fixed-request.json',read_regular(self.path,64*1024))
        write_bytes(output/'fixed-request-binding.json',encoded(dict(profile=PROFILE,request_sha256=self.request_hash,
            operator_review_status='operator_declared_unverified',publication_allowed=False)))
        self.frozen_hash=digest(self.root)

    def check(self):
        if time.monotonic()>=self.deadline:raise TimeoutError('fixed comparison request deadline exhausted')
        if (record(self.path,64*1024)[1]!=self.request_hash or record(self.source,1024*1024)[1]!=self.value['manifest_sha256']
                or digest(self.source.parent)!=self.value['bank_sha256']):
            raise ValueError('fixed request or bank bytes changed')
        remaining=self.deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('fixed comparison request deadline exhausted during readback')
        return remaining

    def protect(self,inputs,settings):
        self.check()
        secrets=[canonical(settings['credential_file']),*self.private,self.root,self.store]
        disjoint(secrets,[canonical(p) for p in inputs])
        disjoint([canonical(settings['credential_file']),canonical(self.private[2])],[self.source.parent,self.root])

    def freeze_baseline(self,baseline):
        from .comparison_custody import bundle_binding
        self.baseline_bundle=bundle_binding(baseline,self.deadline)

    def bind(self,baseline,candidate,image_sha256):
        self.check()
        if digest(self.root)!=self.frozen_hash:raise ValueError('frozen comparison bank changed')
        from agent_harness_lab.cli import evaluator
        runner=evaluator()
        spec=replace(self.template,baseline_hash=runner.digest(baseline),candidate_hash=runner.digest(candidate),runtime_hash=image_sha256)
        spec.validate()
        from .comparison_custody import bundle_binding
        if bundle_binding(baseline,self.deadline)!=self.baseline_bundle:raise ValueError('frozen baseline bundle changed')
        bundles=dict(baseline=self.baseline_bundle,candidate=bundle_binding(candidate,self.deadline))
        source=dict(self.source_value,experiment=spec.to_dict(),max_trials=spec.max_trials,workflow_bundles=bundles,
            workflow_request_sha256=self.request_hash,no_nested_agents=True)
        write_bytes(self.root/'bound-manifest.json',encoded(source))
        manifest=self.root/'bound-manifest.json'
        from .oracle_history import bank_authority
        bank_authority(source,export_root=self.root)
        return manifest

    def execute(self,manifest,baseline,candidate,args,client,backend):
        from .oracle_history import HistoryStore
        store=HistoryStore(self.store)
        self.check()
        summary=store.run(manifest,baseline,candidate,cgroup_root=args.cgroup_root,image_root=args.image_root,
            image_sha256=args.image_sha256,disk_bytes=args.disk_bytes,budget_seconds=min(300,self.deadline-time.monotonic()),
            parent_deadline=self.deadline,inference_client=client,inference_settings=args.inference_settings,
            sandbox_backend=backend,source_sink=getattr(args,'source_sink',None),workflow_request=manifest.parent.parent/'fixed-request.json',permission_profile=self.permission_profile)
        self.check()
        if self.permission_profile is not None and (backend.permission_audit!=self.permission_profile
                or backend.permission_qualification()['qualification']!='qualified'):
            return dict(status='requires_intervention',reason='permission_qualification_not_met',
                controller_run_id=summary['run_id'],publication_allowed=False,automatic_promotion=False)
        if summary['state']!='sealed':
            return dict(status='incomplete',reason='fresh_comparison_incomplete',controller_run_id=summary['run_id'],
                analysis=store.analyze(summary['run_id']),publication_allowed=False,automatic_promotion=False)
        receipt=store.decision(summary['run_id'])
        checked=store.verify_decision(summary['run_id'],receipt['receipt_sha256'])
        if time.monotonic()>=self.deadline:raise TimeoutError('comparison decision deadline exhausted')
        self.check()
        return dict(status='inconclusive',reason='certification_prerequisites_unverified',
            recommendation='review_candidate' if checked['analysis']['status']=='supported' else 'candidate_not_supported',
            controller_run_id=summary['run_id'],decision=checked,analysis=checked['analysis'],
            request_sha256=self.request_hash,operator_review='unverified',served_model='unverified',native_identity='unverified',
            cache_policy=self.value['cache_policy'],publication_allowed=False,automatic_promotion=False)
