"""One report read from inside a caller-supplied CI artifact ZIP, in memory: nothing is extracted, fetched or nested."""
import hashlib
import struct
import zlib
from zipfile import BadZipFile
from agent_harness_lab.evaluation.integrity import relative
from .hardened import HostileReport
from .hindsight_zip import _archive, _member_bytes, _members  # the hardened classic-ZIP validators, shared
from .schema import digest


def report_identity(path, member=None):
    """The fingerprint naming one selected report: its file path, or its archive path and member."""
    return digest(path) if member is None else digest([path, member])


def check_reports(reports, formats, name):
    """Validate a selection's report declarations: path and format, plus an optional archive member; every one is unique."""
    for report in reports:
        if (not isinstance(report, dict) or {'path', 'format'} - set(report) or set(report) - {'path', 'format', 'member'}
                or report['format'] not in formats):
            raise ValueError('invalid '+name+' declaration')
        relative(report['path'])
        if 'member' in report: relative(report['member'])
    if len({(r['path'], r.get('member')) for r in reports}) != len(reports): raise ValueError('duplicate '+name+' path')


def report_bytes(raw, sha256, report, limit, warnings):
    """Return (bytes, SHA-256) of the report: the file itself, or its declared member when the file is an artifact ZIP.

    A member is bound by its own bytes, so the same report in another archive keeps one revision. Any archive that is
    not a plain, unencrypted, link-free classic ZIP with safe names, or whose member lies about its size or checksum,
    is refused whole as HostileReport; the archive is never extracted and a member is never opened as an archive.
    """
    if 'member' not in report: return raw, sha256
    warnings.add('archive_origin_unverified')
    try:
        with _archive(raw) as zf:
            member = _members(zf).get(report['member'])
            if member is not None:
                if member.file_size > limit: raise HostileReport('artifact ZIP member bound exceeded')
                data = _member_bytes(zf, member, raw)
                return data, hashlib.sha256(data).hexdigest()
    except HostileReport: raise
    except (ValueError, BadZipFile, EOFError, zlib.error, struct.error, OverflowError):
        raise HostileReport('artifact ZIP is malformed or unsafe') from None
    raise ValueError('artifact ZIP lacks the declared member')
