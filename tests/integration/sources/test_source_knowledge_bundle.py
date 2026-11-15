"""Synthetic extracted bundles on disk: allowlisted, bounded, read-only, with every limitation observable."""
import importlib.util
import io
import json
import os
import socket
import stat
import struct
import subprocess
import sys
import zlib
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest

from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources import knowledge_bundle
from agent_harness_lab.sources.knowledge_bundle import Bundle, BundleLimit, Manifest, read, read_zip, summary
from agent_harness_lab.sources.knowledge_links import links
from agent_harness_lab.sources.knowledge_origins import clusters
from agent_harness_lab.sources.okf import MissingOKFDependency

needs_yaml = pytest.mark.skipif(not importlib.util.find_spec('yaml'), reason='optional OKF extra required')
SECRET = 'SYNTHETIC_SECRET'


def put(root, files):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode())
    return root


def reasons(bundle):
    return sorted((f['path'], f['reason']) for f in bundle.findings)


def paths(bundle):
    return [p.path for p in bundle.pages]


def note(body='body', **fields):
    meta = ''.join(f'{key}: {value}\n' for key, value in {'type': 'Note', **fields}.items())
    return f'---\n{meta}---\n{body}\n'


@pytest.fixture
def root(tmp_path):
    (tmp_path / 'bundle').mkdir()
    return tmp_path / 'bundle'


def whole(**options):
    return Manifest(roots=('.',), **options)


class TestPlainMarkdown:
    def test_pages_without_frontmatter_are_read_with_empty_metadata_and_no_dependency(self, root, monkeypatch):
        monkeypatch.setitem(sys.modules, 'yaml', None)  # the standard-library default must not need the parser
        put(root, {'README.md': f'# Title {SECRET}\n', 'docs/guide.md': 'Plain [[README]].\n'})
        bundle = read(root, whole())
        assert paths(bundle) == ['README.md', 'docs/guide.md'] and [p.metadata for p in bundle.pages] == [{}, {}]
        assert bundle.findings == () and bundle.okf_version == 'not_applicable'

    def test_a_frontmatter_page_without_the_optional_parser_fails_closed(self, root, monkeypatch):
        monkeypatch.setitem(sys.modules, 'yaml', None)
        put(root, {'a.md': note()})
        with pytest.raises(MissingOKFDependency):
            read(root, whole())

    def test_pages_are_sorted_and_the_read_is_deterministic(self, root):
        put(root, {'z.md': 'z', 'a/b.md': 'b', 'a.md': 'a', 'm.md': 'm'})
        first, second = read(root, whole()), read(root, whole())
        assert paths(first) == sorted(paths(first)) and first == second and first.sha256 == second.sha256

    def test_a_changed_page_changes_the_bundle_fingerprint(self, root):
        put(root, {'a.md': 'one'})
        before = read(root, whole()).sha256
        put(root, {'a.md': 'two'})
        assert read(root, whole()).sha256 != before

    def test_non_markdown_and_hidden_entries_are_counted_but_never_read(self, root):
        put(root, {'a.md': 'a', 'image.png': b'\x89PNG', 'notes.txt': 'x', '.git/config.md': 'x', '.hidden.md': 'x',
                   '.obsidian/workspace.md': 'x'})
        bundle = read(root, whole())
        assert paths(bundle) == ['a.md'] and bundle.non_markdown_files == 2 and bundle.hidden_entries == 3

    @needs_yaml
    def test_a_utf8_bom_before_the_fence_is_not_a_page_without_metadata(self, root):
        put(root, {'a.md': b'\xef\xbb\xbf' + note().encode()})
        assert read(root, whole()).pages[0].metadata == {'type': 'Note'}

    def test_undecodable_text_is_a_finding_and_other_pages_are_still_read(self, root):
        put(root, {'bad.md': b'\xff\xfe\x00', 'good.md': 'ok'})
        bundle = read(root, whole())
        assert paths(bundle) == ['good.md'] and reasons(bundle) == [('bad.md', 'not_utf8')]


