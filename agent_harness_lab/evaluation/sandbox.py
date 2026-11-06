"""Optional offline Linux role isolation. Never falls back to host execution."""
from dataclasses import asdict, dataclass, replace
from contextlib import ExitStack, nullcontext
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import selectors
import shutil
import signal
import subprocess
import sys
import time
from .cgroup import Cgroup
from .image import RuntimeImage
from .integrity import digest
from .analysis_inputs import input_digest,check_role
from .workspace import PrivateWorkspace, policy

ROLES = {'proposer', 'evaluator_author', 'executor', 'grader'}
# Runs on the host before bubblewrap: joining a new anonymous session keyring (KEYCTL_JOIN_SESSION_KEYRING, NULL name)
# drops possession of the validator's session keyring, whose keys bubblewrap's namespaces would otherwise leave
# readable. A kernel without keys (ENOSYS) has no keyring to leak; any other failure stops before bubblewrap runs.
KEYRING_UNAVAILABLE, KEYRING_EXIT = 'ahl: keyring isolation unavailable', 125
KEYRING_TRAMPOLINE = f'''import ctypes, errno, os, sys
number = {{'x86_64': 250, 'aarch64': 219, 'riscv64': 219, 'loongarch64': 219}}.get(os.uname().machine)
libc = ctypes.CDLL(None, use_errno=True); libc.syscall.restype = ctypes.c_long
if number is None or (libc.syscall(number, 1, None) < 0 and ctypes.get_errno() != errno.ENOSYS):
    sys.stderr.write({KEYRING_UNAVAILABLE!r} + '\\n'); sys.exit({KEYRING_EXIT})
os.execv(sys.argv[1], sys.argv[1:])
'''


class KeyringUnavailable(ValueError):
    """The keyring trampoline refused to start bubblewrap."""


def keyring_launcher():
    """Host argv prefix that runs the next argument through KEYRING_TRAMPOLINE: every sandbox launch starts with it."""
    if not sys.executable:
        raise ValueError('no interpreter for the keyring trampoline')
    return [sys.executable, '-I', '-S', '-c', KEYRING_TRAMPOLINE]


def _keyring_refused(process):
    """Whether a launch exited through the trampoline's refusal, which happens before bubblewrap starts."""
    if process.returncode != KEYRING_EXIT or process.stderr is None:
        return False
    try:
        os.set_blocking(process.stderr.fileno(), False)
        return KEYRING_UNAVAILABLE.encode() in os.read(process.stderr.fileno(), 4096)
    except (OSError, ValueError):
        return False


def _file_sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


# bubblewrap's clone() EAGAIN: the host's process count or the user's own process limit, never a missing namespace.
CLONE_EXHAUSTED = 'Resource temporarily unavailable'
# Since Linux 5.14 RLIMIT_NPROC counts each user per user namespace and a namespace keeps its creator's limit for the
# real UID, so a cap set inside the sandbox's own user namespace counts the sandbox's processes only. Earlier kernels
# count every process of the real UID, which a sandbox-scoped bound cannot use.
NAMESPACED_NPROC_KERNEL = (5, 14)
INFO_BYTES = 4096


def _namespaced_nproc(release=None):
    numbers = re.match(r'(\d+)\.(\d+)', release if release is not None else os.uname().release)
    return bool(numbers) and tuple(map(int, numbers.groups())) >= NAMESPACED_NPROC_KERNEL


def _launch_failure(error):
    """A launch that fails before bubblewrap runs: the controller's own fork EAGAIN is the user's process limit."""
    return 'process_limit_exhausted' if getattr(error, 'errno', None) == errno.EAGAIN else 'resource_boundary_unavailable'


def _unbounded(reason):
    return dict(enforced=False, scope=None, limit=None, reason=reason)


def _parent_pid(pid):
    try:
        return int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        return None


