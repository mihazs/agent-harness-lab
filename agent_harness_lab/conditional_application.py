"""Conditional root-instruction application; configuration never issues authority."""
from copy import deepcopy
import hashlib
import math
import os
from pathlib import Path
import secrets
import tempfile
import time
import weakref

from .private_journal import encoded,record,sync_directory,write_bytes
from .evaluation.integrity import read_regular
from .evaluation.regressions import fields,sha256,text
from .evaluation import review_authority as authority
from .harness_change_plan import HarnessChangePlan
from .promotion.journal import file_record

POLICY='controller-agents-conditional-apply-v1'
AUTH_PROFILE='controller-instruction-authorization-v1'
PURPOSE='conditional_instruction_application'
METHOD_FIELDS='experiment_id repeats seed max_trials min_roots effect_margin family_margin alpha family_weights precision_half_width'
APPLICATION_FIELDS='policy review_store authorization_id bank_case_id delegation_request delegation_signature method'


def hashed(value):return hashlib.sha256(encoded(value)).hexdigest()


def method(value):
    fields(value,METHOD_FIELDS)
    if not isinstance(value['family_weights'],dict) or not value['family_weights']:raise ValueError('predeclared family weights required')
    for family in value['family_weights']:text(family)
    from .evaluation.plan import ExperimentSpec
    tasks=tuple(('declared-'+str(index),'declared-root-'+str(index),family) for index,family in enumerate(sorted(value['family_weights'])))
    spec=ExperimentSpec(value['experiment_id'],tasks,value['repeats'],value['seed'],value['max_trials'],value['min_roots'],
        value['effect_margin'],value['family_margin'],value['alpha'],tuple(sorted(value['family_weights'].items())),
        'unbound-baseline','unbound-candidate','unbound-oracle','unbound-runtime',profile='certification',partition='certification',
        precision_half_width=value['precision_half_width'])
    spec.validate();text(value['experiment_id'])
    return deepcopy(value)


def validate_settings(value):
    fields(value,APPLICATION_FIELDS)
    if value['policy']!=POLICY:raise ValueError('unsupported conditional application policy')
    for key in ('authorization_id','bank_case_id'):sha256(value[key])
    from .config import reference_path
    for key in ('review_store','delegation_request','delegation_signature'):reference_path(Path('/'),value[key])
    method(value['method'])
    return value


def selected(settings):return bool(settings and settings.get('schema_version')==2)


def target_record(project):
    project=authority._path(project)
    current=file_record(project/'AGENTS.md')
    if current is None or current['mode']&0o111:raise ValueError('conditional application supports an existing nonexecutable root AGENTS.md only')
    return current


def opened(store,authorization_id):
    case=store.case(authorization_id);events=store.history(case);inputs,pin=record(case/'inputs.json')
    fields(inputs,'schema_version profile project baseline nonce')
    if (inputs['schema_version']!=1 or inputs['profile']!=AUTH_PROFILE or hashed(inputs)!=authorization_id
            or events[0][0]['kind']!='application_authorization_created'
            or events[0][0]['payload']!=dict(inputs_sha256=pin)):
        raise ValueError('invalid application authorization locator')
    authority._path(inputs['project']);sha256(inputs['nonce'])
    from .promotion.journal import validate_snapshot
    validate_snapshot(inputs['baseline'])
    return case,inputs,events


def allocate(store,project):
    project=authority._path(project)
    if project.is_relative_to(store.root) or store.root.is_relative_to(project):raise ValueError('application authorization store overlaps project')
    inputs=dict(schema_version=1,profile=AUTH_PROFILE,project=str(project),baseline=target_record(project),nonce=secrets.token_hex(32))
    ident=hashed(inputs)
    with store.locked(create=True),tempfile.TemporaryDirectory(dir=store.root,prefix='.application-stage-') as folder:
        authority.check_sources(store,[project])
        if len(store.case_ids(256))>=256:raise ValueError('application case count bound exceeded')
        case=Path(folder)/'case';case.mkdir(mode=0o700);(case/'events').mkdir(mode=0o700)
        write_bytes(case/'inputs.json',encoded(inputs));sync_directory(case)
        store.append(case,[],'application_authorization_created','application_authorization',dict(inputs_sha256=record(case/'inputs.json')[1]))
        cases=store.root/'cases';cases.mkdir(mode=0o700,exist_ok=True)
        destination=store.case(ident)
        if destination.exists():raise ValueError('application authorization already exists')
        case.rename(destination);sync_directory(cases);sync_directory(store.root)
    return dict(profile=AUTH_PROFILE,authorization_id=ident,authorization='not_authorized',publication_allowed=False)