@needs_yaml
class TestOKFBundle:
    @pytest.fixture(autouse=True)
    def _files(self, root):
        put(root, {
            'index.md': '---\nokf_version: "0.2"\n---\n# Index\n* [Orders](tables/orders.md) - table\n',
            'log.md': '# Directory Update Log\n\n## 2026-05-22\n* **Update**: x\n',
            'tables/orders.md': note('See [customers](/tables/customers.md) and [[missing-page]].',
                title='Orders', resource='https://example.invalid/orders', custom_field='kept'),
            'tables/customers.md': note('Customers.', status='deprecated'),
            'tables/index.md': '# Tables\n',
        })

    def test_valid_fields_and_unknown_keys_round_trip_and_reserved_files_are_set_aside(self, root):
        bundle = read(root, whole(format='okf-0.2'))
        orders = next(p for p in bundle.pages if p.path == 'tables/orders.md')
        assert orders.metadata == {'type': 'Note', 'title': 'Orders', 'resource': 'https://example.invalid/orders',
                                   'custom_field': 'kept'}
        assert paths(bundle) == ['tables/customers.md', 'tables/orders.md']
        assert bundle.okf_version == 'supported' and bundle.reserved_files == 3 and bundle.findings == ()

    def test_edges_between_pages_keep_missing_targets_observable(self, root):
        bundle = read(root, whole(format='okf-0.2'))
        status = {(e.source, e.target): e.status for e in links(((p.path, p.body) for p in bundle.pages), root_relative=True)}
        assert status == {('tables/orders.md', '/tables/customers.md'): 'resolved',
                          ('tables/orders.md', 'missing-page'): 'missing'}

    @pytest.mark.parametrize('index, version, reason', [
        pytest.param(None, 'missing', 'okf_version_missing', id='no-index'),
        pytest.param('# Index only\n', 'missing', 'okf_version_missing', id='index-without-frontmatter'),
        pytest.param('---\nokf_version: "0.3"\n---\n', 'unsupported', 'okf_version_unsupported', id='future-version'),
        pytest.param('---\nokf_version: 0.2\n---\n', 'unsupported', 'okf_version_unsupported', id='unquoted-number-is-not-the-declared-text'),
        pytest.param('---\nokf_version: [x\n---\n', 'unreadable', 'okf_version_unreadable', id='malformed-frontmatter'),
    ])
    def test_unknown_or_absent_version_is_a_limitation_and_the_concepts_are_still_read(self, root, index, version, reason):
        (root / 'index.md').unlink()
        if index is not None: put(root, {'index.md': index})
        bundle = read(root, whole(format='okf-0.2'))
        assert bundle.okf_version == version and reason in [f['reason'] for f in bundle.findings]
        assert paths(bundle) == ['tables/customers.md', 'tables/orders.md']

    def test_a_concept_without_a_type_is_kept_and_reported(self, root):
        put(root, {'untyped.md': '---\ntitle: x\n---\nbody\n', 'bare.md': 'no frontmatter\n', 'blank.md': '---\ntype: "  "\n---\n'})
        bundle = read(root, whole(format='okf-0.2'))
        assert {'untyped.md', 'bare.md', 'blank.md'} <= set(paths(bundle))
        assert reasons(bundle) == [('bare.md', 'frontmatter_missing'), ('blank.md', 'type_missing'), ('untyped.md', 'type_missing')]

    def test_reserved_names_are_only_reserved_in_the_okf_format(self, root):
        assert 'index.md' in paths(read(root, whole())) and 'log.md' in paths(read(root, whole()))


