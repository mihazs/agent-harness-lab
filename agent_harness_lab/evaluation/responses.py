"""Explicit controller-owned Responses inference; no credentials reach workers."""
import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from weakref import WeakKeyDictionary
from .integrity import read_regular, unique_object

_CLIENT_PIDS = WeakKeyDictionary()
_ROLE_SCOPES = WeakKeyDictionary()
_REQUEST_CONTEXTS = WeakKeyDictionary()
_RESPONDING = WeakKeyDictionary()

# Trusted controller child: its parent can terminate DNS/TLS/header reads at deadline.
HTTP_CLIENT = r'''import base64,http.client,json,sys
connection = None
try:
 request = json.loads(sys.stdin.buffer.read(16*1024*1024+1))
 connection = http.client.HTTPSConnection('api.openai.com',443,timeout=request['seconds'])
 connection.request('POST','/v1/responses',body=base64.b64decode(request['body'],validate=True),
  headers={'Authorization':'Bearer '+request['credential'],'Content-Type':'application/json'})
 response = connection.getresponse()
 if response.status != 200 or response.getheader('Content-Type','').split(';')[0].strip().lower() != 'application/json':
  raise ValueError('unsupported response')
 if response.getheader('Content-Encoding','identity').lower() != 'identity': raise ValueError('encoded response')
 raw = response.read(request['max_response_bytes']+1)
 if len(raw)>request['max_response_bytes']: raise ValueError('response too large')
 sys.stdout.buffer.write(raw)
except BaseException:
 sys.exit(2)
finally:
 if connection is not None: connection.close()
'''


class InferenceError(RuntimeError):
    """Fixed local disposition; never includes provider bodies or credentials."""


def invalid_number(value):
    raise ValueError('nonfinite inference JSON number')


def read_settings(path):
    raw = read_regular(path,65536)
    value = json.loads(raw,object_pairs_hook=unique_object,parse_constant=invalid_number)
    caps = dict(max_requests=1000,max_tool_calls=10000,max_output_tokens=1000000,
                max_request_bytes=4*1024*1024,max_response_bytes=4*1024*1024)
    from .instruction_context import PROFILE
    required={'schema_version','model','credential_file',*caps}
    if (not isinstance(value,dict) or set(value) != required | ({'instruction_protocol'} if 'instruction_protocol' in value else set())
            or 'instruction_protocol' in value and value['instruction_protocol']!=PROFILE
            or type(value['schema_version']) is not int or value['schema_version'] != 1):
        raise ValueError('unsupported inference settings schema')
    if any(type(value[key]) is not int or not 0 < value[key] <= maximum for key,maximum in caps.items()):
        raise ValueError('inference caps must be explicit bounded positive integers')
    model, credential = value['model'],value['credential_file']
    if not isinstance(model,str) or not 0 < len(model) <= 128 or any(ord(c)<33 for c in model):
        raise ValueError('inference requires an explicit model identifier')
    if not isinstance(credential,str) or not Path(credential).is_absolute() or len(credential)>4096 or any(ord(c)<32 for c in credential):
        raise ValueError('inference requires an explicit absolute credential file')
    return value,hashlib.sha256(raw).hexdigest()


def credential_path(settings, forbidden_roots):
    path = Path(settings['credential_file'])
    if any(path.resolve().is_relative_to(Path(root).resolve()) for root in forbidden_roots):
        raise ValueError('inference credential cannot be inside worker-visible inputs or outputs')
    return path


def read_credential(settings, forbidden_roots):
    path = credential_path(settings,forbidden_roots)
    info = path.lstat()
    if info.st_uid != os.geteuid() or info.st_mode&0o077:
        raise ValueError('inference credential requires private owner-only permissions')
    raw = read_regular(path,8192).decode('ascii').strip()
    if not raw or any(not 33 <= ord(c) <= 126 for c in raw):
        raise ValueError('inference credential must be a single opaque ASCII token')
    return raw