def install_policy(store,path):
    from .evaluation.bank_qualification import source_roots
    with store.locked(create=True):return authority.install(store,path,source_roots(store),purpose=PURPOSE)


def summary(case_id,inputs,events):
    """Metadata only: the locator and a recorded state never issue a live grant."""
    return dict(schema_version=1,profile=AUTH_PROFILE,case_id=case_id,state=events[-1][0]['state'],
        event_sha256=events[-1][1],authorization='not_authorized',automatic_promotion=False,publication_allowed=False)


def source_roots(inputs):
    return [authority._path(inputs['project'])]


def prepare(store,authorization_id,project,settings,configuration_sha256,options,reviewer):
    """Freeze preauthorization inputs before any future proposal or final exposure."""
    from .evaluation.task_bank import identifier
    identifier(reviewer);text(reviewer);sha256(configuration_sha256)
    if not selected(settings) or settings['application']['authorization_id']!=authorization_id:
        raise ValueError('application configuration does not select this authorization')
    from .config import reference_path
    if authority._path(reference_path(project,settings['application']['review_store']))!=store.root:
        raise ValueError('application authorization belongs to another review store')
    with store.locked():
        case,inputs,events=opened(store,authorization_id)
        if events[-1][0]['state']!='application_authorization':raise ValueError('application authorization is not preparable')
        if authority._path(project)!=Path(inputs['project']) or target_record(project)!=inputs['baseline']:
            raise ValueError('application baseline changed before authorization')
        pinned=binding(project,settings,configuration_sha256,options)
        review=dict(schema_version=1,profile=AUTH_PROFILE,case_id=authorization_id,
            expected_event_sha256=events[-1][1],reviewer=dict(identity=reviewer),binding=pinned)
        value=authority.request(store,review,purpose=PURPOSE);raw=encoded(value);pin=hashed(value)
        folder=store.root/'review-requests';folder.mkdir(mode=0o700,exist_ok=True);path=folder/(pin+'.json')
        if path.exists():
            if read_regular(path,authority.MAX_BYTES)!=raw:raise ValueError('application request changed')
        else:write_bytes(path,raw);sync_directory(folder)
        store.append(case,events,'application_authorization_prepared','application_prepared',dict(request_sha256=pin))
        return dict(profile=AUTH_PROFILE,authorization_id=authorization_id,request_sha256=pin,
            authorization='not_authorized',publication_allowed=False)


def authorize(store,authorization_id,request_path,signature_path,*,deadline):
    """Authenticate a host-installed public-key policy; never issue a live grant."""
    value,pin=record(request_path,authority.MAX_BYTES)
    signature=read_regular(signature_path,16*1024).decode('ascii')
    with store.locked():
        case,inputs,events=opened(store,authorization_id)
        if events[-1][0]['state']!='application_prepared' or events[-1][0]['payload']!={'request_sha256':pin}:
            raise ValueError('application authorization request is stale')
        review=authority.request_value(store,value,purpose=PURPOSE)
        fields(review,'schema_version profile case_id expected_event_sha256 reviewer binding')
        if (type(review['schema_version']) is not int or review['schema_version']!=1 or review['profile']!=AUTH_PROFILE
                or review['case_id']!=authorization_id or review['expected_event_sha256']!=events[-2][1]):
            raise ValueError('application authorization scope changed')
        fields(review['reviewer'],'identity');text(review['reviewer']['identity'])
        if review['binding']['baseline']!=inputs['baseline'] or target_record(inputs['project'])!=inputs['baseline']:
            raise ValueError('application baseline changed before approval')
        proof=authority.verify(store,value,signature,purpose=PURPOSE,deadline=deadline)
        authentication=authority.save(store,value,signature,proof)
        if authority.active(store,purpose=PURPOSE)[1]!=proof['policy_sha256'] or time.monotonic()>=deadline:
            raise ValueError('application authority changed before recording')
        store.append(case,events,'application_authorized','application_authorized',
            dict(request_sha256=pin,authentication=authentication))
        return summary(authorization_id,inputs,events)


