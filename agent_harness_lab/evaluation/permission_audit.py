"""Opt-in Linux permission-result observation. This file also runs as a trusted child."""
import ctypes
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import resource
import secrets
import selectors
import signal
import stat
import struct
import sys
import time
from weakref import WeakKeyDictionary, ref

PROFILE = 'permission-results-v1'
ARCH = 0xc000003e
MAX_FRAME = 131072
MAX_RESULT = 65536
MAX_EVENTS = 128
MAX_TASKS = 256
MAX_STOPS = 200000
MAX_SETUP_GAPS = 128
MAX_EXECUTABLE_BYTES = 128*1024*1024
DENIALS = (errno.EACCES, errno.EPERM, errno.EROFS)
_CUSTODY = WeakKeyDictionary()
_COMPLETIONS = WeakKeyDictionary()


def canonical_argv(argv):
    effective=list(argv)
    if effective and effective[0]=='/usr/bin/python3':effective.insert(1,'-B')
    return effective


def _fd_identity(descriptor):
    info=os.fstat(descriptor)
    return info.st_dev,info.st_ino,info.st_mode


def _executable_digest(pid,deadline):
    """Hash the kernel-selected executable while the traced process is stopped at exec."""
    try:
        with open(f'/proc/{pid}/exe','rb',buffering=0) as stream:
            before=os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size>MAX_EXECUTABLE_BYTES: return None
            digest=hashlib.sha256();total=0
            while True:
                if time.monotonic()>=deadline:return None
                chunk=stream.read(256*1024)
                if not chunk:break
                total+=len(chunk)
                if total>MAX_EXECUTABLE_BYTES:return None
                digest.update(chunk)
            after=os.fstat(stream.fileno())
            fields=('st_dev','st_ino','st_mode','st_size','st_mtime_ns','st_ctime_ns')
            if total!=before.st_size or any(getattr(before,key)!=getattr(after,key) for key in fields):return None
            return digest.hexdigest()
    except (OSError,ValueError):return None


class _Completion: pass


def _take_completion(completion,ticket,process):
    state=_COMPLETIONS.pop(completion,None) if type(completion) is _Completion else None
    return json.loads(state[2]) if state is not None and state[0] is ticket and state[1] is process else None


def _consume_completion(completion,ticket,process):
    return _take_completion(completion,ticket,process) is not None


def encoded(value): return json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode()


def hashed(value): return hashlib.sha256(encoded(value)).hexdigest()


def policy():
    return dict(profile=PROFILE,architecture='linux-x86_64',permission_errnos=list(DENIALS),
        clone3='blocked_ENOSYS_compatible_fallback',clone_untraced='blocked_EPERM',unsupported_abi='unqualified',
        genuine_sigtrap='forwarded',job_control='unsupported_unqualified',python_bytecode_cache='canonical_top_level_python_B_and_inherited_env',
        semantic_intent='unknown',enoent='not_a_permission_denial',expected_role_exceptions=[],
        completion_scope='role_permission_results_only',setup_coverage='unverified',
        setup_info_loss='phase1_nonroot_single_tgid_wait4_after_role_terminal_only',
        max_setup_gaps=MAX_SETUP_GAPS,max_events=MAX_EVENTS,max_tasks=MAX_TASKS,max_stops=MAX_STOPS)


def supported():
    return sys.platform=='linux' and platform.machine()=='x86_64' and sys.byteorder=='little'


def qualification(reports):
    if any(r.get('qualification')=='violated' for r in reports): return 'violated'
    return 'qualified' if reports and all(r.get('qualification')=='qualified' for r in reports) else 'unknown'



