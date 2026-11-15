"""A reviewed adapter version is staged through the journal and rolled back without overwriting later work."""
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation.adapter_deployment import AdapterJournal, check, preview_rollback, rollback, stage
from agent_harness_lab.evaluation.adapters import AdapterSpec, Authorization
from agent_harness_lab.promotion.journal import file_record, snapshot_record
from agent_harness_lab.surfaces import PINS

ADAPTER = '.ahl/adapters/structured-error-v1/adapter.py'
SPEC = '.ahl/adapters/structured-error-v1/spec.json'
V1 = b'def present(envelope):\n    return {"original": envelope}\n'
V2 = b'def present(envelope):\n    return {"original": envelope, "v": 2}\n'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def mapping(raw=V1, **changes):
    data = dict(adapter_id='structured-error-v1', version='1', content_digest=digest(raw), tools=['Read', 'Grep'],
                input_contract='tool-envelope-v1', output_contract='presented-envelope-v1',
                transformations=['identity', 'structured-error-v1'], side_effects='none', filesystem=[], network=[],
                credentials=[], nested_tools=[], limits=dict(max_input_bytes=4096, max_output_bytes=8192, timeout_ms=500),
                previous_digest=None, runtime='claude-code', runtime_version=PINS['claude-code'])
    data.update(changes)
    return data


def v2_mapping(**changes):
    return mapping(V2, version='2', previous_digest=digest(V1), **changes)


@pytest.fixture
def authorization():
    return Authorization('project', ('Read', 'Grep'), ('structured-error-v1',), 'v1')


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'unrelated.txt').write_text('keep')
    return root


@pytest.fixture
def journal(tmp_path, project):
    return AdapterJournal(project, tmp_path / 'private/adapter-journal')


def deploy(journal, authorization, candidate, raw):
    """Stage and apply one candidate that is its own authorized contract."""
    record = stage(journal, AdapterSpec.from_mapping(candidate), authorization, candidate, raw)
    return journal.execute(record['deployment'])


def observe(project):
    """Link, bytes and mode of both artifacts exactly as they are, so a conflict can be shown to leave them alone."""
    return {name: (path.is_symlink(), path.read_bytes() if path.exists() else None,
                   os.lstat(path).st_mode if path.exists() or path.is_symlink() else None)
            for name in (ADAPTER, SPEC) for path in (project / name,)}


def snapshot(project):
    return {p.relative_to(project).as_posix(): p.read_bytes() for p in sorted(project.rglob('*')) if p.is_file()}


def test_staging_a_candidate_writes_nothing_until_the_journal_applies_it(journal, project, authorization):
    record = stage(journal, AdapterSpec.from_mapping(mapping()), authorization, mapping(), V1)

    assert record['status'] == 'prepared'
    assert not (project / '.ahl').exists()


def test_applying_a_staged_candidate_writes_the_exact_bytes_without_an_execute_bit(journal, project, authorization):
    record = deploy(journal, authorization, mapping(), V1)

    assert record['status'] == 'committed'
    assert (project / ADAPTER).read_bytes() == V1
    assert json.loads((project / SPEC).read_bytes()) == AdapterSpec.from_mapping(mapping()).as_mapping()
    assert (project / ADAPTER).stat().st_mode & 0o777 == 0o644
    assert record['metadata']['adapter'] == dict(
        adapter_id='structured-error-v1', content_digest=digest(V1), previous_digest=None,
        spec_digest=AdapterSpec.from_mapping(mapping()).digest, authorization_digest=authorization.digest)


def test_rolling_back_the_first_deployment_removes_only_its_artifacts_and_replays_idempotently(journal, project, authorization):
    record = deploy(journal, authorization, mapping(), V1)
    (project / 'later.txt').write_text('later work')

    result = rollback(journal, record['deployment'], authorization)

    assert not (project / ADAPTER).exists() and not (project / SPEC).exists()
    assert (project / 'unrelated.txt').read_text() == 'keep' and (project / 'later.txt').read_text() == 'later work'
    assert rollback(journal, record['deployment'], authorization)['deployment'] == result['deployment']


def test_rolling_back_a_second_version_restores_the_first_version_bytes_and_contract(journal, project, authorization):
    deploy(journal, authorization, mapping(), V1)
    first = snapshot(project)
    second = deploy(journal, authorization, v2_mapping(), V2)
    assert (project / ADAPTER).read_bytes() == V2

    rollback(journal, second['deployment'], authorization)

    assert snapshot(project) == first
    assert (project / ADAPTER).stat().st_mode & 0o777 == 0o644


