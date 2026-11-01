"""Optional offline Linux role isolation. Never falls back to host execution."""
from dataclasses import asdict, dataclass
from contextlib import nullcontext
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
from .integrity import digest

ROLES = {'proposer', 'evaluator_author', 'executor', 'grader'}


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
    def __init__(self, binary='bwrap', *, cgroup_root=None):
        self.binary = shutil.which(binary)
        self.cgroup_root = cgroup_root

    def command(self, argv, workspace, inputs, role):
        if role not in ROLES or not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError('invalid role/command')
        if not self.binary:
            raise ValueError('bubblewrap is unavailable')
        # One explicit writable workspace; only controller-declared readonly inputs.
        digest(workspace)
        workspace = Path(workspace).resolve()
        command = [self.binary, '--unshare-all', '--unshare-user', '--unshare-net', '--die-with-parent',
                   '--new-session', '--cap-drop', 'ALL', '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin',
                   '--setenv', 'HOME', '/home/worker', '--setenv', 'LANG', 'C.UTF-8']
        command += ['--setenv', 'AHL_SANDBOX_ROLE', role]
        for system in ('/usr', '/bin', '/lib', '/lib64'):
            if Path(system).exists():
                command += ['--ro-bind', system, system]
        command += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/home/worker',
                    '--bind', str(workspace), '/work']
        for name, folder in sorted(inputs.items()):
            if name not in ('harness', 'fixture', 'oracle') or (name == 'oracle' and role != 'grader'):
                raise ValueError('role cannot access this input')
            digest(folder)
            folder = Path(folder).resolve()
            if workspace.is_relative_to(folder) or folder.is_relative_to(workspace):
                raise ValueError('writable workspace and readonly inputs must not overlap')
            command += ['--ro-bind', str(folder), '/inputs/' + name]
        return command + ['--remount-ro', '/', '--chdir', '/work', '--', *argv]

    def capability(self, *, seconds=3):
        Limits(seconds=seconds).validate()
        if sys.platform != 'linux' or not self.binary:
            return dict(status='unsupported', reason='linux_bubblewrap_unavailable', automatic_promotion=False)
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            try:
                run = self.run(['/usr/bin/true'], Path(folder), {}, 'grader', Limits(seconds=seconds))
            except TimeoutError:
                return dict(status='unsupported', reason='namespace_probe_timeout', automatic_promotion=False)
            except (OSError, ValueError, subprocess.SubprocessError):
                return dict(status='unsupported', reason='resource_boundary_unavailable', automatic_promotion=False)
        available = run['exit_code'] == 0 and run['status'] == 'completed' and run['inputs_unchanged'] is True
        return dict(status='available' if available else 'unsupported',
                    reason=None if available else 'namespace_probe_failed',
                    automatic_promotion=False,
                    cgroup_v2=run['attestation']['cgroup_v2'],
                    limitations=['no aggregate disk quota', 'inference broker not implemented']
                        + ([] if self.cgroup_root is not None else ['no aggregate cgroup controls']))

    def run(self, argv, workspace, inputs, role, limits=Limits()):
        limits.validate()
        start = time.monotonic()
        command = self.command(argv, workspace, inputs, role)
        before = {name: digest(folder) for name, folder in inputs.items()}
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
        boundary = Cgroup(self.cgroup_root, limits, start+limits.seconds) if self.cgroup_root is not None else nullcontext(None)
        with boundary as group, subprocess.Popen(command, cwd=workspace, env={'PATH': '/usr/bin:/bin'},
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True, preexec_fn=constrain) as process:
            poll = selectors.DefaultSelector()
            try:
                for index, pipe in enumerate((process.stdout, process.stderr)):
                    os.set_blocking(pipe.fileno(), False)
                    poll.register(pipe, selectors.EVENT_READ, index)
                while poll.get_map():
                    remaining = limits.seconds - (time.monotonic() - start)
                    if remaining <= 0:
                        reason = 'timeout'; break
                    for key, _ in poll.select(min(.1, remaining)):
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
            except BaseException:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                process.wait(timeout=2)
                raise
            finally:
                poll.close()
        unchanged = all(digest(folder) == before[name] for name, folder in inputs.items())
        cgroup = group.report if group is not None else None
        if cgroup and (cgroup['events']['pids.events']['max'] or any(cgroup['events']['memory.events'][k] for k in ('max', 'oom', 'oom_kill'))):
            reason = reason or 'resource_limit'
        return dict(exit_code=process.returncode, status=reason or ('completed' if process.returncode == 0 else 'failed'),
                    stdout=bytes(buffers[0][:limits.output_bytes]).decode('utf-8', 'replace'),
                    stderr=bytes(buffers[1][:limits.output_bytes]).decode('utf-8', 'replace'),
                    elapsed_seconds=time.monotonic() - start, inputs_unchanged=unchanged,
                    attestation=dict(backend='bubblewrap-offline-v1', role=role, network='none',
                        binary_sha256=hashlib.sha256(Path(self.binary).read_bytes()).hexdigest(),
                        input_digests=before, automatic_promotion=False,
                        resource_limits=asdict(limits),
                        cgroup_v2=cgroup,
                        aggregate_disk_bound_verified=False, inference_supported=False))
