"""Provenance-preserving link edges between the Markdown pages of one selected knowledge bundle.

links() reads `[[wikilinks]]`, `![[embeds]]`, inline Markdown links and images, reference definitions and
`<scheme:...>` autolinks from page bodies an authorized caller already holds, such as OKFExport.pages, an LLM
wiki, a Markdown knowledge graph or an ADR directory. It never opens a file, follows a link or fetches a URL:
a remote link is an opaque reference. Links inside fenced code blocks and one-line code spans are not edges.
Every link stays visible with one of STATUSES, so a missing, ambiguous, escaping or unsupported target is
reported instead of dropped or guessed. summary() keeps only counts; link text and paths stay in memory.
"""
from collections import Counter, deque
from dataclasses import dataclass
import posixpath
import re
from urllib.parse import unquote

PROFILE = 'knowledge-links-v1'
KINDS = ('wikilink', 'wikilink_embed', 'markdown', 'markdown_image', 'reference_definition', 'autolink')
STATUSES = ('resolved', 'missing', 'ambiguous', 'outside_bundle', 'remote', 'unknown', 'invalid')
MAX_PAGES = 10_000
MAX_PAGE_CHARS = 1 << 20
MAX_LINKS_PER_PAGE = 10_000
MAX_TARGET_CHARS = 2048
UNSUPPORTED = ('html_links', 'indented_code_blocks', 'multiline_code_spans', 'reference_link_usages',
               'nested_link_text', 'site_root_paths', 'frontmatter_links')
_FENCE = re.compile(r' {0,3}(`{3,}|~{3,})(.*)$')
_WIKI = re.compile(r'(!?)\[\[([^\[\]\n]{1,%d})\]\]' % MAX_TARGET_CHARS)
_INLINE = re.compile(r'(!?)\[(?:[^\[\]\n]|\[[^\[\]\n]*\]){0,1000}\]\(')
_DEFINITION = re.compile(r' {0,3}\[([^\[\]\n^][^\[\]\n]{0,998})\]:[ \t]*(<[^<>\n]*>|\S+)')
_AUTOLINK = re.compile(r'<([A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*)>')
_SCHEME = re.compile(r'[A-Za-z][A-Za-z0-9+.-]*:')
_ESCAPED = re.compile(r'\\[\[\]!<`]')
_ANGLE = re.compile(r'<([^<>\n]{0,%d})>' % MAX_TARGET_CHARS)
# CommonMark requires at least three levels of balanced parentheses in a bare destination.
_BARE = re.compile(r'(?:[^\s()]|\((?:[^\s()]|\((?:[^\s()]|\([^\s()]*\))*\))*\))*')
_TITLE = re.compile(r'"[^"\n]{0,1000}"|\'[^\'\n]{0,1000}\'|\([^()\n]{0,1000}\)')


class LinkLimit(ValueError):
    """A bundle, page or link count exceeds the reader's fixed bounds; nothing is partially returned."""


@dataclass(frozen=True)
class Edge:
    """One link occurrence: the page and line it was read from, its raw target and how it resolved."""
    source: str
    line: int
    kind: str
    target: str
    status: str
    reason: str
    resolved: str | None = None


def links(pages):
    """Edges of (path, body) pairs; paths are distinct bundle-relative POSIX `.md` paths, bodies Markdown text."""
    found, paths = [], set()
    for number, page in enumerate(pages, 1):
        if number > MAX_PAGES: raise LinkLimit('too many pages')
        path, body = page
        _check_path(path)
        if path in paths: raise ValueError('duplicate page path')
        if not isinstance(body, str): raise ValueError('page body must be text')
        if len(body) > MAX_PAGE_CHARS: raise LinkLimit('page too large')
        paths.add(path)
        occurrences = list(_occurrences(body))
        if len(occurrences) > MAX_LINKS_PER_PAGE: raise LinkLimit('too many links on one page')
        found.extend((path, *occurrence) for occurrence in occurrences)
    index = _Index(paths)
    return tuple(Edge(source, line, kind, target, *index.resolve(source, kind, target))
                 for source, line, kind, target in found)


def summary(edges, pages=None):
    """Counts-only report of edges; pass the page paths to count pages nothing resolves to."""
    edges = tuple(edges)
    resolved = {edge.resolved for edge in edges if edge.status == 'resolved' and edge.resolved != edge.source}
    report = dict(profile=PROFILE, links=len(edges), statuses={s: 0 for s in STATUSES}, kinds={k: 0 for k in KINDS},
        reasons=dict(sorted(Counter(edge.reason for edge in edges).items())),
        pages_with_links=len({edge.source for edge in edges}), distinct_resolved_targets=len(resolved),
        self_links=sum(edge.status == 'resolved' and edge.resolved == edge.source for edge in edges),
        pages_without_inbound=None if pages is None else len(set(pages) - resolved),
        unsupported=list(UNSUPPORTED), files_read=False, remote_fetched=False, link_text_included=False,
        truth_established=False, authority='none', publication_allowed=False)
    for edge in edges:
        report['statuses'][edge.status] += 1; report['kinds'][edge.kind] += 1
    return report


def _check_path(path):
    if (not isinstance(path, str) or not path or len(path) > 1024 or not path.endswith('.md')
            or path.startswith('/') or any(c in path for c in '\\\0\n\r') or posixpath.normpath(path) != path
            or '..' in path.split('/')):
        raise ValueError('page paths must be normalized bundle-relative .md paths')


