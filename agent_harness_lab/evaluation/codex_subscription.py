"""Use a saved Codex login without placing it in a trial workspace."""
import json
import os
from pathlib import Path
import select
import shutil
import stat
import subprocess
import sys

if sys.platform == 'linux':
    import fcntl

MAX_AUTH = 1024 * 1024
SEED = '/tmp/ahl-codex-login'
LOGIN_FD = 200
CODEX_VERSION = '0.170.0'
STATE_FILES = ('installation_id', *(base + suffix
    for base in ('goals_1.sqlite', 'logs_2.sqlite', 'memories_1.sqlite', 'queue_1.sqlite', 'state_5.sqlite')
    for suffix in ('', '-shm', '-wal')))


def bubblewrap():
    try:
        executable = Path('/usr/bin/bwrap').resolve(strict=True)
        if executable.stat().st_mode & (stat.S_ISUID | stat.S_ISGID):
            return None
        for path in (executable, *executable.parents):
            entry = path.stat()
            if entry.st_uid != 0 or entry.st_mode & 0o022:
                return None
        return str(executable) if os.access(executable, os.X_OK) else None
    except OSError:
        return None


def source(environ):
    if not (sys.platform == 'linux' and hasattr(os, 'memfd_create') and bubblewrap()):
        return None
    home = Path(environ.get('CODEX_HOME') or Path(environ.get('HOME', str(Path.home()))) / '.codex')
    try:
        home = home.resolve(strict=True)
        entry = (home / 'auth.json').lstat()
        if (home == Path('/') or not stat.S_ISREG(entry.st_mode) or entry.st_uid != os.getuid()
                or entry.st_mode & 0o077 or not 0 < entry.st_size <= MAX_AUTH):
            return None
        return home / 'auth.json'
    except OSError:
        return None


def trusted_runtime(binary, environ, forbidden_roots):
    try:
        search_path = environ.get('PATH', os.defpath)
        selected = shutil.which(binary, path=search_path)
        default = shutil.which('codex', path=search_path)
        if not selected or not default:
            return False
        executable = Path(selected).resolve(strict=True)
        if executable != Path(default).resolve(strict=True) or not executable.is_file():
            return False
        if any(executable.is_relative_to(Path(root).resolve()) for root in forbidden_roots):
            return False
        for path in (executable, *executable.parents):
            entry = path.stat()
            if entry.st_uid not in (0, os.getuid()) or entry.st_mode & 0o022:
                return False
        return os.access(executable, os.X_OK)
    except (OSError, ValueError):
        return False


def command(auth, trial, workspace, argv):
    return [sys.executable, '-I', '-S', str(Path(__file__).resolve()), 'launch', str(auth),
            str(trial), str(workspace), *argv]


def cleanup(home):
    link = Path(home) / 'auth.json'
    try:
        target = os.readlink(link).split('/')
        if len(target) == 5 and target[:2] == ['', 'proc'] and target[2].isdigit() and target[3:] == ['fd', str(LOGIN_FD)]:
            link.unlink()
    except FileNotFoundError:
        pass


