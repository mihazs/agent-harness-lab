"""Temporary native audit controls; no interchange implementation or foreign execution."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.statistics import analyze
from tests.integration.evaluation import test_evaluation_foundation as foundation
from tests.unit.evaluation import test_bank_qualification as bank


def test_official_foreign_score_truthiness_and_match_equals_mappings_disagree():
    # Independently synthetic example of documented weighted Promptfoo semantics.
    output = 'Goodbye world'
    scores = [int(output == 'Hello world'), int('world' in output)]
    score = (2*scores[0]+scores[1])/3
    foreign_pass = score >= .5
    assert not foreign_pass
    assert bool(score)  # The rejected bool(score) design falsely passes.
    # The documented legacy OpenAI Match prefix is not exact answer equality.
    completion, references = '4 extra text', ['4']
    assert any(completion.startswith(answer) for answer in references)
    assert not (completion in references)


def test_external_scores_or_provenance_cannot_supply_current_typed_grades():
    spec = foundation.plan(n=4)
    rows = foundation.records(spec)
    for changed in (replace(rows[0], outcome=.9),
                    replace(rows[0], provenance='external_promptfoo_result')):
        result = analyze(spec, [changed, *rows[1:]])
        assert result == dict(status='incomplete', reason='unknown_or_invalid_grade')


def test_actual_bank_cli_rejects_foreign_sample_without_execution_or_export(tmp_path):
    samples = [dict(input='Return the synthetic numeral four', ideal='4'),
               dict(vars={'question':'Return four'}, **{'assert':[{'type':'equals','value':'4'}]})]
    root = tmp_path
    for index, sample in enumerate(samples):
        source = root/f'foreign-{index}.json'; source.write_text(json.dumps(sample))
        export = root/f'export-{index}'
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()), \
                patch('subprocess.Popen', side_effect=AssertionError('no execution')):
            with pytest.raises(SystemExit) as caught:
                main(['bank','--manifest',str(source),'--export',str(export)])
        assert caught.value.code == 2
        assert not export.exists()


def test_existing_validated_review_labels_and_export_stay_unqualified():
    fixture = bank.BankQualificationTests(); fixture.setUp()
    try:
        fixture.test_self_labels_cannot_qualify_actual_cli_or_export()
    finally:
        fixture.doCleanups()
