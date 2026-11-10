from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import random
import tempfile
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
from agent_harness_lab.evaluation.planning import required_roots
from agent_harness_lab.evaluation.statistics import TrialRecord, analyze
from agent_harness_lab.evaluation.task_bank import CertificationLedger, validate
from agent_harness_lab.evaluation.integrity import artifacts, digest
from agent_harness_lab.evaluation.sandbox import SandboxBackend, Limits
from agent_harness_lab.promotion.policy import decide, legacy_coverage


def plan(n=4, repeats=2, profile='certification'):
    return ExperimentSpec('synthetic', tuple((f't{i}', f'r{i}', 'family') for i in range(n)),
                          repeats, 13, 2*n*repeats, 3, .05, .1, .05, (('family', 1),),
                          'base', 'candidate', 'grader', 'runtime', profile, 'certification')


def records(spec, outcomes=None):
    outcomes = outcomes or (lambda trial: trial['version'] == 'candidate')
    return [TrialRecord(t['trial_id'], outcomes(t), 'graded', 'executable_grader',
                        'base', 'candidate', 'grader', 'runtime') for t in schedule(spec)]


def test_scheduler_replay_counterbalance_and_budget():
    spec = plan()
    first = schedule(spec)
    assert first == schedule(spec)
    assert len(first) == 16
    for task in {t['task'] for t in first}:
        assert [t['version'] for t in first if t['task'] == task][::2].count('baseline') == 1
    with pytest.raises(ValueError): schedule(replace(spec, max_trials=15))
    assert spec.digest != replace(spec, effect_margin=.06).digest


def test_empty_duplicate_missing_unknown_and_provenance_fail_closed():
    spec, data = plan(), records(plan())
    for bad in ([], data[:-1], data[:-1]+[data[0]], [replace(data[0], outcome=None)]+data[1:]):
        assert analyze(spec, bad)['status'] == 'incomplete'
    assert analyze(spec, [replace(data[0], runtime_hash='different')]+data[1:])['status'] == 'blocked'
    assert analyze(spec, [replace(data[0], critical_regression=True)]+data[1:])['status'] == 'rejected'
    assert analyze(spec, [replace(data[0], outcome=None, critical_regression=True)]+data[1:])['status'] == 'rejected'


@pytest.mark.parametrize('changes', [dict(critical_regression='false'),
    dict(critical_regression=None), dict(critical_regression=True, provenance='self_report'),
    dict(critical_regression=True, status='crashed')])
def test_unverified_critical_regression_remains_incomplete(changes):
    spec = plan()
    data = records(spec)
    result = analyze(spec, [replace(data[0], **changes), *data[1:]])
    assert (result['status'], result['reason']) == ('incomplete', 'unknown_or_invalid_grade')


def test_repeated_related_roots_are_not_independent():
    spec = replace(plan(), tasks=tuple((f't{i}', 'shared', 'family') for i in range(4)))
    result = analyze(spec, records(spec))
    assert result['status'] == 'inconclusive'
    assert result['roots'] == 1
    # A lucky pair cannot pass even with repeated copies of the same root.
    assert not decide(spec, result).automatic


def test_benefit_harm_and_cooperative_gate():
    spec = plan(n=1000, repeats=1)
    benefit = analyze(spec, records(spec))
    assert benefit['status'] == 'supported'
    assert decide(spec, benefit).status == 'blocked'
    # A caller-declared isolation boolean is recorded but cannot satisfy the isolation element.
    decision = decide(spec, dict(benefit,oracle_validity='current'), isolation_verified=True, effective_runtime_verified=True,
                      graders_audited=True, lineage_reviewed=True, certification_reserved=True)
    assert (decision.status, decision.reason) == ('blocked', 'certification_prerequisites_unverified')
    assert decision.isolation == 'unknown'
    assert not decision.automatic
    harm = analyze(spec, records(spec, lambda t: t['version'] == 'baseline'))
    assert harm['primary']['high'] < 0
    assert harm['status'] == 'rejected'
    smoke = replace(spec, profile='descriptive_smoke')
    assert decide(smoke, analyze(smoke, records(smoke))).status == 'inconclusive'


