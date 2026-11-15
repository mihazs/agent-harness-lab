"""`ahl adapters` reviews a candidate against a controller-owned manifest, stages it through a private journal and rolls it back."""
import json
import os

import pytest

from agent_harness_lab import cli
from agent_harness_lab.promotion.journal import Journal
from tests.support.adapter_command import (ADAPTER, SPEC, V1, V2, digest, spec_mapping, v2_mapping,
                                           write_candidate, write_manifest)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'unrelated.txt').write_text('keep')
    return root


@pytest.fixture
def private(tmp_path, monkeypatch):
    monkeypatch.setenv('AHL_STATE_HOME', str(tmp_path / 'state'))
    return tmp_path / 'private'


@pytest.fixture
def manifest(project, private):
    return write_manifest(private, project)


def run(capsys, *argv):
    code = cli.main([str(item) for item in argv])
    out, err = capsys.readouterr()
    return code, json.loads(out) if out.strip() else None, err


def stage(capsys, project, manifest, files, *extra):
    spec, adapter = files
    return run(capsys, 'adapters', 'stage', project, '--manifest', manifest, '--candidate', spec, '--adapter-bytes', adapter, *extra)


def deploy(capsys, project, manifest, private, mapping=None, raw=V1, name='candidate'):
    files = write_candidate(private, mapping or spec_mapping(), raw, name)
    reviewed = stage(capsys, project, manifest, files)[1]
    code, packet, _ = stage(capsys, project, manifest, files, '--authorize-spec', reviewed['candidate_spec_sha256'])
    assert code == 0 and packet['status'] == 'deployed'
    return packet


def tree(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), os.lstat(p).st_mode) for p in sorted(root.rglob('*')) if p.is_file()}


def test_reviewing_a_candidate_prints_its_digest_and_writes_nothing(capsys, project, private, manifest, tmp_path):
    before = tree(project)

    code, packet, err = stage(capsys, project, manifest, write_candidate(private, spec_mapping(), V1))

    assert code == 0 and err == ''
    assert packet['status'] == 'awaiting_review' and packet['reasons'] == []
    assert packet['files'] == [ADAPTER, SPEC]
    assert packet['executes_adapter_code'] is False and packet['automatic_promotion'] is False
    assert packet['certification'] == 'not_established' and packet['behavioral_effect'] == 'not_established'
    assert packet['runtime']['status'] == 'unsupported'
    assert '--authorize-spec ' + packet['candidate_spec_sha256'] in packet['authorize_with']
    assert tree(project) == before and not (tmp_path / 'state').exists()


def test_authorizing_the_exact_digest_deploys_both_files_without_an_execute_bit(capsys, project, private, manifest):
    packet = deploy(capsys, project, manifest, private)

    assert (project / ADAPTER).read_bytes() == V1 and (project / ADAPTER).stat().st_mode & 0o777 == 0o644
    assert json.loads((project / SPEC).read_bytes())['content_digest'] == digest(V1)
    assert (project / 'unrelated.txt').read_text() == 'keep'
    assert '--deployment ' + packet['deployment'] in packet['rollback_with']


def test_a_digest_naming_another_candidate_is_invalid_input_and_writes_nothing(capsys, project, private, manifest):
    before = tree(project)

    code, packet, err = stage(capsys, project, manifest, write_candidate(private, spec_mapping(), V1), '--authorize-spec', '0' * 64)

    assert code == 2 and packet is None and 'different candidate spec' in err
    assert tree(project) == before


@pytest.mark.parametrize(('change', 'reason'), [
    pytest.param(dict(tools=['Read', 'Grep', 'Bash']), 'expanded_tools', id='new-tool'),
    pytest.param(dict(limits=dict(max_input_bytes=8192, max_output_bytes=8192, timeout_ms=500)), 'expanded_limit_max_input_bytes', id='raised-limit'),
    pytest.param(dict(network=['example.invalid']), 'cannot hold network', id='network-destination'),
    pytest.param(dict(filesystem=['/etc']), 'cannot hold filesystem', id='path-permission'),
    pytest.param(dict(credentials=['TOKEN']), 'cannot hold credentials', id='credential-dependency'),
    pytest.param(dict(nested_tools=['Bash']), 'cannot hold nested_tools', id='nested-tool-call'),
    pytest.param(dict(side_effects='writes'), 'side effects are not permitted', id='side-effect-class'),
])
def test_a_candidate_that_expands_the_authorized_contract_is_blocked_and_leaves_no_trace(capsys, project, private, manifest, tmp_path, change, reason):
    before = tree(project)

    code, packet, _ = stage(capsys, project, manifest, write_candidate(private, spec_mapping(**change), V1))

    assert code == 3 and packet['status'] == 'blocked' and any(reason in text for text in packet['reasons'])
    assert 'authorize_with' not in packet and tree(project) == before and not (tmp_path / 'state').exists()


