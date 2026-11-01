"""Private quota-backed workspaces, with bounded descriptor handoff and extraction."""
import array
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import struct
import tempfile
import time
from .integrity import contained, digest, read_regular, relative, tree
from .seccomp import ordinary_filesystems, sealed_filter

MAGIC = b'ahl-tmpfs-v1'
READY = b'ahl-storage-verified-v1'
MAX_FRAME = 65536

BOOTSTRAP = '''import array,json,os,socket,struct
channel=socket.socket(fileno=1)
descriptor=os.open('/work',os.O_RDONLY|os.O_DIRECTORY)
channel.sendmsg([b'ahl-tmpfs-v1'],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[descriptor]))])
def receive(size):
 data=b''
 while len(data)<size:
  part=channel.recv(size-len(data))
  if not part: raise ValueError('incomplete startup frame')
  data+=part
 return data
size=struct.unpack('!I',receive(4))[0]
if not 0<size<=65536: raise ValueError('startup frame bound exceeded')
command=json.loads(receive(size))
if not isinstance(command,list) or not command or command[0]!='/usr/bin/bwrap' or not all(isinstance(a,str) and '\\0' not in a for a in command):
 raise ValueError('invalid worker startup')
os.close(descriptor)
os.set_inheritable(1,True)
os.execv(command[0],command)
'''

VERIFY = '''import json,os,sys
policy=json.loads(sys.argv[1])
for path,key in (('/work','work'),('/tmp','tmp'),('/dev/shm','shm')):
 info=os.statvfs(path)
 assert info.f_blocks*info.f_frsize == policy[key]
 assert not info.f_flag & os.ST_RDONLY
for path in ('/','/dev','/inputs'):
 assert os.statvfs(path).f_flag & os.ST_RDONLY
os.write(1,b'ahl-storage-verified-v1')
command=json.loads(sys.argv[2])
os.execvp(command[0],command)
'''


def policy(total):
    page = os.sysconf('SC_PAGE_SIZE')
    if type(total) is not int or total < 4*page or total % (4*page):
        raise ValueError('disk bytes must be a positive multiple of four memory pages')
    return dict(total=total, work=total//2, tmp=total//4, shm=total//4)


def directory(root, parts, *, create=False):
    current = os.dup(root)
    try:
        for part in parts:
            if create:
                try: os.mkdir(part, 0o700, dir_fd=current)
                except FileExistsError: pass
            nested = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current); current = nested
        return current
    except BaseException:
        os.close(current)
        raise


def names(values):
    if (not isinstance(values, (tuple, list)) or len(values) > 100
            or not all(isinstance(v, str) for v in values) or len(set(values)) != len(values)):
        raise ValueError('invalid storage artifact allowlist')
    for name in values:
        relative(name)
        if len(name) > 4096 or len(name.split('/')) > 64:
            raise ValueError('artifact path bound exceeded')
    return tuple(values)