def _bound_namespace(process, info, release, processes, deadline):
    """Caps RLIMIT_NPROC on the sandbox's first process while bubblewrap holds it on --block-fd. That process already
    lives in the sandbox's user namespace, so the cap counts only the sandbox's processes; the real UID keeps the host
    limit the namespace was created under. Without a verified cap the run continues and reports the bound as not
    enforced. The deadline leaves the sandbox blocked for the caller's timeout cleanup."""
    report, data = None, b''
    with selectors.DefaultSelector() as poll:
        poll.register(info, selectors.EVENT_READ)
        while len(data) <= INFO_BYTES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return _unbounded('timeout')
            if poll.select(min(.1, remaining)):
                chunk = os.read(info, INFO_BYTES)
                if not chunk:
                    break
                data += chunk
    try:
        pid = json.loads(data)['child-pid']
    except (ValueError, KeyError, TypeError):
        pid = None
    if type(pid) is not int or pid <= 0 or _parent_pid(pid) != process.pid:
        report = _unbounded('sandbox_not_started')
    else:
        try:
            resource.prlimit(pid, resource.RLIMIT_NPROC, (processes, processes))
            if resource.prlimit(pid, resource.RLIMIT_NPROC) != (processes, processes):
                raise OSError('process cap not read back')
        except OSError:
            report = _unbounded('namespace_limit_unavailable')
        else:
            report = dict(enforced=True, scope='user_namespace', limit=processes, reason=None)
    try:
        os.write(release, b'1')
    except OSError:
        pass  # bubblewrap already exited
    return report


@dataclass(frozen=True)
class Limits:
    seconds: float = 10
    memory_bytes: int = 256 * 1024 * 1024
    processes: int = 32
    file_bytes: int = 1024 * 1024
    output_bytes: int = 1024 * 1024

    def validate(self):
        if not math.isfinite(self.seconds) or self.seconds <= 0 or any(
                type(v) is not int or v <= 0 for v in
                (self.memory_bytes, self.processes, self.file_bytes, self.output_bytes)):
            raise ValueError('invalid sandbox resource limits')


