"""One explicitly selected Hindsight ZIP, parsed in memory without extracting files."""
import io
import stat
import struct
import zlib
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile
from agent_harness_lab.evaluation.integrity import contained, read_regular, regular_sha256, relative
from .hardened import HostileReport, limits, load_json
from .hindsight import LIMITS, PINNED, admitted, archive_snapshot, entries, parse, text
from .schema import digest, label
from .snapshot import LocalExport

PROVIDER = 'hindsight-bank-zip'
SOURCE_VERSION = 'v1'
MAX_ZIP_ENTRIES = 20000
EOCD = struct.Struct('<4s4H2LH')
LOCAL = struct.Struct('<4s5H3L2H')


def _archive(raw):
    """Bound and validate the classic ZIP directory before ZipFile allocates its entry objects."""
    start = max(0, len(raw)-65557)
    position = raw.rfind(b'PK\x05\x06', start)
    if position < 0 or position+EOCD.size > len(raw): raise ValueError('missing ZIP directory')
    signature, disk, directory_disk, on_disk, count, size, offset, comment = EOCD.unpack_from(raw, position)
    if (signature != b'PK\x05\x06' or disk or directory_disk or on_disk != count or count == 65535
            or count > MAX_ZIP_ENTRIES or size == 0xffffffff or offset == 0xffffffff
            or position+EOCD.size+comment != len(raw) or offset+size != position):
        raise ValueError('unsupported or oversized ZIP directory')
    cursor, found = offset, 0
    while cursor < position:
        if cursor+46 > position or raw[cursor:cursor+4] != b'PK\x01\x02':
            raise ValueError('invalid ZIP directory entry')
        name_length, extra_length, comment_length = struct.unpack_from('<HHH', raw, cursor+28)
        cursor += 46 + name_length + extra_length + comment_length
        found += 1
        if cursor > position or found > count: raise ValueError('invalid ZIP directory size')
    if cursor != position or found != count: raise ValueError('invalid ZIP directory count')
    return ZipFile(io.BytesIO(raw), 'r')


def _extra(value):
    cursor = 0
    while cursor < len(value):
        if cursor+4 > len(value): raise ValueError('invalid ZIP extra field')
        kind, length = struct.unpack_from('<HH', value, cursor)
        cursor += 4+length
        if kind == 1 or cursor > len(value): raise ValueError('unsupported ZIP extra field')


def _members(zf):
    """Refuse ambiguous paths, links, encryption, and unsupported compression without extracting."""
    names, offsets = {}, set()
    for member in zf.infolist():
        name = member.filename
        parts = name.rstrip('/').split('/')
        mode = member.external_attr >> 16
        kind = stat.S_IFMT(mode)
        _extra(member.extra)
        if (not name or name != member.orig_filename or name.startswith('/') or '\\' in name or ':' in name
                or any(ord(c) < 32 or ord(c) == 127 for c in name)
                or any(p in ('', '.', '..') for p in parts) or name in names or member.header_offset in offsets
                or member.header_offset < 0 or member.header_offset >= zf.start_dir
                or member.flag_bits & 1 or member.compress_type not in (ZIP_STORED, ZIP_DEFLATED)
                or kind not in (0, stat.S_IFREG, stat.S_IFDIR) or member.is_dir() != (kind == stat.S_IFDIR) and kind != 0):
            raise ValueError('unsafe Hindsight ZIP member')
        names[name] = member
        offsets.add(member.header_offset)
    return names


