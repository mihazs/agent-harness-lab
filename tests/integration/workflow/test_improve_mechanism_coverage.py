"""ahl improve lists the proposer's mechanisms beside its receipt; a tie or an accepted patch never closes one (#10)."""
import json
from contextlib import ExitStack
from pathlib import Path

import pytest

from agent_harness_lab.diagnostics.mechanisms import MECHANISM_GUIDANCE
from agent_harness_lab.workflow import improve
from tests.integration.diagnostics.test_analysis_brief import history
from tests.integration.diagnostics.test_analysis_pipeline import Pipeline
from tests.integration.workflow.test_workflow import FAKE, WorkflowTests

LEGACY = "proposal={'status':'candidate','mechanisms':['synthetic'],'falsifiers':['artifact check']}"
CITING = ("p('proposer-prompt.txt').write_text(prompt); import re; named=re.search(r'(\\S+analysis-brief\\.json)',prompt); brief=json.loads(p(named.group(1)).read_text()); "
          "proposal={'status':'candidate','falsifiers':[],'mechanisms':[dict(mechanism_id='repeat-error',symptom='synthetic repeated error',"
          "evidence_refs=[brief['mechanisms'][0]['mechanism_id']],component='instruction',boundary='error repeats after the same call',"
          "competing_explanations=['expected negative test'],falsifier='no repeat after the edit')]}")
assert LEGACY in FAKE


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    for name, folder in (('CODEX_HOME', 'codex'), ('CLAUDE_CONFIG_DIR', 'claude'), ('XDG_STATE_HOME', 'state')):
        monkeypatch.setenv(name, str(tmp_path/folder))
    return Pipeline(tmp_path)


@pytest.fixture
def wf():
    with ExitStack() as cleanups:
        yield WorkflowTests(cleanups.callback)


def run(wf, pipeline, *, tied=False):
    wf.collect.stop()
    wf.agent.write_text(FAKE.replace(LEGACY, CITING))
    pipeline.capture(history(3))
    if tied:
        (wf.project/'AGENTS.md').write_text('good')
    args = wf.args()
    args.events = str(pipeline.root/'synthetic.jsonl')
    return improve(args)


@pytest.mark.parametrize('tied', (False, True), ids=('accepted_patch', 'tie'))
def test_a_cited_mechanism_stays_tracked_whatever_the_candidate_outcome(wf, pipeline, tied):
    result = run(wf, pipeline, tied=tied)
    assert result['status'] == ('no_change' if tied else 'inconclusive')
    receipt = result['mechanism_coverage']
    output = Path(result['output'])
    assert (receipt['authority'], receipt['publication_allowed'], receipt['causal_claim']) == ('none', False, False)
    assert (receipt['mechanisms'], receipt['coverage_complete'], receipt['coverage']['open']) == (1, False, 1)
    (entry,) = json.loads((output/receipt['artifact']).read_text())['entries']
    assert (entry['mechanism_id'], entry['disposition'], entry['coverage']) == ('repeat-error', 'proposed', 'open')
    assert (entry['evidence_count'], entry['responsible_component']) == (1, 'insufficient_evidence')
    assert receipt['admission'][0]['status'] == 'admitted'
    prompt = ' '.join((output/'preparation/proposer-prompt.txt').read_text().split())
    assert ' '.join(MECHANISM_GUIDANCE.split()) in prompt
    assert json.loads((output/'workflow.json').read_text())['mechanism_coverage'] == receipt


def test_a_proposal_in_the_old_free_text_form_is_recorded_as_refused(wf):
    result = improve(wf.args())
    assert result['status'] == 'inconclusive'
    receipt = result['mechanism_coverage']
    assert receipt['mechanisms'] == 0 and receipt['admission'][0]['status'] == 'rejected'
    assert receipt['admission'][0]['rejection_reasons'] == ['falsifier_not_object', 'mechanism_not_object']