@needs_yaml
class TestMalformedFrontmatter:
    @pytest.mark.parametrize('text, reason', [
        pytest.param('---\ntype: Note\nbody without a closing fence\n', 'frontmatter_unterminated', id='unterminated'),
        pytest.param('---\ntype: [unclosed\n---\n', 'frontmatter_unsupported_yaml', id='broken-yaml'),
        pytest.param('---\n- a\n- b\n---\n', 'frontmatter_not_a_mapping', id='list-root'),
        pytest.param('---\nplain scalar\n---\n', 'frontmatter_not_a_mapping', id='scalar-root'),
        pytest.param('---\n---\n', 'frontmatter_not_a_mapping', id='empty-block'),
        pytest.param('---\nbase: &anchor {a: 1}\ncopy: *anchor\n---\n', 'frontmatter_unsupported_yaml', id='anchor-and-alias'),
        pytest.param('---\ntype: !!python/object/apply:os.system [echo hi]\n---\n', 'frontmatter_unsupported_yaml', id='python-object-tag'),
        pytest.param('---\ntype: Note\ntype: Other\n---\n', 'frontmatter_unsupported_yaml', id='duplicate-key'),
        pytest.param('---\n1: Note\n---\n', 'frontmatter_unsupported_yaml', id='non-text-key'),
        pytest.param('--- \ntype: Note\n---\n', 'frontmatter_fence_padded', id='padded-opening-fence'),
    ])
    def test_malformed_or_unsafe_frontmatter_is_reported_and_never_guessed(self, root, text, reason):
        put(root, {'bad.md': text, 'good.md': note()})
        bundle = read(root, whole())
        assert paths(bundle) == ['good.md'] and reasons(bundle) == [('bad.md', reason)]

    def test_a_yaml_tag_never_runs_code(self, root, tmp_path):
        marker = tmp_path / 'ran'
        put(root, {'bad.md': f'---\ntype: !!python/object/apply:subprocess.call [[touch, {marker}]]\n---\n'})
        read(root, whole())
        assert not marker.exists()


@needs_yaml
class TestAllowlist:
    @pytest.fixture(autouse=True)
    def _files(self, root):
        put(root, {'README.md': 'readme', 'docs/adr/0001.md': note(), 'docs/runbook.md': note(),
                   'private/secret.md': note(SECRET), 'docs-extra.md': 'outside'})

    def test_only_listed_roots_are_read(self, root):
        bundle = read(root, Manifest(roots=('README.md', 'docs')))
        assert paths(bundle) == ['README.md', 'docs/adr/0001.md', 'docs/runbook.md']

    def test_a_root_is_a_directory_boundary_not_a_text_prefix(self, root):
        assert 'docs-extra.md' not in paths(read(root, Manifest(roots=('docs',))))

    def test_a_missing_root_is_a_finding_not_an_empty_success(self, root):
        bundle = read(root, Manifest(roots=('docs', 'wiki')))
        assert reasons(bundle) == [('wiki', 'root_missing')] and paths(bundle)

    def test_a_root_that_is_not_markdown_or_a_directory_yields_nothing(self, root):
        put(root, {'data.json': '{}'})
        bundle = read(root, Manifest(roots=('data.json',)))
        assert bundle.pages == () and bundle.non_markdown_files == 1

    def test_declared_types_admit_only_listed_types_and_report_the_rest(self, root):
        put(root, {'docs/other.md': note(type='Playbook'), 'docs/bare.md': 'no frontmatter'})
        bundle = read(root, Manifest(roots=('docs',), doc_types=('Note',)))
        assert paths(bundle) == ['docs/adr/0001.md', 'docs/runbook.md']
        assert reasons(bundle) == [('docs/bare.md', 'frontmatter_missing'), ('docs/other.md', 'type_not_allowed')]