def _approved(store,authorization_id,args,deadline,*,state):
    """Recheck original signed inputs and current policy, never trust event labels."""
    case,inputs,events=opened(store,authorization_id)
    if events[-1][0]['state']!=state:raise ValueError('application authorization is unavailable or already consumed')
    event=next((event for event,_ in reversed(events) if event['kind']=='application_authorized'),None)
    if event is None:raise ValueError('signed application authorization required')
    authentication=event['payload']['authentication'];fields(authentication,'profile record_sha256')
    sha256(authentication['record_sha256'])
    if authentication['profile']!=authority.PROFILE:raise ValueError('unsupported application authentication')
    saved,pin=record(store.root/'review-auth'/(authentication['record_sha256']+'.json'),2*authority.MAX_BYTES)
    fields(saved,'schema_version profile request signature proof')
    if (type(saved['schema_version']) is not int or saved['schema_version']!=1 or saved['profile']!=authority.PROFILE
            or pin!=authentication['record_sha256']):raise ValueError('application authentication record changed')
    review=authority.request_value(store,saved['request'],purpose=PURPOSE)
    fields(review,'schema_version profile case_id expected_event_sha256 reviewer binding')
    prepared=next((event for event,_ in events if event['kind']=='application_authorization_prepared'),None)
    if (review['profile']!=AUTH_PROFILE or review['case_id']!=authorization_id or prepared is None
            or review['expected_event_sha256']!=prepared['previous_sha256']
            or prepared['payload']!={'request_sha256':hashed(saved['request'])}
            or event['payload']['request_sha256']!=hashed(saved['request'])):
        raise ValueError('application authenticated parent/request changed')
    current=binding(args.project,args.project_settings,args.configuration_sha256,vars(args))
    if inputs['project']!=str(authority._path(args.project)) or review['binding']!=current:
        raise ValueError('application signed inputs changed')
    proof=authority.verify(store,saved['request'],saved['signature'],purpose=PURPOSE,deadline=deadline)
    if proof!=saved['proof']:raise ValueError('application authentication proof changed')
    return case,inputs,events,current


class _Session:
    __slots__=('__weakref__',)


_SESSIONS=weakref.WeakKeyDictionary()


def _owned(session):
    data=_SESSIONS.get(session) if type(session) is _Session else None
    if data is None or data['pid']!=os.getpid() or data['closed'] or time.monotonic()>=data['deadline']:
        raise ValueError('current controller-owned application session required')
    return data


def begin(args,*,deadline,backend):
    """Consume preauthorization before collection or provider work; interruption spends it."""
    from .evaluation.regression_review import ReviewStore
    from .config import reference_path
    settings=args.project_settings
    if not selected(settings):raise ValueError('conditional application configuration required')
    application=settings['application'];store=ReviewStore(reference_path(args.project,application['review_store']))
    with store.locked():
        case,inputs,events,current=_approved(store,application['authorization_id'],args,deadline,state='application_authorized')
        # Journal intent precedes external work. A failed or interrupted begin is not retryable.
        store.append(case,events,'application_started','application_running',dict(binding_sha256=hashed(current)))
    from .evaluation import bank_requalification,closed_inputs
    domain=None
    try:
        domain=bank_requalification.begin(store,application['bank_case_id'],
            reference_path(args.project,application['delegation_request']),
            reference_path(args.project,application['delegation_signature']),project=args.project,deadline=deadline)
        checked=closed_inputs.check(domain)
        if len(checked['sources'])!=1:raise ValueError('conditional application requires one exact signed evidence source')
        source=checked['sources'][0]
        if source['kind']=='events_file':args.events=Path(source['selection']);args.source_root=None
        elif source['kind']=='native_source_root':args.events=None;args.source_root=Path(source['selection'])
        else:raise ValueError('unsupported signed application source')
        args.format=source['format'];args.source_version=source['version'];args.session=source['session']
        args.evaluation_store=Path(checked['history_store']);args._closed_input_domain=domain
        session=_Session();_SESSIONS[session]=dict(pid=os.getpid(),args=args,store=store,
            authorization_id=application['authorization_id'],deadline=deadline,domain=domain,backend=backend,
            binding=current,closed=False,phase='construction',plan=None,final_run=None)
        return session
    except BaseException:
        if domain is not None:closed_inputs.close(domain)
        raise


def close(session):
    data=_SESSIONS.get(session) if type(session) is _Session else None
    if data is None or data['pid']!=os.getpid():raise ValueError('owned application session required for cleanup')
    if data['closed']:return
    from .evaluation import closed_inputs
    closed_inputs.close(data['domain']);data['closed']=True


def prepare_bank(session,output):
    """Freeze reviewed final inputs before any development/model exposure."""
    data=_owned(session)
    if data['phase']!='construction' or 'bank_manifest' in data:raise ValueError('final bank already prepared')
    from .evaluation.bank_tools import export_manifest
    store=data['store'];application=data['args'].project_settings['application']
    with store.locked():
        case,inputs,events=store.opened(application['bank_case_id'])
        qualified=next((event for event,_ in events if event['kind']=='bank_qualified'),None)
        if qualified is None:raise ValueError('reviewed final bank required')
        bank=store.root/'revisions'/qualified['payload']['revision_sha256']/'bank.json'
        manifest=export_manifest(bank,Path(output)/'conditional-final-bank',max_trials=application['method']['max_trials'])
    path=Path(output)/'conditional-final-bank'/'manifest.json'
    data['bank_manifest']=path;data['bank_manifest_sha256']=record(path,1024*1024)[1]
    data['bank_export']=deepcopy(manifest)