class PrivateWorkspace:
    def __init__(self, backend, workspace, inputs, role, argv, total, deadline, outputs):
        runtime = [backend.image.root] if backend.image else [Path(p) for p in ('/usr', '/bin', '/lib', '/lib64') if Path(p).exists()]
        ordinary_filesystems([workspace, *inputs.values(), *runtime])
        self.backend, self.workspace = backend, Path(workspace)
        self.deadline, self.outputs = deadline, names(outputs)
        if role == 'grader' and self.outputs: raise ValueError('graders cannot export storage artifacts')
        self.policy, self.fd, self.verified = policy(total), None, False
        self.seeded_bytes = None
        self.filter_fd = self.filter_policy = None
        self.receiver = self.sender = None
        self.command = self.layout(backend.command(['/usr/bin/python3', '-I', '-c', BOOTSTRAP], workspace, inputs, role), bootstrap=True)
        inner_argv = ['/usr/bin/python3', '-I', '-c', VERIFY, json.dumps(self.policy), json.dumps(argv)]
        inner = backend.command(inner_argv, workspace, inputs, role)
        inner[0] = '/usr/bin/bwrap'
        for index, value in enumerate(inner):
            if value == '--ro-bind':
                destination = inner[index+2]
                if destination == '/' or destination.startswith('/inputs/'):
                    inner[index+1] = destination
        index = inner.index('--bind'); inner[index+1] = '/work'
        self.frame = json.dumps(self.layout(inner, bootstrap=False), allow_nan=False).encode()
        if len(self.frame) > MAX_FRAME:
            raise ValueError('worker startup frame bound exceeded')

    def layout(self, command, *, bootstrap):
        command = list(command)
        if bootstrap:
            index = command.index('--bind')
            command[index:index+3] = ['--perms', '0700', '--size', str(self.policy['work']), '--tmpfs', '/work']
        index = next(i for i, flag in enumerate(command) if flag == '--tmpfs' and command[i+1] == '/tmp')
        command[index:index] = ['--size', str(self.policy['tmp'])]
        index = command.index('--dev')+2
        command[index:index] = ['--perms', '0700', '--size', str(self.policy['shm']), '--tmpfs', '/dev/shm']
        index = command.index('--chdir')
        command[index:index] = ['--remount-ro', '/dev']
        return command

    def __enter__(self):
        try:
            self.filter_fd, self.filter_policy = sealed_filter()
            self.receiver, self.sender = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
            self.command[1:1] = ['--seccomp', str(self.filter_fd)]
            return self
        except BaseException:
            self.__exit__()
            raise

    def timeout(self):
        remaining = self.deadline-time.monotonic()
        if remaining <= 0: raise TimeoutError('storage startup budget exhausted')
        self.receiver.settimeout(remaining)

    def start(self, group):
        self.sender.close()
        received, descriptors = b'', []
        try:
            while len(received) < len(MAGIC):
                self.timeout()
                part, controls, flags, _ = self.receiver.recvmsg(
                    len(MAGIC)-len(received), socket.CMSG_SPACE(4), socket.MSG_CMSG_CLOEXEC)
                invalid_control = False
                for level, kind, data in controls:
                    if (level, kind) != (socket.SOL_SOCKET, socket.SCM_RIGHTS):
                        invalid_control = True
                        continue
                    rights = array.array('i'); rights.frombytes(data[:len(data)//rights.itemsize*rights.itemsize])
                    descriptors.extend(rights)
                    if len(data) % rights.itemsize: raise ValueError('malformed storage descriptors')
                if invalid_control or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or not part:
                    raise ValueError('truncated storage handoff')
                received += part
            if received != MAGIC or len(descriptors) != 1:
                raise ValueError('invalid storage handoff')
            info, capacity = os.fstat(descriptors[0]), os.fstatvfs(descriptors[0])
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700
                    or capacity.f_flag & os.ST_RDONLY or capacity.f_blocks*capacity.f_frsize != self.policy['work']
                    or os.listdir(descriptors[0])):
                raise ValueError('invalid private storage directory')
            self.fd = descriptors.pop()
            self.seed()
            usage = os.fstatvfs(self.fd)
            self.seeded_bytes = (usage.f_blocks-usage.f_bfree)*usage.f_frsize
            # Controller-seeded pages are charged outside the worker's cgroup.
            group.limit_memory(min(int(group.controls['memory.max']), self.policy['total']-self.seeded_bytes))
            self.timeout(); self.receiver.sendall(struct.pack('!I', len(self.frame))+self.frame)
            ready = b''
            while len(ready) < len(READY):
                self.timeout()
                part = self.receiver.recv(len(READY)-len(ready))
                if not part: raise ValueError('worker storage policy was not verified')
                ready += part
            if ready != READY: raise ValueError('worker storage policy mismatch')
            self.verified = True
        finally:
            for descriptor in descriptors: os.close(descriptor)

    def seed(self):
        frozen = tree(self.workspace)
        before = digest(self.workspace)
        for index, path in enumerate(self.workspace.rglob('*')):
            self.timeout()
            if index >= 5000 or len(path.relative_to(self.workspace).parts) > 64 or path.is_symlink():
                raise ValueError('storage seed entry/depth boundary violated')
            if path.is_dir():
                descriptor = directory(self.fd, path.relative_to(self.workspace).parts, create=True)
                os.close(descriptor)
        for name, record in frozen.items():
            raw = read_regular(self.workspace/name, 10*1024*1024)
            if hashlib.sha256(raw).hexdigest() != record['sha256']:
                raise ValueError('storage seed changed')
            parent = directory(self.fd, name.split('/')[:-1], create=True)
            try:
                descriptor = os.open(name.split('/')[-1], os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, record['mode'], dir_fd=parent)
                with os.fdopen(descriptor, 'wb') as output:
                    os.fchmod(output.fileno(), record['mode'])
                    output.write(raw)
            finally: os.close(parent)
            self.timeout()
        if digest(self.workspace) != before: raise ValueError('storage seed changed during copying')

    def extract(self):
        self.timeout()
        result, total = {}, 0
        for name in self.outputs:
            parent = directory(self.fd, name.split('/')[:-1])
            try:
                descriptor = os.open(name.split('/')[-1], os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK, dir_fd=parent)
                try:
                    info = os.fstat(descriptor)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024*1024-total:
                        raise ValueError('invalid bounded storage artifact')
                    raw = os.read(descriptor, 1024*1024-total+1)
                    if len(raw) != info.st_size: raise ValueError('storage artifact size mismatch')
                    result[name] = raw; total += len(raw)
                    self.timeout()
                finally: os.close(descriptor)
            finally: os.close(parent)
        for name, raw in result.items():
            self.timeout()
            target = contained(self.workspace, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
                temporary = Path(output.name)
                try:
                    output.write(raw); output.flush(); os.fchmod(output.fileno(), mode)
                    os.replace(temporary, target)
                finally: temporary.unlink(missing_ok=True)
        self.timeout()
        return dict(artifacts=len(result), bytes=total)

    def __exit__(self, *error):
        if self.filter_fd is not None: os.close(self.filter_fd)
        if self.fd is not None: os.close(self.fd)
        if self.receiver is not None: self.receiver.close()
        if self.sender is not None: self.sender.close()