class TestRefusedEntries:
    def test_a_symlinked_page_is_refused(self, root, tmp_path):
        put(root, {'a.md': 'a'})
        outside = tmp_path / 'outside.md'; outside.write_text(SECRET)
        (root / 'link.md').symlink_to(outside)
        with pytest.raises(ValueError, match='symlinks'):
            read(root, whole())

    def test_a_symlinked_directory_is_refused(self, root, tmp_path):
        outside = tmp_path / 'outside'; put(outside, {'x.md': SECRET})
        (root / 'dir').symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError, match='symlinks'):
            read(root, whole())

    def test_a_symlink_hidden_behind_an_allowed_root_path_is_refused(self, root, tmp_path):
        outside = tmp_path / 'outside'; put(outside, {'x.md': SECRET})
        (root / 'docs').symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError):
            read(root, Manifest(roots=('docs',)))

    def test_a_symlinked_bundle_root_is_refused(self, root, tmp_path):
        link = tmp_path / 'link'; link.symlink_to(root, target_is_directory=True)
        with pytest.raises(ValueError, match='regular directory'):
            read(link, whole())

    def test_a_missing_or_file_bundle_root_is_refused(self, root):
        put(root, {'a.md': 'a'})
        for target in (root / 'absent', root / 'a.md'):
            with pytest.raises(ValueError, match='regular directory'):
                read(target, whole())

    def test_a_hardlinked_page_is_refused(self, root, tmp_path):
        put(root, {'a.md': 'a'})
        os.link(root / 'a.md', tmp_path / 'alias.md')
        with pytest.raises(ValueError, match='hardlinks'):
            read(root, whole())

    def test_a_named_pipe_is_refused_without_being_opened(self, root):
        put(root, {'a.md': 'a'})
        os.mkfifo(root / 'pipe.md')
        with pytest.raises(ValueError, match='special files'):
            read(root, whole())

    def test_a_manifest_object_is_required(self, root):
        with pytest.raises(ValueError, match='manifest'):
            read(root, {'roots': ['.']})

    def test_a_page_name_that_is_not_a_safe_relative_path_is_a_finding(self, root):
        put(root, {'good.md': 'ok', 'back\\slash.md': 'x'})
        bundle = read(root, whole())
        assert paths(bundle) == ['good.md'] and reasons(bundle) == [('back\\slash.md', 'path_unsafe')]


class TestBounds:
    def test_an_oversized_page_is_a_finding_and_is_never_read(self, root):
        put(root, {'big.md': 'x' * 11, 'small.md': 'ok'})
        bundle = read(root, whole(max_page_bytes=10))
        assert paths(bundle) == ['small.md'] and reasons(bundle) == [('big.md', 'page_too_large')]

    def test_a_page_exactly_at_the_bound_is_read(self, root):
        put(root, {'edge.md': 'x' * 10})
        assert paths(read(root, whole(max_page_bytes=10))) == ['edge.md']

    def test_too_many_pages_raise_instead_of_truncating(self, root):
        put(root, {f'{i}.md': 'x' for i in range(3)})
        with pytest.raises(BundleLimit):
            read(root, whole(max_pages=2))

    def test_exactly_the_page_bound_is_accepted(self, root):
        put(root, {f'{i}.md': 'x' for i in range(2)})
        assert len(read(root, whole(max_pages=2)).pages) == 2

    def test_the_aggregate_byte_bound_raises_instead_of_truncating(self, root):
        put(root, {'a.md': 'x' * 6, 'b.md': 'x' * 6})
        with pytest.raises(BundleLimit):
            read(root, whole(max_bytes=11))

    def test_too_many_directory_entries_raise_before_any_page_is_read(self, root, monkeypatch):
        monkeypatch.setattr(knowledge_bundle, 'MAX_ENTRIES', 3)
        put(root, {f'{i}.png': b'x' for i in range(4)})
        with pytest.raises(BundleLimit):
            read(root, whole())

    def test_a_tree_deeper_than_the_bound_raises(self, root, monkeypatch):
        monkeypatch.setattr(knowledge_bundle, 'MAX_DEPTH', 2)
        put(root, {'a/b/c/d.md': 'x'})
        with pytest.raises(BundleLimit):
            read(root, whole())

    def test_a_page_larger_than_its_bound_does_not_count_toward_the_aggregate(self, root):
        put(root, {'big.md': 'x' * 100, 'a.md': 'ok'})
        assert paths(read(root, whole(max_page_bytes=10, max_bytes=10))) == ['a.md']