def _member_bytes(zf, member, raw):
    """Validate local headers and exact bounded decompression, including stream end and CRC."""
    with zf.open(member): pass  # ZipFile checks the local filename against the central directory.
    offset = member.header_offset
    if offset < 0 or offset+LOCAL.size > zf.start_dir: raise ValueError('invalid ZIP local header')
    signature, _, flags, method, _, _, _, _, _, name_length, extra_length = LOCAL.unpack_from(raw, offset)
    start = offset+LOCAL.size+name_length+extra_length
    end = start+member.compress_size
    if (signature != b'PK\x03\x04' or flags != member.flag_bits or method != member.compress_type
            or start > end or end > zf.start_dir):
        raise ValueError('invalid ZIP member boundaries')
    _extra(raw[offset+LOCAL.size+name_length:start])
    compressed = raw[start:end]
    if method == ZIP_STORED:
        data = compressed
    else:
        decoder = zlib.decompressobj(-15)
        data = decoder.decompress(compressed, member.file_size+1)
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('invalid ZIP compressed stream')
    if len(data) != member.file_size or zlib.crc32(data) != member.CRC:
        raise ValueError('invalid ZIP member checksum or size')
    return data


class HindsightZipArchive(LocalExport):
    """Parse one bounded ZIP; validate its selected bank before reading any document."""

    def read(self, source):
        if (source.kind, source.provider, source.version) != ('local_file', PROVIDER, SOURCE_VERSION):
            raise ValueError('unsupported Hindsight ZIP source')
        descriptor, selection = self.selection(source)
        fields = {'schema_version', 'hindsight_version', 'instance', 'bank_id', 'archive'}
        if (not isinstance(selection, dict) or set(selection)-{'limits'} != fields
                or type(selection['schema_version']) is not int or selection['schema_version'] != 1
                or text(selection['bank_id']) is None
                or digest(selection) != source.scope_sha256):
            raise ValueError('invalid Hindsight ZIP selection')
        version = selection['hindsight_version']
        if not isinstance(version, str) or version not in PINNED: raise ValueError('unsupported Hindsight release')
        label(selection['instance']); relative(selection['archive'])
        bounds = limits(selection.get('limits'), LIMITS)
        left = dict(max_json_containers=bounds['max_json_containers'], max_json_values=bounds['max_json_values'])
        decoded = []
        def consume(position, raw, sha256):
            total, records = 0, 1
            with _archive(raw) as zf:
                members = _members(zf)
                def decode(name):
                    nonlocal total
                    member = members.get(name)
                    if member is None or member.is_dir(): raise ValueError('missing Hindsight ZIP member')
                    if member.file_size > bounds['max_file_bytes']: raise HostileReport('Hindsight ZIP file bound exceeded')
                    total += member.file_size
                    if total > bounds['max_archive_bytes']: raise HostileReport('Hindsight ZIP byte bound exceeded')
                    data = _member_bytes(zf, member, raw)
                    spent = []
                    value = load_json(data, dict(bounds, **left), 'Hindsight ZIP', spent=spent)
                    left['max_json_containers'] -= spent[0][0]; left['max_json_values'] -= spent[0][1]
                    return value
                manifest = decode('manifest.json')
                names = entries(manifest, bounds)
                if manifest['source_bank_id'] != selection['bank_id']: raise PermissionError('Hindsight archive is for another bank')
                parts = {'manifest.json': manifest}
                for name in names[1:]:
                    value = decode(name); records += admitted(name, value)
                    if records > bounds['max_rows']: raise HostileReport('Hindsight row bound exceeded')
                    parts[name] = value
                decoded.append(parts)
        try:
            archive_hash, = self.capture(source, descriptor, selection, [contained(self.manifest.parent, selection['archive'])],
                bounds['max_archive_bytes'], consume, 'Hindsight ZIP changed during capture', read_regular, regular_sha256)
        except (BadZipFile, EOFError, zlib.error, struct.error, OverflowError):
            raise ValueError('invalid Hindsight ZIP archive') from None
        self.claims, self.summary = parse(decoded.pop(), selection, version, bounds)
        self.summary['source_form'] = 'caller_supplied_zip'
        self.summary['warnings'] = sorted(set(self.summary['warnings']) | {'archive_origin_unverified'})
        native = digest(('hindsight_zip', selection['instance'], selection['bank_id'], selection['archive']))
        return archive_snapshot(source, self.claims, self.summary, native, archive_hash)
