"""Synthetic Hindsight ZIP captures; no extraction, server or backend access."""
import contextlib
import copy
import io
import json
import stat
import struct
import warnings
import zlib
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources import hindsight_zip
from agent_harness_lab.sources.hardened import limits
from agent_harness_lab.sources.hindsight import LIMITS, parse
from agent_harness_lab.sources.hindsight_zip import HindsightZipArchive
from agent_harness_lab.sources.schema import SourceSpec, digest
from tests.unit.sources.test_source_hindsight import PARTS, SECRET


def pack(parts):
    out = io.BytesIO()
    with ZipFile(out, 'w', compression=ZIP_DEFLATED) as zf:
        for name, value in parts:
            zf.writestr(name, value if isinstance(value, (bytes, str)) else json.dumps(value))
    return out.getvalue()


@pytest.fixture
def source(tmp_path):
    archive = tmp_path/'bank.zip'; archive.write_bytes(pack(PARTS.items()))
    selection = dict(schema_version=1, hindsight_version='1.1.0', instance='synthetic-instance',
        bank_id='fixture-bank', archive='bank.zip')
    spec = SourceSpec('local_file', 'hindsight-bank-zip', 'v1', digest(str(tmp_path.resolve())),
        digest('synthetic-project'), digest(selection), identity_provenance='source_declared_id', enabled=True)
    (tmp_path/'selection.json').write_text(json.dumps(selection))
    manifest = tmp_path/'source.json'
    manifest.write_text(json.dumps(dict(schema_version=1, source=spec.to_dict(), data='selection.json')))
    return archive, selection, spec, manifest


def test_zip_claims_are_private_partial_and_unverified(source):
    _, selection, spec, manifest = source
    connector = HindsightZipArchive(manifest); snapshot = connector.read(spec)
    assert connector.summary['claims'] == dict(retained_document=2, extracted_fact=3, fact_relation=1,
        consolidated_observation=1, invalidated_fact=1)
    assert connector.summary['source_form'] == 'caller_supplied_zip'
    expected, _ = parse(PARTS, selection, '1.1.0', limits(None, LIMITS))
    assert connector.claims == expected
    assert 'archive_origin_unverified' in connector.summary['warnings']
    assert all((r.causal_status, r.independence, r.capture_completeness) == ('unknown', 'unknown', 'partial') for r in snapshot.records)
    for private in (SECRET, 'fixture-bank', 'doc-alpha', 'unit-1', 'bank.zip'):
        assert private not in repr((connector.claims, connector.summary, snapshot))


def test_wrong_bank_is_refused_before_opening_documents(source):
    archive, _, spec, manifest = source
    parts = copy.deepcopy(PARTS); parts['manifest.json']['source_bank_id'] = 'other-bank'
    archive.write_bytes(pack(parts.items()))
    opened, original = [], ZipFile.open
    def recording(zf, item, *args, **kwargs):
        opened.append(item.filename if isinstance(item, ZipInfo) else item)
        return original(zf, item, *args, **kwargs)
    with patch.object(ZipFile, 'open', recording):
        with pytest.raises(PermissionError): HindsightZipArchive(manifest).read(spec)
    assert opened == ['manifest.json']


@pytest.mark.parametrize('bad', ['../escape', '/absolute', 'documents/../escape', 'documents\\escape', 'C:escape', 'line\nbreak'])
def test_unsafe_member_names_are_refused(source, bad):
    archive, _, spec, manifest = source
    archive.write_bytes(pack([*PARTS.items(), (bad, '{}')]))
    with pytest.raises(ValueError): HindsightZipArchive(manifest).read(spec)


def test_duplicate_and_symlink_members_are_refused(source):
    archive, _, spec, manifest = source
    with pytest.warns(UserWarning): archive.write_bytes(pack([*PARTS.items(), ('manifest.json', PARTS['manifest.json'])]))
    with pytest.raises(ValueError): HindsightZipArchive(manifest).read(spec)
    out = io.BytesIO()
    with ZipFile(out, 'w') as zf:
        for name, data in PARTS.items(): zf.writestr(name, json.dumps(data))
        info = ZipInfo('link'); info.create_system = 3; info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, 'target')
    archive.write_bytes(out.getvalue())
    with pytest.raises(ValueError): HindsightZipArchive(manifest).read(spec)


def test_spoofed_directory_count_fails_before_zipfile_open(source):
    archive, _, spec, manifest = source
    raw = bytearray(archive.read_bytes()); end = raw.rfind(b'PK\x05\x06')
    struct.pack_into('<HH', raw, end+8, 1, 1); archive.write_bytes(raw)
    with patch('agent_harness_lab.sources.hindsight_zip.ZipFile', side_effect=AssertionError('unbounded open')):
        with pytest.raises(ValueError): HindsightZipArchive(manifest).read(spec)


