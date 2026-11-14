"""Blind lineage adjudication reports relations a `reviewed` label cannot, and never certifies."""
import hashlib
import json
from pathlib import Path
import shutil
import stat
from unittest.mock import patch

import pytest

import agent_harness_lab
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.adjudication import PROFILE, _alias, evaluate, write_packet

BAD_REVIEWER = 'codex-self-review-controls-v1'  # the starter bank's declared reviewer


@pytest.fixture
def bank(tmp_path):
    shutil.copytree(Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1', tmp_path/'bank')
    return tmp_path/'bank/bank.json'


def edit(bank, **by_task):
    value = json.loads(bank.read_text())
    for task in value['tasks']:
        task.update(by_task.get(task['task_id'], {}))
    bank.write_text(json.dumps(value))


def packet(bank, tmp_path):
    return write_packet(bank, tmp_path/'private/packet.json')['packet_sha256']


def submit(tmp_path, bank, pin, name, related=(), unsure=(), **override):
    digest = hashlib.sha256(bank.read_bytes()).hexdigest()
    value = dict(schema_version=1, profile=PROFILE, packet_sha256=pin, adjudicator=name,
                 related_groups=[[_alias(digest, t) for t in group] for group in related],
                 unsure=[_alias(digest, t) for t in unsure])
    value.update(override)
    path = tmp_path/f'{name}.json'
    path.write_text(json.dumps(value))
    return path


def judge(tmp_path, bank, *verdicts):
    pin = packet(bank, tmp_path)
    paths = [submit(tmp_path, bank, pin, f'judge{i}', **v) for i, v in enumerate(verdicts)]
    return evaluate(bank, paths)


def test_packet_is_private_and_hides_every_label(tmp_path, bank):
    written = write_packet(bank, tmp_path/'private/packet.json')
    path = tmp_path/'private/packet.json'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    text = path.read_text()
    value = json.loads(text)
    assert written['items'] == len(value['items']) == 7 and value['profile'] == PROFILE
    for label in ('synthetic-starter', BAD_REVIEWER, 'root_id', 'partition', 'review_status', 'independence'):
        assert label not in text
    assert all(set(item) == {'alias', 'prompt', 'fixture_sha256', 'fixture_files'} for item in value['items'])
    assert [i['alias'] for i in value['items']] == sorted(i['alias'] for i in value['items'])
    assert written['publication_allowed'] is False
    with pytest.raises(OSError):
        write_packet(bank, path)  # a packet is never overwritten


def test_packet_cannot_be_written_beside_the_bank(tmp_path, bank):
    with pytest.raises(ValueError, match='overlap'):
        write_packet(bank, bank.parent/'packet.json')
    assert not (bank.parent/'packet.json').exists()


def test_no_relation_found_is_not_independence_and_labels_stay_unproven(tmp_path, bank):
    edit(bank, **{t: dict(independence='reviewed') for t in ('localization', 'tool-recovery')})
    report = judge(tmp_path, bank, {}, {})
    assert report['outcome'] == 'no_relation_found' and report['quorum_met']
    assert set(report['tasks'].values()) == {'adjudicated_independent'}
    assert report['declared_reviewed_without_adjudicated_independence'] == []
    assert report['independence_verified'] is False and report['certification_eligible'] is False
    assert report['authenticated_adjudicators'] is False and report['limitations']


def test_one_adjudicator_cannot_support_a_reviewed_label(tmp_path, bank):
    edit(bank, **{'tool-recovery': dict(independence='reviewed')})
    report = judge(tmp_path, bank, {})
    assert report['outcome'] == 'insufficient_adjudicators' and not report['quorum_met']
    assert report['declared_reviewed_without_adjudicated_independence'] == ['tool-recovery']


def test_consensus_relation_across_partitions_is_leakage(tmp_path, bank):
    edit(bank, **{'tool-recovery': dict(partition='certification')})
    verdict = dict(related=[('tool-recovery', 'instruction-conflict')])
    report = judge(tmp_path, bank, verdict, verdict)
    assert report['outcome'] == 'relations_found'
    assert report['tasks']['tool-recovery'] == report['tasks']['instruction-conflict'] == 'cross_partition_relation'
    assert report['tasks']['skill-routing'] == 'adjudicated_independent'


def test_consensus_relation_between_declared_roots_is_undeclared(tmp_path, bank):
    verdict = dict(related=[('tool-recovery', 'skill-routing')])
    report = judge(tmp_path, bank, verdict, verdict)
    assert report['outcome'] == 'relations_found'
    assert report['counts'] == {'undeclared_relation': 2, 'adjudicated_independent': 5}


def test_split_verdict_is_disputed_and_inconclusive(tmp_path, bank):
    report = judge(tmp_path, bank, dict(related=[('tool-recovery', 'skill-routing')]), {})
    assert report['outcome'] == 'inconclusive'
    assert report['tasks']['tool-recovery'] == report['tasks']['skill-routing'] == 'disputed'


def test_a_relation_inside_one_declared_root_is_the_declared_grouping(tmp_path, bank):
    verdict = dict(related=[('localization', 'regression')])
    assert judge(tmp_path, bank, verdict, {})['outcome'] == 'no_relation_found'


def test_unsure_tasks_stay_unproven_and_never_hide_a_relation_vote(tmp_path, bank):
    report = judge(tmp_path, bank, dict(related=[('tool-recovery', 'skill-routing')], unsure=['instruction-conflict']),
                   dict(unsure=['tool-recovery']))
    assert report['outcome'] == 'inconclusive'
    assert report['tasks']['instruction-conflict'] == 'unsure'
    assert report['tasks']['tool-recovery'] == report['tasks']['skill-routing'] == 'disputed'


def test_partner_unsure_mark_does_not_make_the_other_task_independent(tmp_path, bank):
    edit(bank, **{t: dict(independence='reviewed') for t in ('tool-recovery', 'skill-routing')})
    report = judge(tmp_path, bank, dict(related=[('tool-recovery', 'skill-routing')]),
                   dict(related=[('tool-recovery', 'skill-routing')]), dict(unsure=['tool-recovery']))
    assert report['outcome'] == 'inconclusive'
    assert report['tasks']['tool-recovery'] == report['tasks']['skill-routing'] == 'disputed'
    assert report['declared_reviewed_without_adjudicated_independence'] == ['skill-routing', 'tool-recovery']


@pytest.mark.parametrize('case', ['reviewer', 'duplicate', 'unknown', 'overlap', 'schema', 'stale'])
def test_invalid_submissions_are_rejected(tmp_path, bank, case):
    pin = packet(bank, tmp_path)
    good = submit(tmp_path, bank, pin, 'judge-a')
    other = {
        'reviewer': lambda: submit(tmp_path, bank, pin, BAD_REVIEWER.upper()),
        'duplicate': lambda: submit(tmp_path, bank, pin, 'JUDGE-A'),
        'unknown': lambda: submit(tmp_path, bank, pin, 'judge-b', related_groups=[['0'*16, '1'*16]]),
        'overlap': lambda: submit(tmp_path, bank, pin, 'judge-b', related=[('tool-recovery', 'skill-routing')], unsure=['tool-recovery']),
        'schema': lambda: submit(tmp_path, bank, pin, 'judge-b', extra=True),
        'stale': lambda: submit(tmp_path, bank, '0'*64, 'judge-b'),
    }[case]()
    with pytest.raises(ValueError):
        evaluate(bank, [good, other])


def test_changed_bank_invalidates_prior_adjudication(tmp_path, bank):
    pin = packet(bank, tmp_path)
    paths = [submit(tmp_path, bank, pin, name) for name in ('judge-a', 'judge-b')]
    edit(bank, **{'tool-recovery': dict(partition='certification')})
    with pytest.raises(ValueError, match='different packet'):
        evaluate(bank, paths)


def test_cli_writes_a_packet_then_reports_adjudications_without_certifying(tmp_path, bank):
    out = tmp_path/'cli/packet.json'
    with patch('builtins.print') as printed:
        assert main(['bank', '--manifest', str(bank), '--adjudication-packet', str(out)]) == 3
    written = json.loads(printed.call_args.args[0])
    assert written['adjudication_packet']['items'] == 7 and out.is_file()
    paths = [str(submit(tmp_path, bank, written['adjudication_packet']['packet_sha256'], n)) for n in ('judge-a', 'judge-b')]
    with patch('builtins.print') as printed:
        assert main(['bank', '--manifest', str(bank), '--adjudications', *paths]) == 3
    result = json.loads(printed.call_args.args[0])
    assert result['blind_adjudication']['outcome'] == 'no_relation_found'
    assert result['certification_eligible'] is False
    with patch('builtins.print'), pytest.raises(SystemExit) as refused:
        main(['bank', '--manifest', str(bank), '--adjudication-packet', str(tmp_path/'again.json'), '--adjudications', *paths])
    assert refused.value.code == 2 and not (tmp_path/'again.json').exists()