def validate_frame(frame):
    if (not isinstance(frame,dict) or set(frame)!={'schema_version','profile','nonce','role','command','execs','cgroup','limits','deadline','controller_pid'}
            or type(frame['controller_pid']) is not int or frame['controller_pid']<=1
            or type(frame['schema_version']) is not int or frame['schema_version']!=1 or frame['profile']!=PROFILE
            or frame['role'] not in ('proposer','evaluator_author','executor','grader')
            or not isinstance(frame['nonce'],str) or len(frame['nonce'])!=32 or any(c not in '0123456789abcdef' for c in frame['nonce'])
            or not isinstance(frame['execs'],list) or len(frame['execs'])!=5
            or any(not isinstance(h,str) or len(h)!=64 or any(c not in '0123456789abcdef' for c in h) for h in frame['execs'])
            or not isinstance(frame['command'],list) or not 1<=len(frame['command'])<=256
            or any(not isinstance(a,str) or '\0' in a for a in frame['command']) or not frame['command'][0].startswith('/')
            or not isinstance(frame['cgroup'],str) or not frame['cgroup'].startswith('/') or len(frame['cgroup'])>4096 or '\0' in frame['cgroup']
            or type(frame['deadline']) not in (int,float) or not math.isfinite(frame['deadline'])
            or not isinstance(frame['limits'],dict) or set(frame['limits'])!={'seconds','memory_bytes','processes','file_bytes','output_bytes'}):
        raise ValueError('invalid observer launch')
    limits=frame['limits']
    if (type(limits['seconds']) not in (int,float) or not math.isfinite(limits['seconds']) or limits['seconds']<=0
            or any(type(limits[k]) is not int or limits[k]<=0 for k in ('memory_bytes','processes','file_bytes','output_bytes'))
            or hashed(frame['command'])!=frame['execs'][0]):raise ValueError('invalid observer limits/exec binding')


class Observation:
    """A fresh private protocol pipe; no file/result-import route establishes a capture."""
    def __init__(self,command,execs,group,limits,deadline,role,*,source_ticket=None):
        if not supported(): raise ValueError('permission audit requires Linux x86_64')
        self.reader=self.writer=None
        self.nonce=secrets.token_hex(16);self.data=bytearray();self.eof=False
        self.frame=dict(schema_version=1,profile=PROFILE,nonce=self.nonce,role=role,
            command=command,execs=[hashed(list(argv)) for argv in execs],
            cgroup=str(Path(group.root).absolute()/group.name),limits=dict(vars(limits)),deadline=deadline,controller_pid=os.getpid())
        validate_frame(self.frame)
        self.raw=encoded(self.frame)
        if len(self.raw)>MAX_FRAME:
            self.close();raise ValueError('permission audit launch frame exceeds bound')
        from .sandbox import keyring_launcher
        launcher=keyring_launcher()
        self.reader,self.writer=os.pipe2(os.O_CLOEXEC|os.O_NONBLOCK)
        # The observer starts in a new empty session keyring, which the traced bubblewrap and role inherit.
        self.command=[*launcher,sys.executable,'-I','-S',str(Path(__file__).resolve()),str(self.writer),str(os.getpid())]
        if source_ticket is not None:
            _CUSTODY[self]=dict(ticket=ref(source_ticket),raw=self.raw,launch=tuple(self.command),
                reader=self.reader,writer=self.writer,reader_id=_fd_identity(self.reader),writer_id=_fd_identity(self.writer),
                process=None,sent=False,written=bytearray(),wire=bytearray(),eof=False,lost=False,closed=False)

    def start(self,process):
        state=_CUSTODY.get(self)
        if state is not None:
            import subprocess
            state['lost']=(state['lost'] or state['process'] is not None or type(process) is not subprocess.Popen
                or tuple(process.args)!=state['launch'] or self.raw!=state['raw']
                or encoded(self.frame)!=state['raw'] or self.reader!=state['reader'] or self.writer!=state['writer']
                or _fd_identity(self.reader)!=state['reader_id'] or _fd_identity(self.writer)!=state['writer_id'])
            state['process']=process
        os.close(self.writer);self.writer=None
        descriptor=process.stdin.fileno();os.set_blocking(descriptor,False)
        if state is not None:state['stdin_id']=_fd_identity(descriptor)
        pending=struct.pack('!I',len(self.raw))+self.raw
        with selectors.DefaultSelector() as poll:
            poll.register(descriptor,selectors.EVENT_WRITE)
            while pending:
                remaining=self.frame['deadline']-time.monotonic()
                if remaining<=0: raise TimeoutError('permission audit startup deadline exhausted')
                if poll.select(min(.05,remaining)):
                    try:
                        count=os.write(descriptor,pending)
                        if state is not None:state['written'].extend(pending[:count])
                        pending=pending[count:]
                    except BlockingIOError:pass
        if state is not None and _fd_identity(descriptor)!=state['stdin_id']:state['lost']=True
        process.stdin.close()
        if state is not None:
            state['sent']=state['written']==struct.pack('!I',len(state['raw']))+state['raw']

    def receive(self):
        raw=os.read(self.reader,min(65536,MAX_RESULT+1-len(self.data)))
        state=_CUSTODY.get(self)
        if state is not None:
            if self.reader!=state['reader'] or _fd_identity(self.reader)!=state['reader_id'] or state['eof']:
                state['lost']=True
            state['wire'].extend(raw)
            if not raw:state['eof']=True
            if len(state['wire'])>MAX_RESULT:state['lost']=True
        if not raw:self.eof=True
        self.data.extend(raw)
        if len(self.data)>MAX_RESULT:raise ValueError('permission audit result exceeds bound')

    def result(self):
        return _result(self.data,self.eof,self.nonce,self.frame['role'])

    def _owns(self,ticket,process,command,execs,group,role):
        state=_CUSTODY.get(self)
        if state is None:return False
        frame=json.loads(state['raw'])
        return (not state['lost'] and state['sent'] and state['ticket']() is ticket and state['process'] is process
            and frame['command']==command and frame['execs']==[hashed(list(argv)) for argv in execs]
            and frame['role']==role and frame['cgroup']==str(Path(group.root).absolute()/group.name))

    def _completion(self,ticket,process,command,role):
        state=_CUSTODY.pop(self,None)
        if state is None:return None
        frame=json.loads(state['raw'])
        report=_result(state['wire'],state['eof'],frame['nonce'],frame['role'])
        if (state['lost'] or not state['closed'] or self.raw!=state['raw'] or encoded(self.frame)!=state['raw']
                or tuple(self.command)!=state['launch'] or self.data!=state['wire'] or self.eof!=state['eof']
                or not state['sent'] or state['ticket']() is not ticket
                or state['process'] is not process or process.poll() is None
                or tuple(process.args)!=state['launch'] or frame['command']!=command or frame['role']!=role
                or time.monotonic()>=frame['deadline'] or report['status']!='complete'):
            return None
        completion=_Completion();_COMPLETIONS[completion]=(ticket,process,encoded(report))
        return completion

    def close(self):
        state=_CUSTODY.get(self)
        if state is not None and not state['closed']:
            if self.reader!=state['reader'] or _fd_identity(self.reader)!=state['reader_id']:state['lost']=True
            state['closed']=True
        for name in ('reader','writer'):
            descriptor=getattr(self,name,None)
            if descriptor is not None:os.close(descriptor);setattr(self,name,None)


