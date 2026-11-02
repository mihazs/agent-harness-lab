"""Synthetic oracle identity controls; no grader or runtime is executed."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.oracle import capture, identity


class OracleIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)/'manifest'; self.root.mkdir()
        self.tasks = []; self.bundles = {}
        for name in ('first','second'):
            folder = self.root/name; folder.mkdir()
            (folder/'grade.py').write_text('raise SystemExit(0)\n')
            (folder/'expected.json').write_text('{"accepted":true}')
            (folder/'controls.json').write_text('["broken","complete"]')
            self.bundles[name] = dict(path=name,digest=digest(folder))
            self.tasks.append(dict(id=name,grader=['/usr/bin/python3','-I',str(folder/'grade.py'),
                '{workspace}','{trace}','--expected='+str(folder/'expected.json'),'literal']))

    def capture(self): return identity(self.root,self.tasks,self.bundles)

    def test_capture_hashes_to_the_exact_existing_identity(self):
        self.tasks[0]['artifacts'] = ['answer.json']
        value = capture(self.root,self.tasks,self.bundles)
        self.assertEqual(set(value),{'schema_version','algorithm','tasks'})
        self.assertEqual(value['schema_version'],1)
        self.assertEqual(value['algorithm'],'declared-oracle-argv-v1')
        self.assertEqual(set(value['tasks']),{'first','second'})
        for name in value['tasks']:
            self.assertEqual(set(value['tasks'][name]),{'bundle_sha256','artifacts','argv'})
        self.assertEqual(value['tasks']['first']['artifacts'],['answer.json'])
        self.assertIsNone(value['tasks']['second']['artifacts'])
        raw = json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
        self.assertEqual(hashlib.sha256(raw).hexdigest(),self.capture())

    def test_bundle_dependency_is_shared_across_different_task_subsets(self):
        self.bundles['second'] = dict(self.bundles['first'])
        self.tasks[1]['grader'] = list(self.tasks[0]['grader'])
        first = capture(self.root,self.tasks[:1],{'first':self.bundles['first']})
        second = capture(self.root,self.tasks[1:],{'second':self.bundles['second']})
        together = capture(self.root,self.tasks,self.bundles)
        for value in (first,second,together):
            self.assertEqual({entry['bundle_sha256'] for entry in value['tasks'].values()},
                             {self.bundles['first']['digest']})
        self.assertNotEqual(identity(self.root,self.tasks[:1],{'first':self.bundles['first']}),self.capture())

    def test_capture_detaches_every_mutable_caller_container(self):
        self.tasks[0]['artifacts'] = ['answer.json']
        tasks,bundles = deepcopy(self.tasks),deepcopy(self.bundles)
        value = capture(self.root,self.tasks,self.bundles)
        self.assertEqual((self.tasks,self.bundles),(tasks,bundles))
        frozen = deepcopy(value)
        self.tasks[0]['artifacts'].append('other.json'); self.tasks[0]['grader'].append('new literal')
        self.bundles['first']['path'] = 'changed'
        self.assertEqual(value,frozen)
        value['tasks']['first']['artifacts'].append('captured-only.json')
        value['tasks']['first']['argv'][0]['literal'] = 'captured-only'
        self.assertNotIn('captured-only.json',self.tasks[0]['artifacts'])
        self.assertEqual(self.tasks[0]['grader'][0],tasks[0]['grader'][0])

    def test_v0441_frozen_identity_bytes_are_unchanged(self):
        # Captured before extraction at cc18399984041b16cb70a67eba18fd58d7926382.
        self.tasks[0]['artifacts'] = ['answer.json']
        for name in self.bundles:
            for path in (self.root/name).iterdir(): path.chmod(0o644)
            self.bundles[name]['digest'] = digest(self.root/name)
        self.assertEqual(self.capture(),'1fcaa3679f0080b65f878acefe03f0d89b7cec812b1a2b1d633b1586f2d6bd52')

    def test_relocation_bundle_renaming_and_task_map_order_preserve_identity(self):
        before = self.capture(); relocated = self.root.parent/'relocated'; shutil.copytree(self.root,relocated)
        tasks = deepcopy(self.tasks); bundles = dict(reversed(list(self.bundles.items())))
        for task in tasks:
            task['grader'] = [arg.replace(str(self.root),str(relocated)) for arg in task['grader']]
        (relocated/'first').rename(relocated/'renamed')
        bundles['first'] = {**bundles['first'],'path':'renamed'}
        tasks[0]['grader'] = [arg.replace(str(relocated/'first'),str(relocated/'renamed')) for arg in tasks[0]['grader']]
        self.assertEqual(before,identity(relocated,list(reversed(tasks)),bundles))

    def test_every_bundle_file_and_file_mode_is_bound_even_without_argv_reference(self):
        for name in ('grade.py','expected.json','controls.json','helper.py'):
            with self.subTest(name=name):
                before = self.capture(); target = self.root/'first'/name
                target.write_text('changed synthetic oracle input')
                with self.assertRaisesRegex(ValueError,'integrity'): self.capture()
                self.bundles['first']['digest'] = digest(self.root/'first')
                self.assertNotEqual(before,self.capture())
        before = self.capture(); (self.root/'first/grade.py').chmod(0o750)
        with self.assertRaisesRegex(ValueError,'integrity'): self.capture()
        self.bundles['first']['digest'] = digest(self.root/'first')
        self.assertNotEqual(before,self.capture())

    def test_argv_flags_literals_placeholders_and_external_runtime_path_are_bound(self):
        before = self.capture()
        for index in (0,1,3,4,6):
            tasks = deepcopy(self.tasks); tasks[0]['grader'][index] += '-changed'
            self.assertNotEqual(before,identity(self.root,tasks,self.bundles))
        tasks = deepcopy(self.tasks); tasks[0]['grader'][2] = 'first/grade.py'
        self.assertEqual(before,identity(self.root,tasks,self.bundles))
        for literal in ('--empty=','x'*1024):
            tasks = deepcopy(self.tasks); tasks[0]['grader'].append(literal)
            self.assertNotEqual(before,identity(self.root,tasks,self.bundles))

    def test_artifact_selection_cannot_change_under_the_same_oracle_pin(self):
        before = self.capture()
        self.tasks[0]['artifacts'] = ['result.json']
        selected = self.capture()
        self.assertNotEqual(before, selected)
        self.tasks[0]['artifacts'].append('other.json')
        self.assertNotEqual(selected, self.capture())
        for invalid in (None, ['../outside'], ['result.json','result.json']):
            self.tasks[0]['artifacts'] = invalid
            with self.assertRaises(ValueError): self.capture()

    def test_existing_external_reference_never_reads_private_body_or_becomes_a_literal(self):
        external = self.root/'external.json'; external.write_text('synthetic external mutable reference')
        for value in (str(external),'external.json','--expected='+str(external)):
            tasks = deepcopy(self.tasks); tasks[0]['grader'].append(value)
            with patch('agent_harness_lab.evaluation.oracle.read_regular',side_effect=AssertionError('must reject external path before body read')):
                # Internal refs are visited first; remove them to isolate external-reference handling.
                tasks[0]['grader'] = ['/usr/bin/python3',value]
                with self.assertRaisesRegex(ValueError,'outside'): identity(self.root,tasks[:1],{'first':self.bundles['first']})

    def test_runtime_inside_own_bundle_is_relocation_stable_and_other_task_bundle_is_external(self):
        tasks = deepcopy(self.tasks); tasks[0]['grader'] = [str(self.root/'first/grade.py'),'{workspace}']
        first = identity(self.root,tasks,self.bundles)
        tasks[0]['grader'][0] = 'first/grade.py'
        self.assertEqual(first,identity(self.root,tasks,self.bundles))
        tasks[0]['grader'].append(str(self.root/'second/expected.json'))
        with self.assertRaisesRegex(ValueError,'outside'): identity(self.root,tasks,self.bundles)

    def test_missing_bundles_bad_hashes_paths_and_duplicate_ids_reject(self):
        for mutate in (lambda t,b:b.pop('first'),lambda t,b:b.update(extra=b['first']),
                lambda t,b:b['first'].update(digest='A'*64),lambda t,b:b['first'].update(digest='bad'),
                lambda t,b:b['first'].update(path='../first'),lambda t,b:b['first'].update(path=str(self.root/'first')),
                lambda t,b:t[1].update(id='first'),lambda t,b:b['first'].update(path='missing')):
            tasks,bundles = deepcopy(self.tasks),deepcopy(self.bundles); mutate(tasks,bundles)
            with self.assertRaises(ValueError): identity(self.root,tasks,bundles)
        self.tasks[0]['grader'].append('../escape')
        with self.assertRaises(ValueError): self.capture()

    def test_invalid_or_excessive_inputs_and_unsafe_bundle_members_reject(self):
        for tasks in ([],None,[None],[dict(id='bad',grader=[])],self.tasks*501):
            with self.assertRaises(ValueError): identity(self.root,tasks,self.bundles)
        for value in ('','nul\0arg','x'*4097,'\ud800'):
            tasks = deepcopy(self.tasks); tasks[0]['grader'].append(value)
            with self.assertRaises(ValueError): identity(self.root,tasks,self.bundles)
        with patch('agent_harness_lab.evaluation.oracle.MAX_BYTES',1),self.assertRaises(ValueError): self.capture()
        (self.root/'first/link').symlink_to(self.root/'second/grade.py')
        with self.assertRaises(ValueError): self.capture()

    def test_changes_during_reference_capture_cannot_return_an_old_identity(self):
        from agent_harness_lab.evaluation import oracle
        read = oracle.read_regular
        def mutate(path,maximum):
            raw = read(path,maximum); (self.root/'first/controls.json').write_text('changed during capture')
            return raw
        with patch.object(oracle,'read_regular',side_effect=mutate),self.assertRaisesRegex(ValueError,'changed'):
            self.capture()
