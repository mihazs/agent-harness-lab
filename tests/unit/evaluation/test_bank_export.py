"""Relative bank exports retain frozen oracles and reject unsafe destinations."""
from contextlib import chdir
import json
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest

import agent_harness_lab
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.bank_tools import export_manifest, frozen_grader


@pytest.fixture
def root(tmp_path):
    source = Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1'
    shutil.copytree(source,tmp_path/'input')
    return tmp_path


def test_relative_source_and_nested_destination_produce_usable_frozen_manifest(root):
    with chdir(root):
        manifest = export_manifest('input/bank.json','nested/export')
    output = root/'nested/export'
    assert json.loads((output/'manifest.json').read_text()) == manifest
    for task in manifest['tasks']:
        bundle,allowed = frozen_grader(output,task,manifest['grader_bundles'][task['id']])
        assert bundle.is_dir(); assert allowed
        assert (output/task['fixture']).is_dir()
    for control in manifest['grader_self_tests']:
        assert (output/control['fixture']).is_dir()


def test_cli_relative_destination_exports_without_upgrading_independence(root):
    with chdir(root), patch('builtins.print') as printed:
        code = main(['bank','--manifest','input/bank.json','--export','export'])
    assert code == 3
    result = json.loads(printed.call_args.args[0])
    assert not result['certification_eligible']
    assert result['export']['tasks'] == 7
    assert (root/'export/manifest.json').is_file()


def test_parent_traversal_cannot_hide_destination_inside_source(root):
    (root/'alias').mkdir()
    with chdir(root), pytest.raises(ValueError):
        export_manifest('input/bank.json','alias/../input/export')
    assert not (root/'input/export').exists()


def test_explicit_adversarial_export_ceiling_preserves_the_default(root):
    with chdir(root), patch('builtins.print'):
        with pytest.raises(SystemExit) as refused:
            main(['bank','--adversarial','--export','too-small'])
        assert refused.value.code == 2
        assert not (root/'too-small').exists()
        code=main(['bank','--adversarial','--export','adversarial','--max-trials','28'])
    assert code == 3
    manifest=json.loads((root/'adversarial/manifest.json').read_text())
    assert (len(manifest['tasks']),manifest['max_trials']) == (14,28)


@pytest.mark.parametrize('limit',[
    pytest.param(True,id='bool'),pytest.param(0,id='zero'),pytest.param(-1,id='negative'),
    pytest.param(1.5,id='fractional'),pytest.param(10001,id='above-maximum')])
def test_invalid_export_ceilings_refuse_before_writing(root,limit):
    with pytest.raises(ValueError):
        export_manifest(root/'input/bank.json',root/'invalid',limit)
    assert not (root/'invalid').exists()


@pytest.mark.parametrize('output',[
    pytest.param('link/export',id='through-symlink'),pytest.param('input/export',id='inside-source'),
    pytest.param('existing',id='existing-output')])
def test_symlink_source_overlap_and_existing_output_refuse_without_writes(root,output):
    (root/'link').symlink_to(root,target_is_directory=True)
    existing = root/'existing'; existing.mkdir(); (existing/'keep').write_text('preserve')
    with chdir(root), pytest.raises((ValueError,FileExistsError)):
        export_manifest('input/bank.json',output)
    assert not (root/'export').exists()
    assert not (root/'input/export').exists()
    assert [p.name for p in existing.iterdir()] == ['keep']
    assert (existing/'keep').read_text() == 'preserve'
