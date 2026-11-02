"""Frozen grader-defect controls, operator review and explicit immutable application."""
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import tempfile
import time

from agent_harness_lab.private_journal import MAX_EVENTS, PrivateJournal, encoded, record, sync_directory, write_bytes
from .bank_revisions import sync_tree
from .bank_tools import materialize_sources
from .integrity import contained, copy_frozen, digest, read_regular, tree
from .regression_controls import STATES, audit_controls, containment
from .regression_review import review_value
from .regressions import fields, references, sha256, text
from .task_bank import TaskSpec, identifier, validate

MAX_CASES = 128


def transition(state,kind,target,previous_kind=None):
    if state is None: return kind == target == 'reported'
    if kind == 'audit_started': return state == 'reported' and target == 'auditing'
    if kind == 'audit_finished': return state == 'auditing' and target in ('reported','audited','quarantined')
    if kind == 'audit_abandoned': return state == 'auditing' and target == 'reported'
    if kind == 'reviewed': return state == 'audited' and target == 'reviewed'
    if kind == 'applied': return state == 'reviewed' and target == 'applied'
    if kind == 'rerun_started': return state == target == 'applied'
    if kind == 'rerun_finished': return state == target == 'applied' and previous_kind=='rerun_started'
    return kind == target and target in ('quarantined','rejected','retired') and state not in (target,'rejected','retired')


def declaration(value):
    fields(value,'schema_version parent replacement controls image_sha256 evidence_refs reason')
    if type(value['schema_version']) is not int or value['schema_version'] != 1: raise ValueError('unsupported correction schema')
    fields(value['parent'],'bank sha256 task_id'); sha256(value['parent']['sha256']); identifier(value['parent']['task_id'])
    fields(value['replacement'],'bundle sha256 grader_version'); sha256(value['replacement']['sha256'])
    text(value['replacement']['grader_version']); identifier(value['replacement']['grader_version'])
    sha256(value['image_sha256']); references(value['evidence_refs']); text(value['reason'])
    if not value['evidence_refs']: raise ValueError('correction needs explicit source evidence')
    controls = value['controls']
    if not isinstance(controls,list) or len(controls) != 5: raise ValueError('correction needs five controls')
    for control in controls:
        fields(control,'state fixture digest'); sha256(control['digest'])
        if control['state'] not in STATES: raise ValueError('unsupported correction control')
    if {c['state'] for c in controls} != set(STATES): raise ValueError('duplicate or missing correction control')
    return value


def artifact_review(value,author,corrective_signal):
    reviewer = value['reviewer']
    if (not corrective_signal or reviewer['identity'].casefold() == author.casefold()
            or reviewer['basis_kind'] != 'independent_artifact_review' or not value['evidence_refs']):
        raise ValueError('correction needs discriminative audits and separate operator artifact review')


def proposed_diff(spec,task):
    return dict(parent_bank_sha256=spec['parent']['sha256'],task_id=task.task_id,
        fixture_sha256=task.fixture_digest,root_id=task.root_id,family=task.family,partition=task.partition,
        project_group=task.project_group,time_group=task.time_group,
        old_grader_sha256=task.grader_digest,old_grader_version=task.grader_version,
        replacement_grader_sha256=spec['replacement']['sha256'],
        replacement_grader_version=spec['replacement']['grader_version'],applied=False)


