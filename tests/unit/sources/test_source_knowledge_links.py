"""Synthetic Markdown knowledge bundles: every link is an observable edge, never a fetch or a guess."""
import importlib.util
import json
import time
from pathlib import Path

import pytest

from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources.knowledge_links import (KINDS, MAX_LINKS_PER_PAGE, MAX_PAGES, PROFILE, STATUSES,
    LinkLimit, links, summary)

SECRET = 'SYNTHETIC_LINK_TEXT'


def edge_map(edges):
    return {(edge.source, edge.target): (edge.status, edge.reason, edge.resolved) for edge in edges}


@pytest.fixture
def bundle():
    return {
        'index.md': '# Index\nSee [[alpha]], [[notes/beta|Beta]] and [[gamma#Section]].\n',
        'notes/alpha.md': f'Back to [the index](../index.md "title") and [{SECRET}](beta.md#part).\n',
        'notes/beta.md': 'An [ADR](../decisions/0001-record.md) and [[alpha]].\n',
        'decisions/0001-record.md': '# Record\nStatus: accepted\n',
    }


def test_wikilinks_and_relative_links_resolve_to_bundle_pages(bundle):
    edges = edge_map(links(bundle.items()))
    assert edges[('index.md', 'alpha')] == ('resolved', 'page_name', 'notes/alpha.md')
    assert edges[('index.md', 'notes/beta|Beta')] == ('resolved', 'bundle_path', 'notes/beta.md')
    assert edges[('index.md', 'gamma#Section')] == ('missing', 'no_page', None)
    assert edges[('notes/alpha.md', '../index.md')] == ('resolved', 'relative_path', 'index.md')
    assert edges[('notes/alpha.md', 'beta.md#part')] == ('resolved', 'relative_path', 'notes/beta.md')
    assert edges[('notes/beta.md', '../decisions/0001-record.md')] == ('resolved', 'relative_path', 'decisions/0001-record.md')


def test_edges_keep_line_and_kind_provenance():
    edge, = links([('page.md', 'first\n\nsecond ![[diagram.png]]\n')])
    assert (edge.source, edge.line, edge.kind) == ('page.md', 3, 'wikilink_embed')
    assert (edge.status, edge.reason) == ('unknown', 'non_page_target')


@pytest.mark.parametrize('target, expected', [
    ('../outside.md', ('outside_bundle', 'parent_traversal')),
    ('a/../../../outside.md', ('outside_bundle', 'parent_traversal')),
    ('/docs/page.md', ('unknown', 'root_relative_path')),
    ('https://example.invalid/page.md', ('remote', 'scheme')),
    ('mailto:someone@example.invalid', ('remote', 'scheme')),
    ('//example.invalid/page.md', ('remote', 'network_path')),
    ('image.png', ('unknown', 'non_page_target')),
    ('folder/', ('unknown', 'non_page_target')),
    ('Other.MD', ('unknown', 'non_page_target')),
    ('OTHER.md', ('missing', 'case_only_match')),
    ('%ff.md', ('invalid', 'bad_percent_encoding')),
    ('a%5Cb.md', ('invalid', 'unsupported_character')),
])
def test_markdown_targets_that_are_not_bundle_pages_stay_observable(target, expected):
    edge = next(e for e in links([('top.md', f'[x]({target})'), ('other.md', '')]) if e.source == 'top.md')
    assert (edge.status, edge.reason, edge.resolved) == (*expected, None)


def test_remote_links_are_opaque_and_never_fetched(monkeypatch):
    import socket, urllib.request
    def forbidden(*args, **kwargs): raise AssertionError('network access')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    edges = links([('page.md', 'See <https://example.invalid/x> and [y](http://example.invalid/y).\n')])
    assert [(e.kind, e.status) for e in edges] == [('markdown', 'remote'), ('autolink', 'remote')]
    assert summary(edges)['remote_fetched'] is False


def test_percent_encoded_space_resolves_once_without_double_decoding():
    pages = [('a.md', '[x](my%20page.md) [y](my%2520page.md)'), ('my page.md', ''), ('my%20page.md', '')]
    edges = edge_map(links(pages))
    assert edges[('a.md', 'my%20page.md')][2] == 'my page.md'
    assert edges[('a.md', 'my%2520page.md')][2] == 'my%20page.md'


def test_ambiguous_page_names_are_not_resolved_by_arrival_order():
    pages = [('a/note.md', ''), ('b/note.md', ''), ('index.md', '[[note]] [[a/note]] [[x/note]]')]
    for order in (pages, list(reversed(pages))):
        edges = edge_map(links(order))
        assert edges[('index.md', 'note')] == ('ambiguous', 'several_pages', None)
        assert edges[('index.md', 'a/note')] == ('resolved', 'bundle_path', 'a/note.md')
        assert edges[('index.md', 'x/note')] == ('missing', 'no_page', None)