def prepare_final(session,plan,baseline,candidate,client,backend,*,trials_left):
    """Bind the actual owned proposal and the complete predeclared fixed schedule."""
    data=_owned(session);args=data['args']
    if data['phase']!='construction' or type(plan) is not HarnessChangePlan or data['backend'] is not backend:
        raise ValueError('owned construction phase and immutable plan required')
    operations=plan.operations
    if (len(operations)!=1 or operations[0].path!='AGENTS.md' or operations[0].action!='update'
            or operations[0].facets!=('instruction',) or plan.unresolved_operations):
        raise ValueError('conditional application supports one root instruction update only')
    from .workflow import candidate_states
    original={row.path:(row.sha256,row.mode) for row in plan.before}
    plan.validate_inventories(candidate_states(baseline,original),candidate_states(candidate,original))
    if set(original)!={'AGENTS.md'} or any(row.mode&0o111 for row in (*plan.before,*plan.after)):
        raise ValueError('conditional instruction inventory changed')
    from .promotion.prerequisites import current
    from .evaluation import closed_inputs
    from .evaluation.construction_access import _scope
    scope=current();access=_scope(args._construction_access)
    if scope is None or not scope._valid() or access['client'] is not client or access['backend'] is not backend:
        raise ValueError('same live construction, client and runtime required')
    closed_inputs.check(data['domain'])
    with data['store'].locked():_approved(data['store'],data['authorization_id'],args,data['deadline'],state='application_running')
    path=data['bank_manifest'];source,pin=record(path,1024*1024)
    if pin!=data['bank_manifest_sha256'] or source!=data['bank_export']:raise ValueError('frozen final bank changed')
    from .evaluation.plan import ExperimentSpec
    from .evaluation.oracle import identity
    from .evaluation.comparison_custody import bundle_binding
    from .evaluation.precision import preflight
    from .cli import evaluator
    declared=args.project_settings['application']['method'];runner=evaluator()
    spec=ExperimentSpec(declared['experiment_id'],tuple((t['id'],t['root'],t['family']) for t in source['tasks']),
        declared['repeats'],declared['seed'],declared['max_trials'],declared['min_roots'],
        declared['effect_margin'],declared['family_margin'],declared['alpha'],tuple(sorted(declared['family_weights'].items())),
        runner.digest(baseline),runner.digest(candidate),identity(path.parent,source['tasks'],source['grader_bundles']),args.image_sha256,
        profile='certification',partition='certification',precision_half_width=declared['precision_half_width'])
    spec.validate();precision=preflight(spec,trial_limit=trials_left,budget_seconds=data['deadline']-time.monotonic())
    if not precision['execution_allowed']:return dict(status='inconclusive',reason='precision_target_infeasible',precision_preflight=precision)
    request=dict(schema_version=1,profile=POLICY,authorization_id=data['authorization_id'],
        plan_sha256=plan.sha256,configuration_sha256=args.configuration_sha256,method=declared)
    request_path=path.parent/'application-request.json';write_bytes(request_path,encoded(request))
    source.update(experiment=spec.to_dict(),workflow_request_sha256=hashed(request),
        workflow_bundles=dict(baseline=bundle_binding(baseline,data['deadline']),candidate=bundle_binding(candidate,data['deadline'])),
        no_nested_agents=True)
    bound=path.parent/'bound-manifest.json';write_bytes(bound,encoded(source))
    data.update(phase='final_prepared',plan=plan,scope=scope,client=client,baseline=Path(baseline),candidate=Path(candidate),
        manifest=bound,manifest_sha256=record(bound,1024*1024)[1],spec=spec,request_path=request_path,precision=precision)
    return None