class SandboxBackend:
    """Role mounts are controller-selected; candidate content is never policy."""
    def __init__(self, binary='bwrap', *, cgroup_root=None, image_root=None, image_sha256=None, disk_bytes=None, permission_audit=None):
        self.binary = shutil.which(binary)
        self.cgroup_root = cgroup_root
        if (image_root is None) != (image_sha256 is None):
            raise ValueError('runtime image root and SHA-256 pin must be supplied together')
        self.image = RuntimeImage(image_root, image_sha256) if image_root is not None else None
        self.disk_bytes = disk_bytes
        from .permission_audit import PROFILE
        if permission_audit not in (None,PROFILE):raise ValueError('unsupported permission audit profile')
        self.permission_audit=permission_audit
        self.permission_reports=[]
        if disk_bytes is not None:
            policy(disk_bytes)
            if cgroup_root is None: raise ValueError('bounded storage requires aggregate cgroup controls')

    def permission_qualification(self):
        from .permission_audit import qualification
        return dict(profile=self.permission_audit,status='requested' if self.permission_audit else 'not_requested',
            qualification=qualification(self.permission_reports) if self.permission_audit else 'unknown',
            runs=len(self.permission_reports),observation_scope='declared_role_permission_errno_results_only',
            semantic_intent='unknown',automatic_promotion=False,publication_allowed=False)

    def command(self, argv, workspace, inputs, role):
        """Host argv for one role run: bubblewrap_command() launched through KEYRING_TRAMPOLINE, so the run starts in a
        new empty session keyring and cannot search or read keys of the validator's session keyring."""
        sandboxed = self.bubblewrap_command(argv, workspace, inputs, role)
        return keyring_launcher() + sandboxed

    def bubblewrap_command(self, argv, workspace, inputs, role):
        """The bubblewrap argv alone. Only nested or traced launches whose host process already went through the
        trampoline use it directly; a host launch uses command()."""
        check_role(inputs,role)
        if role not in ROLES or not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError('invalid role/command')
        if not self.binary:
            raise ValueError('bubblewrap is unavailable')
        # One explicit writable workspace; only controller-declared readonly inputs.
        digest(workspace)
        workspace = Path(workspace).resolve()
        command = [self.binary, '--unshare-all', '--unshare-user', '--unshare-net', '--die-with-parent',
                   '--new-session', '--cap-drop', 'ALL', '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin',
                   '--setenv', 'HOME', '/home/worker', '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'PWD', '/work']
        command += ['--setenv', 'AHL_SANDBOX_ROLE', role]
        if self.permission_audit:
            command += ['--setenv','PYTHONDONTWRITEBYTECODE','1']
        if self.image:
            for folder in (workspace, *map(Path, inputs.values())):
                if folder.resolve().is_relative_to(self.image.root) or self.image.root.is_relative_to(folder.resolve()):
                    raise ValueError('runtime image and role data must not overlap')
            command += ['--ro-bind', str(self.image.root), '/', '--tmpfs', '/inputs']
        else:
            for system in ('/usr', '/bin', '/lib', '/lib64'):
                if Path(system).exists():
                    command += ['--ro-bind', system, system]
        command += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/home/worker', '--dir', '/inputs',
                    '--bind', str(workspace), '/work']
        for name, folder in sorted(inputs.items()):
            if name not in ('harness', 'fixture', 'oracle') or (name == 'oracle' and role != 'grader'):
                raise ValueError('role cannot access this input')
            input_digest(inputs,name)
            folder = Path(folder).resolve()
            if workspace.is_relative_to(folder) or folder.is_relative_to(workspace):
                raise ValueError('writable workspace and readonly inputs must not overlap')
            command += ['--ro-bind', str(folder), '/inputs/' + name]
        if self.image:
            command += ['--remount-ro', '/inputs']
        return command + ['--remount-ro', '/', '--chdir', '/work', '--', *argv]

    def hook_trial_command(self, argv, root, *, writable, cwd, env, runtime):
        """bubblewrap argv for one hook trial: no network, only env in the environment, the system directories and the
        runtime file read-only, the trial root read-only at its own path and only the listed trial folders writable.
        Nothing else of the host, its home and credential stores included, is mounted, and a host-side trampoline
        (KEYRING_TRAMPOLINE) gives the run a new empty session keyring before bubblewrap starts."""
        if not self.binary:
            raise ValueError('bubblewrap is unavailable')
        launcher = keyring_launcher()
        if not argv or not all(isinstance(a, str) and '\0' not in a for a in argv):
            raise ValueError('invalid hook trial command')
        root, cwd, runtime = (Path(path).resolve(strict=True) for path in (root, cwd, runtime))
        if not cwd.is_relative_to(root) or not runtime.is_file():
            raise ValueError('hook trial command outside its trial')
        command = launcher + [self.binary, '--unshare-all', '--unshare-user', '--unshare-net', '--die-with-parent',
                              '--new-session', '--cap-drop', 'ALL', '--clearenv']
        for key, value in sorted(env.items()):
            if not isinstance(key, str) or not isinstance(value, str) or not key or '=' in key or '\0' in key + value:
                raise ValueError('invalid hook trial environment')
            command += ['--setenv', key, value]
        systems = [system for system in ('/usr', '/bin', '/lib', '/lib64') if Path(system).exists()]
        for system in systems:
            command += ['--ro-bind', system, system]
        command += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--ro-bind', str(root), str(root)]
        for folder in writable:
            if Path(folder).is_symlink() or not Path(folder).is_dir():
                raise ValueError('a writable hook trial folder must be a directory')
            folder = Path(folder).resolve()
            if folder == root or not folder.is_relative_to(root):
                raise ValueError('a writable hook trial folder must lie inside the trial')
            command += ['--bind', str(folder), str(folder)]
        if not any(runtime.is_relative_to(Path(system).resolve()) for system in systems):
            command += ['--ro-bind', str(runtime), str(runtime)]
        return command + ['--remount-ro', '/', '--chdir', str(cwd), '--', str(runtime), *argv[1:]]

    def hook_trial_capability(self, *, seconds=3):
        """The hook trial's own probe: its exact command on /usr/bin/true. Unlike capability() it sets no per-user
        process limit, which a hook trial never applies, so a host running many processes is not a namespace failure."""
        Limits(seconds=seconds).validate()
        if sys.platform != 'linux' or not self.binary:
            return dict(status='unsupported', reason='linux_bubblewrap_unavailable', automatic_promotion=False)
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('home', 'tmp', 'workspace'):
                (root / name).mkdir()
            try:
                argv = self.hook_trial_command(['/usr/bin/true'], root, writable=[root / 'home', root / 'tmp'],
                                               cwd=root / 'workspace', env={'PATH': '/usr/bin:/bin'}, runtime='/usr/bin/true')
                probe = subprocess.run(argv, cwd=root, env={'PATH': '/usr/bin:/bin'}, stdin=subprocess.DEVNULL,
                                       capture_output=True, timeout=seconds)
            except subprocess.TimeoutExpired:
                return dict(status='unsupported', reason='namespace_probe_timeout', automatic_promotion=False)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                return dict(status='unsupported', reason=_launch_failure(error), automatic_promotion=False)
        if probe.returncode == 0:
            return dict(status='available', reason=None, automatic_promotion=False,
                        limitations=['full isolation certification not established', 'no resource limits',
                                     'runtime dependencies unpinned'])
        if probe.returncode == KEYRING_EXIT and KEYRING_UNAVAILABLE.encode() in probe.stderr:
            reason = 'keyring_isolation_unavailable'
        elif CLONE_EXHAUSTED.encode() in probe.stderr:
            reason = 'process_limit_exhausted'  # clone() EAGAIN: the user's own process limit, not namespaces
        else:
            reason = 'namespace_probe_failed'
        return dict(status='unsupported', reason=reason, automatic_promotion=False)

    def capability(self, *, seconds=3):
        Limits(seconds=seconds).validate()
        if sys.platform != 'linux' or not self.binary:
            return dict(status='unsupported', reason='linux_bubblewrap_unavailable', automatic_promotion=False)
        if self.permission_audit:
            from .permission_audit import supported
            if not supported() or self.image is None or self.disk_bytes is None or self.cgroup_root is None:
                return dict(status='unsupported',reason='permission_audit_profile_unavailable',automatic_promotion=False)
        import tempfile
        from .controller_receipts import _capability_probe
        with tempfile.TemporaryDirectory() as folder:
            try:
                checkpoint=len(self.permission_reports)
                if self.permission_audit:
                    fixture=Path(folder)/'fixture';fixture.mkdir();(fixture/'marker').write_text('synthetic')
                    work=Path(folder)/'work';work.mkdir()
                    code="from pathlib import Path\nPath('/work/allowed').write_text('synthetic')\ntry:Path('/inputs/fixture/marker').write_text('bad')\nexcept OSError as exc:assert exc.errno in (1,13,30)\nelse:raise AssertionError('readonly boundary absent')"
                    with _capability_probe():
                        run=self.run(['/usr/bin/python3','-c',code],work,{'fixture':fixture},'grader',Limits(seconds=seconds))
                else:
                    with _capability_probe():
                        run = self.run(['/usr/bin/true'], Path(folder), {}, 'grader', Limits(seconds=seconds))
                del self.permission_reports[checkpoint:]
            except TimeoutError:
                return dict(status='unsupported', reason='namespace_probe_timeout', automatic_promotion=False)
            except KeyringUnavailable:
                return dict(status='unsupported', reason='keyring_isolation_unavailable', automatic_promotion=False)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                return dict(status='unsupported', reason=_launch_failure(error), automatic_promotion=False)
        if run['exit_code'] == KEYRING_EXIT and KEYRING_UNAVAILABLE in run['stderr']:
            return dict(status='unsupported', reason='keyring_isolation_unavailable', automatic_promotion=False)
        available = run['exit_code'] == 0 and run['status'] == 'completed' and run['inputs_unchanged'] is True
        if self.permission_audit:
            observed=run['attestation']['permission_audit']
            available=available and observed['status']=='complete' and observed['qualification']=='violated'
        bound = run['attestation'].get('process_bound')
        return dict(status='available' if available else 'unsupported',
                    reason=None if available else
                        'process_limit_exhausted' if CLONE_EXHAUSTED in run.get('stderr', '') else 'namespace_probe_failed',
                    automatic_promotion=False,
                    process_bound=bound,
                    cgroup_v2=run['attestation']['cgroup_v2'],
                    runtime_image=run['attestation'].get('runtime_image'),
                    storage_policy=run['attestation'].get('storage_policy'),
                    permission_audit=dict(profile=self.permission_audit,probe='observed_swallowed_readonly_denial') if self.permission_audit else None,
                    limitations=['full isolation certification not established']
                        + ([] if self.disk_bytes is not None else ['no aggregate disk quota'])
                        + ([] if self.image else ['runtime dependencies unpinned'])
                        + ([] if self.cgroup_root is not None else ['no aggregate cgroup controls'])
                        + ([] if bound is None or bound['enforced'] else ['no sandbox process bound']))

    def run(self, argv, workspace, inputs, role, limits=Limits(), *, artifact_allowlist=None, working_state=None):
        from .controller_receipts import _kernel_started, _kernel_spawned, _kernel_finished
        source_ticket = _kernel_started(role,argv)
        limits.validate()
        audit_capture=None
        if self.permission_audit:
            # Reserve coverage before preparation/launch can raise.
            audit_capture=dict(status='unknown',qualification='unknown',reason='capture_unresolved')
            self.permission_reports.append(audit_capture)
        start = time.monotonic()
        permission_command=None
        if self.permission_audit:
            from .permission_audit import hashed, canonical_argv
            if not isinstance(argv,(list,tuple)) or not argv or any(not isinstance(a,str) or '\0' in a for a in argv):
                raise ValueError('permission audit requires an explicit argv')
            requested=list(argv);effective=canonical_argv(requested)
            permission_command=dict(requested_argv_sha256=hashed(requested),effective_argv_sha256=hashed(effective),
                normalization='canonical_python_no_bytecode_v1' if effective!=requested else 'unchanged')
            argv=effective
        if self.permission_audit:
            from .permission_audit import supported
            if not supported() or self.image is None or self.disk_bytes is None or self.cgroup_root is None:
                raise ValueError('permission audit requires the full supported isolation profile')
        if working_state is not None:
            from .working_state import WorkingState
            if not isinstance(working_state,WorkingState) or role == 'grader' or self.disk_bytes is None or artifact_allowlist is not None:
                raise ValueError('working state requires a private worker workspace without artifact export')
        image = self.image.verify(deadline=start+limits.seconds) if self.image else None
        command = self.command(argv, workspace, inputs, role)
        if self.disk_bytes is not None and role != 'grader' and artifact_allowlist is None and working_state is None:
            raise ValueError('bounded workers require an explicit artifact allowlist')
        before = {name: input_digest(inputs,name) for name in inputs}
        if time.monotonic() - start >= limits.seconds:
            raise TimeoutError('sandbox budget exhausted before launch')
        def constrain():
            if group is not None:
                group.attach()
                os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
                resource.setrlimit(resource.RLIMIT_RTPRIO, (0, 0))
            resource.setrlimit(resource.RLIMIT_CPU, (math.ceil(limits.seconds), math.ceil(limits.seconds) + 1))
            for key, value in ((resource.RLIMIT_AS, limits.memory_bytes),
                               (resource.RLIMIT_FSIZE, limits.file_bytes), (resource.RLIMIT_NOFILE, 64),
                               (resource.RLIMIT_CORE, 0)):
                resource.setrlimit(key, (value, value))
        # The process bound never counts the invoking user's other processes: the cgroup's pids.max, or a cap inside the
        # sandbox's own user namespace set by _bound_namespace(); otherwise the receipt reports it as not enforced.
        bound_fds = {}
        if self.cgroup_root is not None:
            process_bound = dict(enforced=True, scope='cgroup', limit=limits.processes, reason=None)
        elif '--unshare-user' not in command or '--unshare-all' not in command:
            process_bound = _unbounded('no_user_namespace')
        elif not _namespaced_nproc():
            process_bound = _unbounded('kernel_counts_real_uid')
        else:
            process_bound = None
            (bound_fds['info'], bound_fds['info_child']), (bound_fds['block_child'], bound_fds['block']) = os.pipe(), os.pipe()
            index = command.index('--unshare-all')
            command = (command[:index] + ['--info-fd', str(bound_fds['info_child']), '--block-fd', str(bound_fds['block_child'])]
                       + command[index:])
        buffers, reason = [bytearray(), bytearray()], None
        aggregate = replace(limits, memory_bytes=min(limits.memory_bytes, self.disk_bytes)) if self.disk_bytes is not None else limits
        boundary = Cgroup(self.cgroup_root, aggregate, start+limits.seconds) if self.cgroup_root is not None else nullcontext(None)
        storage = PrivateWorkspace(self, workspace, inputs, role, argv, self.disk_bytes,
            start+limits.seconds, artifact_allowlist if artifact_allowlist is not None else [], working_state,limits=limits) if self.disk_bytes is not None else nullcontext(None)
        storage_report = None
        audit_report=dict(status='not_requested',qualification='unknown',observation_scope='unobserved')
        with storage as scratch:
            with ExitStack() as stack:
                stack.callback(lambda: [os.close(bound_fds.pop(name)) for name in list(bound_fds)])
                group=stack.enter_context(boundary)
                audit=None
                if self.permission_audit:
                    from .permission_audit import Observation
                    audit=Observation(scratch.command,scratch.audit_execs,group,limits,start+limits.seconds,role,**(dict(source_ticket=source_ticket) if source_ticket is not None else {}))
                    stack.callback(audit.close)
                descriptors=tuple(([scratch.filter_fd] if scratch else [])+([audit.writer] if audit else [])
                    +[bound_fds[name] for name in ('info_child','block_child') if name in bound_fds])
                process=stack.enter_context(subprocess.Popen(audit.command if audit else scratch.command if scratch else command,
                    cwd=workspace, env={'PATH': '/usr/bin:/bin'}, stdin=subprocess.PIPE if audit else subprocess.DEVNULL,
                    stdout=scratch.sender if scratch else subprocess.PIPE, stderr=subprocess.PIPE,
                    pass_fds=descriptors,
                    start_new_session=True, preexec_fn=None if audit else constrain))
                poll = selectors.DefaultSelector()
                try:
                    if audit:
                        audit.start(process);poll.register(audit.reader,selectors.EVENT_READ,2)
                    if scratch: scratch.start(group)
                    _kernel_spawned(source_ticket,process,scratch,group,*([audit] if audit is not None else []))
                    if bound_fds:
                        for name in ('info_child', 'block_child'):  # bubblewrap holds its own ends
                            os.close(bound_fds.pop(name))
                        process_bound = _bound_namespace(process, bound_fds['info'], bound_fds['block'], limits.processes,
                                                         start + limits.seconds)
                    for index, pipe in enumerate((scratch.receiver if scratch else process.stdout, process.stderr)):
                        os.set_blocking(pipe.fileno(), False)
                        poll.register(pipe, selectors.EVENT_READ, index)
                    while poll.get_map():
                        remaining = limits.seconds - (time.monotonic() - start)
                        if remaining <= 0:
                            reason = 'timeout'; break
                        for key, _ in poll.select(min(.1, remaining)):
                            if key.data==2:
                                audit.receive()
                                if audit.eof:poll.unregister(key.fileobj)
                                continue
                            raw = os.read(key.fileobj.fileno(), min(65536, limits.output_bytes + 1))
                            if not raw:
                                poll.unregister(key.fileobj)
                            else:
                                available = limits.output_bytes - sum(map(len, buffers))
                                buffers[key.data].extend(raw[:available])
                                if len(raw) > available:
                                    reason = 'output_limit'; break
                        if reason:
                            break
                    if not reason:
                        try:
                            process.wait(timeout=max(.01, limits.seconds - (time.monotonic() - start)))
                        except subprocess.TimeoutExpired:
                            reason = 'timeout'
                    # Always clean descendants, including children that closed output pipes.
                    try: os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    process.wait(timeout=2)
                    if audit:
                        audit_report=audit.result()
                        if audit_report['status']!='complete':reason=reason or 'permission_audit_unavailable'
                except BaseException as error:
                    try: os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    process.wait(timeout=2)
                    # Before the storage handoff no role code has run, so this exit is the trampoline's own refusal.
                    if isinstance(error, Exception) and scratch is not None and not scratch.verified and _keyring_refused(process):
                        raise KeyringUnavailable(KEYRING_UNAVAILABLE) from error
                    raise
                finally:
                    poll.close()
            unchanged = all(input_digest(inputs,name) == before[name] for name in inputs)
            if image:
                image.update(verified_before=True, verified_after=False)
                try:
                    self.image.verify(deadline=start+limits.seconds)
                    image['verified_after'] = True
                except TimeoutError:
                    reason, unchanged = reason or 'timeout', False
            cgroup = group.report if group is not None else None
            if cgroup and (cgroup['events']['pids.events']['max'] or any(cgroup['events']['memory.events'][k] for k in ('max', 'oom', 'oom_kill'))):
                reason = reason or 'resource_limit'
            # The cgroup has stopped the whole tree before any artifact is read.
            if scratch:
                storage_report = dict(policy=scratch.policy, verified=scratch.verified,
                    controller_seeded_bytes=scratch.seeded_bytes,
                    hugetlb_policy=scratch.filter_policy,
                    extracted=scratch.extract() if not reason and (process.returncode == 0 or
                        working_state is not None and 0 < process.returncode < 128) and unchanged else None)
        if self.permission_audit:
            if reason and audit_report['qualification']!='violated':audit_report.update(status='unknown',qualification='unknown')
            audit_capture.update(audit_report)
        result = dict(exit_code=process.returncode, status=reason or ('completed' if process.returncode == 0 else 'failed'),
                    stdout=bytes(buffers[0][:limits.output_bytes]).decode('utf-8', 'replace'),
                    stderr=bytes(buffers[1][:limits.output_bytes]).decode('utf-8', 'replace'),
                    elapsed_seconds=time.monotonic() - start, inputs_unchanged=unchanged,
                    attestation=dict(backend='bubblewrap-offline-v1', role=role, network='none',
                        binary_sha256=_file_sha256(self.binary),
                        input_digests=before, automatic_promotion=False,
                        resource_limits=asdict(limits),
                        process_bound=process_bound,
                        cgroup_v2=cgroup,
                        runtime_image=image,
                        storage_policy=storage_report,
                        permission_audit=audit_report,
                        permission_command=permission_command,
                        aggregate_disk_bound_verified=bool(storage_report and storage_report['verified']), inference_supported=False))
        _kernel_finished(source_ticket,result,argv,scratch.export_fingerprints if scratch else None)
        return result