def test_zip64_directory_and_encrypted_entry_are_refused(source):
    archive, _, spec, manifest = source
    raw = bytearray(archive.read_bytes()); end = raw.rfind(b'PK\x05\x06')
    struct.pack_into('<HH', raw, end+8, 65535, 65535); archive.write_bytes(raw)
    with pytest.raises(ValueError): HindsightZipArchive(manifest).read(spec)
    raw = bytearray(pack(PARTS.items())); directory = raw.find(b'PK\x01\x02')
    flags, = struct.unpack_from('<H', raw, directory+8)
    struct.pack_into('<H', raw, directory+8, flags | 1); archive.write_bytes(raw)
    with pytest.raises(ValueError): HindsightZipArchive(manifest).read(spec)


def test_duplicate_local_offsets_are_refused_without_filename_warning(source):
    archive, _, spec, manifest = source
    raw = bytearray(archive.read_bytes()); first = raw.find(b'PK\x01\x02'); second = raw.find(b'PK\x01\x02', first+4)
    original_offset, = struct.unpack_from('<L', raw, first+42)
    struct.pack_into('<L', raw, second+42, original_offset); archive.write_bytes(raw)
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        with pytest.raises(ValueError): HindsightZipArchive(manifest).read(spec)


def test_forged_uncompressed_size_and_crc_cannot_hide_trailing_content(source):
    archive, _, spec, manifest = source
    prefix = json.dumps(PARTS['manifest.json']).encode()
    raw = bytearray(pack([('manifest.json', prefix+b'forged trailing bytes'),
                          *((name, value) for name, value in PARTS.items() if name != 'manifest.json')]))
    directory = raw.find(b'PK\x01\x02')
    struct.pack_into('<L', raw, directory+16, zlib.crc32(prefix))
    struct.pack_into('<L', raw, directory+24, len(prefix))
    archive.write_bytes(raw)
    with pytest.raises(ValueError): HindsightZipArchive(manifest).read(spec)


def test_missing_and_corrupt_members_fail_closed(source):
    archive, _, spec, manifest = source
    archive.write_bytes(pack((name, data) for name, data in PARTS.items() if name != 'documents/000001.json'))
    with pytest.raises(ValueError): HindsightZipArchive(manifest).read(spec)
    raw = bytearray(pack(PARTS.items())); raw[35] ^= 255; archive.write_bytes(raw)
    with pytest.raises(ValueError): HindsightZipArchive(manifest).read(spec)


def test_member_size_and_archive_mutation_are_checked(source):
    archive, selection, spec, manifest = source
    selection['limits'] = {'max_file_bytes': 64}
    (manifest.parent/'selection.json').write_text(json.dumps(selection))
    spec = SourceSpec(**{**spec.to_dict(), 'scope_sha256': digest(selection)})
    manifest.write_text(json.dumps(dict(schema_version=1, source=spec.to_dict(), data='selection.json')))
    with pytest.raises(ValueError): HindsightZipArchive(manifest).read(spec)
    del selection['limits']; (manifest.parent/'selection.json').write_text(json.dumps(selection))
    spec = SourceSpec(**{**spec.to_dict(), 'scope_sha256': digest(selection)})
    manifest.write_text(json.dumps(dict(schema_version=1, source=spec.to_dict(), data='selection.json')))
    with patch('agent_harness_lab.sources.hindsight_zip.regular_sha256', return_value='0'*64):
        with pytest.raises(ValueError): HindsightZipArchive(manifest).read(spec)
    assert archive.exists()


def test_compressed_oversize_document_is_refused_before_decompression(source):
    archive, selection, spec, manifest = source
    parts = copy.deepcopy(PARTS); parts['documents/000000.json']['original_text'] = 'x'*1000000
    archive.write_bytes(pack(parts.items()))
    assert archive.stat().st_size < 10000
    selection['limits'] = {'max_file_bytes': 1024}
    (manifest.parent/'selection.json').write_text(json.dumps(selection))
    spec = SourceSpec(**{**spec.to_dict(), 'scope_sha256': digest(selection)})
    manifest.write_text(json.dumps(dict(schema_version=1, source=spec.to_dict(), data='selection.json')))
    with patch('agent_harness_lab.sources.hindsight_zip._member_bytes', wraps=hindsight_zip._member_bytes) as read:
        with pytest.raises(ValueError): HindsightZipArchive(manifest).read(spec)
    assert [call.args[1].filename for call in read.call_args_list] == ['manifest.json']


def test_cli_preview_import_is_offline_and_private(source, tmp_path):
    _, _, spec, manifest = source
    cache = tmp_path/'cache.json'
    def invoke(op):
        out, err = io.StringIO(), io.StringIO()
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(['sources', op, '--manifest', str(manifest), '--project-sha256', spec.project_sha256,
                         '--scope-sha256', spec.scope_sha256, '--cache', str(cache)])
        assert all(s not in out.getvalue()+err.getvalue() for s in (SECRET, 'fixture-bank', 'bank.zip'))
        return code, json.loads(out.getvalue())
    code, preview = invoke('preview')
    assert code == 0 and preview['hindsight_zip_summary']['source_form'] == 'caller_supplied_zip'
    assert invoke('import')[0] == 0 and invoke('import')[1]['imported'] == 0
    assert SECRET not in cache.read_text()
