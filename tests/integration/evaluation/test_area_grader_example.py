"""The objective UI grader of examples/area-grader obeys the grader contract: true is exit 0, false is exit 1, and a grader
that cannot decide is exit 2 with `valid: false` and no outcome, never a pass or a fail (#800).

Everything is synthetic: a handful of HTML strings and tiny generated PNGs; no browser, model or network.
"""
from importlib import machinery, util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.support import png

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / 'examples/area-grader'
PAGE = (EXAMPLE / 'controls/complete/index.html').read_text()
EXPECTED = json.loads((EXAMPLE / 'expected.json').read_text())
loader = machinery.SourceFileLoader('area_grader_example', str(EXAMPLE / 'grade.py.txt'))
grader = util.module_from_spec(util.spec_from_loader(loader.name, loader))
loader.exec_module(grader)


def run(workspace, reference=EXAMPLE / 'expected.json'):
    return grader.main(['grade', str(workspace), str(reference)])


def outcome(capsys, workspace, reference=EXAMPLE / 'expected.json'):
    code = run(workspace, reference)
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def bundle(tmp_path):
    """A grader bundle (expected.json beside golden.png) and a complete workspace to damage one way at a time."""
    folder, workspace = tmp_path / 'bundle', tmp_path / 'workspace'
    folder.mkdir()
    workspace.mkdir()
    (folder / 'golden.png').write_bytes(png.golden())
    (workspace / 'shot.png').write_bytes(png.golden())
    (workspace / 'index.html').write_text(PAGE)
    return folder, workspace


def reference(folder, **changes):
    (folder / 'expected.json').write_text(json.dumps({**EXPECTED, **changes}))
    return folder / 'expected.json'


CONTROLS = json.loads((EXAMPLE / 'controls.json').read_text())


def test_the_example_lists_every_control_state_the_bank_audit_requires_and_each_directory_once():
    assert {c['state'] for c in CONTROLS} >= {'broken', 'partial', 'complete', 'alternative_valid'}
    assert sorted(c['fixture'] for c in CONTROLS) == sorted(f'controls/{p.name}' for p in (EXAMPLE / 'controls').iterdir())


@pytest.mark.parametrize('control', CONTROLS, ids=lambda c: c['state'])
def test_each_committed_control_ends_with_the_exit_code_the_contract_gives_its_state(control):
    expected = control['state'] in ('complete', 'alternative_valid')  # the rule of the bank control audit
    run = subprocess.run([sys.executable, str(EXAMPLE / 'grade.py.txt'), str(EXAMPLE / control['fixture']), str(EXAMPLE / 'expected.json')],
                         capture_output=True, text=True, check=False)
    envelope = json.loads(run.stdout)
    assert (run.returncode, envelope['valid'], envelope['outcome']) == (0 if expected else 1, True, expected)
    assert envelope['schema_version'] == 1 and envelope['grader_id'] and envelope['grader_version']


def test_the_committed_images_are_the_ones_the_test_encoder_writes():
    assert (EXAMPLE / 'golden.png').read_bytes() == (EXAMPLE / 'controls/complete/shot.png').read_bytes() == png.golden()
    assert (EXAMPLE / 'controls/broken/shot.png').read_bytes() == png.png(8, 6, lambda x, y: (200, 30, 30))
    assert (EXAMPLE / 'controls/superficial/shot.png').read_bytes() == png.png(8, 6, lambda x, y: (255, 255, 255))


def test_the_superficial_control_has_the_right_markup_and_only_its_pixels_fail():
    assert (EXAMPLE / 'controls/superficial/index.html').read_text() == PAGE


def test_a_complete_workspace_passes_every_declared_check(bundle, capsys):
    folder, workspace = bundle
    assert outcome(capsys, workspace, reference(folder)) == (0, dict(schema_version=1, valid=True, grader_id='ui-objective', grader_version='v1', outcome=True))


@pytest.mark.parametrize('filter', range(5), ids=lambda f: f'filter-{f}')
def test_every_png_scanline_filter_decodes_to_the_same_picture(bundle, capsys, filter):
    folder, workspace = bundle
    (workspace / 'shot.png').write_bytes(png.png(8, 6, lambda x, y: (30, 90, 200) if y < 2 else (255, 255, 255), filter=filter))
    assert outcome(capsys, workspace, reference(folder))[1]['outcome'] is True


@pytest.mark.parametrize(('page', 'why'), [
    (PAGE.replace('<button type="submit">Save</button>', ''), 'the wanted element is missing'),
    (PAGE.replace('Save', 'Cancel'), 'the element has other text'),
    (PAGE.replace('type="submit"', 'type="button"'), 'the element lacks a wanted attribute'),
    (PAGE.replace('<html lang="en">', '<html>'), 'the page declares no language'),
    (PAGE.replace(' alt="Company logo"', ' alt=""'), 'an image has an empty alt text'),
    (PAGE.replace('<input id="email"', '<input id="other"'), 'a control has no label'),
], ids=['missing-element', 'other-text', 'other-attribute', 'no-lang', 'empty-alt', 'unlabelled-control'])
def test_a_page_that_breaks_one_dom_or_accessibility_check_fails_with_a_valid_false_outcome(bundle, capsys, page, why):
    folder, workspace = bundle
    (workspace / 'index.html').write_text(page)
    assert outcome(capsys, workspace, reference(folder)) == (1, dict(schema_version=1, valid=True, grader_id='ui-objective', grader_version='v1', outcome=False)), why