class CorrectionStore(PrivateJournal):
    def __init__(self,root):
        super().__init__(root,'grader-correction-review-v1',transition)

    def propose(self,spec_path,author):
        identifier(author); text(author)
        spec_path = Path(spec_path).absolute()
        if '..' in spec_path.parts: raise ValueError('correction declaration path must be canonical')
        spec,spec_hash = record(spec_path,256*1024); declaration(spec)
        parent = contained(spec_path.parent,spec['parent']['bank'])
        bank,bank_hash = record(parent,1024*1024)
        if bank_hash != spec['parent']['sha256'] or validate(parent)['bank_sha256'] != bank_hash:
            raise ValueError('correction parent bank changed')
        selected = [t for t in bank['tasks'] if t['task_id'] == spec['parent']['task_id']]
        if len(selected) != 1: raise ValueError('unknown correction task')
        task = TaskSpec.parse(selected[0])
        if task.permission != 'approved' or task.partition not in ('construction','development'):
            raise ValueError('correction requires approved development/construction inputs')
        if task.grader_digest == spec['replacement']['sha256'] or task.grader_version == spec['replacement']['grader_version']:
            raise ValueError('correction requires a distinct bundle and grader version')
        sources = {'fixture':(contained(parent.parent,task.fixture),task.fixture_digest),
            'old-oracle':(contained(parent.parent,task.grader_bundle),task.grader_digest),
            'replacement':(contained(spec_path.parent,spec['replacement']['bundle']),spec['replacement']['sha256'])}
        sources.update({'controls/'+str(i):(contained(spec_path.parent,c['fixture']),c['digest']) for i,c in enumerate(spec['controls'])})
        if any(self.root.is_relative_to(p) or p.is_relative_to(self.root) for p,_ in sources.values()):
            raise ValueError('correction store and selected source trees must not overlap')
        case_id = hashlib.sha256(encoded(dict(spec_sha256=spec_hash,parent_bank_sha256=bank_hash))).hexdigest()
        with self.locked(create=True), tempfile.TemporaryDirectory(dir=self.root,prefix='.correction-stage-') as temporary:
            cases = contained(self.root,'cases'); cases.mkdir(mode=0o700,exist_ok=True)
            count = 0
            with os.scandir(cases) as entries:
                for index,entry in enumerate(entries):
                    if index >= MAX_CASES: raise ValueError('correction case capacity exhausted')
                    sha256(entry.name)
                    if not entry.is_dir(follow_symlinks=False): raise ValueError('invalid correction directory')
                    count += 1
            if count >= MAX_CASES: raise ValueError('correction case capacity exhausted')
            destination = self.case(case_id)
            if destination.exists(): raise ValueError('correction already proposed; history retained')
            staged = Path(temporary)/'case'; staged.mkdir(mode=0o700); (staged/'events').mkdir(mode=0o700)
            copied_files = copied_bytes = 0
            for relative,(source,expected) in sources.items():
                files = tree(source); copied_files += len(files)
                copied_bytes += sum(len(read_regular(contained(source,name),64*1024*1024)) for name in files)
                if copied_files > 3500 or copied_bytes > 60*1024*1024: raise ValueError('correction snapshot bound exceeded')
                target = contained(staged,relative); target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                copy_frozen(source,target,expected)
            inputs = dict(schema_version=1,author=author,spec_sha256=spec_hash,parent_bank_sha256=bank_hash,
                assets={name:expected for name,(_,expected) in sources.items()})
            write_bytes(staged/'spec.json',read_regular(spec_path,256*1024)); write_bytes(staged/'parent-bank.json',read_regular(parent,1024*1024))
            if record(staged/'spec.json')[1] != spec_hash or record(staged/'parent-bank.json',1024*1024)[1] != bank_hash:
                raise ValueError('correction declarations changed during copying')
            write_bytes(staged/'inputs.json',encoded(inputs)); events = []
            self.append(staged,events,'reported','reported',dict(inputs_sha256=record(staged/'inputs.json')[1]))
            sync_tree(staged); staged.rename(destination); sync_directory(cases); sync_directory(self.root)
            return self.summary(case_id,*self.opened(case_id))

    def opened(self,case_id):
        case = self.case(case_id); events = self.history(case)
        inputs,inputs_hash = record(case/'inputs.json')
        fields(inputs,'schema_version author spec_sha256 parent_bank_sha256 assets')
        if type(inputs['schema_version']) is not int or inputs['schema_version'] != 1 or events[0][0]['payload'] != dict(inputs_sha256=inputs_hash):
            raise ValueError('correction input binding changed')
        identifier(inputs['author']); text(inputs['author'])
        spec,spec_hash = record(case/'spec.json'); declaration(spec)
        bank,bank_hash = record(case/'parent-bank.json',1024*1024)
        if (spec_hash != inputs['spec_sha256'] or bank_hash != inputs['parent_bank_sha256']
                or spec['parent']['sha256'] != bank_hash
                or hashlib.sha256(encoded(dict(spec_sha256=spec_hash,parent_bank_sha256=bank_hash))).hexdigest() != case_id):
            raise ValueError('frozen correction declaration changed')
        task = TaskSpec.parse(next(t for t in bank['tasks'] if t['task_id'] == spec['parent']['task_id']))
        expected = {'fixture':task.fixture_digest,'old-oracle':task.grader_digest,'replacement':spec['replacement']['sha256']}
        expected.update({'controls/'+str(i):c['digest'] for i,c in enumerate(spec['controls'])})
        if inputs['assets'] != expected or any(digest(contained(case,p)) != h for p,h in expected.items()):
            raise ValueError('frozen correction assets changed')
        for index,(event,_) in enumerate(events):
            if event['kind']=='applied':
                from .bank_corrections import application_event
                evidence,_ = self.checked_evidence(case,spec,task,events[:index])
                application_event(self,case_id,events,index,evidence)
            for name,expected_hash in event['payload'].get('reports_sha256',{}).items():
                sha256(expected_hash)
                attempt = contained(case,event['payload']['attempt'])
                if name not in ('old','replacement') or record(contained(attempt,name+'/review.json'),1024*1024)[1] != expected_hash:
                    raise ValueError('recorded correction audit changed')
            if event['kind'] in ('reviewed','quarantined','rejected','retired','audit_abandoned'):
                fields(event['payload'],'review'); value = review_value(event['payload']['review'],case_id)
                audited = next((e for e,_ in reversed(events[:index]) if e['kind']=='audit_finished' and e['state']=='audited'),None)
                evidence = audited['payload']['evidence_sha256'] if audited is not None else None
                decision = 'reported' if event['kind']=='audit_abandoned' else event['kind']
                if (value['decision'] != decision or value['expected_event_sha256'] != event['previous_sha256']
                        or value['evidence_sha256'] != evidence):
                    raise ValueError('persisted correction review binding changed')
                if decision == 'reviewed':
                    _,packet = self.checked_evidence(case,spec,task,events[:index])
                    artifact_review(value,inputs['author'],packet['corrective_signal'])
        if any(e['kind'].startswith('rerun_') for e,_ in events):
            from .correction_rerun import validate_events
            validate_events(self,case_id,case,events)
        return case,inputs,events,spec,task

    def packet(self,case,spec,task,attempt):
        result = dict(schema_version=1,reports={},controls={},proposed_bank_diff=proposed_diff(spec,task),
            task_failure_verified=False,historical_task_outcome='unknown',bank_replacement=False)
        for name,bundle,version in (('old','old-oracle',task.grader_version),
                ('replacement','replacement',spec['replacement']['grader_version'])):
            report,report_hash = record(attempt/name/'review.json',1024*1024)
            if (type(report.get('schema_version')) is not int or report['schema_version'] != 1
                    or report.get('task_failure_verified') is not False or report.get('publication_allowed') is not False
                    or report.get('grader_bundle_sha256') != digest(case/bundle) or report.get('image_sha256') != spec['image_sha256']):
                raise ValueError('audit report differs from frozen oracle/image')
            checks = report.get('checks')
            if not isinstance(checks,list) or not 0 < len(checks) <= 5: raise ValueError('missing controller audit observations')
            for index,row in enumerate(checks):
                control = spec['controls'][index]; expected = control['state'] in ('complete','alternative_valid')
                if (row.get('control') != control['state'] or row.get('expected') is not expected
                        or row.get('input_sha256') != control['digest']
                        or not containment({'attestation':row.get('attestation')},spec['image_sha256'])):
                    raise ValueError('audit control binding or containment changed')
                if row.get('observed') is not None:
                    observed_identity = row.get('grader_identity')
                    if (type(row['observed']) is not bool or row.get('grader_status') != ('passed' if row['observed'] else 'failed')
                            or not isinstance(observed_identity,dict)
                            or set(observed_identity) != {'grader_id','grader_version'}
                            or any(not isinstance(v,str) or not 0<len(v)<=128 for v in observed_identity.values())
                            or observed_identity['grader_version'] != version and (name=='replacement' or report.get('status')!='grader_error')):
                        raise ValueError('audit typed grader identity changed')
                with tempfile.TemporaryDirectory() as temporary:
                    copy = Path(temporary)/'control'; copy_frozen(case/'controls'/str(index),copy,control['digest'])
                    materialize_sources(copy)
                    if digest(copy) != row.get('materialized_sha256'): raise ValueError('materialized control identity changed')
            if name == 'replacement' and (report.get('status') != 'grader_controls_passed' or len(checks) != 5
                    or any(row['observed'] is not row['expected'] for row in checks)):
                raise ValueError('replacement oracle has not passed every control')
            result['reports'][name] = report_hash
            result['controls'][name] = checks
        result['old_status'] = record(attempt/'old/review.json',1024*1024)[0]['status']
        result['replacement_status'] = 'grader_controls_passed'
        result['corrective_signal'] = result['old_status'] in ('controls_failed','grader_error')
        return result

    def checked_evidence(self,case,spec,task,events):
        event = next(e for e,_ in reversed(events) if e['kind']=='audit_finished' and e['state']=='audited')
        attempt = contained(case,event['payload']['attempt']); evidence,evidence_hash = record(attempt/'evidence.json')
        if evidence_hash != event['payload']['evidence_sha256'] or evidence != self.packet(case,spec,task,attempt):
            raise ValueError('sealed correction observations changed')
        return evidence_hash,evidence

    def summary(self,case_id,case,inputs,events,spec,task):
        evidence_hash = None; evidence = {}
        latest = next((e['payload'] for e,_ in reversed(events) if e['kind']=='audit_finished'),{})
        attempt = next((e['payload']['attempt'] for e,_ in reversed(events) if e['kind']=='audit_started'),None)
        if any(e['kind']=='audit_finished' and e['state']=='audited' for e,_ in events):
            evidence_hash,evidence = self.checked_evidence(case,spec,task,events)
        result = dict(schema_version=1,case_id=case_id,state=events[-1][0]['state'],event_sha256=events[-1][1],
            evidence_sha256=evidence_hash,old_audit_status=evidence.get('old_status',latest.get('old_status')),
            replacement_audit_status=evidence.get('replacement_status',latest.get('replacement_status')),corrective_signal=evidence.get('corrective_signal',False),
            oracle_approval='operator_attested' if events[-1][0]['state']=='reviewed' else 'unverified',
            proposed_bank_diff=proposed_diff(spec,task),audit_attempt=attempt,
            task_failure_verified=False,historical_task_outcome='unknown',independence='unverified',
            bank_replacement=False,automatic_promotion=False,publication_allowed=False)
        applied = next((e for e,_ in events if e['kind']=='applied'),None)
        if applied is not None:
            from .bank_corrections import application_summary,committed_application
            if events[-1][0]['state']=='applied':
                result.update(application_summary(committed_application(self,case_id,applied['payload'])))
            else:
                result.update(bank_replacement=True,current_eligible=False,rerun_status='pending_rerun',
                    revision_sha256=applied['payload']['revision_sha256'],privacy='private')
            result['oracle_approval'] = 'operator_attested' if events[-1][0]['state']=='applied' else 'unverified'
            result['proposed_bank_diff']['applied'] = True
        if any(e['kind'].startswith('rerun_') for e,_ in events):
            from .correction_rerun import validate_events
            result.update(validate_events(self,case_id,case,events))
        return result

    def apply(self,case_id,parent_path,history_store,request_path):
        from .bank_corrections import apply
        return apply(self,case_id,parent_path,history_store,request_path)

    def rerun(self,case_id,request_path,baseline,candidate,**profile):
        from .correction_rerun import rerun
        return rerun(self,case_id,request_path,baseline,candidate,**profile)

    def show(self,case_id):
        with self.locked(): return self.summary(case_id,*self.opened(case_id))

    def audit(self,case_id,backend,*,budget_seconds=60):
        started = time.monotonic()
        if type(budget_seconds) not in (int,float) or not math.isfinite(budget_seconds) or not 0<budget_seconds<=300:
            raise ValueError('correction audit needs a positive bounded budget')
        deadline = started+budget_seconds
        with self.locked():
            case,inputs,events,spec,task = self.opened(case_id)
            if events[-1][0]['state'] != 'reported': raise ValueError('audit requires a reported correction')
            if len(events)+5 > MAX_EVENTS: raise ValueError('correction lacks audit/review/intervention capacity')
            if (backend.cgroup_root is None or backend.image is None or backend.disk_bytes is None
                    or backend.image.sha256 != spec['image_sha256']):
                raise ValueError('correction needs its complete pinned offline profile')
            image = Path(backend.image.root).absolute()
            if '..' in image.parts or any(p.is_symlink() for p in (image,*image.parents)):
                raise ValueError('runtime image path must be canonical')
            if self.root.is_relative_to(image) or image.is_relative_to(self.root):
                raise ValueError('correction journal must remain outside runtime views')
            relative = 'attempts/'+secrets.token_hex(12); attempt = contained(case,relative); attempt.mkdir(parents=True,mode=0o700)
            self.append(case,events,'audit_started','auditing',dict(attempt=relative))
            def unchanged():
                try: self.opened(case_id); return True
                except (ValueError,OSError): return False
            controls = [dict(c,fixture=case/'controls'/str(i)) for i,c in enumerate(spec['controls'])]
            report_metadata = dict(attempt=relative,reports_sha256={})
            try:
                results = {}
                for name,bundle,version in (('old','old-oracle',task.grader_version),
                        ('replacement','replacement',spec['replacement']['grader_version'])):
                    results[name] = audit_controls(case/bundle,inputs['assets'][bundle],controls,task.artifacts,version,
                        attempt/name,backend,deadline=deadline,started=started,integrity=unchanged,
                        receipt_fields=dict(schema_version=1,grader_bundle_sha256=inputs['assets'][bundle],
                            image_sha256=spec['image_sha256'],task_failure_verified=False,publication_allowed=False))
                    observed,observed_hash = record(attempt/name/'review.json',1024*1024)
                    if observed.get('status') != results[name].get('status'): raise ValueError('audit result differs from recorded execution')
                    report_metadata['reports_sha256'][name] = observed_hash
                    report_metadata[('old' if name=='old' else 'replacement')+'_status'] = observed['status']
                status = results['replacement']['status']
                if (status == 'grader_controls_passed' and results['old']['status']
                        in ('blocked','budget_exhausted','grader_timeout','containment_error')):
                    self.append(case,events,'audit_finished','reported',dict(report_metadata,status=results['old']['status']))
                elif status == 'grader_controls_passed':
                    packet = self.packet(case,spec,task,attempt); sync_tree(attempt)
                    write_bytes(attempt/'evidence.json',encoded(packet)); sync_directory(attempt)
                    if not unchanged() or time.monotonic() >= deadline: raise TimeoutError('audit sealing incomplete')
                    self.append(case,events,'audit_finished','audited',dict(attempt=relative,evidence_sha256=record(attempt/'evidence.json')[1]))
                else:
                    target = 'reported' if status in ('blocked','budget_exhausted','grader_timeout') else 'quarantined'
                    self.append(case,events,'audit_finished',target,dict(report_metadata,status=status))
            except TimeoutError:
                self.append(case,events,'audit_finished','reported',dict(report_metadata,status='budget_exhausted'))
            except (ValueError,OSError,TypeError,KeyError):
                self.append(case,events,'audit_finished','quarantined',dict(report_metadata,status='invalid_or_incomplete_evidence'))
            return self.summary(case_id,*self.opened(case_id))

    def review(self,case_id,path):
        value,_ = record(path,64*1024); review_value(value,case_id)
        with self.locked():
            opened = self.opened(case_id); case,inputs,events,spec,task = opened
            if any(e['kind']=='applied' for e,_ in events):
                current = dict(event_sha256=events[-1][1],state=events[-1][0]['state'],
                    evidence_sha256=self.checked_evidence(case,spec,task,events)[0])
            else: current = self.summary(case_id,*opened)
            if (value['expected_event_sha256'] != current['event_sha256']
                    or value['evidence_sha256'] != current['evidence_sha256']):
                raise ValueError('stale correction review')
            decision = value['decision']
            if decision == 'reviewed':
                if current['state'] != 'audited': raise ValueError('positive correction review requires completed audits')
                artifact_review(value,inputs['author'],current['corrective_signal'])
                self.checked_evidence(case,spec,task,events)
            if decision == 'reported' and current['state'] != 'auditing': raise ValueError('only an unfinished audit may be abandoned')
            self.append(case,events,'audit_abandoned' if decision=='reported' else decision,decision,dict(review=value))
            return self.summary(case_id,*self.opened(case_id))


