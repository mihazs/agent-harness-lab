"""`ahl analyze` and `ahl improve` carry the harness profile findings to the proposer as citable hypotheses (#1119)."""
import json
from contextlib import ExitStack
from pathlib import Path

import pytest

from agent_harness_lab import workflow
from agent_harness_lab.workflow import improve
from tests.integration.diagnostics.test_analysis_brief import history
from tests.integration.diagnostics.test_analysis_pipeline import Pipeline
from tests.integration.workflow.test_workflow import FAKE, WorkflowTests
from tests.support.profile_fixture import build, package

LEGACY = "proposal={'status':'candidate','mechanisms':['synthetic'],'falsifiers':['artifact check']}"
CITING = ("p('proposer-prompt.txt').write_text(prompt); import re; named=re.search(r'(\\S+analysis-brief\\.json)',prompt); brief=json.loads(p(named.group(1)).read_text()); "
          "proposal={'status':'candidate','falsifiers':[],'mechanisms':[dict(mechanism_id='stale-line',symptom='the agent runs a script the repository lacks',"
          "evidence_refs=[brief['harness_profile']['findings'][0]['id']],component='instruction',boundary='the line names the script',"
          "competing_explanations=['the script lives in a subpackage'],falsifier='the agent never runs it')]}")
FILES = {'package.json': package({'test': 'vitest run'}), 'pnpm-lock.yaml': '', 'AGENTS.md': 'Use pnpm. Run `pnpm test`, then `pnpm run deploy`.\n'}
assert LEGACY in FAKE


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    for name, folder in (('CODEX_HOME', 'codex'), ('CLAUDE_CONFIG_DIR', 'claude'), ('XDG_STATE_HOME', 'state')):
        monkeypatch.setenv(name, str(tmp_path/folder))
    return Pipeline(tmp_path)


def plant(project, files):
    build(project.parent, files, name=project.name)


def test_analyze_writes_the_private_report_and_a_bounded_brief_section(pipeline):
    plant(pipeline.project, FILES)
    code, _, _ = pipeline.invoke('analyze', history(3))
    assert code == 0
    output = pipeline.root/'output'
    private = json.loads((output/'harness-profile.json').read_text())
    section = json.loads((output/'analysis-brief.json').read_text())['harness_profile']
    assert private['publication_allowed'] is False and [row['kind'] for row in private['findings']] == ['command_target_missing']
    assert [row['id'] for row in section['findings']] == [row['id'] for row in private['findings']]
    assert section['artifact'] == 'harness-profile.json' and 'Ignore' not in json.dumps(section)
    assert 'Harness profile findings' in (output/'report.md').read_text() and 'hp/command_target_missing/' in (output/'report.md').read_text()


def test_a_project_without_an_instruction_file_says_so_in_the_brief(pipeline):
    plant(pipeline.project, {'package.json': package({'test': 'vitest run'})})
    pipeline.invoke('analyze', history(2))
    section = json.loads((pipeline.root/'output/analysis-brief.json').read_text())['harness_profile']
    assert (section['status'], section['findings']) == ('no_instruction_file', [])
    assert 'Harness profile findings' not in (pipeline.root/'output/report.md').read_text()


@pytest.fixture
def wf():
    with ExitStack() as cleanups:
        yield WorkflowTests(cleanups.callback)


def run(wf, pipeline):
    wf.collect.stop()
    wf.agent.write_text(FAKE.replace(LEGACY, CITING))
    plant(wf.project, FILES)
    pipeline.capture(history(3))
    args = wf.args()
    args.events = str(pipeline.root/'synthetic.jsonl')
    return improve(args)


def test_the_proposer_is_told_to_read_the_findings_and_may_cite_one_as_evidence(wf, pipeline):
    result = run(wf, pipeline)
    output = Path(result['output'])
    prompt = ' '.join((output/'preparation/proposer-prompt.txt').read_text().split())
    assert ' '.join(workflow.PROFILE_GUIDANCE.split()) in prompt
    admission = result['mechanism_coverage']['admission'][0]
    assert (admission['status'], admission['rejected']) == ('admitted', 0)
    assert (output/'harness-profile.json').is_file() and (wf.project/'AGENTS.md').read_text().startswith('Use pnpm')
    assert result['changed_files'] == [] and result['status'] != 'applied'
