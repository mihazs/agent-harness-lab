"""Owned Linux startup observations; parsed worker reports never issue authority."""
import array
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import select
import socket
import stat
import time
from .integrity import unique_object

PROFILE='ahl-linux-restricted-process-v1'
MARKER=b'ahl-boundary-pipe-v1'
MAX_BYTES=4096
NAMESPACES=('mnt','net','user','pid','ipc','uts')


def encoded(value):return json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode()


def pin(value):return hashlib.sha256(encoded(value)).hexdigest()


# Executed only by the exact trusted VERIFY argv in the monitor's exec chain.
# The only transferred descriptor is a read end; both worker descriptors close
# before VERIFY returns to exec the untrusted role. Role stdout is separate data.
VERIFY = r'''import array,hashlib,json,os,resource,socket,struct
def bounded(path,limit=65536):
 with open(path) as stream: value=stream.read(limit+1)
 if len(value)>limit: raise ValueError('boundary readback exceeds bound')
 return value
def hashed(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
contract=json.loads(sys.argv[3])
status=dict(line.split(':',1) for line in bounded('/proc/self/status').splitlines() if ':' in line)
mounts={}
inventory=bounded('/proc/self/mountinfo')
points={line.split()[4] for line in inventory.splitlines()}
for path in contract['mounts']:
 info=os.stat(path);volume=os.statvfs(path)
 mounts[path]=dict(identity=[info.st_dev,info.st_ino],readonly=bool(volume.f_flag & os.ST_RDONLY),
  mounted=path in points,capacity=volume.f_blocks*volume.f_frsize)
observed=dict(nonce=contract['nonce'],mounts=mounts,
 namespaces={name:hashed(os.readlink('/proc/self/ns/'+name)) for name in contract['namespaces']},
 uid=list(os.getresuid()),gid=list(os.getresgid()),
 uid_map=[list(map(int,line.split())) for line in bounded('/proc/self/uid_map',4096).splitlines()],
 gid_map=[list(map(int,line.split())) for line in bounded('/proc/self/gid_map',4096).splitlines()],
 capabilities={key:int(status[key].strip(),16) for key in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb')},
 no_new_privileges=int(status['NoNewPrivs']),seccomp=int(status['Seccomp']),
 environment_sha256=hashed(dict(os.environ)),
 network_devices=sorted(line.split(':',1)[0].strip() for line in bounded('/proc/net/dev',4096).splitlines() if ':' in line),
 limits={name:list(resource.getrlimit(getattr(resource,name))) for name in contract['rlimits']})
raw=json.dumps(observed,sort_keys=True,separators=(',',':')).encode()
if len(raw)>4096:raise ValueError('boundary observation exceeds bound')
reader,writer=os.pipe2(os.O_CLOEXEC)
try:
 if os.write(writer,struct.pack('!I',len(raw))+raw)!=len(raw)+4:raise ValueError('incomplete boundary observation')
 os.close(writer);writer=None
 channel=socket.socket(fileno=os.dup(1))
 try:
  marker=b'ahl-boundary-pipe-v1'
  if channel.sendmsg([marker],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[reader]))])!=len(marker):
   raise ValueError('incomplete boundary descriptor handoff')
 finally:channel.close()
finally:
 os.close(reader)
 if writer is not None:os.close(writer)
'''


def contract(backend,inputs,role,limits,deadline,storage):
    if not backend.permission_audit or limits is None:return None
    if os.geteuid()==0:raise ValueError('restricted protocol requires an unprivileged host controller')
    mounts={path:dict(readonly=True,mounted=True) for path in ('/','/dev','/inputs')}
    roots={'/':backend.image.root,**{'/inputs/'+name:Path(path) for name,path in inputs.items()}}
    for path,root in roots.items():
        info=root.stat();mounts[path]=dict(readonly=True,mounted=True,identity=[info.st_dev,info.st_ino])
    for path,key in (('/work','work'),('/tmp','tmp'),('/dev/shm','shm')):
        mounts[path]=dict(readonly=False,mounted=True,capacity=storage[key])
    environment=dict(PATH='/usr/bin:/bin',HOME='/home/worker',LANG='C.UTF-8',PWD='/work',AHL_SANDBOX_ROLE=role,PYTHONDONTWRITEBYTECODE='1')
    return dict(profile=PROFILE,nonce=os.urandom(16).hex(),role=role,image_sha256=backend.image.sha256,
        deadline=deadline,resources=asdict(limits),storage=storage,mounts=mounts,
        namespaces={name:pin(os.readlink('/proc/self/ns/'+name)) for name in NAMESPACES},
        # --dev requires bubblewrap's devpts setup namespace. Its final child
        # maps the controller's numeric UID/GID to 0 in that immediate parent;
        # that parent is itself unprivileged on the trusted host.
        uid=os.geteuid(),gid=os.getegid(),uid_map=[[os.geteuid(),0,1]],gid_map=[[os.getegid(),0,1]],environment_sha256=pin(environment),
        rlimits=dict(RLIMIT_CPU=[math.ceil(limits.seconds),math.ceil(limits.seconds)+1],
            RLIMIT_AS=[limits.memory_bytes]*2,RLIMIT_FSIZE=[limits.file_bytes]*2,RLIMIT_NOFILE=[64,64],RLIMIT_CORE=[0,0],RLIMIT_RTPRIO=[0,0]))