def test_a_blocked_candidate_cannot_be_deployed_by_naming_its_digest(capsys, project, private, manifest):
    files = write_candidate(private, spec_mapping(network=['example.invalid']), V1)
    digest_ = stage(capsys, project, manifest, files)[1]['candidate_spec_sha256']

    code, packet, _ = stage(capsys, project, manifest, files, '--authorize-spec', digest_)

    assert code == 3 and packet['status'] == 'blocked' and not (project / '.ahl').exists()


def test_a_candidate_for_an_adapter_the_manifest_does_not_authorize_is_blocked(capsys, project, private, manifest):
    code, packet, _ = stage(capsys, project, manifest, write_candidate(private, spec_mapping(adapter_id='other-adapter'), V1))

    assert code == 3 and packet['reasons'] == ['adapter_not_authorized'] and not (project / '.ahl').exists()


def test_a_candidate_whose_bytes_differ_from_the_declared_digest_is_invalid_input(capsys, project, private, manifest):
    code, packet, err = stage(capsys, project, manifest, write_candidate(private, spec_mapping(), V2))

    assert code == 2 and packet is None and 'differ from the declared digest' in err and not (project / '.ahl').exists()


@pytest.mark.parametrize('spec_text', [
    pytest.param('[]', id='not-an-object'),
    pytest.param('{"adapter_id": "structured-error-v1", "adapter_id": "x"}', id='duplicate-key'),
    pytest.param('not json', id='not-json'),
])
def test_a_malformed_candidate_file_is_invalid_input(capsys, project, private, manifest, spec_text):
    spec, adapter = write_candidate(private, spec_mapping(), V1)
    spec.write_text(spec_text)

    code, packet, err = stage(capsys, project, manifest, (spec, adapter))

    assert code == 2 and packet is None and err.startswith('ahl adapters:') and not (project / '.ahl').exists()


def test_a_manifest_inside_the_project_cannot_authorize_anything(capsys, project, private):
    inside = write_manifest(project / 'config', project)

    code, packet, err = stage(capsys, project, inside, write_candidate(private, spec_mapping(), V1))

    assert code == 2 and packet is None and 'outside the project' in err and not (project / '.ahl').exists()


def test_a_manifest_written_for_another_project_cannot_authorize_this_one(capsys, project, private, tmp_path):
    other = tmp_path / 'other'
    other.mkdir()

    code, packet, err = stage(capsys, project, write_manifest(private, other), write_candidate(private, spec_mapping(), V1))

    assert code == 2 and 'different project' in err and not (project / '.ahl').exists()


def test_staging_without_a_manifest_is_a_usage_error_so_nothing_is_authorized_by_default(capsys, project, private):
    spec, adapter = write_candidate(private, spec_mapping(), V1)

    with pytest.raises(SystemExit) as stopped:
        cli.main(['adapters', 'stage', str(project), '--candidate', str(spec), '--adapter-bytes', str(adapter)])

    assert stopped.value.code == 2 and not (project / '.ahl').exists()


def test_a_second_version_must_follow_the_deployed_one(capsys, project, private, manifest):
    code, packet, err = stage(capsys, project, manifest, write_candidate(private, v2_mapping(), V2))

    assert code == 2 and 'does not follow the deployed adapter version' in err and not (project / '.ahl').exists()
    deploy(capsys, project, manifest, private)
    deploy(capsys, project, manifest, private, v2_mapping(), V2, 'second')
    assert (project / ADAPTER).read_bytes() == V2


def test_previewing_a_rollback_changes_nothing_and_names_the_command_that_restores(capsys, project, private, manifest):
    packet = deploy(capsys, project, manifest, private)
    before = tree(project)

    code, preview, _ = run(capsys, 'adapters', 'rollback', project, '--manifest', manifest, '--deployment', packet['deployment'])

    assert code == 0 and preview['status'] == 'rollback_eligible' and preview['preview']['files'] == [ADAPTER, SPEC]
    assert '--execute' in preview['execute_with'] and tree(project) == before


