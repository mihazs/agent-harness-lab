"""Explicit public export of the compare viewer (#37): provenance gate, stripped events, refusals; synthetic records only."""
import json
import stat

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import benchmark, compare, side_by_side as sbs
from agent_harness_lab.evaluation.resource_decision import success_cost
from tests.unit.evaluation.test_compare_resource import favorable
from tests.unit.evaluation.test_compare_viewer import GOLDEN, fixture
from tests.unit.evaluation.test_compare_viewer_audit import audit
from tests.unit.evaluation.test_resource_decision import sc_pairs
from tests.unit.evaluation.test_side_by_side_report import CANARY

PROVENANCE = dict(source_kind='synthetic', approved_publication=True, source_ref='synthetic-fixture-1', derived_from_private=False)


def public_fixture(**extra):
    """The golden experiment plus a provenance declaration, a hostile event and an event mapping that public mode must drop."""
    value = fixture()
    value['trials'][0]['events'] = [*value['trials'][0]['events'], dict(event_id='e2', kind='tool_call', name=CANARY, status=CANARY, error_class=CANARY)]
    value['event_mappings'] = [dict(value['planned'][0], baseline_event_id='e1', candidate_event_id='gone')]
    return dict(value, provenance=dict(PROVENANCE), **extra)


def test_public_report_is_labelled_and_the_tool_still_publishes_nothing():
    report = sbs.build(public_fixture(), public=True)
    assert report['publication'] == 'explicit_public_export'
    assert report['public_export']['provenance'] == PROVENANCE
    assert (report['publication_allowed'], report['automatic_promotion'], report['pooled_treatment_effect'], report['causal_attribution']) == (False, False, None, 'none')


def test_public_report_keeps_every_verdict_and_count_of_the_private_report():
    value = public_fixture()
    private, public = sbs.build(value), sbs.build(value, public=True)
    assert public['summary']['verdicts'] == private['summary']['verdicts']
    assert [(p['task'], p['verdict'], p['reasons']) for p in public['pairs']] == [(p['task'], p['verdict'], p['reasons']) for p in private['pairs']]
    assert public['analysis'] == private['analysis'] and public['summary']['usage'] == private['summary']['usage']


def test_public_report_drops_events_and_mappings_but_counts_them():
    value = public_fixture()
    public = sbs.build(value, public=True)
    fixed = next(p for p in public['pairs'] if p['task'] == 'fixed')['arms']
    assert (fixed['baseline'][0]['events_total'], fixed['candidate'][0]['events_total']) == (2, 1)
    assert all(r['events'] == [] and r['events_omitted'] is True for p in public['pairs'] for arm in sbs.ARMS for r in p['arms'][arm])
    assert sbs.build(value)['summary']['invalid_mappings'] == 1
    assert all(p['timeline'] == dict(alignment='omitted', reason='public_export_omits_events', rows=[]) for p in public['pairs'])
    assert public['summary']['invalid_mappings'] == 0 and public['invalid_mappings'] == []
    assert CANARY not in json.dumps(public) and '"e1"' not in json.dumps(public)


def test_private_report_ignores_a_provenance_declaration_and_keeps_the_golden_page():
    report = sbs.build(public_fixture())
    assert report['publication'] == 'private' and 'public_export' not in report
    assert compare.render(sbs.build(fixture())) == GOLDEN.read_text()


def provenance(**change):
    return {name: value for name, value in dict(PROVENANCE, **change).items() if value is not ...}


@pytest.mark.parametrize('declared', [
    pytest.param(None, id='missing'),
    pytest.param('synthetic', id='not-an-object'),
    pytest.param(provenance(source_kind='private'), id='private-source'),
    pytest.param(provenance(source_kind='user_session'), id='unknown-source-kind'),
    pytest.param(provenance(source_kind=...), id='no-source-kind'),
    pytest.param(provenance(approved_publication=False), id='not-approved'),
    pytest.param(provenance(approved_publication='yes'), id='approval-not-a-boolean'),
    pytest.param(provenance(approved_publication=...), id='no-approval'),
    pytest.param(provenance(derived_from_private=True), id='derived-from-private'),
    pytest.param(provenance(derived_from_private=...), id='derivation-unknown'),
    pytest.param(provenance(owner='someone'), id='unexpected-field'),
    pytest.param(provenance(source_ref=''), id='empty-reference'),
    pytest.param(provenance(source_ref='x' * (sbs.MAX_TEXT + 1)), id='oversized-reference'),
    pytest.param(provenance(source_ref='fixture\n1'), id='control-character-reference'),
    pytest.param(provenance(source_ref=...), id='no-reference'),
])
def test_public_export_is_refused_without_an_approved_synthetic_or_public_declaration(declared):
    value = public_fixture()
    if declared is None: del value['provenance']
    else: value['provenance'] = declared
    with pytest.raises(ValueError, match='provenance'):
        sbs.build(value, public=True)


