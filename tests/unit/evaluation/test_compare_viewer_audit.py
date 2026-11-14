"""Browser-free audit of the `ahl compare` page (#37): a parsed-DOM allowlist, hostile content and keyboard structure.

A real browser harness is not available offline, so these checks parse the rendered page the way a browser would
tokenize it and assert that hostile input can only ever become text, and that the page keeps its keyboard structure.
"""
from html.parser import HTMLParser
import re

import pytest

from agent_harness_lab.evaluation import compare
from tests.unit.evaluation.test_compare_viewer import fixture, page

HOSTILE = ('"><script>alert(1)</script>', '<img src=x onerror=alert(1)>', "' onfocus='alert(1)", 'javascript:alert(1)',
           '</style><script>alert(1)</script>', '</pre></details><a href="https://example.invalid/x">x</a>')
TAGS = {'html': set(), 'meta': {'charset', 'name', 'content', 'http-equiv'}, 'title': set(), 'style': set(), 'body': set(),
        'a': {'href'}, 'h1': set(), 'h2': set(), 'h3': set(), 'p': set(), 'strong': set(), 'ul': set(), 'li': set(), 'pre': set(),
        'div': {'class'}, 'table': set(), 'thead': set(), 'tbody': set(), 'tr': set(), 'td': set(), 'th': {'scope'},
        'details': {'id', 'open'}, 'summary': set()}
EXTRA_ATTRIBUTES = {('html', 'lang'), ('h2', 'id'), ('details', 'id')}


class Audit(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.problems, self.ids, self.links, self.headings, self.text, self.stack = [], [], [], [], [], []

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)
        if tag not in TAGS: self.problems.append(f'tag <{tag}>')
        for name, value in attrs:
            if name in TAGS.get(tag, ()) or (tag, name) in EXTRA_ATTRIBUTES: pass
            else: self.problems.append(f'attribute {tag}[{name}]')
            if name == 'id': self.ids.append(value)
            if name == 'href': self.links.append(value)
        if tag in ('h1', 'h2', 'h3'): self.headings.append(int(tag[1]))

    def handle_endtag(self, tag):
        if tag in self.stack: del self.stack[len(self.stack) - 1 - self.stack[::-1].index(tag):]

    def handle_data(self, data):
        if self.stack[-1:] != ['style']: self.text.append(data)


def audit(document):
    """Return every way `document` is not a script-free, link-free, keyboard-navigable page; empty when it is fine."""
    parser = Audit()
    parser.feed(document)
    problems = list(parser.problems)
    problems += [f'link {href}' for href in parser.links if not href.startswith('#') or href[1:] not in parser.ids]
    problems += [f'duplicate id {name}' for name in set(parser.ids) if parser.ids.count(name) > 1]
    problems += ['heading order' for before, after in zip(parser.headings, parser.headings[1:]) if after > before + 1]
    problems += ['heading count'] if parser.headings.count(1) != 1 else []
    problems += ['details without summary' for _ in re.findall(r'<details[^>]*>(?!<summary>)', document)]
    problems += ['table header without scope' for _ in re.findall(r'<th(?! scope="col">)(?=[ >])', document)]
    problems += ['tabindex'] if 'tabindex' in document else []
    problems += ['no focus style'] if 'a:focus' not in document or 'summary:focus' not in document else []
    return problems, ''.join(parser.text)


def hostile_experiment(payload):
    value = fixture()
    value['experiment_id'] = payload
    for item in value['planned'] + value['trials']: item['task'], item['root'], item['family'] = payload + item['task'], payload + item['root'], payload
    value['trials'][0]['events'] = [dict(event_id=payload, kind='tool_call', name=payload, status=payload, error_class=payload)]
    value['analysis'] = {'status': payload, 'reason': payload, payload: [payload, {payload: payload}]}
    return value


def test_clean_page_has_no_audit_problems():
    problems, _ = audit(page(fixture()))
    assert problems == []


@pytest.mark.parametrize('payload', HOSTILE, ids=['tag-breakout', 'img-handler', 'attribute-breakout', 'javascript-url', 'style-breakout', 'link-injection'])
def test_hostile_text_in_every_displayed_field_stays_text(payload):
    problems, text = audit(page(hostile_experiment(payload)))
    assert problems == []
    assert payload in text


def test_audit_catches_a_renderer_that_stops_escaping(monkeypatch):
    monkeypatch.setattr(compare, '_e', lambda value: '' if value is None else str(value))
    problems, _ = audit(page(hostile_experiment(HOSTILE[0])))
    assert any(problem.startswith('tag <script>') for problem in problems)


@pytest.mark.parametrize('before, after, expected', [
    ('id="pairs"', 'id="elsewhere"', 'link #pairs'),
    ('<summary>', '<p>', 'details without summary'),
    ('scope="col"', 'scope="row"', 'table header without scope'),
    ('a:focus', 'a:hover', 'no focus style'),
    ('<a href="#pairs">', '<a tabindex="1" href="#pairs">', 'tabindex'),
], ids=['skip-target', 'summary', 'header-scope', 'focus-style', 'positive-tabindex'])
def test_audit_catches_a_page_that_loses_keyboard_structure(before, after, expected):
    document = page(fixture())
    assert before in document
    assert expected in audit(document.replace(before, after, 1))[0]