def snapshot(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
                or not 0 < info.st_size <= MAX_AUTH):
            raise ValueError('saved Codex login is unavailable')
        data = os.read(fd, MAX_AUTH + 1)
        if len(data) != info.st_size:
            raise ValueError('saved Codex login changed while reading')
        login = json.loads(data)
        if not isinstance(login, dict) or login.get('auth_mode') not in ('chatgpt', 'chatgptAuthTokens') or not isinstance(login.get('tokens'), dict):
            raise ValueError('saved Codex login is not a subscription login')
        del login
        memory = os.memfd_create('ahl-codex-login', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        try:
            if os.write(memory, data) != len(data):
                raise ValueError('saved Codex login snapshot was incomplete')
            del data
            os.lseek(memory, 0, os.SEEK_SET)
            # Linux UAPI constants are absent from some Python builds.
            fcntl.fcntl(memory, getattr(fcntl, 'F_ADD_SEALS', 1033), 0x0f)
            return memory
        except BaseException:
            os.close(memory)
            raise
    finally:
        os.close(fd)


def launch(auth, trial, workspace, argv):
    auth = Path(auth)
    if auth.name != 'auth.json':
        raise ValueError('invalid saved Codex login path')
    auth = auth.parent.resolve(strict=True) / auth.name
    trial, workspace = Path(trial).resolve(), Path(workspace).resolve()
    home = Path(os.environ['CODEX_HOME']).resolve()
    config = home / 'config.toml'
    if (not workspace.is_relative_to(trial) or trial.is_relative_to('/tmp')
            or not config.is_relative_to(trial) or trial.is_relative_to(auth.parent)
            or auth.parent.is_relative_to(trial)):
        raise ValueError('subscription trial paths are incompatible')
    masks = {auth.parent, (Path.home() / '.codex').resolve()}
    masks = {path for path in masks if path.is_dir() and not path.is_relative_to('/tmp')}
    masks = {path for path in masks if not any(path != other and path.is_relative_to(other) for other in masks)}
    if any(path == Path('/') or trial.is_relative_to(path) or path.is_relative_to(trial) for path in masks):
        raise ValueError('subscription trial paths are incompatible')
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agent_harness_lab.evaluation.sandbox import keyring_launcher
    executable = Path(shutil.which(argv[0]) or argv[0]).resolve(strict=True)
    if not trusted_runtime(argv[0], os.environ, (trial, workspace)):
        raise ValueError('subscription runtime is not trusted')
    if any(path.is_relative_to(executable.parent) for path in masks):
        raise ValueError('Codex executable directory overlaps a login directory')
    runtime = bubblewrap()
    if runtime is None:
        raise ValueError('subscription sandbox is unavailable')
    fd = snapshot(auth)
    runtime_home = trial / 'codex-runtime'
    runtime_home.mkdir(mode=0o700)
    for name in STATE_FILES:
        (home / name).touch(mode=0o600, exist_ok=False)
    for name in ('skills', 'tmp'):
        (home / name).mkdir(mode=0o700)
    scratch = [os.open('/dev/null', os.O_RDONLY) for _ in STATE_FILES]
    info_read, info_write = os.pipe()
    block_read, block_write = os.pipe()
    process = None
    target = None
    linked = False
    try:
        bubblewrap_args = [runtime, '--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc',
                      '--tmpfs', '/tmp', '--ro-bind', str(executable.parent), '/tmp/ahl-codex-bin',
                      '--bind', str(trial), str(trial), '--ro-bind', str(home), str(home),
                      '--ro-bind', str(home), str(runtime_home), '--setenv', 'CODEX_HOME', str(runtime_home)]
        argv = ['/tmp/ahl-codex-bin/' + executable.name, *argv[1:]]
        for mask in sorted(masks):
            bubblewrap_args += ['--tmpfs', str(mask)]
        for name, empty_fd in zip(STATE_FILES, scratch):
            bubblewrap_args += ['--bind-data', str(empty_fd), str(runtime_home / name)]
        for name in ('skills', 'tmp'):
            bubblewrap_args += ['--tmpfs', str(runtime_home / name)]
        bubblewrap_args += ['--perms', '0600', '--file', str(fd), SEED, '--info-fd', str(info_write),
                       '--block-fd', str(block_read), '--die-with-parent', '--chdir', str(workspace), '--',
                       sys.executable, '-I', '-S', str(Path(__file__).resolve()), 'inside', str(auth),
                       str(os.getpid()), str(fd), *argv]
        env = dict(os.environ, TMPDIR='/tmp')
        process = subprocess.Popen(keyring_launcher() + bubblewrap_args, env=env,
                                   pass_fds=(fd, info_write, block_read, *scratch))
        os.close(info_write); info_write = None
        os.close(block_read); block_read = None
        if not select.select([info_read], [], [], 10)[0]:
            raise ValueError('subscription sandbox startup timed out')
        info = json.loads(os.read(info_read, 4096))
        pid = info.get('child-pid')
        if type(pid) is not int or pid < 1:
            raise ValueError('subscription sandbox did not report its process')
        target = f'/proc/{pid}/fd/{LOGIN_FD}'
        (home / 'auth.json').symlink_to(target)
        linked = True
        os.write(block_write, b'1')
        os.close(block_write); block_write = None
        return process.wait()
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        link = home / 'auth.json'
        if linked and link.is_symlink() and os.readlink(link) == target:
            link.unlink()
        for pipe in (info_read, info_write, block_read, block_write):
            if pipe is not None:
                os.close(pipe)
        for empty_fd in scratch:
            os.close(empty_fd)
        os.close(fd)


def inside(auth, host_pid, host_fd, argv):
    fd = snapshot(SEED)
    copied = False
    try:
        os.unlink(SEED)
        os.dup2(fd, LOGIN_FD, inheritable=False)
        copied = True
        if os.readlink(Path(os.environ['CODEX_HOME']) / 'auth.json') != f'/proc/{os.getpid()}/fd/{LOGIN_FD}':
            raise ValueError('subscription login binding changed')
        if os.access(os.environ['CODEX_HOME'], os.W_OK):
            raise ValueError('subscription login directory is writable')
        check = ('command -v head >/dev/null && head -c 1 /proc/self/status >/dev/null || exit 1; '
                 'if [ -w "$CODEX_HOME" ] || chmod u+w "$CODEX_HOME" 2>/dev/null; then exit 1; fi; '
                 'if (printf x > "$CODEX_HOME/skills/.ahl-probe") 2>/dev/null; then exit 1; fi; '
                 'if (printf x > "$CODEX_HOME/tmp/.ahl-probe") 2>/dev/null; then exit 1; fi; '
                 'if (printf x > "$CODEX_HOME/state_5.sqlite") 2>/dev/null; then exit 1; fi; '
                 'for p in "$CODEX_HOME/auth.json" "$HOME/.codex/auth.json" "$1" '
                 '"/proc/$2/fd/$3" "/proc/$2/root$1" "/proc/$4/fd/$3" "/proc/$4/root$1" '
                 '/tmp/ahl-codex-login /tmp/ahl-codex-bin/auth.json; do '
                 'if head -c 1 "$p" >/dev/null 2>&1; then exit 1; fi; done')
        login = subprocess.run([argv[0], 'login', 'status'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        probe = subprocess.run([argv[0], '-c', 'sandbox_mode="workspace-write"', 'sandbox', '--',
                                'sh', '-c', check, 'sh', auth, host_pid, host_fd, str(os.getppid())],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if login.returncode or probe.returncode:
            raise ValueError('subscription login or sandbox verification failed')
        return subprocess.run(argv).returncode
    finally:
        if copied and fd != LOGIN_FD:
            os.close(LOGIN_FD)
        os.close(fd)


if __name__ == '__main__':
    try:
        if sys.argv[1] == 'launch':
            sys.exit(launch(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:]))
        if sys.argv[1] == 'inside':
            sys.exit(inside(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:]))
        raise ValueError('invalid subscription launcher mode')
    except (OSError, ValueError, IndexError) as exc:
        sys.stderr.write(f'Codex subscription trial could not be started safely ({type(exc).__name__}).\n')
        sys.exit(1)