def _occurrences(body):
    """(line, kind, raw target) of each link outside fenced code, in reading order."""
    fence = None
    for number, line in enumerate(body.split('\n'), 1):
        match = _FENCE.match(line)
        if fence is not None:
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence) and not match.group(2).strip():
                fence = None
            continue
        if match and not (match.group(1)[0] == '`' and '`' in match.group(2)):
            fence = match.group(1); continue
        yield from ((number, kind, target) for kind, target in _line_links(_mask(line)))


def _mask(line):
    """The line with escaped link characters and one-line code spans blanked, keeping its length."""
    line = _ESCAPED.sub('  ', line)
    runs = list(re.finditer(r'`+', line))
    later = {}
    for number, run in enumerate(runs): later.setdefault(len(run.group()), deque()).append(number)
    pieces, kept, index = [], 0, 0
    while index < len(runs):
        same = later[len(runs[index].group())]
        while same and same[0] <= index: same.popleft()
        if not same: index += 1; continue  # an unmatched backtick run is literal text
        closing = same.popleft()
        start, end = runs[index].start(), runs[closing].end()
        pieces += [line[kept:start], ' ' * (end - start)]; kept, index = end, closing + 1
    return ''.join(pieces) + line[kept:]


def _line_links(line):
    definition = _DEFINITION.match(line)
    if definition:
        target = definition.group(2)
        yield 'reference_definition', target[1:-1] if target.startswith('<') else target
        return
    for match in _WIKI.finditer(line):
        yield 'wikilink_embed' if match.group(1) else 'wikilink', match.group(2)
    line = _WIKI.sub(lambda m: ' ' * len(m.group()), line)
    position = 0
    while (match := _INLINE.search(line, position)) is not None:
        position = match.start() + len(match.group(1)) + 1  # an image inside link text is read on its own
        target = _destination(line, match.end())
        if target is not None: yield 'markdown_image' if match.group(1) else 'markdown', target
    for match in _AUTOLINK.finditer(line): yield 'autolink', match.group(1)


def _destination(line, start):
    """The destination of an inline link whose '(' ends at start, or None when the text is not a link."""
    index = start
    while index < len(line) and line[index] in ' \t': index += 1
    if line[index:index + 1] == '<':
        angle = _ANGLE.match(line, index)
        if angle is None: return None
        target, index = angle.group(1), angle.end()
    else:
        bare = _BARE.match(line, index)
        target, index = bare.group(), bare.end()
    after = index
    while index < len(line) and line[index] in ' \t': index += 1
    if index > after and line[index:index + 1] in ('"', "'", '('):
        title = _TITLE.match(line, index)
        if title is None: return None
        index = title.end()
        while index < len(line) and line[index] in ' \t': index += 1
    return target if line[index:index + 1] == ')' else None


class _Index:
    def __init__(self, paths):
        self.paths = paths
        self.folded = {path.casefold() for path in paths}
        self.names = {}
        for path in paths: self.names.setdefault(posixpath.basename(path), []).append(path)

    def resolve(self, source, kind, target):
        """(status, reason, resolved path or None) of one raw target."""
        if len(target) > MAX_TARGET_CHARS: return 'invalid', 'target_too_long', None
        if kind == 'autolink': return 'remote', 'scheme', None
        if kind.startswith('wikilink'): return self._wiki(source, target)
        return self._markdown(source, target)

    def _wiki(self, source, target):
        name = target.replace('\\|', '|').partition('|')[0]
        name, _, anchor = name.partition('#')
        name = name.strip()
        if not name: return ('resolved', 'same_page_anchor', source) if anchor.strip() else ('invalid', 'empty_target', None)
        parts = name.split('/')
        if name.startswith('/'): return 'unknown', 'root_relative_path', None
        if any(part in ('.', '..', '') for part in parts): return 'unknown', 'relative_wikilink', None
        wanted = name if name.endswith('.md') else name + '.md'
        if '/' in name:
            if wanted in self.paths: return 'resolved', 'bundle_path', wanted
            candidates = sorted(path for path in self.paths if path.endswith('/' + wanted))
        else:
            candidates = sorted(self.names.get(wanted, ()))
        if len(candidates) == 1: return 'resolved', 'path_suffix' if '/' in name else 'page_name', candidates[0]
        if candidates: return 'ambiguous', 'several_pages', None
        if any(path.casefold() == wanted.casefold() or path.casefold().endswith('/' + wanted.casefold())
               for path in self.paths):
            return 'missing', 'case_only_match', None
        if posixpath.splitext(posixpath.basename(name))[1] not in ('', '.md'):
            return 'unknown', 'non_page_target', None
        return 'missing', 'no_page', None

    def _markdown(self, source, target):
        if target.startswith('//'): return 'remote', 'network_path', None
        if _SCHEME.match(target): return 'remote', 'scheme', None
        if target.startswith('#'): return 'resolved', 'same_page_anchor', source
        path = re.split(r'[?#]', target, maxsplit=1)[0]
        try: path = unquote(path, errors='strict')
        except UnicodeDecodeError: return 'invalid', 'bad_percent_encoding', None
        if any(c in path for c in '\\\0\n\r'): return 'invalid', 'unsupported_character', None
        if not path: return ('resolved', 'same_page_query', source) if target else ('invalid', 'empty_target', None)
        if path.startswith('/'): return 'unknown', 'root_relative_path', None
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(source), path))
        if joined == '..' or joined.startswith('../'): return 'outside_bundle', 'parent_traversal', None
        if path.endswith('/') or not joined.endswith('.md'): return 'unknown', 'non_page_target', None
        if joined in self.paths: return 'resolved', 'relative_path', joined
        if joined.casefold() in self.folded: return 'missing', 'case_only_match', None
        return 'missing', 'no_page', None