def test_preview_changes_nothing_and_reports_the_eligible_files(journal, project, authorization):
    record = deploy(journal, authorization, mapping(), V1)
    before = snapshot(project)

    result = preview_rollback(journal, record['deployment'], authorization)

    assert result == dict(status='rollback_eligible', deployment=record['deployment'], files=sorted([ADAPTER, SPEC]))
    assert snapshot(project) == before


def _edit(project):
    (project / ADAPTER).write_bytes(b'user edit')


def _mode(project):
    (project / ADAPTER).chmod(0o600)


def _delete(project):
    (project / ADAPTER).unlink()


def _symlink(project):
    (project / ADAPTER).unlink()
    (project / ADAPTER).symlink_to(project / 'unrelated.txt')


def _spec_edit(project):
    (project / SPEC).write_bytes(b'{}')


@pytest.mark.parametrize('change', [_edit, _mode, _delete, _symlink, _spec_edit],
                         ids=['bytes-edited', 'mode-changed', 'file-deleted', 'symlink-replaced', 'spec-edited'])
def test_a_concurrent_change_to_a_deployed_artifact_blocks_rollback_and_is_preserved(journal, project, authorization, change):
    record = deploy(journal, authorization, mapping(), V1)
    change(project)
    state = observe(project)

    assert preview_rollback(journal, record['deployment'], authorization)['status'] == 'conflict'
    with pytest.raises(ValueError):
        rollback(journal, record['deployment'], authorization)

    assert observe(project) == state


def test_a_newer_deployment_blocks_rolling_back_the_older_one(journal, project, authorization):
    first = deploy(journal, authorization, mapping(), V1)
    deploy(journal, authorization, v2_mapping(), V2)

    assert preview_rollback(journal, first['deployment'], authorization)['status'] == 'conflict'
    with pytest.raises(ValueError):
        rollback(journal, first['deployment'], authorization)
    assert (project / ADAPTER).read_bytes() == V2


@pytest.mark.parametrize('narrowed', [Authorization('project', ('Read',), ('structured-error-v1',), 'v2'),
                                      Authorization('project', ('Read', 'Grep'), (), 'v2')],
                         ids=['tool-no-longer-authorized', 'adapter-no-longer-authorized'])
def test_rollback_refuses_to_restore_a_version_the_project_no_longer_authorizes(journal, project, authorization, narrowed):
    deploy(journal, authorization, mapping(), V1)
    second = deploy(journal, authorization, v2_mapping(), V2)

    assert preview_rollback(journal, second['deployment'], narrowed)['status'] == 'conflict'
    with pytest.raises(ValueError):
        rollback(journal, second['deployment'], narrowed)
    assert (project / ADAPTER).read_bytes() == V2


def test_rolling_back_a_first_deployment_needs_no_authorization_because_it_only_removes_capability(journal, project, authorization):
    record = deploy(journal, authorization, mapping(), V1)
    revoked = Authorization('project', (), (), 'v2')

    rollback(journal, record['deployment'], revoked)

    assert not (project / ADAPTER).exists()


def _expanded_tools():
    return mapping(tools=['Read', 'Grep', 'Bash'])


def _network():
    return mapping(network=['example.invalid'])


def _other_adapter():
    return mapping(adapter_id='other-adapter')


def _unknown_field():
    return dict(mapping(), extra=1)


@pytest.mark.parametrize('candidate', [_expanded_tools, _network, _other_adapter, _unknown_field],
                         ids=['expanded-tools', 'network-destination', 'changed-adapter-id', 'unknown-field'])
def test_a_candidate_that_expands_the_authorized_contract_is_rejected_before_anything_is_journaled(
        journal, project, authorization, tmp_path, candidate):
    with pytest.raises(ValueError, match='rejected'):
        stage(journal, AdapterSpec.from_mapping(mapping()), authorization, candidate(), V1)

    assert not (project / '.ahl').exists()
    assert not list((tmp_path / 'private/adapter-journal').glob('*.json'))


def test_bytes_that_differ_from_the_declared_digest_are_rejected(journal, project, authorization):
    with pytest.raises(ValueError, match='declared digest'):
        stage(journal, AdapterSpec.from_mapping(mapping()), authorization, mapping(), V1 + b'# tampered\n')

    assert not (project / '.ahl').exists()


def test_a_second_version_that_does_not_name_the_deployed_bytes_as_its_parent_is_rejected(journal, project, authorization):
    deploy(journal, authorization, mapping(), V1)
    candidate = mapping(V2, version='2', previous_digest=digest(b'something else'))

    with pytest.raises(ValueError, match='rejected|follow'):
        stage(journal, AdapterSpec.from_mapping(candidate), authorization, candidate, V2)


