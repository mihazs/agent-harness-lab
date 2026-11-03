import json
import unittest
import os
import agent_harness_lab
from pathlib import Path
from unittest.mock import patch

import test_construction_access as fixtures
from agent_harness_lab.evaluation import construction_access as access


class ConstructionConsumerTests(unittest.TestCase):
    def setUp(self):
        fixtures.ConstructionAccessTests.setUp(self)
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()))
    def test_author_destination_cannot_diverge_from_owned_snapshot(self):
        author = access.WorkflowAccess.author
        changed = []
        def mutate(scope,*args,**kwargs):
            result = author(scope,*args,**kwargs)
            path = result[0]
            source = json.loads(path.read_bytes())
            source['tasks'][0]['prompt'] += '\nsynthetic destination rewrite'
            path.write_text(json.dumps(source))
            changed.append(True)
            return result
        with patch.object(access.WorkflowAccess,'author',mutate),self.assertRaisesRegex(ValueError,'published canonical output differs'):
            self.f.run_controlled()
        self.assertEqual(changed,[True])
        self.assertNotIn('synthetic destination rewrite',json.dumps(self.f.histories))

    def test_proposal_read_cannot_replace_owned_output_after_publication(self):
        original = access.record
        changed = []
        def mutate(path,*args,**kwargs):
            path = Path(path)
            if path.name == 'proposal.json' and path.parent.name == 'proposer-0' and not changed:
                value = json.loads(path.read_bytes())
                value['status'] = 'no_change'
                path.write_text(json.dumps(value))
                changed.append(True)
            return original(path,*args,**kwargs)
        with patch.object(access,'record',side_effect=mutate),self.assertRaisesRegex(ValueError,'published proposal differs'):
            self.f.run_controlled()
        self.assertEqual(changed,[True])

    def test_candidate_rewrite_after_proposer_cannot_be_frozen_as_owned(self):
        propose=access.WorkflowAccess.propose
        changed=[]
        def mutate(scope,*args,**kwargs):
            result=propose(scope,*args,**kwargs)
            (result[1]/'AGENTS.md').write_text('synthetic foreign candidate')
            changed.append(True)
            return result
        with patch.object(access.WorkflowAccess,'propose',mutate),self.assertRaisesRegex(ValueError,'published canonical output differs'):
            self.f.run_controlled()
        self.assertEqual(changed,[True])
        self.assertNotIn('evaluator_author',self.f.roles)

    def test_oracle_capture_aba_during_validation_cannot_be_freshly_pinned(self):
        from agent_harness_lab.evaluation import integrity
        digest=integrity.digest;changed=[]
        def mutate(path):
            path=Path(path)
            if path.name=='oracle' and path.parent.name=='evaluation-0' and not changed:
                expected=path/'expected.json';before=expected.read_bytes()
                expected.write_text('{"scenario":99}')
                changed.append(True)
                try:return digest(path)
                finally:expected.write_bytes(before)
            return digest(path)
        with patch.object(integrity,'digest',side_effect=mutate),self.assertRaisesRegex(ValueError,'grader capture differs from actual author output'):
            self.f.run_controlled()
        self.assertEqual(changed,[True])
