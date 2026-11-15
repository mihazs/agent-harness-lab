"""The adapter authorization manifest is controller-owned: outside the project, bound to it and strict about its fields."""
import json
import os

import pytest

from agent_harness_lab.evaluation.adapter_cli import MAX_MANIFEST, load_manifest
from tests.support.adapter_command import ADAPTER_ID, manifest_mapping, spec_mapping, write_json, write_manifest


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    return root


@pytest.fixture
def private(tmp_path):
    return tmp_path / 'private'


def test_a_valid_manifest_yields_the_authorization_and_the_authorized_spec_of_each_adapter(project, private):
    path = write_manifest(private, project)

    authorization, authorized = load_manifest(path, project)

    assert authorization.tools == ('Read', 'Grep') and authorization.adapter_ids == (ADAPTER_ID,)
    assert authorized[ADAPTER_ID].as_mapping() == spec_mapping()


def test_the_digest_of_the_authorization_changes_when_the_manifest_grants_another_tool(project, private):
    narrow = load_manifest(write_manifest(private, project), project)[0]
    wide = load_manifest(write_manifest(private, project, 'wide.json', tools=['Read', 'Grep', 'Bash']), project)[0]

    assert narrow.digest != wide.digest


@pytest.mark.parametrize('change', [
    pytest.param(dict(schema='ahl.adapter-authorization.v0'), id='unknown-schema'),
    pytest.param(dict(project='0' * 24), id='another-project'),
    pytest.param(dict(version=1), id='non-text-version'),
    pytest.param(dict(tools='Read'), id='tools-not-a-list'),
    pytest.param(dict(tools=['Read', 7]), id='tool-not-text'),
    pytest.param(dict(adapters=[]), id='no-adapter'),
    pytest.param(dict(adapters=[spec_mapping(), spec_mapping()]), id='duplicate-adapter'),
    pytest.param(dict(adapters=[spec_mapping(network=['example.invalid'])]), id='adapter-with-a-grant'),
    pytest.param(dict(adapters=[{k: v for k, v in spec_mapping().items() if k != 'limits'}]), id='adapter-missing-a-field'),
])
def test_a_manifest_that_departs_from_the_declared_contract_is_refused(project, private, change):
    path = write_manifest(private, project, **change)

    with pytest.raises(ValueError):
        load_manifest(path, project)


@pytest.mark.parametrize('drop', ['schema', 'project', 'version', 'tools', 'adapters'])
def test_a_manifest_missing_a_field_is_refused(project, private, drop):
    data = manifest_mapping(project)
    del data[drop]

    with pytest.raises(ValueError, match='exactly the declared fields'):
        load_manifest(write_json(private / 'm.json', data), project)


def test_a_manifest_with_an_unknown_field_is_refused(project, private):
    with pytest.raises(ValueError, match='exactly the declared fields'):
        load_manifest(write_json(private / 'm.json', manifest_mapping(project, extra=1)), project)


def test_a_manifest_inside_the_project_is_refused_because_the_optimizer_can_edit_it(project):
    path = write_manifest(project / 'config', project)

    with pytest.raises(ValueError, match='outside the project'):
        load_manifest(path, project)


@pytest.mark.parametrize('mode', [pytest.param(0o660, id='group-writable'), pytest.param(0o602, id='other-writable')])
def test_a_manifest_that_others_can_write_is_refused(project, private, mode):
    path = write_manifest(private, project)
    os.chmod(path, mode)

    with pytest.raises(ValueError, match='writable by group or others'):
        load_manifest(path, project)


def test_a_symlinked_manifest_is_refused(project, private):
    real = write_manifest(private, project)
    link = private / 'link.json'
    link.symlink_to(real)

    with pytest.raises(ValueError):
        load_manifest(link, project)


def test_a_manifest_over_the_byte_bound_is_refused(project, private):
    path = private / 'big.json'
    private.mkdir()
    path.write_text(' ' * (MAX_MANIFEST + 1))
    os.chmod(path, 0o600)

    with pytest.raises(ValueError):
        load_manifest(path, project)


def test_a_manifest_with_a_duplicate_key_is_refused(project, private):
    path = private / 'dup.json'
    private.mkdir()
    path.write_text(json.dumps(manifest_mapping(project))[:-1] + ', "version": "2"}')
    os.chmod(path, 0o600)

    with pytest.raises(ValueError):
        load_manifest(path, project)


def test_a_missing_manifest_is_a_file_error_not_an_authorization(project, private):
    with pytest.raises(FileNotFoundError):
        load_manifest(private / 'absent.json', project)
