"""Explicit controller-owned Responses inference; no credentials reach workers."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from .integrity import read_regular, unique_object

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
    if (not isinstance(value,dict) or set(value) != {'schema_version','model','credential_file',*caps}
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


def read_credential(settings, forbidden_roots):
    path = Path(settings['credential_file'])
    if any(path.resolve().is_relative_to(Path(root).resolve()) for root in forbidden_roots):
        raise ValueError('inference credential cannot be inside worker-visible inputs or outputs')
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


class Client:
    def __init__(self, settings, credential, deadline, *, transport=None):
        self.settings,self.credential,self.deadline = dict(settings),credential,deadline
        self.transport = request if transport is None else transport
        self.requests = self.tool_calls = 0
        self.request_bytes = self.response_bytes = 0
        self.poisoned = False
        self.item_ids,self.call_ids = set(),set()
        self.usage = []

    def report(self):
        return dict(runtime='controller-responses-executor-v1',request_attempts=self.requests,tool_call_reservations=self.tool_calls,
            requested_model=self.settings['model'],effective_model_verified=False,provider_reported_usage=list(self.usage),
            request_bytes=self.request_bytes,response_bytes_observed=self.response_bytes,
            reserved_output_tokens=self.requests*self.settings['max_output_tokens'],
            limits={key:self.settings[key] for key in ('max_requests','max_tool_calls','max_output_tokens','max_request_bytes','max_response_bytes')},
            output_token_cap_scope='per_request; whole-run reservation ceiling is max_requests * max_output_tokens',
            automatic_promotion=False)

    def respond(self, history, deadline):
        if self.poisoned: raise InferenceError('prior_inference_request_unresolved_no_retry')
        deadline = min(deadline,self.deadline)
        if time.monotonic() >= deadline: raise TimeoutError('inference deadline exhausted')
        if self.requests >= self.settings['max_requests']: raise InferenceError('inference_request_count_exhausted')
        body = json.dumps(dict(model=self.settings['model'],store=False,parallel_tool_calls=False,
            tools=[TOOL],max_output_tokens=self.settings['max_output_tokens'],input=history),allow_nan=False).encode('utf-8')
        if len(body)>self.settings['max_request_bytes']: raise InferenceError('inference_request_bytes_exhausted')
        self.requests += 1
        self.request_bytes += len(body)
        self.poisoned = True
        raw = self.transport(self.credential,body,self.settings['max_response_bytes'],deadline)
        self.response_bytes += len(raw)
        if len(raw)>self.settings['max_response_bytes']: raise InferenceError('inference_response_bytes_exhausted')
        if time.monotonic() >= deadline: raise TimeoutError('inference deadline exhausted after response')
        try:
            value = json.loads(raw,object_pairs_hook=unique_object,parse_constant=invalid_number)
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
                    if (set(item)-{'type','id','call_id','name','arguments','status'} or item.get('name') != 'run'
                            or item.get('status') not in (None,'completed')): raise ValueError('unsupported function call')
                    call_id = item['call_id']
                    if not isinstance(call_id,str) or not 0 < len(call_id) <= 256 or call_id in self.call_ids|call_ids:
                        raise ValueError('invalid or repeated function call ID')
                    call_ids.add(call_id)
                    arguments = json.loads(item['arguments'],object_pairs_hook=unique_object,parse_constant=invalid_number)
                    if not isinstance(arguments,dict) or set(arguments) != {'argv'}: raise ValueError('invalid run arguments')
                    argv = arguments['argv']
                    if (not isinstance(argv,list) or not 0 < len(argv) <= 100
                            or not all(isinstance(arg,str) and arg and '\0' not in arg and len(arg.encode('utf-8'))<=4096 for arg in argv)
                            or sum(len(arg.encode('utf-8')) for arg in argv)>32768): raise ValueError('invalid bounded argv')
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
        if self.tool_calls+len(calls)>self.settings['max_tool_calls']: raise InferenceError('inference_tool_count_exhausted')
        self.tool_calls += len(calls)
        self.item_ids.update(item_ids); self.call_ids.update(call_ids)
        self.usage.append({key:usage[key] for key in ('input_tokens','output_tokens','total_tokens')})
        self.poisoned = False
        return output,calls


def run_trial(client, backend, workspace, harness, prompt, artifacts, destination, seconds):
    from .working_state import WorkingState
    from .sandbox import Limits
    deadline = min(client.deadline,time.monotonic()+seconds)
    state = WorkingState(workspace,deadline)
    history = [dict(role='developer',content='Read and follow task guidance under /inputs/harness. '
        'Work only in /work using run(argv). Complete the supplied task and write its required artifacts. '
        'Host credentials, network and other runs are unavailable; final claims do not supply a grade.'),
        dict(role='user',content=prompt)]
    commands = []
    while True:
        output,calls = client.respond(history,deadline)
        history.extend(output)
        if not calls:
            exported = state.export(artifacts,destination,deadline)
            return dict(status='completed',exit_code=0,timeout=False,stdout='',stderr='',inputs_unchanged=True,
                inference=client.report(),worker_commands=commands,artifact_workspace=str(exported))
        call_id,argv = calls[0]
        remaining = deadline-time.monotonic()
        if remaining <= 0: raise TimeoutError('executor deadline exhausted before command')
        run = backend.run(argv,workspace,{'harness':harness},'executor',Limits(seconds=remaining),working_state=state)
        attestation = run.get('attestation',{})
        storage = attestation.get('storage_policy') or {}
        image = attestation.get('runtime_image') or {}
        cgroup = attestation.get('cgroup_v2') or {}
        if (run.get('status') not in ('completed','failed') or type(run.get('exit_code')) is not int
                or (run['exit_code']==0) != (run['status']=='completed') or run.get('inputs_unchanged') is not True
                or storage.get('verified') is not True or storage.get('extracted') is None
                or image.get('verified_before') is not True or image.get('verified_after') is not True
                or cgroup.get('empty_after_cleanup') is not True or attestation.get('aggregate_disk_bound_verified') is not True):
            raise InferenceError('executor_containment_or_state_failure')
        commands.append({key:run[key] for key in ('status','exit_code','attestation')})
        history.append(dict(type='function_call_output',call_id=call_id,output=json.dumps(
            {key:run[key] for key in ('status','exit_code','stdout','stderr')},allow_nan=False)))