@needs_yaml
class TestInertContent:
    def test_attested_computation_fields_are_inert_text_and_nothing_runs(self, root, tmp_path):
        marker = tmp_path / 'ran'
        command = f'{sys.executable} -c "open({str(marker)!r}, \'w\')"'
        put(root, {'revenue.md': ('---\ntype: Attested Computation\nruntime: python\n'
                                  f'executor:\n  resource: {json.dumps(command)}\nattester:\n  resource: {json.dumps(command)}\n---\n'
                                  f'# Computation\n\n    import os; os.system({command!r})\n<script>x()</script>\n![](http://example.invalid/x.png)\n')})
        bundle = read(root, whole(format='okf-0.2'))
        assert not marker.exists()
        page, = bundle.pages
        assert page.metadata['executor']['resource'] == command and 'os.system' in page.body
        assert clusters([(page.path, page.metadata)])[0]['flags'] == {'inert_execution_claim': 1}

    def test_reading_opens_no_network_connection_and_launches_no_process(self, root, monkeypatch):
        put(root, {'a.md': note('[remote](https://example.invalid/x) ![i](http://example.invalid/i.png)')})
        def forbidden(*args, **kwargs): raise AssertionError('reading must stay offline and inert')
        monkeypatch.setattr(socket, 'socket', forbidden)
        monkeypatch.setattr(subprocess, 'Popen', forbidden)
        assert paths(read(root, whole())) == ['a.md']

    def test_reading_leaves_the_bundle_unchanged(self, root):
        put(root, {'a.md': note(), 'b.md': 'x'})
        before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in root.iterdir()}
        read(root, whole())
        assert {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in root.iterdir()} == before


@needs_yaml
class TestCorrelatedOrigins:
    def test_pages_summarizing_one_source_stay_one_origin_and_an_uncited_page_stays_unknown(self, root):
        cited = '---\ntype: Session Summary\nsources:\n  - resource: session://synthetic-1\n---\n'
        put(root, {'a.md': cited + 'one', 'b.md': cited + 'two', 'c.md': note('none')})
        bundle = read(root, whole())
        found = clusters((p.path, p.metadata) for p in bundle.pages)
        assert [(c['paths'], c['basis']) for c in found] == [(['a.md', 'b.md'], 'shared_cited_source'), (['c.md'], 'no_cited_source')]


@needs_yaml
class TestSummary:
    def test_summary_reports_counts_only_and_stays_unpublishable(self, root):
        put(root, {'index.md': '---\nokf_version: "0.2"\n---\n', 'secret/SYNTHETIC_PATH.md': note(SECRET, status='draft'),
                   'bad.md': '---\n[\n---\n', 'image.png': b'x'})
        report = summary(read(root, whole(format='okf-0.2')))
        text = json.dumps(report)
        assert SECRET not in text and 'SYNTHETIC_PATH' not in text
        assert report['pages'] == 1 and report['finding_reasons'] == {'frontmatter_unsupported_yaml': 1}
        assert report['okf_version'] == 'supported' and report['reserved_files'] == 1 and report['non_markdown_files'] == 1
        assert report['lifecycle_claims']['draft'] == 1 and report['trust_claims']['unverified'] == 1
        assert not public_export_allowed([report])

    def test_a_bundle_value_is_immutable(self, root):
        put(root, {'a.md': 'x'})
        bundle = read(root, whole())
        assert isinstance(bundle, Bundle)
        with pytest.raises(AttributeError):
            bundle.pages = ()


def pack(files, compression=ZIP_DEFLATED):
    out = io.BytesIO()
    with ZipFile(out, 'w', compression=compression) as zf:
        for name, value in files:
            zf.writestr(name, value)
    return out.getvalue()


@pytest.fixture
def archive(tmp_path):
    return tmp_path / 'bundle.zip'