def request(credential, body, max_response_bytes, deadline):
    frame = json.dumps(dict(credential=credential,body=base64.b64encode(body).decode('ascii'),
        max_response_bytes=max_response_bytes,seconds=max(.001,deadline-time.monotonic()))).encode()
    remaining = deadline-time.monotonic()
    if remaining <= 0: raise TimeoutError('inference deadline exhausted before dispatch')
    try:
        with subprocess.Popen([sys.executable,'-I','-S','-c',HTTP_CLIENT],stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={},start_new_session=True) as process:
            try:
                remaining = deadline-time.monotonic()
                if remaining <= 0: raise TimeoutError('inference deadline exhausted during transport startup')
                stdout,_ = process.communicate(frame,timeout=remaining)
            except BaseException:
                process.kill(); process.communicate()
                raise
            returncode = process.returncode
    except subprocess.TimeoutExpired:
        raise TimeoutError('inference request deadline exhausted; no retry') from None
    if returncode != 0 or len(stdout)>max_response_bytes:
        raise InferenceError('inference_transport_failed_no_retry')
    if time.monotonic() >= deadline: raise TimeoutError('inference response arrived after deadline')
    return stdout


TOOL = dict(type='function',name='run',description='Run one argv command in the isolated task workspace.',
    strict=True,parameters=dict(type='object',properties=dict(argv=dict(type='array',items=dict(type='string'))),
                                required=['argv'],additionalProperties=False))


