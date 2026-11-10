"""Synthetic evaluation feedback must respect the frozen task partitions."""
import json

import pytest
from pathlib import Path

from agent_harness_lab import cli
from agent_harness_lab.workflow import improve
from tests.integration.workflow.test_workflow import wf as wf


@pytest.mark.parametrize(('changed_manifest','expected_reason'),[(False,'trial_split_mismatch'),(True,'evaluation_manifest_changed')])
def test_mislabeled_heldout_trial_never_reaches_feedback(wf,monkeypatch,changed_manifest,expected_reason):
    original = cli.evaluator
    def evaluator():
        runner = original(); evaluate = runner.evaluate
        def mutate_result(options):
            result = evaluate(options)
            path = Path(options.output)/'results.json'; document = json.loads(path.read_text())
            heldout = next(row for row in document['trials'] if row['split'] == 'heldout')
            heldout['split'] = 'development'; path.write_text(json.dumps(document))
            if changed_manifest:
                manifest = Path(options.manifest); source = json.loads(manifest.read_text())
                next(task for task in source['tasks'] if task['id'] == heldout['task'])['split'] = 'development'
                manifest.write_text(json.dumps(source))
            return result
        runner.evaluate = mutate_result
        return runner
    monkeypatch.setattr(cli,'evaluator',evaluator)
    result = improve(wf.args())
    assert result['status'] == 'incomplete' and result['reason'] == expected_reason
    assert not (Path(result['output'])/'feedback-0.json').exists()
    assert (wf.project/'AGENTS.md').read_text() == 'bad'
