"""Static comparison viewer (#37 stage 2): golden page, escaping, privacy, output safety; synthetic records only."""
import json
import os
from pathlib import Path
import re
import stat

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import compare, side_by_side as sbs
from tests.unit.evaluation.test_side_by_side_report import ab, experiment, trial

GOLDEN = Path(__file__).resolve().parents[2] / 'support' / 'golden_profiles' / 'ahl-compare-viewer-v1.html'
EVENTS = [dict(event_id='e1', kind='tool_call', name='read', status='ok')]


def fixture():
    trials = (ab('fixed', False, True, events=EVENTS) + ab('broken', True, False) + ab('gone-candidate', True, True)[:1]
              + [trial('ungraded', 'baseline', True), trial('ungraded', 'candidate', None, status='infrastructure_error')])
    return experiment(trials, planned=[(t, 0) for t in ('fixed', 'broken', 'gone-candidate', 'ungraded', 'absent')],
                      analysis=dict(status='inconclusive', reason='insufficient_independent_roots', roots=3))


def page(value):
    return compare.render(sbs.build(value))


def test_golden_page_and_missing_trial_is_labelled():
    document = page(fixture())
    if os.environ.get('AHL_UPDATE_GOLDEN'): GOLDEN.write_text(document)
    assert document == GOLDEN.read_text()
    assert '<td>missing</td>' in document and 'inconclusive' in document and 'insufficient_independent_roots' in document


def test_task_id_script_is_escaped_and_no_remote_urls_or_scripts():
    value = fixture()
    for item in value['planned'] + value['trials']: item['task'] = '<script>alert(1)</script>'
    document = page(value)
    assert '<script' not in document and '&lt;script&gt;alert(1)&lt;/script&gt;' in document
    assert not re.search(r'https?://|<link|<img|<iframe|\son\w+=', page(fixture()))
    assert "default-src 'none'" in document


def test_verdicts_are_text_labels_and_no_color_semantics():
    document = page(fixture())
    for label in ('improved', 'regressed', 'unknown'): assert f'<td>{label}</td>' in document
    style = re.search(r'<style>(.*?)</style>', document).group(1)
    assert not re.search(r'color|#[0-9a-f]{3,6}', style, re.I) and 'style=' not in document


def test_unknown_usage_is_not_zero_and_event_bodies_never_render():
    value = fixture()
    value['trials'][0]['events'][0]['arguments'] = 'SECRET-BODY'
    document = page(value)
    assert 'SECRET-BODY' not in document and '<td>unknown</td>' in document


def test_public_or_foreign_report_is_refused():
    report = sbs.build(fixture())
    with pytest.raises(ValueError): compare.render(dict(report, publication_allowed=True))
    with pytest.raises(ValueError): compare.render(dict(report, profile='other'))


def test_cli_writes_private_files_refuses_existing_output_and_leaves_input_unchanged(tmp_path, capsys):
    source = tmp_path / 'experiment.json'
    source.write_text(json.dumps(fixture())); before = source.read_bytes()
    output = tmp_path / 'review'
    assert main(['compare', '--experiment', str(source), '--output', str(output)]) == 0
    assert (output / 'report.html').read_text() == GOLDEN.read_text()
    assert stat.S_IMODE(output.stat().st_mode) == 0o700 and stat.S_IMODE((output / 'report.html').stat().st_mode) == 0o600
    assert json.loads((output / 'report.json').read_text())['publication_allowed'] is False
    assert source.read_bytes() == before
    assert main(['compare', '--experiment', str(source), '--output', str(output)]) == 2
    assert 'exists' in capsys.readouterr().err


def test_cli_rejects_symlink_and_malformed_input_without_creating_output(tmp_path):
    source = tmp_path / 'experiment.json'
    source.write_text(json.dumps(fixture()))
    link = tmp_path / 'link.json'; link.symlink_to(source)
    bad = tmp_path / 'bad.json'; bad.write_text('{"profile": "nope"}')
    for name in (link, bad):
        assert main(['compare', '--experiment', str(name), '--output', str(tmp_path / 'out')]) == 2
        assert not (tmp_path / 'out').exists()