class ActionLedger:
    """Bounded private observations; omitted evidence never becomes a negative fact."""
    MAX_RECORDS = 4096
    MAX_BYTES = 8*1024*1024
    OUTPUT_BYTES = 8192
    MAX_SCOPES = 4096
    MAX_MESSAGE_PARTS = 64

    def __init__(self,credential):
        self.credential = credential
        self.ledger_id = secrets.token_hex(16)
        self.rows = {}; self.bytes = self.sequence = self.scope_sequence = self.order = 0
        self.scopes = {}; self.trajectory = None; self.scope = self.begin('unscoped',None)

    def begin(self,role,trial_id,*,trajectory=None):
        if trial_id is not None and (not isinstance(trial_id,str) or not 0<len(trial_id)<=256):
            raise ValueError('bounded controller trial identity required')
        self.scope_sequence += 1; scope = 'scope-'+str(self.scope_sequence)
        label = self.text(trial_id,256)
        self.current = dict(role=role,trial_id=label['text'],trial_id_visibility=label['visibility'],
            omitted_records=0,omitted_updates=0,scope_retained=len(self.scopes)<self.MAX_SCOPES)
        if self.current['scope_retained']: self.scopes[scope] = self.current
        self.scope = scope
        self.trajectory = None
        if trajectory is not None:
            from .trajectory_assertions import _begin_capture
            self.trajectory = _begin_capture(self,trajectory,trial_id,role)
        return scope

    def text(self,value,limit):
        if not isinstance(value,str): return dict(text=None,visibility='unavailable',observed_text_utf8_bytes=None)
        try: observed_bytes = len(value.encode('utf-8'))
        except UnicodeEncodeError: observed_bytes = None
        redacted = bool(self.credential and self.credential in value)
        if redacted: value = value.replace(self.credential,'[REDACTED_CREDENTIAL]')
        raw = value.encode('utf-8','replace'); selected = raw[:limit]; clipped = len(raw)>limit
        result = dict(text=selected.decode('utf-8','ignore'),visibility='redacted' if redacted else 'truncated' if clipped else 'observed',
            observed_text_utf8_bytes=observed_bytes,truncated=clipped,redacted=redacted)
        if observed_bytes is None:
            result.update(visibility='unknown_encoding_loss',encoding_loss=True,reason='source_not_valid_utf8')
        return result

    @staticmethod
    def size(value): return len(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8'))

    def step(self):
        self.order += 1
        return self.order

    def add(self,kind,request_ordinal,**fields):
        self.sequence += 1; ident = self.ledger_id+':'+str(self.sequence)
        row = dict(controller_id=ident,scope_id=self.scope,kind=kind,request_ordinal=request_ordinal,observed_ordinal=self.step(),**fields)
        if self.trajectory is not None and kind=='tool_request': self.trajectory.event('request')
        size = self.size(row)
        if not self.current['scope_retained'] or len(self.rows)>=self.MAX_RECORDS or self.bytes+size>self.MAX_BYTES:
            self.current['omitted_records'] += 1
        else: self.rows[ident]=row; self.bytes+=size
        return ident

    def update(self,ident,**fields):
        if self.trajectory is not None:
            if fields.get('decision')=='denied':
                fields['decision_ordinal']=self.step(); self.trajectory.event('denied')
            if fields.get('backend_invoked') is True: self.trajectory.event('dispatch')
            if fields.get('worker_outcome')=='observed': self.trajectory.event('result')
        if ident not in self.rows:
            self.current['omitted_updates'] += 1; return
        old = self.rows[ident]; new = dict(old,**fields,last_observation_ordinal=self.step()); delta = self.size(new)-self.size(old)
        if self.bytes+delta>self.MAX_BYTES:
            self.scopes[old['scope_id']]['omitted_updates'] += 1
            new = dict(controller_id=ident,scope_id=old['scope_id'],kind=old['kind'],visibility='unknown_ledger_limit')
            delta = self.size(new)-self.size(old)
        self.rows[ident] = new; self.bytes += delta

    def report(self,scope=None):
        scope = scope or self.scope; meta = self.scopes.get(scope,self.current if scope==self.scope else None)
        if meta is None: raise ValueError('action scope not retained')
        return deepcopy(dict(schema_version=1,privacy='private',publication_allowed=False,ledger_id=self.ledger_id,
            scope_id=scope,role=meta['role'],trial_id=meta['trial_id'],trial_id_visibility=meta['trial_id_visibility'],
            actions=[row for row in self.rows.values() if row['scope_id']==scope],
            coverage=dict(omitted_records=meta['omitted_records'],omitted_updates=meta['omitted_updates'],
                records_complete=not(meta['omitted_records'] or meta['omitted_updates']) and meta['scope_retained'],
                scope='controller_record_retention_only',max_records=self.MAX_RECORDS,max_bytes=self.MAX_BYTES),
            redaction_policy='exact_controller_credential',
            argv_semantic_authorization='unknown',subprocess_visibility='unobserved',read_visibility='unobserved',transient_write_visibility='unobserved'))


def run_arguments(item):
    arguments = json.loads(item['arguments'],object_pairs_hook=unique_object,parse_constant=invalid_number)
    if not isinstance(arguments,dict) or set(arguments)!={'argv'}: raise ValueError('invalid run arguments')
    argv = arguments['argv']
    if (not isinstance(argv,list) or not 0<len(argv)<=100
            or not all(isinstance(arg,str) and arg and '\0' not in arg and len(arg.encode('utf-8'))<=4096 for arg in argv)
            or sum(len(arg.encode('utf-8')) for arg in argv)>32768): raise ValueError('invalid bounded argv')
    return argv


def summarize_provider_usage(report, *, public=False):
    """Summarize recorded request usage without treating missing attempts as free."""
    if public is not False: raise ValueError('provider usage summary is private')
    if not isinstance(report,dict) or report.get('runtime') != 'controller-responses-executor-v1':
        raise ValueError('unsupported controller usage report')
    attempts,rows = report.get('request_attempts'),report.get('provider_reported_usage')
    if type(attempts) is not int or not 0 <= attempts <= 1000 or not isinstance(rows,list) or len(rows) > attempts:
        raise ValueError('invalid controller request coverage')
    fields = ('input_tokens','output_tokens','total_tokens','cached_input_tokens')
    counts = {name:[] for name in fields}
    for row in rows:
        if not isinstance(row,dict): raise ValueError('invalid provider usage record')
        for name in fields[:3]:
            if type(row.get(name)) is not int or not 0 <= row[name] <= 10**12:
                raise ValueError('invalid provider usage record')
        if row['input_tokens'] + row['output_tokens'] != row['total_tokens']:
            raise ValueError('inconsistent provider usage totals')
        cached = row.get('cached_input_tokens')
        if cached is not None and (type(cached) is not int or not 0 <= cached <= row['input_tokens']):
            raise ValueError('invalid provider cached input usage')
        for name in fields: counts[name].append(row.get(name))
    result = {}
    for name,values in counts.items():
        known = [value for value in values if value is not None]
        missing = attempts - len(known)
        result[name] = dict(known_subtotal=sum(known), known_requests=len(known), unknown_requests=missing,
                            total=sum(known) if attempts and not missing else None)
    return dict(profile='controller-provider-usage-summary-v1', source='provider_reported_unverified',
                request_attempts=attempts, reported_responses=len(rows), unreported_attempts=attempts-len(rows),
                usage=result, cached_input_included_in_input=True, cache_write_tokens=None, billing_completeness='unknown',
                publication='private', publication_allowed=False, automatic_promotion=False)


class Client:
    def __init__(self, settings, credential, deadline, *, transport=None, record=None, configuration_sha256=None, configuration_references_sha256=None):
        _CLIENT_PIDS[self] = (os.getpid(),configuration_sha256,deepcopy(settings),deadline,deepcopy(TOOL),configuration_references_sha256) if settings.get('instruction_protocol') is not None else os.getpid()
        self.settings,self.credential,self.deadline = dict(settings),credential,deadline
        self.transport = request if transport is None else transport
        self.requests = self.tool_calls = 0
        self.request_bytes = self.response_bytes = 0
        self.poisoned = False
        self.pending = False
        self.record = record
        self.item_ids,self.call_ids = set(),set()
        self._usage = []
        self.actions = ActionLedger(credential)
        self.response_actions = []; self.response_reserved = False; self.response_reason = 'protocol_validation_failed'
        self.response_emissions = []; self.response_protocol_validated = False

    @property
    def usage(self):
        """Return a snapshot of recorded provider usage."""
        return deepcopy(self._usage)

    def observe_message(self,item,item_index):
        content = item.get('content')
        parts = content if isinstance(content,list) and content else [None]
        omitted = max(0,len(parts)-self.actions.MAX_MESSAGE_PARTS)
        self.actions.current['omitted_records'] += omitted
        for index,part in enumerate(parts[:self.actions.MAX_MESSAGE_PARTS]):
            kind = part.get('type') if isinstance(part,dict) else None
            key = {'output_text':'text','refusal':'refusal'}.get(kind) if isinstance(kind,str) else None
            text = self.actions.text(part.get(key) if key else None,self.actions.OUTPUT_BYTES) if isinstance(part,dict) else self.actions.text(None,0)
            self.response_emissions.append(self.actions.add('assistant_emission' if item.get('role')=='assistant' else 'message_observation',self.requests,
                source_item_index=item_index,source_part_index=index,model_message_id=self.actions.text(item.get('id'),256),
                claimed_role=self.actions.text(item.get('role'),128),content_type=self.actions.text(kind,128),content=text,
                content_supported=key is not None and isinstance(part.get(key),str),
                content_parts_omitted=omitted,protocol_validated=False,response_disposition='pending_validation',
                observation_scope='controller_transport_text_only',external_delivery='unknown',native_provenance='unverified'))

    def observe_items(self,output):
        for index,item in enumerate(output):
            if not isinstance(item,dict): continue
            if item.get('type')=='message':
                self.observe_message(item,index); continue
            if item.get('type')=='reasoning': continue
            name = self.actions.text(item.get('name',item.get('type')),256)
            ident = self.actions.text(item.get('call_id'),256)
            values = None
            try:
                values = [self.actions.text(a,4096) for a in run_arguments(item)]
                argv = [v['text'] for v in values]
                visibility = 'redacted' if any(v['redacted'] for v in values) else 'observed'
            except (ValueError,TypeError,KeyError,RecursionError,UnicodeError): argv,visibility = None,'invalid_or_unavailable'
            kind = 'tool_request' if item.get('type')=='function_call' else 'unsupported_response_item'
            argument_fields = dict(argv_observations=values) if self.actions.trajectory is not None else {}
            self.response_actions.append(self.actions.add(kind,self.requests,
                tool_name=name,model_call_id=ident,argv=argv,argv_visibility=visibility,
                source_item_type=self.actions.text(item.get('type'),128),decision_scope='local_controller_dispatch',
                name_policy='declared' if item.get('type')=='function_call' and item.get('name')=='run' else 'undeclared',
                decision='pending_validation',reserved=False,backend_invoked=False,worker_outcome='unobserved',**argument_fields))

    def report(self):
        value = dict(runtime='controller-responses-executor-v1',request_attempts=self.requests,tool_call_reservations=self.tool_calls,
            requested_model=self.settings['model'],effective_model_verified=False,provider_reported_usage=self.usage,
            request_bytes=self.request_bytes,response_bytes_observed=self.response_bytes,
            reserved_output_tokens=self.requests*self.settings['max_output_tokens'],
            limits={key:self.settings[key] for key in ('max_requests','max_tool_calls','max_output_tokens','max_request_bytes','max_response_bytes')},
            output_token_cap_scope='per_request; whole-run reservation ceiling is max_requests * max_output_tokens',
            request_pending=self.pending,further_requests_blocked=self.poisoned,
            automatic_promotion=False)
        if self in _REQUEST_CONTEXTS: value['request_binding'] = deepcopy(_REQUEST_CONTEXTS[self][4])
        return value

    def action_report(self,scope=None):
        return self.actions.report(scope)

    def checkpoint(self):
        if self.record is not None: self.record(self.report())

    def abort(self):
        self.poisoned = True
        self.checkpoint()

    def respond(self, history, deadline):
        if self in _RESPONDING: raise ValueError('reentrant inference request denied')
        _RESPONDING[self] = os.getpid()
        try: return self._respond_guarded(history,deadline)
        finally: _RESPONDING.pop(self,None)

    def _respond_guarded(self, history, deadline):
        if _client_pid(self) != os.getpid():
            raise ValueError('inference client requires its issuing process')
        from .comparison_custody import current
        comparison=current()
        if comparison is not None and comparison.active is not None:comparison.before_request(self)
        request_action = self.actions.add('model_request',self.requests+1,reserved=False,transport_invoked=False,response_observed=False,
            tool_item_visibility='unavailable',emission_visibility='unavailable')
        self.response_actions = []; self.response_reserved = False; self.response_reason = 'protocol_validation_failed'
        self.response_emissions = []; self.response_protocol_validated = False
        self.response_items_observed = False; self.response_transport_invoked = False
        disposition = 'denied_or_unresolved'
        usage_token = object()
        try:
            output,calls,reported_usage = self._respond(history,deadline,request_action,usage_token)
            reservation=_REQUEST_CONTEXTS.get(self)
            if reservation is not None and 'instruction_binding' in reservation[4]:
                _REQUEST_CONTEXTS[self]=(*reservation[:3],True,reservation[4])
                from agent_harness_lab.promotion.prerequisites import current as current_scope
                owner=current_scope()
                if owner is not None:owner._request(self,reservation[2],True)
            if comparison is not None and comparison.active is not None:
                comparison.request(self)
                from .trial_usage import _confirm
                _confirm(comparison,self,usage_token,reported_usage)
            self.actions.update(request_action,disposition='validated')
            disposition = 'validated'
            return output,calls
        except BaseException:
            self.actions.update(request_action,disposition='denied_or_unresolved',reason=self.response_reason)
            for action in self.response_actions:
                if not self.response_reserved: self.actions.update(action,decision='denied',reason=self.response_reason)
                else: self.actions.update(action,dispatch_blocked=True)
            raise
        finally:
            if comparison is not None:
                from .trial_usage import _abort
                _abort(comparison,self,usage_token)
            if self.actions.trajectory is not None and self.response_transport_invoked and not self.response_items_observed:
                self.actions.trajectory.request_gap()
            for emission in self.response_emissions:
                self.actions.update(emission,protocol_validated=self.response_protocol_validated,response_disposition=disposition)

    def _respond(self,history,deadline,request_action,usage_token):
        if self.poisoned:
            self.response_reason='prior_request_unresolved'; raise InferenceError('prior_inference_request_unresolved_no_retry')
        deadline = min(deadline,self.deadline)
        if time.monotonic() >= deadline:
            self.response_reason='request_deadline_exhausted'; raise TimeoutError('inference deadline exhausted')
        if self.requests >= self.settings['max_requests']:
            self.response_reason='request_count_exhausted'; raise InferenceError('inference_request_count_exhausted')
        body = json.dumps(dict(model=self.settings['model'],store=False,parallel_tool_calls=False,
            tools=[TOOL],max_output_tokens=self.settings['max_output_tokens'],input=history),allow_nan=False).encode('utf-8')
        if len(body)>self.settings['max_request_bytes']:
            self.response_reason='request_bytes_exhausted'; raise InferenceError('inference_request_bytes_exhausted')
        role_scope=_ROLE_SCOPES.get(self)
        instruction=planned=None
        if _instruction_selected(self):
            if role_scope is None or len(role_scope)!=5:raise ValueError('owned instruction role required')
            from .instruction_context import binding
            instruction=binding(role_scope[4],self,history,body)
            planned=dict(profile='controller-request-reservation-v1',scope_id=role_scope[1],role=role_scope[2],
                trial_id=self.actions.current['trial_id'],request_ordinal=self.requests+1,
                publication_allowed=False,completion_authority=False,instruction_binding=instruction)
            from .comparison_custody import current
            comparison=current()
            if comparison is not None:planned['comparison']=comparison.reservation(self,role_scope[3])
            from agent_harness_lab.promotion.prerequisites import current as current_scope
            owner=current_scope()
            if owner is not None:owner._request_capacity(planned,self)
            if time.monotonic()>=deadline:raise TimeoutError('instruction capture exhausted request deadline')
        self.requests += 1
        self.request_bytes += len(body)
        self.poisoned = True
        self.pending = True
        self.actions.update(request_action,reserved=True,request_ordinal=self.requests)
        scope = _ROLE_SCOPES.get(self)
        if scope is not None:
            pid,scope_id,role,trial_id = scope[:4]
            if (pid != os.getpid() or self.actions.scope != scope_id
                    or self.actions.current['role'] != role
                    or self.actions.current['trial_id'] != self.actions.text(trial_id,256)['text']):
                raise ValueError('actual inference role scope changed')
            binding = dict(profile='controller-request-reservation-v1',scope_id=scope_id,role=role,
                trial_id=self.actions.current['trial_id'],request_ordinal=self.requests,
                publication_allowed=False,completion_authority=False)
            from .comparison_custody import current
            comparison = current()
            if comparison is not None: binding['comparison'] = comparison.reservation(self,trial_id)
            if planned is not None:
                if binding!={key:value for key,value in planned.items() if key!='instruction_binding'}:raise ValueError('reserved instruction scope changed')
                binding=planned
            token=object()
            _REQUEST_CONTEXTS[self]=(os.getpid(),scope,token,False,binding)
            if comparison is not None and 'comparison' in binding:
                from .trial_usage import _begin
                _begin(comparison,self,binding['comparison'],usage_token)
            if instruction is not None:
                from agent_harness_lab.promotion.prerequisites import current as current_scope
                owner=current_scope()
                if owner is not None:owner._request(self,token,False)
        else:
            _REQUEST_CONTEXTS.pop(self,None)
        if instruction is not None:self.actions.update(request_action,instruction_binding=instruction)
        self.checkpoint()
        self.actions.update(request_action,transport_invoked=True)
        self.response_transport_invoked = True
        raw = self.transport(self.credential,body,self.settings['max_response_bytes'],deadline)
        self.actions.update(request_action,response_observed=True,observed_response_bytes=len(raw))
        self.response_bytes += len(raw)
        if len(raw)>self.settings['max_response_bytes']: raise InferenceError('inference_response_bytes_exhausted')
        if time.monotonic() >= deadline: raise TimeoutError('inference deadline exhausted after response')
        try:
            value = json.loads(raw,object_pairs_hook=unique_object,parse_constant=invalid_number)
            candidate = value.get('output') if isinstance(value,dict) else None
            if isinstance(candidate,list) and 0<len(candidate)<=64:
                self.observe_items(candidate)
                self.response_items_observed = (value.get('status') == 'completed' and value.get('error') is None
                    and value.get('incomplete_details') is None and all(isinstance(item,dict)
                    and item.get('type') in ('message','reasoning','function_call') for item in candidate))
                self.actions.update(request_action,tool_item_visibility='bounded_items_observed',emission_visibility='bounded_items_observed')
            if (not isinstance(value,dict) or value.get('status') != 'completed'
                    or value.get('error') is not None or value.get('incomplete_details') is not None): raise ValueError('response incomplete')
            output = value['output']
            if not isinstance(output,list) or not 0 < len(output) <= 64: raise ValueError('invalid response items')
            calls = []; item_ids = set(); call_ids = set(); final = False
            for item in output:
                if not isinstance(item,dict): raise ValueError('invalid output item')
                ident = item.get('id')
                if not isinstance(ident,str) or not 0 < len(ident) <= 256 or ident in self.item_ids|item_ids:
                    raise ValueError('invalid or duplicate output item ID')
                item_ids.add(ident)
                if item.get('type') == 'reasoning':
                    if (set(item)-{'type','id','summary','encrypted_content','status'} or not isinstance(item.get('encrypted_content'),str)
                            or item.get('status') not in (None,'completed')): raise ValueError('unsupported reasoning item')
                elif item.get('type') == 'message':
                    if item.get('role') != 'assistant' or item.get('status') != 'completed': raise ValueError('invalid assistant item')
                    content = item['content']
                    if not isinstance(content,list) or not content: raise ValueError('empty assistant message')
                    for part in content:
                        key = {'output_text':'text','refusal':'refusal'}.get(part.get('type')) if isinstance(part,dict) else None
                        if key is None or not isinstance(part.get(key),str): raise ValueError('unsupported assistant content')
                    final = True
                elif item.get('type') == 'function_call':
                    if item.get('name')!='run': self.response_reason='undeclared_tool'
                    if (set(item)-{'type','id','call_id','name','arguments','status'} or item.get('name') != 'run'
                            or item.get('status') not in (None,'completed')): raise ValueError('unsupported function call')
                    call_id = item['call_id']
                    if call_id in self.call_ids|call_ids: self.response_reason='duplicate_model_call_id'
                    if not isinstance(call_id,str) or not 0 < len(call_id) <= 256 or call_id in self.call_ids|call_ids:
                        raise ValueError('invalid or repeated function call ID')
                    call_ids.add(call_id)
                    try: argv = run_arguments(item)
                    except (ValueError,TypeError,KeyError,RecursionError,UnicodeError):
                        self.response_reason='malformed_argv'; raise
                    calls.append((call_id,argv))
                else: raise ValueError('hosted or unknown execution item')
            if len(calls)>1 or not calls and not final: raise ValueError('unsupported response completion')
            usage = value.get('usage')
            if (not isinstance(usage,dict) or any(type(usage.get(key)) is not int or not 0<=usage[key]<=10**12
                    for key in ('input_tokens','output_tokens','total_tokens'))
                    or usage['input_tokens']+usage['output_tokens'] != usage['total_tokens']
                    or usage['output_tokens']>self.settings['max_output_tokens']): raise ValueError('invalid provider usage')
        except (ValueError,TypeError,KeyError,RecursionError,UnicodeError):
            raise InferenceError('inference_protocol_error_no_retry') from None
        self.response_protocol_validated = True
        if self.tool_calls+len(calls)>self.settings['max_tool_calls']:
            self.response_reason='tool_count_exhausted'; raise InferenceError('inference_tool_count_exhausted')
        self.tool_calls += len(calls)
        for action in self.response_actions:
            self.actions.update(action,decision='accepted',reserved=True,reservation_id=self.tool_calls)
        self.response_reserved = bool(calls)
        self.item_ids.update(item_ids); self.call_ids.update(call_ids)
        reported_usage = {key:usage[key] for key in ('input_tokens','output_tokens','total_tokens')}
        details = usage.get('input_tokens_details')
        if details is not None:
            cached = details.get('cached_tokens') if isinstance(details,dict) else None
            reported_usage['cached_input_tokens'] = cached if type(cached) is int and 0 <= cached <= usage['input_tokens'] else None
        self._usage.append(deepcopy(reported_usage))
        self.poisoned = False
        self.pending = False
        self.checkpoint()
        return output,calls,reported_usage


def _client_pid(client):
    value=_CLIENT_PIDS.get(client)
    return value[0] if isinstance(value,tuple) else value


def _instruction_selected(client):
    owner=_CLIENT_PIDS.get(client)
    selected=isinstance(owner,tuple)
    if (selected and (client.settings!=owner[2] or client.deadline!=owner[3] or TOOL!=owner[4])
            or not selected and client.settings.get('instruction_protocol') is not None):
        raise ValueError('selected instruction configuration changed')
    return selected


def _read_request(client,token=None,*,complete=False):
    state=_REQUEST_CONTEXTS.get(client)
    if (state is None or _client_pid(client)!=os.getpid() or state[0]!=os.getpid()
            or state[1] is None or state[1] is not _ROLE_SCOPES.get(client) or token is not None and token is not state[2]
            or state[3] is not complete or 'instruction_binding' not in state[4]):
        raise ValueError('current owned instruction request required')
    if not _instruction_selected(client):raise ValueError('owned instruction protocol required')
    return deepcopy(state[4])


def run_role(client, backend, workspace, inputs, role, instructions, prompt, deadline,*,trial_id=None,trajectory=None,task_cwd=None):
    if _client_pid(client) != os.getpid() or client in _ROLE_SCOPES:
        raise ValueError('fresh issuing-process role scope required')
    try:
        if role not in ('proposer','evaluator_author','executor'): raise ValueError('unsupported inference role')
        client.actions.begin(role,trial_id,trajectory=trajectory)
        scope=(os.getpid(),client.actions.scope,role,trial_id)
        if _instruction_selected(client):
            from .instruction_context import prepare
            scope=(*scope,prepare(client,backend,inputs,role,instructions,prompt,min(deadline,client.deadline),task_cwd=task_cwd))
        elif task_cwd is not None:raise ValueError('task cwd requires the controller instruction protocol')
        _ROLE_SCOPES[client] = scope
        return _run_role(client,backend,workspace,inputs,role,instructions,prompt,deadline)
    except BaseException:
        client.abort()
        raise
    finally:
        _ROLE_SCOPES.pop(client,None)
        state=_REQUEST_CONTEXTS.get(client)
        if state is not None:_REQUEST_CONTEXTS[client]=(state[0],None,*state[2:])
        if client.actions.trajectory is not None: client.actions.trajectory.close()


def _run_role(client, backend, workspace, inputs, role, instructions, prompt, deadline):
    from .working_state import WorkingState
    from .sandbox import Limits
    if role not in ('proposer','evaluator_author','executor'): raise ValueError('unsupported inference role')
    deadline = min(client.deadline,deadline)
    state = WorkingState(workspace,deadline)
    history = [dict(role='developer',content=instructions),
        dict(role='user',content=prompt)]
    role_scope=_ROLE_SCOPES.get(client)
    if role_scope is not None and len(role_scope)==5:history=deepcopy(role_scope[4]['prefix'])
    from .construction_seals import observe_input,observe_output
    observe_input(client,backend,workspace,inputs,role,state)
    commands = []
    while True:
        output,calls = client.respond(history,deadline)
        history.extend(output)
        if not calls:
            observe_output(client,backend,state)
            return state,commands
        call_id,argv = calls[0]
        action = client.response_actions[0]
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            client.actions.update(action,dispatch_blocked=True,reason='deadline_exhausted_before_backend')
            raise TimeoutError('executor deadline exhausted before command')
        client.actions.update(action,backend_invoked=True,dispatch_ordinal=client.actions.step(),worker_outcome='pending')
        try: run = backend.run(argv,workspace,inputs,role,Limits(seconds=remaining),working_state=state)
        except BaseException:
            client.actions.update(action,worker_outcome='unavailable',stdout=client.actions.text(None,0),stderr=client.actions.text(None,0))
            raise
        client.actions.update(action,worker_outcome='observed',outcome_ordinal=client.actions.step(),status=client.actions.text(run.get('status'),128),
            exit_code=run.get('exit_code') if type(run.get('exit_code')) is int else None,
            stdout=client.actions.text(run.get('stdout'),client.actions.OUTPUT_BYTES),
            stderr=client.actions.text(run.get('stderr'),client.actions.OUTPUT_BYTES),
            backend_stream_coverage='bounded_observed_text' if run.get('status') in ('completed','failed') else 'unknown_or_truncated',
            containment_validated=False)
        attestation = run.get('attestation',{})
        storage = attestation.get('storage_policy') or {}
        image = attestation.get('runtime_image') or {}
        cgroup = attestation.get('cgroup_v2') or {}
        if (run.get('status') not in ('completed','failed') or type(run.get('exit_code')) is not int or not 0 <= run['exit_code'] < 128
                or (run['exit_code']==0) != (run['status']=='completed') or run.get('inputs_unchanged') is not True
                or storage.get('verified') is not True or storage.get('extracted') is None
                or image.get('verified_before') is not True or image.get('verified_after') is not True
                or cgroup.get('empty_after_cleanup') is not True or attestation.get('aggregate_disk_bound_verified') is not True):
            raise InferenceError('executor_containment_or_state_failure')
        if getattr(backend,'permission_audit',None)=='permission-results-v1':
            audit=attestation.get('permission_audit',{})
            client.actions.update(action,permission_qualification=audit.get('qualification','unknown'))
            if audit.get('status')!='complete' or audit.get('qualification')!='qualified':
                raise InferenceError('permission_qualification_not_met')
        client.actions.update(action,containment_validated=True)
        commands.append({key:run[key] for key in ('status','exit_code','attestation')})
        history.append(dict(type='function_call_output',call_id=call_id,output=json.dumps(
            {key:run[key] for key in ('status','exit_code','stdout','stderr')},allow_nan=False)))


def run_trial(client, backend, workspace, harness, prompt, artifacts, destination, seconds,*,trial_id=None,trajectory=None,task_cwd=None):
    deadline = min(client.deadline,time.monotonic()+seconds)
    state,commands = run_role(client,backend,workspace,{'harness':harness},'executor',
        'Read and follow task guidance under /inputs/harness. '
        'Work only in /work using run(argv). Complete the supplied task and write its required artifacts. '
        'Host credentials, network and other runs are unavailable; final claims do not supply a grade.',prompt,deadline,trial_id=trial_id,trajectory=trajectory,task_cwd=task_cwd)
    exported = state.export(artifacts,destination,deadline)
    captured = dict(_trajectory_capture=client.actions.trajectory) if trajectory is not None else {}
    return dict(status='completed',exit_code=0,timeout=False,stdout='',stderr='',inputs_unchanged=True,
        inference=client.report(),worker_commands=commands,controller_actions=client.action_report(),artifact_workspace=str(exported),**captured)