def test_path_suffix_resolves_only_when_unique():
    pages = [('deep/a/note.md', ''), ('deep/b/note.md', ''), ('index.md', '[[a/note]] [[deep/note]]')]
    edges = edge_map(links(pages))
    assert edges[('index.md', 'a/note')] == ('resolved', 'path_suffix', 'deep/a/note.md')
    assert edges[('index.md', 'deep/note')] == ('missing', 'no_page', None)


@pytest.mark.parametrize('target, expected', [
    ('#Heading', ('resolved', 'same_page_anchor', 'page.md')),
    ('', ('invalid', 'empty_target', None)),
    ('./sibling', ('unknown', 'relative_wikilink', None)),
    ('../up', ('unknown', 'relative_wikilink', None)),
    ('/rooted', ('unknown', 'root_relative_path', None)),
    ('PAGE', ('missing', 'case_only_match', None)),
    ('page.md', ('resolved', 'page_name', 'page.md')),
])
def test_wikilink_edge_cases(target, expected):
    edges = links([('page.md', f'[[{target}]]' if target else '[[ ]]')])
    assert (edges[0].status, edges[0].reason, edges[0].resolved) == expected


def test_table_escaped_alias_separator_is_an_alias():
    edge, = links([('a.md', '| [[b\\|Shown]] |'), ('b.md', '')])
    assert (edge.status, edge.resolved) == ('resolved', 'b.md')


def test_code_fences_code_spans_and_escapes_are_not_links():
    body = ('```\n[[fenced]] [a](fenced.md)\n```\n'
            '~~~~ python\n[[tilde]]\n~~~\nstill [[inside]]\n~~~~\n'
            'Use `[[span]]` and ``[b](span.md)`` here, \\[[escaped]] and \\[c](esc.md).\n'
            '[[after]]\n')
    assert [(e.line, e.target) for e in links([('page.md', body)])] == [(10, 'after')]


def test_unclosed_fence_hides_the_rest_of_the_page():
    assert links([('page.md', 'before [[one]]\n```\n[[two]]\n')])[0].target == 'one'
    assert len(links([('page.md', 'before [[one]]\n```\n[[two]]\n')])) == 1


def test_image_inside_link_text_is_two_edges():
    edges = links([('page.md', '[![badge](badge.svg)](other.md)'), ('other.md', '')])
    assert [(e.kind, e.target, e.status) for e in edges] == [
        ('markdown', 'other.md', 'resolved'), ('markdown_image', 'badge.svg', 'unknown')]


@pytest.mark.parametrize('body, targets', [
    ('[x](<my page.md> "title")', ['my page.md']),
    ("[x](a.md 'single')", ['a.md']),
    ('[x](a_(b).md)', ['a_(b).md']),
    ('[x](a(b(c(d))).md)', ['a(b(c(d))).md']),
    ('[x](a(b(c(d(e)))).md)', []),
    ('[x](a(b.md)', []),
    ('[x](a.md "unterminated)', []),
    ('[x](a.md trailing)', []),
    ('[x]("quoted")', ['"quoted"']),
    ('[x] (a.md)', []),
])
def test_inline_destination_grammar(body, targets):
    assert [edge.target for edge in links([('page.md', body)])] == targets


def test_reference_definitions_are_edges_but_footnotes_are_not():
    body = '[ref]: notes/a.md "Title"\n[^1]: a footnote\n   [angle]: <notes/b c.md>\n'
    edges = links([('page.md', body), ('notes/a.md', ''), ('notes/b c.md', '')])
    assert [(e.kind, e.resolved) for e in edges] == [
        ('reference_definition', 'notes/a.md'), ('reference_definition', 'notes/b c.md')]


def test_loops_and_self_links_are_counted_not_collapsed():
    pages = [('a.md', '[[b]] [self](a.md) [anchor](#top)'), ('b.md', '[[a]]'), ('orphan.md', '')]
    edges = links(pages)
    report = summary(edges, [path for path, _ in pages])
    assert report['self_links'] == 2
    assert report['distinct_resolved_targets'] == 2
    assert report['pages_without_inbound'] == 1
    assert report['statuses']['resolved'] == 4