def _final_admission(session,domain,manifest,baseline,candidate):
    data=_owned(session);args=data['args']
    if (data['domain'] is not domain or data['phase'] not in ('final_prepared','final_reserved','final_running')
            or (Path(manifest),Path(baseline),Path(candidate))!=(data['manifest'],data['baseline'],data['candidate'])
            or record(manifest,1024*1024)[1]!=data['manifest_sha256']):
        raise ValueError('exact owned fixed final application required')
    from .promotion.prerequisites import current
    if current() is not data['scope'] or not data['scope']._valid():raise ValueError('current application Scope required')
    from .one_call import check_configuration
    check_configuration(args)
    _approved(data['store'],data['authorization_id'],args,data['deadline'],state='application_running')
    from .evaluation.comparison_custody import bundle_binding
    source,_=record(manifest,1024*1024)
    from .evaluation.plan import ExperimentSpec
    if (ExperimentSpec.parse(source['experiment']).digest!=data['spec'].digest or source['workflow_bundles']!=dict(
            baseline=bundle_binding(baseline,data['deadline']),candidate=bundle_binding(candidate,data['deadline']))):
        raise ValueError('application frozen schedule or harness changed')
    if data['client'].pending or data['client'].poisoned:raise ValueError('application provider attempt unresolved')
    return dict(plan_sha256=data['plan'].sha256,experiment_sha256=data['spec'].digest,
        configuration_sha256=args.configuration_sha256,policy=POLICY)


def main(argv):
    """Explicit host authorization setup; init and doctor never enter this path."""
    import argparse
    import json
    from . import config
    from .evaluation.regression_review import ReviewStore
    parser=argparse.ArgumentParser(prog='ahl application-review')
    parser.add_argument('--store',type=Path,required=True)
    commands=parser.add_subparsers(dest='command',required=True)
    allocate_command=commands.add_parser('allocate');allocate_command.add_argument('project',type=Path)
    policy_command=commands.add_parser('install-policy');policy_command.add_argument('policy',type=Path)
    prepare_command=commands.add_parser('prepare');prepare_command.add_argument('authorization_id')
    prepare_command.add_argument('project',type=Path);prepare_command.add_argument('--reviewer',required=True)
    authorize_command=commands.add_parser('authorize');authorize_command.add_argument('authorization_id')
    authorize_command.add_argument('request',type=Path);authorize_command.add_argument('signature',type=Path)
    for name in ('show','retire'):
        command=commands.add_parser(name);command.add_argument('authorization_id')
    args=parser.parse_args(argv);store=ReviewStore(args.store)
    try:
        if args.command=='allocate':result=allocate(store,args.project)
        elif args.command=='install-policy':result=install_policy(store,args.policy)
        elif args.command=='prepare':
            project=authority._path(args.project);settings,pin=config.load(project/'ahl.json')
            options=config.execution_options(project,settings,{},set())
            result=prepare(store,args.authorization_id,project,settings,pin,options,args.reviewer)
        elif args.command=='authorize':result=authorize(store,args.authorization_id,args.request,args.signature,deadline=time.monotonic()+10)
        elif args.command=='retire':
            with store.locked():
                case,inputs,events=opened(store,args.authorization_id)
                store.append(case,events,'retired','retired',dict(reason='explicit_operator_retirement'))
                result=summary(args.authorization_id,inputs,events)
        else:result=store.show(args.authorization_id)
        print(json.dumps(result,indent=2));return 0
    except (ValueError,OSError,TypeError,KeyError,TimeoutError):
        parser.exit(2,'application-review: authorization unavailable or inputs changed\n')


def binding(project,settings,configuration_sha256,options):
    """Capture selected inputs, never permission to apply or a claim about future trials."""
    from .config import response_references,reference_path
    from .evaluation.responses import read_settings
    from .evaluation.instruction_context import PROFILE as INSTRUCTION_PROFILE
    application=settings['application'];validate_settings(application)
    references=response_references(project,options)
    inference,_=read_settings(options['inference_settings'])
    if inference.get('instruction_protocol')!=INSTRUCTION_PROFILE:raise ValueError('conditional application requires actual controller-loaded instructions')
    request=reference_path(project,application['delegation_request']);signature=reference_path(project,application['delegation_signature'])
    return dict(policy=POLICY,project_sha256=hashed(str(authority._path(project))),baseline=target_record(project),
        configuration_sha256=configuration_sha256,configuration_references_sha256=hashed(references),
        runtime=dict(agent=options['agent'],image_sha256=options['image_sha256'],permission_audit=options['permission_audit'],
            settings_sha256=references['settings_sha256']),bank_case_id=application['bank_case_id'],
        delegation_request_sha256=hashlib.sha256(read_regular(request,authority.MAX_BYTES)).hexdigest(),
        delegation_signature_sha256=hashlib.sha256(read_regular(signature,16*1024)).hexdigest(),
        method=method(application['method']),limits=deepcopy(settings['limits']),
        allowed_operations=[dict(path='AGENTS.md',action='update',facets=['instruction'])],
        served_model='unverified',native_activation='unverified',external_exposure='unknown',pretraining_exposure='unknown')
