"""Actual private offline comparisons for guarded search, never callback certification."""
from copy import deepcopy
from dataclasses import asdict,replace
import hashlib
import math
from pathlib import Path
import shutil
import stat
import time

from agent_harness_lab.private_journal import PrivateJournal,encoded,record,sync_directory,write_bytes
from agent_harness_lab.evaluation import cohort_history,decision_receipts
from agent_harness_lab.evaluation.bank_revisions import sync_tree
from agent_harness_lab.evaluation.bank_tools import frozen_grader
from agent_harness_lab.evaluation.integrity import contained,read_regular,regular_sha256,relative,tree
from agent_harness_lab.evaluation.oracle import identity
from agent_harness_lab.evaluation.oracle_history import HistoryStore,bank_authority
from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.evaluation.regressions import fields,sha256,text

ALGORITHM='guarded-preservation-search-v1'
MAX_BYTES=10*1024*1024
MAX_FILES=1000
MAX_ATTEMPTS=16
MAX_CONTEXTS=16
MAX_PREPARED_BYTES=64*1024*1024


def hash_value(value): return hashlib.sha256(encoded(value)).hexdigest()


def location(value):
    if not isinstance(value,(str,Path)): raise ValueError('explicit search input path required')
    path=Path(value).absolute()
    text(str(path))
    if len(str(path).encode('utf-8'))>4096: raise ValueError('search path byte bound exceeded')
    if '..' in path.parts or any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('search paths must be canonical and symlink-free')
    return path


def scan(path):
    files=tree(path,max_files=MAX_FILES,max_bytes=MAX_BYTES); data={}; remaining=MAX_BYTES
    for name,metadata in files.items():
        raw=read_regular(contained(path,name),remaining); remaining-=len(raw)
        if hashlib.sha256(raw).hexdigest()!=metadata['sha256']: raise ValueError('search input changed during capture')
        data[name]=raw
    directories={'.':stat.S_IMODE(path.stat().st_mode)}
    for index,item in enumerate(path.rglob('*')):
        if index>=5000: raise ValueError('search directory count bound exceeded')
        if item.is_symlink(): raise ValueError('search input contains a symlink')
        if item.is_dir(): directories[str(item.relative_to(path))]=stat.S_IMODE(item.stat().st_mode)
    return dict(files=files,directories=directories),data


def runtime_hash(data):
    digest=hashlib.sha256()
    for name,raw in sorted(data.items(),key=lambda item:Path(item[0])): digest.update(name.encode()+b'\0'+raw)
    return digest.hexdigest()


def feasibility(spec,analysis,*,baseline):
    """No-op feasibility excludes only superiority, never coverage or NI gates."""
    preservation=analysis.get('preservation',{}); coverage=preservation.get('descriptive',{}).get('coverage',{})
    expected=2*len(spec.tasks)*spec.repeats
    complete=(coverage.get('planned_trials')==expected and coverage.get('received_records')==expected
        and all(coverage.get(k)==0 for k in ('missing_trials','duplicate_trial_ids','duplicate_records','invalid_records',
            'invalid_grades','unknown_grades','provenance_mismatches')) and coverage.get('unexpected_trial_ids')==[])
    effects=analysis.get('behavior')
    observed=effects is None or (effects.get('critical_candidate_effect') is False and effects.get('required_observation_unknown') is False)
    required=[value for value in preservation.get('cohorts',{}).values() if value.get('required') is True]
    ni=(all(value.get('disposition')=='supported' for value in required)
        and bool(analysis.get('families')) and all(value['low']>=-spec.family_margin for value in analysis.get('families',{}).values()))
    eligible=(analysis.get('oracle_validity')=='current' and complete and observed and ni and analysis.get('roots',0)>=spec.min_roots
        and analysis.get('status') not in ('rejected','blocked','incomplete')
        and (baseline or analysis.get('status')=='supported'))
    return dict(safe=eligible,qualified_feasible=eligible,retain_no_op=baseline,
        metrics={'primary_effect':0.0 if baseline else analysis['primary']['mean']} if eligible else {},
        reason='qualified_no_op' if eligible and baseline else 'supported_development_comparison' if eligible else analysis.get('reason','unqualified'),
        feasibility_scope='frozen_endpoint_criteria_only',independence='unverified',native_identity='unverified',
        automatic_promotion=False,publication_allowed=False,adoption_authority=False)


