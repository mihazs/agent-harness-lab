"""Synthetic short prompts cannot become a completed similarity check."""
from contextlib import ExitStack
from dataclasses import replace
import hashlib
import json

import pytest

from agent_harness_lab.evaluation.integrity import digest as fixture_digest
from agent_harness_lab.evaluation.precision import preflight
from agent_harness_lab.evaluation.root_similarity import ALGORITHM, manifest_evidence, prepare
from agent_harness_lab.evaluation.task_bank import inspect
from agent_harness_lab.sources.schema import digest
from tests.integration.evaluation.test_evaluation_foundation import TaskBank
from tests.unit.evaluation.test_precision_solver import spec


@pytest.mark.parametrize(('prompts', 'overlaps'), [
    pytest.param(('fix', 'fix'), 1, id='identical-one-token'),
    pytest.param(('fix', 'sort'), 0, id='distinct-one-token'),
    pytest.param(('fix now', 'sort now'), 0, id='distinct-two-tokens'),
])
def test_short_bank_prompts_cannot_produce_effective_root_evidence(prompts, overlaps):
    with ExitStack() as cleanup:
        bank = TaskBank(cleanup.callback)
        bank.tasks[1]['partition'] = 'development'
        for index, prompt in enumerate(prompts):
            folder = bank.root/f'fixture{index}'
            (folder/'task.json').write_text(json.dumps({'prompt': prompt}))
            bank.tasks[index].update(family='a',fixture_digest=fixture_digest(folder))
        bank.write()
        report = inspect(bank.path)
        grouping = report['content_grouping']
        similarity = grouping['root_similarity']
        assert grouping['overlaps'] == overlaps
        assert similarity['status'] == 'incomplete' and similarity['uninspected_tasks'] == 2
        assert similarity['nominal_roots'] == 2 and similarity['effective_roots'] is None
        assert similarity['evidence'] is None and similarity['independence'] == 'unverified'
        assert any(issue.get('reason') == 'prompt_similarity_incomplete' for issue in report['issues'])
        plan = replace(spec({'a': 2}), tasks=tuple((t['task_id'], t['root_id'], t['family']) for t in bank.tasks))
        with pytest.raises(ValueError, match='complete'):
            prepare(plan, bank.path)
        prior = dict(schema_version=1, algorithm=ALGORITHM, bank_sha256=report['bank_sha256'],
                     grouping_revision_sha256=grouping['revision_sha256'],
                     task_roots=[[t['task_id'], t['root_id']] for t in bank.tasks], groups=[])
        pinned = replace(plan, root_similarity_sha256=digest(prior))
        frozen_hash = pinned.digest
        with pytest.raises(ValueError, match='complete'):
            preflight(pinned, root_similarity_evidence=prior, root_similarity_bank=bank.path)
        assert pinned.digest == frozen_hash
        with pytest.raises(ValueError, match='lexical shingle'):
            manifest_evidence({'tasks': [dict(id=t['task_id'], root=t['root_id'], prompt=p)
                                         for t, p in zip(bank.tasks, prompts)]})


def test_previously_pinned_short_manifest_evidence_fails_closed(tmp_path):
    tasks = [dict(id='t0', root='r0', prompt='fix'), dict(id='t1', root='r1', prompt='fix')]
    source = {'tasks': tasks}
    rows = sorted([t['id'], t['root'], hashlib.sha256(t['prompt'].encode()).hexdigest()] for t in tasks)
    bank_pin = digest(dict(profile='workflow-prompt-similarity-v1', tasks=rows))
    prior = dict(schema_version=1, algorithm=ALGORITHM, bank_sha256=bank_pin,
                 grouping_revision_sha256=digest(dict(algorithm=ALGORITHM, bank_sha256=bank_pin)),
                 task_roots=[['t0','r0'],['t1','r1']], groups=[], source_kind='workflow-prompt-similarity-v1')
    plan = replace(spec({'a': 2}), tasks=(('t0','r0','a'),('t1','r1','a')), root_similarity_sha256=digest(prior))
    original_hash = plan.digest
    path = tmp_path/'manifest.json'; path.write_text(json.dumps(source))
    with pytest.raises(ValueError, match='lexical shingle'):
        preflight(plan, root_similarity_evidence=prior, root_similarity_bank=path)
    assert plan.digest == original_hash  # The old plan is rejected, not silently rewritten.


@pytest.mark.parametrize('second', ['fix', 'sort'])
def test_default_repeated_request_refuses_short_prompts_before_dispatch(second):
    import time
    from agent_harness_lab.evaluation import repeated_workflow
    from agent_harness_lab.private_journal import encoded, record
    from tests.integration.evaluation.test_repeated_access_companion import RepeatedAccessAdmission

    with ExitStack() as cleanup:
        host = RepeatedAccessAdmission(cleanup.callback)
        source = record(host.manifest)[0]
        source['tasks'][0]['prompt'] = 'fix'
        source['tasks'][1]['prompt'] = second
        host.manifest.write_bytes(encoded(source))
        host.request_value.pop('root_similarity')
        host.request_value.update(manifest_sha256=record(host.manifest)[1], bank_sha256=fixture_digest(host.bank))
        host.request.write_bytes(encoded(host.request_value))
        output = host.root/'prepared'; output.mkdir()
        with pytest.raises(ValueError, match='lexical shingle') as error:
            repeated_workflow.Request(host.request_args(host.selected), output, time.monotonic()+30)
        assert 'fix' not in str(error.value) and 'sort' not in str(error.value)
        assert not host.calls and host.key_reads == 0