def test_rolling_back_the_second_version_restores_the_first_and_replays_idempotently(capsys, project, private, manifest):
    deploy(capsys, project, manifest, private)
    first = tree(project)
    second = deploy(capsys, project, manifest, private, v2_mapping(), V2, 'second')
    arguments = ('adapters', 'rollback', project, '--manifest', manifest, '--deployment', second['deployment'], '--execute')

    code, done, _ = run(capsys, *arguments)
    replay_code, replay, _ = run(capsys, *arguments)

    assert code == 0 and done['status'] == 'rolled_back' and tree(project) == first
    assert replay_code == 0 and replay['rollback_deployment'] == done['rollback_deployment']


def test_a_later_edit_makes_the_rollback_a_conflict_that_keeps_the_edit(capsys, project, private, manifest):
    packet = deploy(capsys, project, manifest, private)
    (project / ADAPTER).write_bytes(V1 + b'# local change\n')
    before = tree(project)

    code, result, _ = run(capsys, 'adapters', 'rollback', project, '--manifest', manifest, '--deployment', packet['deployment'], '--execute')

    assert code == 3 and result['status'] == 'conflict' and result['preview']['reason'] == 'intervening_file_path_or_mode_change'
    assert tree(project) == before


def test_a_rollback_that_would_restore_a_version_the_manifest_no_longer_covers_is_a_conflict(capsys, project, private, manifest):
    deploy(capsys, project, manifest, private)
    second = deploy(capsys, project, manifest, private, v2_mapping(), V2, 'second')
    narrowed = write_manifest(private, project, 'narrow.json', tools=['Read'], adapters=[spec_mapping(tools=['Read'])])
    before = tree(project)

    code, result, _ = run(capsys, 'adapters', 'rollback', project, '--manifest', narrowed, '--deployment', second['deployment'], '--execute')

    assert code == 3 and result['preview']['reason'] == 'previous_version_not_authorized' and tree(project) == before


@pytest.mark.parametrize('deployment', [
    pytest.param('not-a-deployment', id='malformed'),
    pytest.param('../' * 8 + 'a' * 20, id='traversal'),
    pytest.param('0' * 32, id='unknown'),
])
def test_an_invalid_or_unknown_deployment_is_invalid_input_and_creates_no_journal(capsys, project, private, manifest, tmp_path, deployment):
    code, result, err = run(capsys, 'adapters', 'rollback', project, '--manifest', manifest, '--deployment', deployment, '--execute')

    assert code == 2 and result is None and err.startswith('ahl adapters:') and not (tmp_path / 'state').exists()


def test_a_failure_while_writing_returns_both_files_to_their_previous_state(capsys, project, private, manifest, monkeypatch):
    real = Journal._write_snapshot
    calls = []

    def fail_on_second_file(self, *args, **kwargs):
        calls.append(args[0])
        if len(calls) == 2:
            raise OSError('simulated disk failure')
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Journal, '_write_snapshot', fail_on_second_file)
    files = write_candidate(private, spec_mapping(), V1)
    digest_ = stage(capsys, project, manifest, files)[1]['candidate_spec_sha256']

    code, packet, err = stage(capsys, project, manifest, files, '--authorize-spec', digest_)

    assert code == 1 and packet is None and 'simulated disk failure' in err
    assert not (project / ADAPTER).exists() and not (project / SPEC).exists()
    monkeypatch.setattr(Journal, '_write_snapshot', real)
    assert stage(capsys, project, manifest, files, '--authorize-spec', digest_)[1]['status'] == 'deployed'


def test_a_crashed_earlier_deployment_is_undone_before_the_next_one_starts(capsys, project, private, manifest, monkeypatch):
    real = Journal._write_snapshot
    calls = []

    def die_after_first_file(self, *args, **kwargs):
        calls.append(args[0])
        if len(calls) == 2:
            raise KeyboardInterrupt
        return real(self, *args, **kwargs)

    files = write_candidate(private, spec_mapping(), V1)
    digest_ = stage(capsys, project, manifest, files)[1]['candidate_spec_sha256']
    with monkeypatch.context() as crash:  # a run that dies without undoing anything, as a killed process does
        crash.setattr(Journal, '_write_snapshot', die_after_first_file)
        crash.setattr('agent_harness_lab.evaluation.adapter_cli._undo_unfinished', lambda journal: None)
        code, _, err = stage(capsys, project, manifest, files, '--authorize-spec', digest_)
    assert code == 1 and 'interrupted' in err and (project / ADAPTER).exists() and not (project / SPEC).exists()

    code, packet, _ = stage(capsys, project, manifest, files, '--authorize-spec', digest_)

    assert code == 0 and packet['status'] == 'deployed' and (project / SPEC).exists()