class HistorySearchContext:
    def __init__(self,store_path,baseline_dir,frozen_manifest,resource_profile,allowed_files):
        self.store=HistoryStore(location(store_path)); self.baseline=location(baseline_dir); self.manifest=location(frozen_manifest)
        fields(resource_profile,'cgroup_root image_root image_sha256 disk_bytes budget_seconds work_root')
        self.resources=deepcopy(resource_profile)
        for key in ('cgroup_root','image_root','work_root'): self.resources[key]=str(location(self.resources[key]))
        sha256(self.resources['image_sha256'])
        if (type(self.resources['disk_bytes']) is not int or self.resources['disk_bytes']<=0
                or type(self.resources['budget_seconds']) not in (int,float)
                or not math.isfinite(self.resources['budget_seconds']) or not 0<self.resources['budget_seconds']<=300):
            raise ValueError('bounded pinned search resource profile required')
        if (not isinstance(allowed_files,(list,tuple)) or not 0<len(allowed_files)<=100
                or any(not isinstance(name,str) for name in allowed_files)
                or len(set(allowed_files))!=len(allowed_files)):
            raise ValueError('bounded unique authorized files required')
        self.allowed=sorted(relative(name) for name in allowed_files)
        for name in self.allowed:
            text(name)
            if len(name.encode('utf-8'))>4096 or len(Path(name).parts)>64: raise ValueError('authorized filename byte/depth bound exceeded')
        self.source,self.source_hash=record(self.manifest,1024*1024)
        self.spec=ExperimentSpec.parse(self.source['experiment']); self.spec.validate()
        if self.spec.sequential is not None:raise ValueError('guarded search requires a fixed final plan')
        if (self.spec.profile!='descriptive_smoke' or self.spec.partition!='development'
                or self.spec.cohort_manifest_sha256 is None or 'controller_search' in self.source
                or self.spec.runtime_hash!=self.resources['image_sha256']):
            raise ValueError('guarded search requires an explicit exposed development preservation plan')
        self.base,self.base_data=scan(self.baseline)
        if runtime_hash(self.base_data)!=self.spec.baseline_hash: raise ValueError('search baseline differs from frozen plan')
        self.selected=self._selection()
        authority=self.source.get('bank_authority')
        if not isinstance(authority,dict): raise ValueError('guarded search requires live controlled-bank authority')
        owner=location(authority['bank_path']).parents[2]
        self.work=location(self.resources['work_root'])
        paths=dict(store=str(self.store.root),baseline=str(self.baseline),manifest=str(self.manifest),
            work=str(self.work),bank_owner=str(owner),image=self.resources['image_root'])
        roots=[self.store.root,self.baseline,self.manifest.parent,owner,location(self.resources['image_root'])]
        if any(self.work.is_relative_to(root) or root.is_relative_to(self.work) for root in roots):
            raise ValueError('prepared search inputs must remain disjoint from authority and input trees')
        if any(self.store.root.is_relative_to(root) or root.is_relative_to(self.store.root) for root in roots[1:]):
            raise ValueError('search history must remain disjoint from input and authority trees')
        self.workspace=PrivateJournal(self.work,'history-search-inputs-v1',lambda *args:False)
        config=dict(schema_version=1,algorithm=ALGORITHM,source_manifest_sha256=self.source_hash,
            selected_inputs_sha256=hash_value(self.selected),baseline_tree_sha256=hash_value(self.base),
            baseline_sha256=self.spec.baseline_hash,cohort_manifest_sha256=self.spec.cohort_manifest_sha256,
            prepared_limits=dict(contexts=MAX_CONTEXTS,attempts_per_context=MAX_ATTEMPTS,
                files_per_attempt=4096,entries_per_attempt=5000,bytes_per_attempt=MAX_PREPARED_BYTES),
            allowed_files=self.allowed,resource_profile=self.resources,paths=paths)
        self._template=dict(deepcopy(config),template_sha256=hash_value(config))
        self._check()

    @property
    def template_binding(self):
        self._check()
        return deepcopy(self._template)

    def _selection(self):
        root=self.manifest.parent
        folders=[task['fixture'] for task in self.source['tasks']]
        folders.extend(value['path'] for value in self.source['grader_bundles'].values())
        folders.extend(value['fixture'] for value in self.source.get('grader_self_tests',[]))
        chosen={}; count=size=entries=0
        for name in sorted(set(folders),key=lambda value:(len(Path(value).parts),value)):
            relative(name)
            if any(Path(name).is_relative_to(Path(prior)) for prior in chosen): continue
            metadata,data=scan(contained(root,name)); count+=len(data); size+=sum(map(len,data.values()))
            entries+=len(data)+len(metadata['directories'])+len(Path(name).parents)
            if count>4096 or entries>5000 or size>64*1024*1024: raise ValueError('search selected-input entry/byte bound exceeded')
            chosen[name]=metadata
        cohort=self.source['cohort_manifest']['path']; raw=read_regular(contained(root,cohort),1024*1024)
        return dict(folders=chosen,files=count,entries=entries,bytes=size,cohort_path=cohort,cohort_sha256=hashlib.sha256(raw).hexdigest())

    def _check(self):
        config={key:value for key,value in self._template.items() if key!='template_sha256'}
        paths=dict(store=str(self.store.root),baseline=str(self.baseline),manifest=str(self.manifest),work=str(self.work),
            bank_owner=str(location(self.source['bank_authority']['bank_path']).parents[2]),image=self.resources['image_root'])
        actual,actual_hash=record(self.manifest,1024*1024)
        if (hash_value(config)!=self._template['template_sha256'] or encoded(self.resources)!=encoded(config['resource_profile'])
                or self.allowed!=config['allowed_files'] or paths!=config['paths'] or encoded(actual)!=encoded(self.source)
                or encoded(ExperimentSpec.parse(self.source['experiment']).to_dict())!=encoded(self.spec.to_dict())
                or hash_value(self.base)!=config['baseline_tree_sha256'] or hash_value(self.selected)!=config['selected_inputs_sha256']
                or runtime_hash(self.base_data)!=config['baseline_sha256']
                or actual_hash!=self.source_hash or scan(self.baseline)[0]!=self.base
                or self._selection()!=self.selected
                or identity(self.manifest.parent,self.source['tasks'],self.source['grader_bundles'])!=self.spec.grader_hash):
            raise ValueError('frozen search source or baseline changed')
        for task in self.source['tasks']: frozen_grader(self.manifest.parent,task,self.source['grader_bundles'][task['id']])
        bound=bank_authority(self.source,export_root=self.manifest.parent)
        cohort_history.capture(self.manifest.parent,self.source,self.spec,bound)

    def _candidate(self,candidate):
        from .search import CandidateRecord
        if not isinstance(candidate,CandidateRecord): raise ValueError('a typed candidate record is required')
        if not isinstance(candidate.changes,dict): raise ValueError('candidate changes must be a file map')
        for name in candidate.changes:
            text(name)
            if len(name.encode('utf-8'))>4096 or len(Path(name).parts)>64: raise ValueError('candidate filename byte/depth bound exceeded')
        try: candidate.validate(self.allowed)
        except (TypeError,AttributeError): raise ValueError('invalid candidate declaration') from None
        value=asdict(candidate)
        for key in ('candidate_id','mechanism','hypothesis','falsifier'): text(value[key])
        for key in ('parents','evidence_refs'):
            if not isinstance(value[key],(tuple,list)) or len(value[key])>100: raise ValueError('bounded candidate attribution required')
            value[key]=list(value[key])
            for item in value[key]: text(item)
        if len(encoded(value))>256*1024: raise ValueError('candidate declaration byte bound exceeded')
        expected=deepcopy(self.base); data=dict(self.base_data)
        for name,content in sorted(candidate.changes.items()):
            path=Path(name)
            if name in expected['directories']: raise ValueError('candidate changes must name files, not directories')
            if content is None:
                expected['files'].pop(name,None); data.pop(name,None); continue
            for parent in reversed(path.parents):
                part=str(parent)
                if part=='.': continue
                if part in expected['files']: raise ValueError('candidate file overlaps a parent file')
                expected['directories'].setdefault(part,0o700)
            raw=content.encode('utf-8'); data[name]=raw
            expected['files'][name]=dict(sha256=hashlib.sha256(raw).hexdigest(),mode=expected['files'].get(name,{}).get('mode',0o600))
        if (len(data)>MAX_FILES or len(data)+len(expected['directories'])-1>5000
                or sum(map(len,data.values()))>MAX_BYTES): raise ValueError('candidate tree bound exceeded')
        return value,candidate.digest,expected,data

    def _declaration(self,record_value,patch,metadata,data,stage,event_id):
        if stage not in ('baseline','development','development_confirmation'): raise ValueError('unsupported guarded search stage')
        text(event_id)
        if len(event_id)>256: raise ValueError('bounded search event identity required')
        if (stage=='baseline')!=(not record_value['changes']): raise ValueError('only the baseline stage can retain the empty patch')
        harness=runtime_hash(data)
        if stage!='baseline' and harness==self.spec.baseline_hash: raise ValueError('candidate did not change the observed harness')
        marker=dict(algorithm=ALGORITHM,template_sha256=self._template['template_sha256'],
            candidate_record_sha256=hash_value(record_value),patch_sha256=patch,tree_sha256=hash_value(metadata),
            harness_sha256=harness,stage=stage,event_id=event_id)
        attempt_id=hash_value(marker)
        attempt=contained(self.work,self._template['template_sha256']+'/'+attempt_id)
        manifest=deepcopy(self.source)
        for task in manifest['tasks']:
            folder=contained(attempt/'export',manifest['grader_bundles'][task['id']]['path'])
            task['grader']=[task['grader'][0],str(folder/'grade.py.txt'),'{workspace}',str(folder/'expected.json')]
        spec=replace(self.spec,experiment_id='guarded-search-'+attempt_id,candidate_hash=harness)
        manifest.update(experiment=spec.to_dict(),controller_search=marker)
        return attempt,attempt_id,manifest,spec

    def evaluate(self,candidate,*,stage,event_id,on_started=None,parent_deadline=None):
        if on_started is not None and not callable(on_started): raise ValueError('search start observer must be callable')
        deadline=time.monotonic()+self.resources['budget_seconds']
        if parent_deadline is not None:
            if type(parent_deadline) not in (int,float) or not math.isfinite(parent_deadline): raise ValueError('finite search parent deadline required')
            deadline=min(deadline,parent_deadline)
        self._check(); value,patch,metadata,data=self._candidate(candidate)
        attempt,ident,manifest,spec=self._declaration(value,patch,metadata,data,stage,event_id)
        attempt_raw=encoded(dict(candidate_record=value,marker=manifest['controller_search'])); manifest_raw=encoded(manifest)
        if len(attempt_raw)>256*1024 or len(manifest_raw)>1024*1024:
            raise ValueError('prepared search metadata bound exceeded')
        if (self.selected['files']+len(data)+len(self.base_data)+5>4096
                or self.selected['entries']+len(data)+len(metadata['directories'])+len(self.base_data)+len(self.base['directories'])+7>5000
                or self.selected['bytes']+sum(map(len,data.values()))+sum(map(len,self.base_data.values()))
                    +len(attempt_raw)+len(manifest_raw)+1024*1024>MAX_PREPARED_BYTES):
            raise ValueError('prepared search input budget exceeded')
        if time.monotonic()>=deadline: raise TimeoutError('search preparation deadline exhausted')
        with self.workspace.locked(create=True):
            existing=[path.name for path in self.work.iterdir() if path.name not in ('.lock','.store.json')]
            for name in existing: sha256(name)
            context=contained(self.work,self._template['template_sha256'])
            if not context.exists() and len(existing)>=MAX_CONTEXTS: raise ValueError('search context count bound exceeded')
            context.mkdir(mode=0o700,exist_ok=True)
            if len(list(context.iterdir()))>=MAX_ATTEMPTS: raise ValueError('search attempt count bound exceeded')
            attempt.mkdir(mode=0o700)
            write_bytes(attempt/'attempt.json',attempt_raw)
            sync_directory(attempt); sync_directory(context); sync_directory(self.work)
        for name in ('baseline','candidate'): shutil.copytree(self.baseline,attempt/name)
        for name,mode in sorted(metadata['directories'].items(),key=lambda item:len(Path(item[0]).parts)):
            target=attempt/'candidate' if name=='.' else contained(attempt/'candidate',name)
            target.mkdir(exist_ok=True,mode=0o700); target.chmod(mode|0o700)
        for name,content in value['changes'].items():
            if content is None: continue
            target=contained(attempt/'candidate',name)
            if target.exists(): target.chmod(metadata['files'][name]['mode']|0o600)
            target.write_bytes(data[name]); target.chmod(metadata['files'][name]['mode'])
        for name in set(self.base_data)-set(data): contained(attempt/'candidate',name).unlink()
        for name,mode in sorted(metadata['directories'].items(),key=lambda item:-len(Path(item[0]).parts)):
            (attempt/'candidate' if name=='.' else contained(attempt/'candidate',name)).chmod(mode)
        export=attempt/'export'; export.mkdir(mode=0o700)
        for name in self.selected['folders']:
            target=contained(export,name); target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            shutil.copytree(contained(self.manifest.parent,name),target)
        cohort=self.selected['cohort_path']; target=contained(export,cohort)
        if not target.exists():
            target.parent.mkdir(parents=True,exist_ok=True,mode=0o700); write_bytes(target,read_regular(contained(self.manifest.parent,cohort),1024*1024))
        write_bytes(export/'manifest.json',manifest_raw); sync_tree(attempt)
        self._check(); self._prepared(attempt,metadata,manifest)
        remaining=deadline-time.monotonic()
        if remaining<=0: raise TimeoutError('search preparation deadline exhausted')
        def started(run_id):
            write_bytes(attempt/'started-run.json',encoded(dict(run_id=run_id))); sync_directory(attempt)
            if on_started is not None: on_started(run_id)
        summary=self.store.run(export/'manifest.json',attempt/'baseline',attempt/'candidate',
            cgroup_root=self.resources['cgroup_root'],image_root=self.resources['image_root'],
            image_sha256=self.resources['image_sha256'],disk_bytes=self.resources['disk_bytes'],
            budget_seconds=remaining,parent_deadline=deadline,on_started=started)
        if time.monotonic()>=deadline: raise TimeoutError('search comparison deadline exhausted')
        receipt=self.store.decision(summary['run_id'])
        binding=dict(schema_version=1,template_sha256=self._template['template_sha256'],candidate_record=value,
            patch_sha256=patch,harness_sha256=runtime_hash(data),tree_sha256=hash_value(metadata),
            stage=stage,event_id=event_id,attempt_id=ident,run_id=summary['run_id'],
            receipt_sha256=receipt['receipt_sha256'],plan_sha256=spec.digest,manifest_sha256=hash_value(manifest))
        result=self.verify(binding)
        if time.monotonic()>=deadline: raise TimeoutError('search receipt deadline exhausted')
        return result

    def _prepared(self,attempt,metadata,manifest):
        if scan(attempt/'baseline')[0]!=self.base or scan(attempt/'candidate')[0]!=metadata:
            raise ValueError('prepared search harness identity changed')
        if record(attempt/'export/manifest.json',1024*1024)[1]!=hash_value(manifest):
            raise ValueError('prepared search manifest changed')
        for name,expected in self.selected['folders'].items():
            if scan(contained(attempt/'export',name))[0]!=expected: raise ValueError('prepared search input changed')
        if regular_sha256(contained(attempt/'export',self.selected['cohort_path']),1024*1024)!=self.selected['cohort_sha256']:
            raise ValueError('prepared search cohort changed')

    def verify(self,binding):
        from .search import CandidateRecord
        fields(binding,'schema_version template_sha256 candidate_record patch_sha256 harness_sha256 tree_sha256 stage event_id attempt_id run_id receipt_sha256 plan_sha256 manifest_sha256')
        if type(binding['schema_version']) is not int or binding['schema_version']!=1: raise ValueError('unsupported search binding')
        self._check(); original=binding['candidate_record']
        fields(original,'candidate_id parents mechanism changes evidence_refs hypothesis falsifier')
        candidate=CandidateRecord(**{**original,'parents':tuple(original['parents']),'evidence_refs':tuple(original['evidence_refs'])})
        value,patch,metadata,data=self._candidate(candidate)
        attempt,ident,manifest,spec=self._declaration(value,patch,metadata,data,binding['stage'],binding['event_id'])
        expected=dict(binding,template_sha256=self._template['template_sha256'],candidate_record=value,patch_sha256=patch,
            harness_sha256=runtime_hash(data),tree_sha256=hash_value(metadata),attempt_id=ident,plan_sha256=spec.digest,manifest_sha256=hash_value(manifest))
        if binding!=expected: raise ValueError('search binding differs from independently rebuilt identities')
        self._prepared(attempt,metadata,manifest)
        sha256(binding['run_id']); sha256(binding['receipt_sha256'])
        with self.workspace.locked():
            if record(attempt/'attempt.json')[0]!=dict(candidate_record=value,marker=manifest['controller_search']): raise ValueError('search attempt declaration changed')
            if record(attempt/'started-run.json')[0]!=dict(run_id=binding['run_id']): raise ValueError('search run custody changed')
        verified=self.store.verify_decision(binding['run_id'],binding['receipt_sha256']); receipt=verified['receipt']
        if (receipt['plan_sha256']!=spec.digest or receipt['manifest_sha256']!=hash_value(manifest)
                or receipt['baseline_sha256']!=self.spec.baseline_hash or receipt['candidate_sha256']!=runtime_hash(data)
                or receipt['cohort_manifest_sha256']!=self.spec.cohort_manifest_sha256
                or receipt['bank_authority_status']!='current_eligible'):
            raise ValueError('controller receipt belongs to another search comparison')
        self._check(); self._prepared(attempt,metadata,manifest)
        verified=self.store.verify_decision(binding['run_id'],binding['receipt_sha256'])
        self._check(); self._prepared(attempt,metadata,manifest)
        checked=self.store.checked(binding['run_id'])
        if (checked[5]!='current' or checked[1][-1][1]!=verified['receipt']['event_sha256']
                or decision_receipts.engine()!=verified['receipt']['analysis_engine']):
            raise ValueError('search authority or engine changed after numeric verification')
        return dict(binding=deepcopy(binding),verification='current_recomputed',
            **feasibility(spec,verified['analysis'],baseline=binding['stage']=='baseline'))
