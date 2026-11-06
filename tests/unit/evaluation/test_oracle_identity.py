"""Synthetic oracle identity controls; no grader or runtime is executed."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.oracle import capture, identity


class OracleManifest:
    """Two synthetic grader bundles under root and the tasks whose argv reference them."""

    def __init__(self, parent):
        self.root = Path(parent)/'manifest'; self.root.mkdir()
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


@pytest.fixture
def oracle(tmp_path):
    return OracleManifest(tmp_path)


def test_capture_hashes_to_the_exact_existing_identity(oracle):
    oracle.tasks[0]['artifacts'] = ['answer.json']
    value = capture(oracle.root,oracle.tasks,oracle.bundles)
    assert set(value) == {'schema_version','algorithm','tasks'}
    assert value['schema_version'] == 1
    assert value['algorithm'] == 'declared-oracle-argv-v1'
    assert set(value['tasks']) == {'first','second'}
    for name in value['tasks']:
        assert set(value['tasks'][name]) == {'bundle_sha256','artifacts','argv'}
    assert value['tasks']['first']['artifacts'] == ['answer.json']
    assert value['tasks']['second']['artifacts'] is None
    raw = json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
    assert hashlib.sha256(raw).hexdigest() == oracle.capture()


def test_bundle_dependency_is_shared_across_different_task_subsets(oracle):
    oracle.bundles['second'] = dict(oracle.bundles['first'])
    oracle.tasks[1]['grader'] = list(oracle.tasks[0]['grader'])
    first = capture(oracle.root,oracle.tasks[:1],{'first':oracle.bundles['first']})
    second = capture(oracle.root,oracle.tasks[1:],{'second':oracle.bundles['second']})
    together = capture(oracle.root,oracle.tasks,oracle.bundles)
    for value in (first,second,together):
        assert {entry['bundle_sha256'] for entry in value['tasks'].values()} == {oracle.bundles['first']['digest']}
    assert identity(oracle.root,oracle.tasks[:1],{'first':oracle.bundles['first']}) != oracle.capture()


def test_capture_detaches_every_mutable_caller_container(oracle):
    oracle.tasks[0]['artifacts'] = ['answer.json']
    tasks,bundles = deepcopy(oracle.tasks),deepcopy(oracle.bundles)
    value = capture(oracle.root,oracle.tasks,oracle.bundles)
    assert (oracle.tasks,oracle.bundles) == (tasks,bundles)
    frozen = deepcopy(value)
    oracle.tasks[0]['artifacts'].append('other.json'); oracle.tasks[0]['grader'].append('new literal')
    oracle.bundles['first']['path'] = 'changed'
    assert value == frozen
    value['tasks']['first']['artifacts'].append('captured-only.json')
    value['tasks']['first']['argv'][0]['literal'] = 'captured-only'
    assert 'captured-only.json' not in oracle.tasks[0]['artifacts']
    assert oracle.tasks[0]['grader'][0] == tasks[0]['grader'][0]


def test_v0441_frozen_identity_bytes_are_unchanged(oracle):
    # Captured before extraction at cc18399984041b16cb70a67eba18fd58d7926382.
    oracle.tasks[0]['artifacts'] = ['answer.json']
    for name in oracle.bundles:
        for path in (oracle.root/name).iterdir(): path.chmod(0o644)
        oracle.bundles[name]['digest'] = digest(oracle.root/name)
    assert oracle.capture() == '1fcaa3679f0080b65f878acefe03f0d89b7cec812b1a2b1d633b1586f2d6bd52'


def test_relocation_bundle_renaming_and_task_map_order_preserve_identity(oracle):
    before = oracle.capture(); relocated = oracle.root.parent/'relocated'; shutil.copytree(oracle.root,relocated)
    tasks = deepcopy(oracle.tasks); bundles = dict(reversed(list(oracle.bundles.items())))
    for task in tasks:
        task['grader'] = [arg.replace(str(oracle.root),str(relocated)) for arg in task['grader']]
    (relocated/'first').rename(relocated/'renamed')
    bundles['first'] = {**bundles['first'],'path':'renamed'}
    tasks[0]['grader'] = [arg.replace(str(relocated/'first'),str(relocated/'renamed')) for arg in tasks[0]['grader']]
    assert before == identity(relocated,list(reversed(tasks)),bundles)


@pytest.mark.parametrize('name', [pytest.param(name, id=name) for name in
                                  ('grade.py','expected.json','controls.json','helper.py')])


def test_every_bundle_file_and_file_mode_is_bound_even_without_argv_reference(oracle, name):
    before = oracle.capture(); target = oracle.root/'first'/name
    target.write_text('changed synthetic oracle input')
    with pytest.raises(ValueError, match='integrity'): oracle.capture()
    oracle.bundles['first']['digest'] = digest(oracle.root/'first')
    assert before != oracle.capture()


def test_bundle_file_mode_is_bound_even_without_argv_reference(oracle):
    before = oracle.capture(); (oracle.root/'first/grade.py').chmod(0o750)
    with pytest.raises(ValueError, match='integrity'): oracle.capture()
    oracle.bundles['first']['digest'] = digest(oracle.root/'first')
    assert before != oracle.capture()


def test_argv_flags_literals_placeholders_and_external_runtime_path_are_bound(oracle):
    before = oracle.capture()
    for index in (0,1,3,4,6):
        tasks = deepcopy(oracle.tasks); tasks[0]['grader'][index] += '-changed'
        assert before != identity(oracle.root,tasks,oracle.bundles)
    tasks = deepcopy(oracle.tasks); tasks[0]['grader'][2] = 'first/grade.py'
    assert before == identity(oracle.root,tasks,oracle.bundles)
    for literal in ('--empty=','x'*1024):
        tasks = deepcopy(oracle.tasks); tasks[0]['grader'].append(literal)
        assert before != identity(oracle.root,tasks,oracle.bundles)


def test_artifact_selection_cannot_change_under_the_same_oracle_pin(oracle):
    before = oracle.capture()
    oracle.tasks[0]['artifacts'] = ['result.json']
    selected = oracle.capture()
    assert before != selected
    oracle.tasks[0]['artifacts'].append('other.json')
    assert selected != oracle.capture()
    for invalid in (None, ['../outside'], ['result.json','result.json']):
        oracle.tasks[0]['artifacts'] = invalid
        with pytest.raises(ValueError): oracle.capture()


def test_existing_external_reference_never_reads_private_body_or_becomes_a_literal(oracle):
    external = oracle.root/'external.json'; external.write_text('synthetic external mutable reference')
    for value in (str(external),'external.json','--expected='+str(external)):
        tasks = deepcopy(oracle.tasks); tasks[0]['grader'].append(value)
        with patch('agent_harness_lab.evaluation.oracle.read_regular',side_effect=AssertionError('must reject external path before body read')):
            # Internal refs are visited first; remove them to isolate external-reference handling.
            tasks[0]['grader'] = ['/usr/bin/python3',value]
            with pytest.raises(ValueError, match='outside'): identity(oracle.root,tasks[:1],{'first':oracle.bundles['first']})


def test_runtime_inside_own_bundle_is_relocation_stable_and_other_task_bundle_is_external(oracle):
    tasks = deepcopy(oracle.tasks); tasks[0]['grader'] = [str(oracle.root/'first/grade.py'),'{workspace}']
    first = identity(oracle.root,tasks,oracle.bundles)
    tasks[0]['grader'][0] = 'first/grade.py'
    assert first == identity(oracle.root,tasks,oracle.bundles)
    tasks[0]['grader'].append(str(oracle.root/'second/expected.json'))
    with pytest.raises(ValueError, match='outside'): identity(oracle.root,tasks,oracle.bundles)


def test_missing_bundles_bad_hashes_paths_and_duplicate_ids_reject(oracle):
    for mutate in (lambda t,b:b.pop('first'),lambda t,b:b.update(extra=b['first']),
            lambda t,b:b['first'].update(digest='A'*64),lambda t,b:b['first'].update(digest='bad'),
            lambda t,b:b['first'].update(path='../first'),lambda t,b:b['first'].update(path=str(oracle.root/'first')),
            lambda t,b:t[1].update(id='first'),lambda t,b:b['first'].update(path='missing')):
        tasks,bundles = deepcopy(oracle.tasks),deepcopy(oracle.bundles); mutate(tasks,bundles)
        with pytest.raises(ValueError): identity(oracle.root,tasks,bundles)
    oracle.tasks[0]['grader'].append('../escape')
    with pytest.raises(ValueError): oracle.capture()


def test_invalid_or_excessive_inputs_and_unsafe_bundle_members_reject(oracle):
    for tasks in ([],None,[None],[dict(id='bad',grader=[])],oracle.tasks*501):
        with pytest.raises(ValueError): identity(oracle.root,tasks,oracle.bundles)
    for value in ('','nul\0arg','x'*4097,'\ud800'):
        tasks = deepcopy(oracle.tasks); tasks[0]['grader'].append(value)
        with pytest.raises(ValueError): identity(oracle.root,tasks,oracle.bundles)
    with patch('agent_harness_lab.evaluation.oracle.MAX_BYTES',1),pytest.raises(ValueError): oracle.capture()
    (oracle.root/'first/link').symlink_to(oracle.root/'second/grade.py')
    with pytest.raises(ValueError): oracle.capture()


def test_changes_during_reference_capture_cannot_return_an_old_identity(oracle):
    from agent_harness_lab.evaluation import oracle as oracle_module
    read = oracle_module.read_regular
    def mutate(path,maximum):
        raw = read(path,maximum); (oracle.root/'first/controls.json').write_text('changed during capture')
        return raw
    with patch.object(oracle_module,'read_regular',side_effect=mutate),pytest.raises(ValueError, match='changed'):
        oracle.capture()