def main(argv=None):
    import argparse
    from .cli import pinned_arguments
    parser = argparse.ArgumentParser(prog='ahl grader-correction',description=__doc__)
    parser.add_argument('--store',type=Path,required=True,help='Explicit private correction review store')
    actions = parser.add_subparsers(dest='action',required=True)
    propose = actions.add_parser('propose',help='Freeze an authorized grader-defect declaration and its controls')
    propose.add_argument('--spec',type=Path,required=True); propose.add_argument('--author',required=True)
    for name in ('show','audit','review','apply','rerun'):
        command = actions.add_parser(name); command.add_argument('--case',required=True)
        if name in ('review','apply','rerun'): command.add_argument('--record',type=Path,required=True)
        if name=='apply':
            command.add_argument('--parent',type=Path,required=True); command.add_argument('--history-store',type=Path,required=True)
        if name in ('audit','rerun'):
            pinned_arguments(command)
        if name=='rerun':
            command.add_argument('--baseline',type=Path,required=True); command.add_argument('--candidate',type=Path,required=True)
    args = parser.parse_args(argv)
    try:
        store = CorrectionStore(args.store)
        if args.action == 'propose': result = store.propose(args.spec,args.author)
        elif args.action == 'audit':
            from .sandbox import SandboxBackend
            backend = SandboxBackend(cgroup_root=args.cgroup_root,image_root=args.image_root,
                image_sha256=args.image_sha256,disk_bytes=args.disk_bytes)
            result = store.audit(args.case,backend,budget_seconds=args.budget_seconds)
        elif args.action == 'review': result = store.review(args.case,args.record)
        elif args.action=='apply':
            from .oracle_history import HistoryStore
            result = store.apply(args.case,args.parent,HistoryStore(args.history_store),args.record)
        elif args.action=='rerun':
            result = store.rerun(args.case,args.record,args.baseline,args.candidate,cgroup_root=args.cgroup_root,
                image_root=args.image_root,image_sha256=args.image_sha256,disk_bytes=args.disk_bytes,budget_seconds=args.budget_seconds)
        else: result = store.show(args.case)
        print(json.dumps(result,indent=2,allow_nan=False))
        if args.action == 'audit' and result['state'] != 'audited':
            return 3 if result['state']=='reported' else 1
        if args.action=='rerun' and result['rerun_status']!='completed': return 1
        return 0
    except BlockingIOError:
        print(json.dumps(dict(status='busy',bank_replacement=False,publication_allowed=False)))
        return 3
    except (ValueError,OSError,TypeError,KeyError,StopIteration):
        parser.exit(2,'grader correction error: invalid or unavailable private input; history retained\n')