def _result(data,eof,nonce,role):
    try:
        value=json.loads(data)
        if (not eof or not isinstance(value,dict) or value.get('nonce')!=nonce
                or value.get('profile')!=PROFILE or value.get('role')!=role
                or value.get('policy_sha256')!=hashed(policy()) or not isinstance(value.get('events'),list)
                or len(value['events'])>MAX_EVENTS or not isinstance(value.get('setup_gaps'),list)
                or len(value['setup_gaps'])>MAX_SETUP_GAPS):raise ValueError('invalid permission audit channel')
        events=value['events']
        for index,event in enumerate(events):
            if (set(event)!={'ordinal','task','stage','arch','syscall','errno','category'}
                    or type(event['ordinal']) is not int or event['ordinal']!=index+1
                    or type(event['arch']) is not int or type(event['errno']) is not int
                    or type(event['task']) is not int or not 0<=event['task']<MAX_TASKS
                    or event['stage'] not in ('setup','role') or event['arch']!=ARCH
                    or type(event['syscall']) is not int or not 0<=event['syscall']<0x40000000
                    or event['category'] not in ('permission_result','blocked_creation')
                    or (event['errno'] not in DENIALS if event['category']=='permission_result' else event['errno']!=errno.ENOSYS)):
                raise ValueError('invalid permission audit event')
        if (set(value)-{'worker_executable_sha256'}!={'schema_version','profile','nonce','role','policy_sha256','status','reason','role_started','all_reaped','syscall_stops','tasks','events','setup_gaps'}
                or type(value['schema_version']) is not int or value['schema_version']!=1
                or type(value['role_started']) is not bool or type(value['all_reaped']) is not bool
                or type(value['syscall_stops']) is not int or not 0<=value['syscall_stops']<=MAX_STOPS
                or type(value['tasks']) is not int or not 1<=value['tasks']<=MAX_TASKS
                or value['status'] not in ('complete','unknown')
                or value['reason'] not in ('complete','unsupported','trace_limit','trace_failure','deadline','signal')
                or any(e['task']>=value['tasks'] for e in events)):
            raise ValueError('invalid permission audit completion')
        executable=value.get('worker_executable_sha256')
        if executable is not None and (not isinstance(executable,str) or len(executable)!=64
                or any(c not in '0123456789abcdef' for c in executable)):
            raise ValueError('invalid worker executable observation')
        gaps=value['setup_gaps'];seen=set()
        for index,gap in enumerate(gaps):
            if (not isinstance(gap,dict) or set(gap)!={'ordinal','task','phase','last_syscall','terminal_status'}
                    or type(gap['ordinal']) is not int or gap['ordinal']!=index+1
                    or type(gap['task']) is not int or not 0<gap['task']<value['tasks'] or gap['task'] in seen
                    or type(gap['phase']) is not int or gap['phase']!=1
                    or type(gap['last_syscall']) is not int or gap['last_syscall']!=61
                    or (gap['terminal_status'] is not None and (type(gap['terminal_status']) is not int
                        or not -64<=gap['terminal_status']<=255))):raise ValueError('invalid setup gap')
            seen.add(gap['task'])
        complete=(value['status']=='complete' and value['reason']=='complete' and value['role_started']
            and value['all_reaped'] and all(gap['terminal_status'] is not None for gap in gaps))
        violated=any(e['stage']=='role' and e['category']=='permission_result' for e in events)
        return dict(schema_version=1,profile=PROFILE,policy_sha256=hashed(policy()),
            status='complete' if complete else 'unknown',qualification='violated' if violated else 'qualified' if complete else 'unknown',
            reason=value.get('reason') if value.get('reason') in ('complete','unsupported','trace_limit','trace_failure','deadline','signal') else 'invalid_channel',
            events=events,setup_gaps=gaps,setup_coverage='incomplete' if gaps else 'unverified',
            role_coverage='complete' if complete else 'unknown',
            syscall_stops=value.get('syscall_stops'),tasks=value.get('tasks'),
            role_started=value.get('role_started') is True,all_tracees_reaped=value.get('all_reaped') is True,
            observation_scope='declared_role_permission_errno_results_only',semantic_intent='unknown',
            hidden_access_attempts='unverified',worker_executable_sha256=executable if complete else None,
            publication_allowed=False,automatic_promotion=False)
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):
        return dict(schema_version=1,profile=PROFILE,status='unknown',qualification='unknown',reason='invalid_channel',
            events=[],setup_gaps=[],setup_coverage='unverified',role_coverage='unknown',
            policy_sha256=hashed(policy()),publication_allowed=False,automatic_promotion=False)