@pytest.mark.parametrize('page', [
    PAGE.replace(' alt="Company logo"', ' alt="" role="presentation"'),
    PAGE.replace('<label for="email">Email</label>', '<label for="email">  Email\n</label>'),
    PAGE.replace('<input id="email" type="email">', '<input id="email" type="email"><input type="hidden" name="token"><input type="submit">'),
    PAGE.replace('<input id="email"', '<input id="other" aria-label="Email"'),
], ids=['decorative-image', 'whitespace-in-text', 'hidden-and-submit-inputs', 'aria-label'])
def test_a_page_that_the_rules_allow_still_passes(bundle, capsys, page):
    folder, workspace = bundle
    (workspace / 'index.html').write_text(page)
    assert outcome(capsys, workspace, reference(folder))[0] == 0


def changed(count, delta):
    """The golden with `count` pixels of the white area moved by `delta` in one channel."""
    return png.png(8, 6, lambda x, y: (30, 90, 200) if y < 2 else (255 - delta if 0 <= 8 * y + x - 16 < count else 255, 255, 255))


@pytest.mark.parametrize(('count', 'delta', 'code'), [
    (0, 0, 0), (6, 8, 0), (6, 9, 1), (1, 9, 1), (8, 40, 1)], ids=['identical', 'within-tolerance', 'one-over-tolerance', 'one-pixel-over-fraction', 'row-changed'])
def test_the_screenshot_diff_applies_the_channel_tolerance_and_the_changed_fraction_exactly(bundle, capsys, count, delta, code):
    folder, workspace = bundle
    (workspace / 'shot.png').write_bytes(changed(count, delta))
    spec = dict(EXPECTED['screenshot'], channel_tolerance=8, max_changed_fraction=0.0)
    assert outcome(capsys, workspace, reference(folder, screenshot=spec))[0] == code


def test_a_changed_fraction_allows_that_share_of_the_pixels_to_differ(bundle, capsys):
    folder, workspace = bundle
    (workspace / 'shot.png').write_bytes(changed(1, 40))  # one pixel of 48 is 2.08%
    assert outcome(capsys, workspace, reference(folder, screenshot=dict(EXPECTED['screenshot'], max_changed_fraction=0.03)))[0] == 0
    assert outcome(capsys, workspace, reference(folder, screenshot=dict(EXPECTED['screenshot'], max_changed_fraction=0.02)))[0] == 1


@pytest.mark.parametrize('damage', ['missing', 'garbage', 'other-size', 'truncated'])
def test_a_missing_or_wrong_screenshot_of_the_agent_is_a_false_outcome_not_a_grader_error(bundle, capsys, damage):
    folder, workspace = bundle
    shot = workspace / 'shot.png'
    {'missing': shot.unlink, 'garbage': lambda: shot.write_bytes(b'not an image'), 'other-size': lambda: shot.write_bytes(png.golden(9, 6)),
     'truncated': lambda: shot.write_bytes(png.golden()[:40])}[damage]()
    assert outcome(capsys, workspace, reference(folder)) == (1, dict(schema_version=1, valid=True, grader_id='ui-objective', grader_version='v1', outcome=False))


def test_a_missing_page_is_a_false_outcome(bundle, capsys):
    folder, workspace = bundle
    (workspace / 'index.html').unlink()
    assert outcome(capsys, workspace, reference(folder))[0] == 1


@pytest.mark.parametrize('damage', [
    dict(schema_version=2), dict(accessibility=['made_up_rule']), dict(dom=[]), dict(accessibility=[]), dict(page=None), dict(screenshot={'actual': 'shot.png'}),
    dict(screenshot=dict(EXPECTED['screenshot'], golden='missing.png')), dict(screenshot=dict(EXPECTED['screenshot'], golden='garbage.png')),
], ids=['schema', 'unknown-rule', 'empty-dom', 'empty-rules', 'no-page', 'incomplete-screenshot', 'missing-golden', 'garbage-golden'])
def test_a_reference_or_golden_the_grader_cannot_use_is_no_outcome_at_all(bundle, capsys, damage):
    folder, workspace = bundle
    (folder / 'garbage.png').write_bytes(b'not an image')
    code, envelope = outcome(capsys, workspace, reference(folder, **damage))
    assert (code, envelope['valid'], envelope['outcome']) == (2, False, None) and envelope['error'] == 'reference_invalid'


@pytest.mark.parametrize('text', ['{', '[]', '"dom"', '{"schema_version": 1}'], ids=['not-json', 'list', 'string', 'no-checks'])
def test_a_reference_that_declares_no_check_never_passes_vacuously(bundle, capsys, text):
    folder, workspace = bundle
    (folder / 'expected.json').write_text(text)
    code, envelope = outcome(capsys, workspace, folder / 'expected.json')
    assert (code, envelope['valid'], envelope['outcome']) == (2, False, None)
