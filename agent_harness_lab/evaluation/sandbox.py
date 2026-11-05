"""Optional offline Linux role isolation. Never falls back to host execution."""
from dataclasses import asdict, dataclass, replace
from contextlib import ExitStack, nullcontext
import hashlib
import math
import os
from pathlib import Path
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


def _file_sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


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
            except (OSError, ValueError, subprocess.SubprocessError):
                return dict(status='unsupported', reason='resource_boundary_unavailable', automatic_promotion=False)
        available = run['exit_code'] == 0 and run['status'] == 'completed' and run['inputs_unchanged'] is True
        if self.permission_audit:
            observed=run['attestation']['permission_audit']
            available=available and observed['status']=='complete' and observed['qualification']=='violated'
        return dict(status='available' if available else 'unsupported',
                    reason=None if available else 'namespace_probe_failed',
                    automatic_promotion=False,
                    cgroup_v2=run['attestation']['cgroup_v2'],
                    runtime_image=run['attestation'].get('runtime_image'),
                    storage_policy=run['attestation'].get('storage_policy'),
                    permission_audit=dict(profile=self.permission_audit,probe='observed_swallowed_readonly_denial') if self.permission_audit else None,
                    limitations=['full isolation certification not established']
                        + ([] if self.disk_bytes is not None else ['no aggregate disk quota'])
                        + ([] if self.image else ['runtime dependencies unpinned'])
                        + ([] if self.cgroup_root is not None else ['no aggregate cgroup controls']))

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
            if group is None:
                resource.setrlimit(resource.RLIMIT_NPROC, (limits.processes, limits.processes))
            for key, value in ((resource.RLIMIT_AS, limits.memory_bytes),
                               (resource.RLIMIT_FSIZE, limits.file_bytes), (resource.RLIMIT_NOFILE, 64),
                               (resource.RLIMIT_CORE, 0)):
                resource.setrlimit(key, (value, value))
        buffers, reason = [bytearray(), bytearray()], None
        aggregate = replace(limits, memory_bytes=min(limits.memory_bytes, self.disk_bytes)) if self.disk_bytes is not None else limits
        boundary = Cgroup(self.cgroup_root, aggregate, start+limits.seconds) if self.cgroup_root is not None else nullcontext(None)
        storage = PrivateWorkspace(self, workspace, inputs, role, argv, self.disk_bytes,
            start+limits.seconds, artifact_allowlist if artifact_allowlist is not None else [], working_state,limits=limits) if self.disk_bytes is not None else nullcontext(None)
        storage_report = None
        audit_report=dict(status='not_requested',qualification='unknown',observation_scope='unobserved')
        with storage as scratch:
            with ExitStack() as stack:
                group=stack.enter_context(boundary)
                audit=None
                if self.permission_audit:
                    from .permission_audit import Observation
                    audit=Observation(scratch.command,scratch.audit_execs,group,limits,start+limits.seconds,role,**(dict(source_ticket=source_ticket) if source_ticket is not None else {}))
                    stack.callback(audit.close)
                descriptors=tuple(([scratch.filter_fd] if scratch else [])+([audit.writer] if audit else []))
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
                except BaseException:
                    try: os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    process.wait(timeout=2)
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
                        cgroup_v2=cgroup,
                        runtime_image=image,
                        storage_policy=storage_report,
                        permission_audit=audit_report,
                        permission_command=permission_command,
                        aggregate_disk_bound_verified=bool(storage_report and storage_report['verified']), inference_supported=False))
        _kernel_finished(source_ticket,result,argv,scratch.export_fingerprints if scratch else None)
        return result