class Entry(ctypes.Structure):
    _fields_=[('nr',ctypes.c_ulonglong),('args',ctypes.c_ulonglong*6)]


class Exit(ctypes.Structure):
    _fields_=[('rval',ctypes.c_longlong),('is_error',ctypes.c_ubyte)]


class Seccomp(ctypes.Structure):
    _fields_=[('nr',ctypes.c_ulonglong),('args',ctypes.c_ulonglong*6),('ret_data',ctypes.c_uint)]


class Payload(ctypes.Union):
    _fields_=[('entry',Entry),('exit',Exit),('seccomp',Seccomp)]


class SyscallInfo(ctypes.Structure):
    _fields_=[('op',ctypes.c_ubyte),('reserved',ctypes.c_ubyte),('flags',ctypes.c_ushort),
        ('arch',ctypes.c_uint),('ip',ctypes.c_ulonglong),('sp',ctypes.c_ulonglong),('data',Payload)]


class Trace:
    def __init__(self,frame):
        self.frame=frame;self.tasks={};self.groups={};self.entries={};self.events=[]
        self.setup_gaps=[]
        self.total=self.stops=0;self.role_started=False;self.reason='complete';self.root_exit=None
        self.worker_executable_sha256=None
        self.libc=ctypes.CDLL(None,use_errno=True);self.libc.ptrace.restype=ctypes.c_long
        self.libc.ptrace.argtypes=[ctypes.c_ulong,ctypes.c_ulong,ctypes.c_void_p,ctypes.c_void_p]

    def call(self,op,pid,addr=0,data=0):
        ctypes.set_errno(0)
        value=self.libc.ptrace(op,pid,ctypes.c_void_p(addr),ctypes.c_void_p(data) if isinstance(data,int) else data)
        if value==-1 and ctypes.get_errno():raise OSError(ctypes.get_errno(),'permission observer unavailable')
        return value

    def word(self,pid,address):return (self.call(2,pid,address)&0xffffffffffffffff).to_bytes(8,'little')

    def argv(self,pid,address):
        values=[];total=0
        for index in range(256):
            pointer=int.from_bytes(self.word(pid,address+index*8),'little')
            if not pointer:return hashed(values)
            raw=bytearray();offset=pointer%8;cursor=pointer-offset
            while True:
                chunk=self.word(pid,cursor)[offset:];offset=0;cursor+=8
                end=chunk.find(b'\0');raw.extend(chunk if end<0 else chunk[:end])
                if total+len(raw)>MAX_FRAME:raise ValueError('exec observation exceeds bound')
                if end>=0:break
            values.append(raw.decode('utf-8'));total+=len(raw)
        raise ValueError('exec argument count exceeds bound')

    def register(self,pid,phase):
        if self.total>=MAX_TASKS:raise OverflowError('trace_limit')
        raw=Path('/proc/'+str(pid)+'/status').read_bytes()
        if len(raw)>16384:raise ValueError('process status exceeds bound')
        tgid=int(next(line.split()[1] for line in raw.splitlines() if line.startswith(b'Tgid:')))
        self.tasks[pid]=dict(phase=phase,id=self.total,tgid=tgid,options=False)
        self.groups.setdefault(tgid,set()).add(pid);self.total+=1

    def retire(self,pid):
        task=self.tasks.pop(pid);self.entries.pop(pid,None)
        members=self.groups[task['tgid']];members.discard(pid)
        if not members:self.groups.pop(task['tgid'])

    def event(self,pid,nr,err,category):
        if len(self.events)>=MAX_EVENTS:raise OverflowError('trace_limit')
        task=self.tasks[pid]
        self.events.append(dict(ordinal=len(self.events)+1,task=task['id'],stage='role' if task['phase']==len(self.frame['execs']) else 'setup',
            arch=ARCH,syscall=nr,errno=err,category=category))

    def quarantine_setup(self,pid):
        task=self.tasks[pid]
        if (task['phase']!=1 or pid==self.root or not self.role_started or task['tgid']!=pid
                or len(self.groups.get(pid,()))!=1 or self.entries.get(pid,{}).get('nr')!=61):return False
        if len(self.setup_gaps)>=MAX_SETUP_GAPS:raise OverflowError('trace_limit')
        gap=dict(ordinal=len(self.setup_gaps)+1,task=task['id'],phase=1,last_syscall=61,terminal_status=None)
        self.setup_gaps.append(gap);task['setup_gap']=gap
        return True

    def stop(self,pid,status):
        task=self.tasks[pid]
        if 'setup_gap' in task:raise ValueError('quarantined setup progressed')
        initial=not task['options']
        if initial:
            self.call(0x4200,pid,data=1|2|4|8|16|0x100000);task['options']=True
        sig=os.WSTOPSIG(status);event=status>>16
        if event in (1,2,3):
            child=ctypes.c_ulong();self.call(0x4201,pid,data=ctypes.byref(child));self.register(child.value,task['phase'])
        elif event==4:
            former=ctypes.c_ulong();self.call(0x4201,pid,data=ctypes.byref(former))
            if former.value!=pid:
                if former.value not in self.tasks:raise ValueError('unknown exec lineage')
                if 'setup_gap' in self.tasks[former.value]:raise ValueError('quarantined setup remapped')
                task['phase']=self.tasks[former.value]['phase'];self.entries[pid]=self.entries.get(former.value,{})
                self.retire(former.value)
            if task['phase']<len(self.frame['execs']):
                if self.entries.get(pid,{}).get('exec')!=self.frame['execs'][task['phase']]:raise ValueError('unexpected setup exec')
                task['phase']+=1
                if task['phase']==len(self.frame['execs']):
                    self.worker_executable_sha256=_executable_digest(pid,self.frame['deadline'])
                    self.role_started=True
        elif sig==(signal.SIGTRAP|0x80):
            if self.stops>=MAX_STOPS:raise OverflowError('trace_limit')
            self.stops+=1
            info=SyscallInfo()
            try:size=self.call(0x420e,pid,ctypes.sizeof(info),ctypes.byref(info))
            except OSError as exc:
                if exc.errno==errno.ESRCH and self.quarantine_setup(pid):return
                raise
            if info.arch!=ARCH or info.flags or info.reserved:raise ValueError('unsupported syscall architecture')
            if info.op==1:
                if size<80 or info.data.entry.nr>=0x40000000:raise ValueError('unsupported syscall info')
                entry=dict(nr=info.data.entry.nr)
                if task['phase']<len(self.frame['execs']) and entry['nr'] in (59,322):
                    entry['exec']=self.argv(pid,info.data.entry.args[1 if entry['nr']==59 else 2])
                self.entries[pid]=entry
            elif info.op==2:
                if size<33:raise ValueError('truncated syscall exit')
                nr=self.entries.get(pid,{}).get('nr');err=-info.data.exit.rval
                if info.data.exit.is_error and err in DENIALS:
                    if nr is None:raise ValueError('unpaired denial exit')
                    self.event(pid,nr,err,'permission_result')
                elif nr==435 and info.data.exit.is_error and err==errno.ENOSYS:self.event(pid,nr,err,'blocked_creation')
            else:raise ValueError('unknown syscall stop')
        elif sig==signal.SIGSYS:raise ValueError('unsupported seccomp termination')
        elif sig in (signal.SIGSTOP,signal.SIGTSTP,signal.SIGTTIN,signal.SIGTTOU) and not initial:
            raise ValueError('unsupported job-control stop')
        synthetic=bool(event or sig==(signal.SIGTRAP|0x80) or initial and sig==signal.SIGSTOP)
        try:self.call(24,pid,data=0 if synthetic else sig)
        except OSError as exc:
            if exc.errno!=errno.ESRCH:raise
            # Exec in another owned thread can kill/rename this stopped TID.
            # Keep it owned until an actual terminal/exec wait proves its fate.
            task['resume_pending']=True

    def run(self):
        if os.getppid()!=self.frame['controller_pid']:raise ValueError('controller parent changed')
        parent=os.getpid()
        child=os.fork()
        if child==0:
            try:
                if self.libc.prctl(1,signal.SIGKILL,0,0,0)!=0 or os.getppid()!=parent:os._exit(125)
                os.close(int(sys.argv[1]));os.close(0)
                fd=os.open('/dev/null',os.O_RDONLY);os.dup2(fd,0)
                if fd:os.close(fd)
                limits=self.frame['limits']
                descriptor=os.open(self.frame['cgroup']+'/cgroup.procs',os.O_WRONLY|os.O_NOFOLLOW)
                try:os.write(descriptor,b'0\n')
                finally:os.close(descriptor)
                os.sched_setscheduler(0,os.SCHED_OTHER,os.sched_param(0));resource.setrlimit(resource.RLIMIT_RTPRIO,(0,0))
                for key,value in ((resource.RLIMIT_AS,limits['memory_bytes']),(resource.RLIMIT_FSIZE,limits['file_bytes']),
                        (resource.RLIMIT_NOFILE,64),(resource.RLIMIT_CORE,0)):
                    resource.setrlimit(key,(value,value))
                cpu=math.ceil(limits['seconds']);resource.setrlimit(resource.RLIMIT_CPU,(cpu,cpu+1))
                self.call(0,0);os.kill(os.getpid(),signal.SIGSTOP)
                os.execv(self.frame['command'][0],self.frame['command'])
            except BaseException:os._exit(125)
        self.root=child
        try:
            self.register(child,0)
            while self.tasks:
                if time.monotonic()>=self.frame['deadline']:raise TimeoutError('deadline')
                if self.stops>=MAX_STOPS:raise OverflowError('trace_limit')
                progressed=False
                for watched in list(self.tasks):
                    if watched not in self.tasks:continue
                    try:pid,status=os.waitpid(watched,0x40000000|os.WNOHANG)
                    except ChildProcessError:
                        task=self.tasks[watched]
                        if watched!=task['tgid'] and task['tgid'] in self.tasks:
                            continue  # Nonleader exec is reported at the still-owned TGID.
                        raise ValueError('lost tracee') from None
                    if not pid:continue
                    progressed=True
                    if pid not in self.tasks:raise ValueError('unknown tracee identity')
                    if os.WIFEXITED(status) or os.WIFSIGNALED(status):
                        if 'setup_gap' in self.tasks[pid]:
                            self.tasks[pid]['setup_gap']['terminal_status']=os.waitstatus_to_exitcode(status)
                        if pid==child:self.root_exit=os.waitstatus_to_exitcode(status)
                        if os.WIFSIGNALED(status) and self.tasks[pid]['phase']==len(self.frame['execs']):self.reason='signal'
                        self.retire(pid)
                    elif os.WIFSTOPPED(status):self.stop(pid,status)
                    else:raise ValueError('unknown trace wait state')
                if not progressed:time.sleep(.0001)
        except TimeoutError:self.reason='deadline'
        except OverflowError:self.reason='trace_limit'
        except (OSError,ValueError,KeyError,UnicodeError):self.reason='trace_failure'
        finally:
            if child not in self.tasks and self.root_exit is None:
                try:os.kill(child,signal.SIGKILL)
                except ProcessLookupError:pass
                try:os.waitpid(child,0x40000000)
                except ChildProcessError:pass
            for pid in self.tasks:
                try:os.kill(pid,signal.SIGKILL)
                except ProcessLookupError:pass
            until=time.monotonic()+1
            while self.tasks and time.monotonic()<until:
                for pid in list(self.tasks):
                    try:found,status=os.waitpid(pid,0x40000000|os.WNOHANG)
                    except ChildProcessError:self.retire(pid);continue
                    if found and (os.WIFEXITED(status) or os.WIFSIGNALED(status)):self.retire(pid)
                    elif found:
                        try:self.call(7,pid,data=signal.SIGKILL)
                        except OSError:pass
                if self.tasks:time.sleep(.001)
        return dict(schema_version=1,profile=PROFILE,nonce=self.frame['nonce'],role=self.frame['role'],
            policy_sha256=hashed(policy()),status='complete' if self.reason=='complete' and not self.tasks and self.role_started else 'unknown',
            reason=self.reason,role_started=self.role_started,all_reaped=not self.tasks,
            syscall_stops=self.stops,tasks=self.total,events=self.events,setup_gaps=self.setup_gaps,
            worker_executable_sha256=self.worker_executable_sha256)