def receive(scratch):
    descriptors=[];payload=b''
    try:
        while len(payload)<len(MARKER):
            scratch.timeout()
            raw,controls,flags,_=scratch.receiver.recvmsg(len(MARKER)-len(payload),socket.CMSG_SPACE(8),socket.MSG_CMSG_CLOEXEC)
            invalid=False
            for level,kind,data in controls:
                if (level,kind)!=(socket.SOL_SOCKET,socket.SCM_RIGHTS):invalid=True;continue
                rights=array.array('i');rights.frombytes(data[:len(data)//rights.itemsize*rights.itemsize]);descriptors.extend(rights)
                if len(data)%rights.itemsize:raise ValueError('invalid boundary descriptor bytes')
            if invalid or flags & (socket.MSG_TRUNC|socket.MSG_CTRUNC) or not raw:raise ValueError('incomplete boundary handoff')
            payload+=raw
        if payload!=MARKER or len(descriptors)!=1:raise ValueError('one boundary read descriptor required')
        descriptor=descriptors[0];info=os.fstat(descriptor)
        if not stat.S_ISFIFO(info.st_mode) or info.st_uid!=os.geteuid():raise ValueError('boundary observation requires an owned pipe')
        import fcntl
        if fcntl.fcntl(descriptor,fcntl.F_GETFL)&os.O_ACCMODE!=os.O_RDONLY:raise ValueError('boundary pipe must be read-only')
        os.set_blocking(descriptor,False);raw=b''
        while True:
            remaining=scratch.deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('boundary observation deadline exhausted')
            if not select.select([descriptor],[],[],min(.1,remaining))[0]:continue
            part=os.read(descriptor,MAX_BYTES+5-len(raw))
            if not part:break
            raw+=part
            if len(raw)>MAX_BYTES+4:raise ValueError('boundary observation exceeds bound')
        if len(raw)<4 or int.from_bytes(raw[:4],'big')!=len(raw)-4:raise ValueError('invalid boundary observation frame')
        value=json.loads(raw[4:],object_pairs_hook=unique_object)
        if not isinstance(value,dict):raise ValueError('boundary observation must be an object')
        return value
    finally:
        for descriptor in descriptors:os.close(descriptor)


def qualify(expected,observed,work_identity,seeded_bytes,group,permission,stage):
    unknown=dict(profile=PROFILE,status='unknown',reason='capture_unavailable')
    if expected is None or observed is None or permission is None:return unknown
    try:
        required={'nonce','mounts','namespaces','uid','gid','uid_map','gid_map','capabilities','no_new_privileges','seccomp','environment_sha256','network_devices','limits'}
        checks=dict(schema=set(observed)==required,
            live=observed['nonce']==expected['nonce'] and time.monotonic()<expected['deadline'],
            mounts=set(observed['mounts'])==set(expected['mounts']) and all(
                all(observed['mounts'][path][key]==value for key,value in rules.items()) for path,rules in expected['mounts'].items()),
            namespaces=set(observed['namespaces'])==set(expected['namespaces']) and all(
                isinstance(observed['namespaces'][name],str) and len(observed['namespaces'][name])==64 and observed['namespaces'][name]!=host
                for name,host in expected['namespaces'].items()),
            user_mapping=observed['uid']==[expected['uid']]*3 and observed['gid']==[expected['gid']]*3
                and observed['uid_map']==expected['uid_map'] and observed['gid_map']==expected['gid_map'],
            privileges=observed['capabilities']==dict.fromkeys(('CapInh','CapPrm','CapEff','CapBnd','CapAmb'),0)
                and observed['no_new_privileges']==1 and observed['seccomp']==2,
            network=observed['network_devices']==['lo'],environment=observed['environment_sha256']==expected['environment_sha256'],
            rlimits=observed['limits']==expected['rlimits'],
            permission=permission['status']=='complete' and permission['role_coverage']=='complete'
                and (stage=='capability_probe' or permission['qualification']=='qualified'),
            workspace=observed['mounts']['/work']['identity']==work_identity)
        controls=group.report['controls']
        checks['cgroup']=(controls==group.controls and int(controls['memory.max'])<=min(expected['resources']['memory_bytes'],expected['storage']['total']-seeded_bytes)
            and controls['pids.max']==str(expected['resources']['processes']) and controls['memory.swap.max']=='0' and controls['cpu.max']=='100000 100000')
        reason=next((key for key,value in checks.items() if not value),None)
        return dict(profile=PROFILE,status='qualified' if reason is None else 'unknown',reason=reason,contract_sha256=pin(expected),observation_sha256=pin(observed))
    except (KeyError,TypeError,ValueError,OSError):return unknown
