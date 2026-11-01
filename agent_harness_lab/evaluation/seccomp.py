"""Deny HugeTLB allocation, which may bypass the ordinary memory controller."""
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import platform
import re
import struct
import sys


def ordinary_filesystems(roots):
    # Existing HugeTLB files can be mapped without the MAP_HUGETLB flag.
    with Path('/proc/self/mountinfo').open('rb') as stream:
        raw = stream.read(1024*1024+1)
    if len(raw) > 1024*1024:
        raise ValueError('mount inventory exceeds storage policy bound')
    roots = [Path(root).resolve() for root in roots]
    for line in raw.decode().splitlines():
        before, after = line.split(' - ', 1)
        if after.split()[0] == 'hugetlbfs':
            mount = Path(re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), before.split()[4])).resolve()
            if any(root.is_relative_to(mount) or mount.is_relative_to(root) for root in roots):
                raise ValueError('HugeTLB-backed runtime and role inputs are unsupported')


def program():
    architecture = platform.machine()
    supported = {'x86_64': (0xc000003e, (319, 29, 9)),
                 'aarch64': (0xc00000b7, (279, 194, 222))}
    if architecture not in supported or sys.byteorder != 'little':
        raise ValueError('storage syscall policy requires Linux x86_64 or aarch64')
    audit, calls = supported[architecture]
    # Check the ABI first; x32/compat syscalls must not bypass native numbers.
    code = [(0x20, 0, 0, 4), (0x15, 1, 0, audit), (0x06, 0, 0, 0x80000000),
            (0x20, 0, 0, 0), (0x35, 0, 1, 0x40000000), (0x06, 0, 0, 0x80000000)]
    for call, offset, flag in zip(calls, (24, 32, 40), (4, 0o4000, 0x40000)):
        code.extend([(0x20, 0, 0, 0), (0x15, 0, 3, call), (0x20, 0, 0, offset),
                     (0x45, 0, 1, flag), (0x06, 0, 0, 0x50000|errno.EPERM)])
    code.append((0x06, 0, 0, 0x7fff0000))
    return b''.join(struct.pack('HBBI', *instruction) for instruction in code)


def sealed_filter():
    raw = program()
    descriptor = os.memfd_create('ahl-storage-policy', os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
    try:
        if os.write(descriptor, raw) != len(raw) or os.pread(descriptor, len(raw), 0) != raw:
            raise ValueError('incomplete storage syscall policy')
        # Linux UAPI values; portable Python builds may omit the symbolic names.
        fcntl.fcntl(descriptor, 1033, 0x0f)  # ADD_SEALS: WRITE, GROW, SHRINK, SEAL.
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor, dict(name='seccomp-deny-hugetlb-v1', architecture=platform.machine(),
                                sha256=hashlib.sha256(raw).hexdigest())
    except BaseException:
        os.close(descriptor)
        raise