def main():
    descriptor=int(sys.argv[1]);expected_parent=int(sys.argv[2]);frame=None
    try:
        libc=ctypes.CDLL(None)
        if libc.prctl(1,signal.SIGKILL,0,0,0)!=0 or expected_parent<=1 or os.getppid()!=expected_parent:
            raise ValueError('parent death policy unavailable')
        header=sys.stdin.buffer.read(4)
        if len(header)!=4:raise ValueError('missing frame')
        size=struct.unpack('!I',header)[0]
        if not 0<size<=MAX_FRAME:raise ValueError('frame bound exceeded')
        raw=sys.stdin.buffer.read(size)
        if len(raw)!=size:raise ValueError('incomplete frame')
        frame=json.loads(raw)
        validate_frame(frame)
        if frame['controller_pid']!=expected_parent:raise ValueError('controller identity mismatch')
        if not supported() or descriptor<=2 or not stat.S_ISFIFO(os.fstat(descriptor).st_mode):raise ValueError('unsupported observer launch')
        trace=Trace(frame);result=trace.run();raw=encoded(result)
        if len(raw)>MAX_RESULT:raise ValueError('result bound exceeded')
        while raw:
            if time.monotonic()>=frame['deadline']:raise TimeoutError('deadline')
            try:raw=raw[os.write(descriptor,raw):]
            except BlockingIOError:time.sleep(.001)
        os.close(descriptor)
        return trace.root_exit if trace.root_exit is not None and trace.root_exit>=0 else 125
    except BaseException:
        try:os.close(descriptor)
        except OSError:pass
        return 125


if __name__=='__main__':os._exit(main())