def test_a_second_version_is_rejected_when_the_deployed_bytes_were_edited_by_hand(journal, project, authorization):
    deploy(journal, authorization, mapping(), V1)
    _edit(project)

    with pytest.raises(ValueError, match='follow'):
        stage(journal, AdapterSpec.from_mapping(v2_mapping()), authorization, v2_mapping(), V2)
    assert (project / ADAPTER).read_bytes() == b'user edit'


def test_redeploying_the_deployed_bytes_is_rejected(journal, project, authorization):
    deploy(journal, authorization, mapping(), V1)
    again = mapping(previous_digest=digest(V1))

    with pytest.raises(ValueError):
        stage(journal, AdapterSpec.from_mapping(again), authorization, again, V1)


@pytest.mark.parametrize('adapter_id', ['../evil', 'a/b', 'UPPER', '.hidden', 'x y', 'a\nb'],
                         ids=['traversal', 'separator', 'uppercase', 'leading-dot', 'space', 'newline'])
def test_an_adapter_id_that_is_not_a_safe_artifact_name_is_rejected(journal, project, authorization, adapter_id):
    candidate = mapping(adapter_id=adapter_id)
    permissive = Authorization('project', ('Read', 'Grep'), (adapter_id,), 'v1')

    with pytest.raises(ValueError, match='artifact name'):
        stage(journal, AdapterSpec.from_mapping(candidate), permissive, candidate, V1)
    assert not (project / '.ahl').exists()


def test_a_symlinked_artifact_folder_is_refused_and_nothing_is_written_through_it(journal, project, authorization, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (project / '.ahl').symlink_to(outside)

    with pytest.raises(ValueError):
        stage(journal, AdapterSpec.from_mapping(mapping()), authorization, mapping(), V1)
    assert not list(outside.rglob('*'))


@pytest.mark.parametrize('name', ['AGENTS.md', '.ahl/adapters/structured-error-v1/other.py', '.ahl/adapters/../x/adapter.py',
                                  '.claude/skills/s/SKILL.md'], ids=['root-instruction', 'other-file', 'traversal', 'skill'])
def test_the_adapter_journal_owns_no_path_but_the_two_declared_artifacts(journal, project, name):
    with pytest.raises(ValueError):
        journal.prepare({name: None}, {name: snapshot_record(b'x', 0o644)})
    assert not (project / name).exists()


def test_the_adapter_journal_refuses_an_executable_adapter_snapshot(journal):
    with pytest.raises(ValueError, match='executable'):
        journal.prepare({ADAPTER: None}, {ADAPTER: snapshot_record(V1, 0o755)})


def test_a_crash_between_the_two_artifacts_is_recovered_to_the_state_before_the_deployment(journal, project, authorization):
    record = stage(journal, AdapterSpec.from_mapping(mapping()), authorization, mapping(), V1)
    original, failed = os.replace, []

    def interrupt(source, target):
        if Path(target) == project / SPEC and not failed:
            failed.append(True)
            raise OSError('synthetic crash')
        return original(source, target)

    with patch('agent_harness_lab.promotion.journal.os.replace', side_effect=interrupt), pytest.raises(OSError):
        journal.execute(record['deployment'])
    assert file_record(project / ADAPTER) is not None and file_record(project / SPEC) is None

    journal.recover(record['deployment'])

    assert file_record(project / ADAPTER) is None and file_record(project / SPEC) is None
    assert (project / 'unrelated.txt').read_text() == 'keep'


@pytest.mark.parametrize(('candidate', 'raw', 'message'), [
    pytest.param(mapping(tools=['Read', 'Grep', 'Bash']), V1, 'adapter candidate rejected', id='expanded-tool'),
    pytest.param(mapping(network=['example.invalid']), V1, 'adapter candidate rejected', id='added-network'),
    pytest.param(mapping(), V2, 'differ from the declared digest', id='bytes-not-declared'),
    pytest.param(v2_mapping(), V2, 'does not follow the deployed adapter version', id='lineage-skips-the-first-version'),
])
def test_check_rejects_what_staging_rejects_without_journaling_or_writing(project, authorization, tmp_path, candidate, raw, message):
    authorized = AdapterSpec.from_mapping(mapping())

    with pytest.raises(ValueError, match=message):
        check(project, authorized, authorization, candidate, raw)

    assert not (project / '.ahl').exists() and not (tmp_path / 'private').exists()


def test_check_accepts_a_candidate_that_follows_the_deployed_version_and_reports_what_it_would_replace(journal, project, authorization):
    deploy(journal, authorization, mapping(), V1)

    spec, before = check(project, AdapterSpec.from_mapping(mapping()), authorization, v2_mapping(), V2)

    assert spec.content_digest == digest(V2)
    assert before[ADAPTER]['sha256'] == digest(V1) and before[SPEC] is not None