class TestArchive:
    def test_an_explicit_archive_is_read_in_memory_and_matches_the_extracted_directory(self, root, archive):
        files = {'README.md': 'readme [[guide]]', 'docs/guide.md': 'guide', 'docs/image.png': b'x', '.git/config': 'x'}
        put(root, files)
        archive.write_bytes(pack(files.items()))
        from_directory, from_archive = read(root, whole()), read_zip(archive, whole())
        assert from_archive.pages == from_directory.pages and from_archive.sha256 == from_directory.sha256
        assert (from_archive.source_form, from_directory.source_form) == ('archive', 'directory')
        assert (from_archive.non_markdown_files, from_archive.hidden_entries) == (1, 1)

    def test_nothing_is_extracted_to_disk(self, archive, tmp_path):
        archive.write_bytes(pack([('a.md', 'a')]))
        read_zip(archive, whole())
        assert sorted(p.name for p in tmp_path.iterdir()) == ['bundle.zip']

    def test_stored_members_and_roots_inside_the_archive_are_honoured(self, archive):
        archive.write_bytes(pack([('wiki/Home.md', 'home'), ('wiki/sub/Page.md', 'page'), ('other/x.md', 'x')], ZIP_STORED))
        assert paths(read_zip(archive, Manifest(roots=('wiki',)))) == ['wiki/Home.md', 'wiki/sub/Page.md']

    def test_a_root_is_a_member_boundary_not_a_text_prefix(self, archive):
        archive.write_bytes(pack([('docs/a.md', 'a'), ('docs-extra/b.md', 'b'), ('docs.md', 'c')]))
        assert paths(read_zip(archive, Manifest(roots=('docs',)))) == ['docs/a.md']

    def test_an_empty_directory_root_exists_but_a_missing_root_is_a_finding(self, archive):
        out = io.BytesIO()
        with ZipFile(out, 'w') as zf:
            zf.writestr('docs/', ''); zf.writestr('a.md', 'a')
        archive.write_bytes(out.getvalue())
        bundle = read_zip(archive, Manifest(roots=('docs', 'wiki', 'a.md')))
        assert paths(bundle) == ['a.md'] and reasons(bundle) == [('wiki', 'root_missing')]

    @needs_yaml
    def test_the_manifest_allowlist_and_okf_rules_apply_to_archive_members(self, archive):
        archive.write_bytes(pack([('index.md', '---\nokf_version: "0.2"\n---\n'), ('a.md', note()), ('b.md', note(type='Other')),
                                  ('c.md', '---\n[\n---\n')]))
        bundle = read_zip(archive, whole(format='okf-0.2', doc_types=('Note',)))
        assert paths(bundle) == ['a.md'] and bundle.okf_version == 'supported'
        assert reasons(bundle) == [('b.md', 'type_not_allowed'), ('c.md', 'frontmatter_unsupported_yaml')]

    @pytest.mark.parametrize('name', [
        pytest.param('../escape.md', id='parent-traversal'),
        pytest.param('/absolute.md', id='absolute'),
        pytest.param('docs/../escape.md', id='embedded-traversal'),
        pytest.param('docs\\escape.md', id='backslash'),
        pytest.param('C:escape.md', id='drive-prefix'),
        pytest.param('line\nbreak.md', id='control-character'),
        pytest.param('./dot.md', id='dot-segment'),
        pytest.param('a//b.md', id='empty-segment'),
    ])
    def test_an_unsafe_member_name_refuses_the_whole_archive_even_outside_the_roots(self, archive, name):
        archive.write_bytes(pack([('docs/ok.md', 'ok'), (name, 'x')]))
        with pytest.raises(ValueError, match='invalid or unsafe'):
            read_zip(archive, Manifest(roots=('docs',)))

    def test_a_duplicate_member_refuses_the_archive(self, archive):
        with pytest.warns(UserWarning):
            archive.write_bytes(pack([('a.md', 'one'), ('a.md', 'two')]))
        with pytest.raises(ValueError, match='invalid or unsafe'):
            read_zip(archive, whole())

    def test_a_symlink_member_refuses_the_archive(self, archive):
        out = io.BytesIO()
        with ZipFile(out, 'w') as zf:
            zf.writestr('a.md', 'a')
            info = ZipInfo('link.md'); info.create_system = 3; info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, 'a.md')
        archive.write_bytes(out.getvalue())
        with pytest.raises(ValueError, match='invalid or unsafe'):
            read_zip(archive, whole())

    def test_an_encrypted_member_refuses_the_archive(self, archive):
        raw = bytearray(pack([('a.md', 'a')])); directory = raw.find(b'PK\x01\x02')
        flags, = struct.unpack_from('<H', raw, directory + 8)
        struct.pack_into('<H', raw, directory + 8, flags | 1)
        archive.write_bytes(raw)
        with pytest.raises(ValueError, match='invalid or unsafe'):
            read_zip(archive, whole())

    def test_a_spoofed_directory_count_refuses_the_archive(self, archive):
        raw = bytearray(pack([('a.md', 'a')])); end = raw.rfind(b'PK\x05\x06')
        struct.pack_into('<HH', raw, end + 8, 5, 5)
        archive.write_bytes(raw)
        with pytest.raises(ValueError, match='invalid or unsafe'):
            read_zip(archive, whole())

    @pytest.mark.parametrize('content', [b'', b'not a zip at all', b'PK\x05\x06' + b'\0' * 18], ids=['empty', 'garbage', 'empty-directory-only'])
    def test_a_file_that_is_not_a_usable_archive_is_refused_or_empty_never_guessed(self, archive, content):
        archive.write_bytes(content)
        try: bundle = read_zip(archive, whole())
        except ValueError: return
        assert bundle.pages == ()

    def test_a_compression_bomb_is_a_finding_and_is_never_decompressed(self, archive, monkeypatch):
        archive.write_bytes(pack([('bomb.md', 'x' * 5_000_000), ('ok.md', 'ok')]))
        assert archive.stat().st_size < 10_000
        calls = []
        from agent_harness_lab.sources import hindsight_zip
        original = hindsight_zip._member_bytes
        monkeypatch.setattr(hindsight_zip, '_member_bytes', lambda zf, member, raw: calls.append(member.filename) or original(zf, member, raw))
        bundle = read_zip(archive, whole(max_page_bytes=1024))
        assert paths(bundle) == ['ok.md'] and reasons(bundle) == [('bomb.md', 'page_too_large')] and calls == ['ok.md']

    def test_declared_sizes_above_the_aggregate_bound_raise_before_any_decompression(self, archive):
        archive.write_bytes(pack([('a.md', 'x' * 600), ('b.md', 'x' * 600)]))
        assert archive.stat().st_size < 1000
        with pytest.raises(BundleLimit):
            read_zip(archive, whole(max_bytes=1000))

    def test_a_forged_declared_size_cannot_hide_extra_content(self, archive):
        prefix = b'short'
        raw = bytearray(pack([('a.md', prefix + b' plus forged trailing bytes')])); directory = raw.find(b'PK\x01\x02')
        struct.pack_into('<L', raw, directory + 16, zlib.crc32(prefix))
        struct.pack_into('<L', raw, directory + 24, len(prefix))
        archive.write_bytes(raw)
        with pytest.raises(ValueError, match='invalid or unsafe'):
            read_zip(archive, whole())

    def test_a_corrupted_member_refuses_the_archive(self, archive):
        raw = bytearray(pack([('a.md', 'a' * 200)])); raw[35] ^= 255
        archive.write_bytes(raw)
        with pytest.raises(ValueError, match='invalid or unsafe'):
            read_zip(archive, whole())

    def test_too_many_pages_in_an_archive_raise_instead_of_truncating(self, archive):
        archive.write_bytes(pack([(f'{i}.md', 'x') for i in range(3)]))
        with pytest.raises(BundleLimit):
            read_zip(archive, whole(max_pages=2))

    def test_an_archive_larger_than_the_byte_bound_is_refused_unread(self, archive):
        archive.write_bytes(pack([('a.md', 'x' * 100)]))
        with pytest.raises(ValueError):
            read_zip(archive, whole(max_bytes=archive.stat().st_size - 1))

    def test_a_symlinked_archive_path_is_refused(self, archive, tmp_path):
        archive.write_bytes(pack([('a.md', 'a')]))
        link = tmp_path / 'link.zip'; link.symlink_to(archive)
        with pytest.raises(ValueError):
            read_zip(link, whole())

    def test_a_manifest_object_is_required(self, archive):
        archive.write_bytes(pack([('a.md', 'a')]))
        with pytest.raises(ValueError, match='manifest'):
            read_zip(archive, {'roots': ['.']})

    def test_archive_summary_says_it_was_opened_in_memory_and_never_extracted(self, archive):
        archive.write_bytes(pack([('a.md', 'a')]))
        report = summary(read_zip(archive, whole()))
        assert report['source_form'] == 'archive' and report['archive_opened'] is True and report['archive_extracted'] is False
        assert not public_export_allowed([report])