def test_summary_holds_counts_only_and_is_never_publishable(bundle):
    report = summary(links(bundle.items()), bundle)
    text = json.dumps(report)
    for private in (SECRET, 'notes/', 'alpha', '0001-record'):
        assert private not in text
    assert report['profile'] == PROFILE
    assert set(report['statuses']) == set(STATUSES) and set(report['kinds']) == set(KINDS)
    assert report['links'] == sum(report['statuses'].values()) == 7
    assert (report['files_read'], report['truth_established'], report['publication_allowed']) == (False, False, False)
    assert not public_export_allowed([report])
    approved = dict(source_kind='synthetic', approved_publication=True, source_ref='fixture', derived_from_private=False)
    assert not public_export_allowed([dict(report, publication_allowed=True, provenance=approved)])


def test_edges_are_reproducible_across_page_order(bundle):
    forward = sorted(links(bundle.items()), key=lambda e: (e.source, e.line, e.target))
    backward = sorted(links(reversed(list(bundle.items()))), key=lambda e: (e.source, e.line, e.target))
    assert forward == backward


@pytest.mark.parametrize('path', ['', '/abs.md', '../up.md', 'a/../b.md', './a.md', 'a//b.md', 'a\\b.md', 'page.txt', 'a/.md/..'])
def test_page_paths_must_be_normalized_and_inside_the_bundle(path):
    with pytest.raises(ValueError, match='page paths'):
        links([(path, '')])


def test_duplicate_paths_and_non_text_bodies_are_refused():
    with pytest.raises(ValueError, match='duplicate'):
        links([('a.md', ''), ('a.md', '')])
    with pytest.raises(ValueError, match='text'):
        links([('a.md', b'[[b]]')])


def test_bounds_refuse_instead_of_truncating():
    with pytest.raises(LinkLimit, match='pages'):
        links((f'p{i}.md', '') for i in range(MAX_PAGES + 1))
    with pytest.raises(LinkLimit, match='links'):
        links([('a.md', '[[b]]' * (MAX_LINKS_PER_PAGE + 1))])
    with pytest.raises(LinkLimit, match='large'):
        links([('a.md', 'x' * ((1 << 20) + 1))])
    edge, = links([('a.md', '[x](' + 'a' * 3000 + '.md)')])
    assert (edge.status, edge.reason) == ('invalid', 'target_too_long')


@pytest.mark.parametrize('body', [
    ('[' * 200_000) + ('[a' * 100_000) + ('](' * 100_000),
    '[a](<' * 200_000, '[a](x (' * 140_000, '[a](b' * 200_000, '<ab:' + 'x' * 1_000_000,
    ''.join('`' * k + ' ' for k in range(2, 900)) + '` ' * 200_000,
])
def test_pathological_lines_stay_linear(body):
    started = time.perf_counter()
    assert links([('a.md', body[:1 << 20])]) == ()
    assert time.perf_counter() - started < 5


def test_html_and_executable_content_are_never_interpreted():
    body = '<a href="other.md">x</a>\n<script>fetch("https://example.invalid")</script>\n'
    assert links([('a.md', body), ('other.md', '')]) == ()


@pytest.mark.skipif(not importlib.util.find_spec('yaml'), reason='optional OKF extra required')
def test_okf_export_pages_feed_the_link_reader(tmp_path):
    from agent_harness_lab.sources.okf import OKFExport
    from agent_harness_lab.sources.schema import SourceSpec, digest
    bundle = tmp_path/'bundle'
    (bundle/'concepts').mkdir(parents=True)
    (bundle/'index.md').write_text('---\nokf_version: "0.2"\n---\n# Index\n')
    (bundle/'_meta.md').write_text('---\ntype: Scope Manifest\nproject: synthetic-project\n---\n')
    (bundle/'concepts/a.md').write_text('---\ntype: Concept\n---\nSee [b](b.md), [[b]] and [out](../../x.md).\n')
    (bundle/'concepts/b.md').write_text('---\ntype: Concept\nrelated: "[[a]]"\n---\nNo links.\n')
    source = SourceSpec('local_file', 'ai-memory-okf', '0.2', digest(str(tmp_path.resolve())), digest('synthetic-project'),
        digest('workspace'), identity_provenance='source_declared_id', enabled=True)
    manifest = tmp_path/'source.json'
    manifest.write_text(json.dumps(dict(schema_version=1, source=source.to_dict(), data='bundle')))
    pages = OKFExport(manifest).pages(source)
    edges = links((page.path, page.body) for page in pages)
    assert [(e.source, e.status, e.resolved) for e in edges] == [
        ('concepts/a.md', 'resolved', 'concepts/b.md'), ('concepts/a.md', 'resolved', 'concepts/b.md'),
        ('concepts/a.md', 'outside_bundle', None)]
    assert Path(bundle/'concepts/a.md').read_text().count('[[b]]') == 1
