"""The descriptive report documentation stays true to the report the code and the command produce.

Offline and synthetic: the field table is compared with reports the command and the library really produce, the abridged
report of the how-to with the printed one, and the fixture relations with the committed expectations.
"""
import json
import re

from agent_harness_lab.evaluation import bayes_descriptive, resource_decision
from agent_harness_lab.evaluation.bayes_descriptive import RELATIONS, report
from tests.integration.docs.test_bayes_decision_docs import EXPLANATION, HOW_TO, REFERENCE, fences, flat, key_paths, names, page, rows, section
from tests.support.bayes_records import KINDS, bank, expected_verdicts, experiment
from tests.support.bayes_scenarios import plan

SOURCES = {'plan', 'analysis', 'resource decision', 'constant', 'caller'}
CONTRACT = dict(schema_version=1, profile=resource_decision.PROFILE, alpha=.05, min_roots=2, endpoints=[
    dict(name='tokens', kind='measured_tokens', unit='tokens', scope='all_assigned', required=True, horizon=1000, max_increase=100)])


def produced(tmp_path):
    """One report per fixture through the command, plus one with an evaluated resource endpoint and one with none measured."""
    reports = {}
    for kind in KINDS:
        directory = tmp_path / kind
        directory.mkdir()
        case = bank(kind)
        code, out, err = experiment(directory, case['spec'], case['records'])
        assert err == '' and code == 3
        reports[kind] = json.loads(out)['descriptive_report']
    row = lambda value: dict(status='completed', measurements={} if value is None else dict(tokens=value))
    for label, tokens in (('evaluated', (200, 100)), ('unmeasured', (200, None))):
        pairs = [dict(root=f'r{i}', baseline=row(tokens[0]), candidate=row(tokens[1])) for i in range(6)]
        decision = resource_decision.decide(CONTRACT, pairs, 'supported')
        reports[label] = report(plan(5, resource_contract_sha256=decision['contract_sha256']),
                                dict(status='inconclusive', primary=dict(mean=.3)), decision)
    return reports


def generic(path):
    return re.sub(r'^desirability\.weights\..+$', 'desirability.weights.<objective>', path)


def tables():
    """The three tables of the reference section, as (fields, relations, fixture relations), each without its header."""
    found = re.findall(r'(?:^\|.*\n?)+', section(page(REFERENCE), 'Descriptive report'), re.M)
    assert len(found) == 3
    return [rows(block) for block in found]


def test_the_field_table_matches_the_produced_reports_in_both_directions(tmp_path):
    documented = {name for cells in tables()[0] for name in names(cells[0])}
    reports = produced(tmp_path)
    union = {generic(path) for value in reports.values() for path in key_paths(value)}
    assert union - documented == set() and documented - union == set()
    top = {name for name in documented if '.' not in name and '[' not in name}
    assert all(set(value) == top for value in reports.values())


def test_every_field_says_where_its_number_comes_from():
    table = tables()[0]
    assert table and all(len(cells) == 3 and {word.strip() for word in cells[2].split(',')} <= SOURCES for cells in table)


def test_the_relation_table_lists_every_relation_the_code_can_print():
    assert {names(cells[0])[0] for cells in tables()[1]} == set(RELATIONS.values()) | {'undetermined'}


def test_the_fixture_relation_table_matches_the_committed_expectations():
    table = {names(cells[0])[0]: names(cells[1])[0] for cells in tables()[2]}
    assert table == {kind: row['descriptive_relation'] for kind, row in expected_verdicts().items()}


def test_the_abridged_report_in_the_how_to_equals_the_report_the_command_prints(tmp_path):
    shown = json.loads(fences(section(page(HOW_TO), 'Read the descriptive report'), 'json')[0])
    case = bank('clear_win')
    code, out, err = experiment(tmp_path, case['spec'], case['records'])

    def holds(documented, actual):
        if isinstance(documented, dict):
            return isinstance(actual, dict) and all(key in actual and holds(item, actual[key]) for key, item in documented.items())
        if isinstance(documented, list):
            return isinstance(actual, list) and len(documented) == len(actual) and all(map(holds, documented, actual))
        return documented == actual

    assert (code, err) == (3, '') and holds(shown, json.loads(out)['descriptive_report'])


def test_every_page_labels_the_report_descriptive_only_and_keeps_the_evidence_status():
    for name, heading in ((REFERENCE, 'Descriptive report'), (HOW_TO, 'Read the descriptive report'), (EXPLANATION, 'Descriptive-only role (Option 5)')):
        text = flat(section(page(name), heading))
        assert 'descriptive' in text.lower() and 'verdict' in text.lower(), name
    reference = flat(section(page(REFERENCE), 'Descriptive report'))
    for phrase in ('no peer-reviewed work validates', 'never an input to the gate', 'Unknown stays unknown', 'never read as zero cost'):
        assert phrase in reference, phrase
    assert 'no peer-reviewed work applies them to agent harness scoring' in flat(section(page(EXPLANATION), 'Descriptive-only role (Option 5)'))


def test_the_status_table_marks_the_report_implemented_and_the_module_states_its_limits():
    status = section(page(EXPLANATION), 'Status')
    assert any('descriptive' in cells[0] and cells[1].startswith('implemented') for cells in rows(status))
    assert 'Descriptive only' in bayes_descriptive.__doc__ and 'never zero' in bayes_descriptive.__doc__
