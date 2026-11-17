"""`ahl trace-import`: convert one named external trajectory into a new private directory of AHL metadata."""
import argparse
import ctypes
import errno
import json
import os
from pathlib import Path
import secrets

from agent_harness_lab.evaluation.integrity import open_directory_nofollow, read_regular
from agent_harness_lab.private_journal import encoded
from agent_harness_lab.read_only import require_writable
from agent_harness_lab.telemetry.schema import serialize
from . import atif
from .mapping import Rejected

FORMATS = {'atif': atif}
EVENTS, REPORT = 'events.v2.jsonl', 'conversion-report.json'


def load(path, limits):
    """The one authorized source, read once through the no-follow, single-link, bounded reader."""
    try:
        if os.lstat(path).st_size > limits.max_bytes:
            raise Rejected('input_too_large', 'input')
        return read_regular(path, limits.max_bytes)
    except Rejected:
        raise
    except (OSError, ValueError):
        raise Rejected('input_unavailable', 'input') from None


def _write_at(directory, name, raw):
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def publish(output, files):
    """Create a private output without following links or replacing an existing destination."""
    output = Path(output).absolute()
    if '..' in output.parts or any(parent.is_symlink() for parent in (output, *output.parents)):
        raise Rejected('unsafe_output', 'output')
    if os.path.lexists(output):
        raise Rejected('output_exists', 'output')
    parent_fd = stage_fd = None
    stage_name = None
    try:
        library = ctypes.CDLL(None, use_errno=True)
        rename = getattr(library, 'renameat2', None)
        flags = 1  # Linux RENAME_NOREPLACE
        if rename is None:
            rename, flags = getattr(library, 'renameatx_np', None), 4  # macOS RENAME_EXCL
        if rename is None: raise OSError(errno.ENOTSUP, 'atomic no-replace rename unavailable')
        require_writable(output)
        parent_fd = open_directory_nofollow(output.parent)
        stage_name = '.trace-import-' + secrets.token_hex(16)
        os.mkdir(stage_name, 0o700, dir_fd=parent_fd)
        stage_fd = os.open(stage_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
        for name, raw in files.items():
            if name not in (EVENTS, REPORT): raise ValueError('unexpected output file')
            _write_at(stage_fd, name, raw)
        os.fsync(stage_fd)
        current = open_directory_nofollow(output.parent)
        try:
            expected, actual = os.fstat(parent_fd), os.fstat(current)
            if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
                raise Rejected('unsafe_output', 'output')
        finally:
            os.close(current)
        stage, named = os.fstat(stage_fd), os.stat(stage_name, dir_fd=parent_fd, follow_symlinks=False)
        if (stage.st_dev, stage.st_ino) != (named.st_dev, named.st_ino): raise Rejected('unsafe_output', 'output')
        if rename(parent_fd, os.fsencode(stage_name), parent_fd, os.fsencode(output.name), flags) != 0:
            error = ctypes.get_errno()
            if error in (errno.EEXIST, errno.ENOTEMPTY): raise Rejected('output_exists', 'output')
            raise OSError(error, 'atomic no-replace rename failed')
        try: os.fsync(parent_fd)
        except OSError: raise Rejected('output_durability_unknown', 'output') from None
    except (OSError, ValueError) as error:
        if isinstance(error, Rejected): raise
        raise Rejected('output_unavailable', 'output') from None
    finally:
        if stage_fd is not None: os.close(stage_fd)
        if parent_fd is not None: os.close(parent_fd)


def import_trace(format_name, source, output, limits=atif.Limits()):
    """Convert, then write events and the loss report; returns the report. Raises Rejected on refusal; a post-publication sync failure leaves a named output with unknown durability."""
    if format_name not in FORMATS:
        raise Rejected('unsupported_format', 'format')
    events, report = FORMATS[format_name].convert(load(source, limits), limits)
    publish(output, {EVENTS: ''.join(serialize(row) + '\n' for row in events).encode(), REPORT: encoded(report.to_dict())})
    return report.to_dict()


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ahl trace-import',
        description='Convert one explicitly named external trajectory file into AHL metadata events and a loss report in a new private directory. '
            'Only allowlisted metadata is kept: message bodies, reasoning, raw tool arguments and results, costs and extension objects are dropped and '
            'counted, tool names and call ids survive only as digests, tool inputs as case-salted fingerprints, and the task outcome and lineage stay unknown. Imported evidence is private '
            'and is not permission to publish.',
        epilog='Example: ahl trace-import --format atif --input trajectory.json --output ./new-private-import\n'
            'Writes OUTPUT (events.v2.jsonl and conversion-report.json, mode 0600 in a 0700 directory); a failed write can leave a private staging directory. Reads only INPUT: it opens no referenced '
            'media, subagent or continuation file, contacts no URL and opens no socket. JSON report on stdout. Exit: 0 converted; 2 rejected input '
            '(typed reason on stderr; output_durability_unknown means output was published but its durability is unknown).')
    parser.add_argument('--format', required=True, choices=sorted(FORMATS), help='Source trajectory format; atif accepts exactly ATIF-v1.8 and rejects every other schema version')
    parser.add_argument('--input', type=Path, required=True, help='Existing authorized trajectory file, a single regular file read once and never copied')
    parser.add_argument('--output', type=Path, required=True, help='New private output directory; it must not already exist')
    args = parser.parse_args(argv)
    try:
        report = import_trace(args.format, args.input, args.output)
    except Rejected as error:
        parser.exit(2, 'trace-import rejected: ' + str(error) + '\n')
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0
