"""Current controller history is required in addition to supplied mathematical records."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
from agent_harness_lab.evaluation.statistics import TrialRecord, analyze
from agent_harness_lab.promotion.policy import decide


class HistoryCliTests(unittest.TestCase):
    def test_raw_untracked_records_cannot_be_recommended_with_other_prerequisites(self):
        spec = ExperimentSpec('synthetic',tuple((f't{i}',f'r{i}','family') for i in range(1000)),
            1,13,2000,2,.05,.1,.05,(('family',1),),'base','candidate','grader','runtime','certification','certification')
        records = [TrialRecord(t['trial_id'],t['version']=='candidate','graded','executable_grader',
            'base','candidate','grader','runtime') for t in schedule(spec)]
        result = analyze(spec,records)
        self.assertEqual(result['status'],'supported')
        decision = decide(spec,result,isolation_verified=True,effective_runtime_verified=True,
            graders_audited=True,lineage_reviewed=True,certification_reserved=True)
        self.assertEqual(decision.status,'blocked')
        self.assertEqual(decision.reason,'oracle_history_unverified_or_invalidated')

    def test_raw_experiment_cli_labels_oracle_validity_unverified(self):
        from dataclasses import asdict
        spec = ExperimentSpec('synthetic',(('t','r','f'),),1,13,2,2,.05,.1,.05,(('f',1),),
            'base','candidate','grader','runtime')
        rows = [asdict(TrialRecord(t['trial_id'],True,'graded','executable_grader',
            'base','candidate','grader','runtime')) for t in schedule(spec)]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'spec.json').write_text(json.dumps(asdict(spec)))
            (root/'records.json').write_text(json.dumps(rows))
            with patch('builtins.print') as output:
                self.assertEqual(main(['experiment','--spec',str(root/'spec.json'),'--records',str(root/'records.json')]),3)
            self.assertEqual(json.loads(output.call_args.args[0])['oracle_validity'],'unverified')

    def test_history_has_no_result_import_or_implicit_store_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'history'
            for argv in (['show','--run','a'*64],['run','--records','forged.json']):
                with self.assertRaises(SystemExit) as exit:
                    main(['experiment-history','--store',str(root),*argv])
                self.assertEqual(exit.exception.code,2)
                self.assertFalse(root.exists())