@pytest.mark.parametrize('declared', [
    pytest.param(provenance(derived_from_private=True), id='derived-from-private'),
    pytest.param(provenance(source_kind='private'), id='private-source'),
    pytest.param('synthetic', id='not-an-object'),
])
def test_one_private_or_mixed_trial_refuses_the_whole_public_export(declared):
    value = public_fixture()
    value['trials'][1]['provenance'] = declared
    with pytest.raises(ValueError, match='provenance'):
        sbs.build(value, public=True)


@pytest.mark.parametrize('block, message', [
    pytest.param(dict(analysis=dict(status='inconclusive', publication_allowed=False)), 'analysis is labelled private', id='analysis-labelled-not-publishable'),
    pytest.param(dict(analysis=dict(status='inconclusive', publication='private')), 'analysis is labelled private', id='analysis-labelled-private'),
    pytest.param(dict(resource_decision=favorable()), 'resource material', id='resource-decision'),
    pytest.param(dict(resource_success_cost=[success_cost(sc_pairs([(10, True)], [(1, False)]), 'elapsed')]), 'resource material', id='cost-per-success-view'),
])
def test_a_private_or_resource_block_refuses_public_export_even_with_clean_provenance(block, message):
    with pytest.raises(ValueError, match=message):
        sbs.build(public_fixture(**block), public=True)


def test_an_unlabelled_analysis_is_copied_verbatim_under_the_declared_provenance():
    report = sbs.build(public_fixture(analysis=dict(status='inconclusive', roots=3)), public=True)
    assert report['analysis'] == dict(status='inconclusive', roots=3) and report['analysis_source'] == 'supplied_verbatim'


def test_a_public_report_is_not_a_benchmark_record_and_cannot_pass_the_benchmark_exporter():
    assert benchmark.public_export_allowed([sbs.build(public_fixture(), public=True)]) is False


def test_public_page_says_it_is_public_omits_events_and_passes_the_offline_audit():
    document = compare.render(sbs.build(public_fixture(), public=True))
    problems, text = audit(document)
    assert problems == []
    assert 'Public synthetic report' in text and 'events and event mappings are omitted' in text and 'synthetic-fixture-1' in text
    assert 'Private' not in text and CANARY not in document and '[e1]' not in text


def test_hostile_text_in_the_declared_reference_stays_text():
    value = public_fixture()
    value['provenance']['source_ref'] = '"><script>alert(1)</script>'
    problems, text = audit(compare.render(sbs.build(value, public=True)))
    assert problems == [] and '"><script>alert(1)</script>' in text


def test_render_refuses_a_report_that_claims_publication_authority():
    report = sbs.build(public_fixture(), public=True)
    with pytest.raises(ValueError, match='grants no publication authority'): compare.render(dict(report, publication_allowed=True))


def test_cli_public_writes_private_modes_leaves_input_unchanged_and_omits_events(tmp_path):
    source = tmp_path / 'experiment.json'
    source.write_text(json.dumps(public_fixture())); before = source.read_bytes()
    output = tmp_path / 'public'
    assert main(['compare', '--experiment', str(source), '--output', str(output), '--public']) == 0
    assert sorted(path.name for path in output.iterdir()) == ['report.html', 'report.json']
    assert stat.S_IMODE((output / 'report.html').stat().st_mode) == 0o600
    written = json.loads((output / 'report.json').read_text())
    assert written['publication'] == 'explicit_public_export' and written['publication_allowed'] is False
    assert CANARY not in (output / 'report.html').read_text() + (output / 'report.json').read_text()
    assert source.read_bytes() == before


def test_cli_without_public_flag_never_writes_a_public_report_for_an_input_with_provenance(tmp_path):
    source = tmp_path / 'experiment.json'
    source.write_text(json.dumps(public_fixture()))
    assert main(['compare', '--experiment', str(source), '--output', str(tmp_path / 'out')]) == 0
    assert json.loads((tmp_path / 'out' / 'report.json').read_text())['publication'] == 'private'


def test_cli_public_refusal_exits_2_names_the_problem_and_creates_no_output(tmp_path, capsys):
    value = public_fixture()
    value['provenance']['derived_from_private'] = True
    source = tmp_path / 'experiment.json'
    source.write_text(json.dumps(value))
    assert main(['compare', '--experiment', str(source), '--output', str(tmp_path / 'out'), '--public']) == 2
    assert 'provenance' in capsys.readouterr().err and not (tmp_path / 'out').exists()


def test_cli_public_still_refuses_an_existing_output_directory(tmp_path, capsys):
    source = tmp_path / 'experiment.json'
    source.write_text(json.dumps(public_fixture()))
    (tmp_path / 'out').mkdir()
    assert main(['compare', '--experiment', str(source), '--output', str(tmp_path / 'out'), '--public']) == 2
    assert 'exists' in capsys.readouterr().err and list((tmp_path / 'out').iterdir()) == []