def test_seeded_null_simulation_false_promotions():
    # Predeclared check: 100 null simulations, seed 19; at most 10 false
    # promotions; also require the Wilson 95% upper bound <= .05.
    # This is an engineering falsification check, not calibration
    # over arbitrary task dependence. Wilson upper bound is reported in docs.
    rng, promoted = random.Random(19), 0
    spec = plan(n=40, repeats=2)
    for _ in range(100):
        # Correlated repeats within each independent root: one draw per root/version.
        outcomes = {(root, v): rng.random() < .5 for _, root, _ in spec.tasks for v in ('baseline','candidate')}
        data = records(spec, lambda t: outcomes[t['root'], t['version']])
        promoted += analyze(spec, data)['status'] == 'supported'
    p, n, z = promoted / 100, 100, 1.96
    upper = (p + z*z/(2*n) + z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / (1+z*z/n)
    assert upper <= .05  # Predeclared acceptable false-promotion rate.


class TaskBank:
    """A synthetic bank with one development and one certification task."""

    def __init__(self, add_cleanup):
        self.temp = tempfile.TemporaryDirectory(); add_cleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tasks = []
        for i, partition in enumerate(('development','certification')):
            fixture = self.root/f'fixture{i}'; fixture.mkdir(); (fixture/'value').write_text(str(i))
            grader = self.root/f'grader{i}'; grader.mkdir(); (grader/'grade.py').write_text('pass\n')
            self.tasks.append(dict(task_id=f't{i}', root_id=f'r{i}', family=f'f{i}', partition=partition,
                project_group=f'p{i}', time_group=f'time{i}', fixture=fixture.name, fixture_digest=digest(fixture),
                grader_bundle=grader.name, grader_digest=digest(grader), grader_version='v1',
                source_kind='synthetic', source_ref='independently synthetic unit fixture', permission='approved',
                reviewer='test-contract', review_status='accepted', independence='reviewed',
                safety=['offline disposable fixture'], artifacts=['answer.json']))
        self.path = self.root/'bank.json'

    def write(self):
        self.path.write_text(json.dumps(dict(schema_version=1, tasks=self.tasks,
            separate_by=['root_id','family','project_group','time_group'])))


@pytest.fixture
def bank():
    with ExitStack() as cleanups:
        yield TaskBank(cleanups.callback)


def test_declared_reviewed_bank_remains_unqualified_and_detects_mutated_oracle(bank):
    bank.write(); checked = validate(bank.path)
    assert checked['review_declarations_complete']
    assert not checked['certification_eligible']
    assert checked['issues'] == []
    (bank.root/'grader1/grade.py').write_text('different\n')
    with pytest.raises(ValueError, match='integrity'): validate(bank.path)


def test_leakage_and_unknown_independence(bank):
    for dimension in ('root_id', 'family', 'project_group', 'time_group'):
        original=bank.tasks[1][dimension]; bank.tasks[1][dimension]=bank.tasks[0][dimension]
        bank.write()
        with pytest.raises(ValueError, match='leakage'): validate(bank.path)
        bank.tasks[1][dimension]=original
    bank.tasks[1]['independence']='unverified'; bank.write()
    checked = validate(bank.path)
    assert not checked['certification_eligible']
    assert not checked['review_declarations_complete']
    assert checked['issues'][0]['status'] == 'independence_or_review_unverified'


def test_certification_access_is_consumed_before_feedback(bank):
    ledger = CertificationLedger(bank.root/'private/final.json')
    ledger.reserve('bank', 'candidate', 'controller')
    with pytest.raises(ValueError): ledger.access('candidate', 'proposer')
    with pytest.raises(ValueError): ledger.access('other', 'evaluator')
    assert ledger.access('candidate', 'evaluator') == 'bank'
    with pytest.raises(ValueError): ledger.access('candidate', 'evaluator')
    with pytest.raises(FileExistsError): ledger.reserve('bank', 'other', 'controller')


def test_artifact_extraction_rejects_escape_hardlink_symlink_and_limits(bank):
    fixture=bank.root/'fixture0'; (fixture/'answer').write_text('ok')
    assert artifacts(fixture,['answer']) == {'answer':b'ok'}
    with pytest.raises(ValueError): artifacts(fixture,['../bank.json'])
    with pytest.raises(ValueError): artifacts(fixture,['answer'],max_bytes=1)
    (fixture/'link').symlink_to(fixture/'answer')
    with pytest.raises(ValueError): artifacts(fixture,['link'])
    (fixture/'link').unlink(); os.link(fixture/'answer',fixture/'hard')
    with pytest.raises(ValueError): artifacts(fixture,['hard'])


def test_role_policy_and_missing_capability_never_fall_back(tmp_path):
    root=tmp_path; (root/'work').mkdir(); (root/'oracle').mkdir()
    backend=SandboxBackend('synthetic-nonexistent')
    assert backend.capability()['status'] == 'unsupported'
    with pytest.raises(ValueError): backend.run(['/usr/bin/true'],root/'work',{},'grader')
    backend.binary='/usr/bin/true'
    with pytest.raises(ValueError): backend.command(['/usr/bin/true'],root/'work',{'oracle':root/'oracle'},'executor')
    command=backend.command(['/usr/bin/true'],root/'work',{},'grader')
    assert '--unshare-net' in command; assert '--clearenv' in command
    assert '--share-net' not in command
    with pytest.raises(ValueError): Limits(seconds=0).validate()


def test_supported_host_isolation_canaries_and_output_limit(tmp_path):
    backend=SandboxBackend()
    if backend.capability()['status'] != 'available':
        pytest.skip('offline namespace backend unavailable; containment not established on this host')
    root=tmp_path; (root/'work').mkdir(); (root/'secret').write_text('CANARY')
    script="import os,pathlib,socket; assert 'SECRET' not in os.environ; assert not pathlib.Path('"+str(root/'secret')+"').exists(); s=socket.socket(); s.settimeout(.2);\ntry: s.connect(('1.1.1.1',443)); raise AssertionError('network escape')\nexcept OSError: pass\n"
    with patch.dict(os.environ, SECRET='CANARY'):
        run=backend.run(['/usr/bin/python3','-c',script],root/'work',{},'grader')
    assert run['exit_code'] == 0
    flood=backend.run(['/usr/bin/python3','-c',"print('x'*100000)"],root/'work',{},'grader',Limits(output_bytes=1024))
    assert flood['status'] == 'output_limit'
    assert len(flood['stdout']) <= 1024


def test_vacuous_legacy_result_cannot_become_no_change():
    manifest={'tasks':[{'id':'t'}]}
    assert not legacy_coverage(manifest,[])
    assert not legacy_coverage(manifest,[{'version':'candidate','task':'t'}]*2)
    assert legacy_coverage(manifest,[{'version':v,'task':'t'} for v in ('baseline','candidate')])


def test_certification_concurrent_access_is_single_use(tmp_path):
    ledger=CertificationLedger(tmp_path/'final.json')
    ledger.reserve('bank','candidate','controller')
    def access():
        try: return ledger.access('candidate','evaluator')
        except ValueError: return 'denied'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _: access(),range(2)))
    assert sorted(results) == ['bank','denied']


def test_symlinked_ancestor_is_rejected(tmp_path):
    root=tmp_path; (root/'real/tree').mkdir(parents=True)
    (root/'alias').symlink_to(root/'real',target_is_directory=True)
    with pytest.raises(ValueError): digest(root/'alias/tree')


def test_precision_assumptions_do_not_authorize_extra_trials():
    wide=required_roots(.5,.05)
    narrow=required_roots(.1,.05)
    assert narrow['independent_roots'] > wide['independent_roots']
    assert not narrow['automatic_budget_change']
    assert narrow['power'] == 'not_estimated'
    for args in ((.01,.05),(.1,.1),(float('nan'),0)):
        with pytest.raises(ValueError): required_roots(*args)
