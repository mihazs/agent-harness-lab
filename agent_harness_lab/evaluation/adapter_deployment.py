"""Journaled staging and conflict-safe rollback of one reviewed presentation adapter version.

stage() turns a candidate spec and its exact bytes into a prepared journal deployment of two files,
.ahl/adapters/<id>/adapter.py and spec.json, after review_candidate_spec rejects any expanded authority, the bytes match
the declared digest and the candidate names the bytes actually deployed as its parent. Nothing is written until the
caller applies the deployment with AdapterJournal.execute. preview_rollback() and rollback() reuse the promotion
journal: a later edit, mode change, symlink, deletion or newer deployment is a conflict that writes nothing, and a
rollback never restores a version the project's current authorization no longer covers (removing the first version
always passes, because it only removes capability). Nothing calls this module by default, no hook or runtime is
touched, and no behavioral rollout is automatic. Keep the journal folder separate from the project's promotion journal.
"""
import base64
import json
import re

from ..promotion.journal import Journal, file_record, snapshot_record
from .adapters import AdapterSpec, review_candidate_spec, verify_adapter_bytes
from .integrity import contained

ROOT = '.ahl/adapters'
ARTIFACT_MODE = 0o644  # adapter bytes are mounted read-only into the sandbox and never executed in place
_OWNED = re.compile(re.escape(ROOT) + r'/[a-z0-9][a-z0-9._-]{0,127}/(?:adapter\.py|spec\.json)')


class AdapterJournal(Journal):
    """The generic transaction journal, owning exactly the two declared artifacts of each adapter and nothing else."""

    def owned(self, name, target=None, *, probe=True, admitted=frozenset()):
        if not isinstance(name, str) or not _OWNED.fullmatch(name):
            raise ValueError('transaction outside the declared adapter artifacts')
        if target is not None and target['mode'] & 0o111:
            raise ValueError('adapter artifacts are never executable')
        return contained(self.project, name)


def _artifacts(adapter_id):
    names = (f'{ROOT}/{adapter_id}/adapter.py', f'{ROOT}/{adapter_id}/spec.json')
    if not all(_OWNED.fullmatch(name) for name in names):
        raise ValueError('adapter id is not a safe artifact name')
    return names


def check(project, authorized, authorization, candidate, adapter_bytes):
    """Every rejection stage() makes, from the project's current bytes alone and with nothing journaled or written.

    Returns the parsed spec and the before records of its two artifacts."""
    review = review_candidate_spec(authorized, candidate, authorization)
    if review['status'] != 'within_authorized_capabilities':
        raise ValueError('adapter candidate rejected: ' + ', '.join(review['reasons']))
    spec = candidate if isinstance(candidate, AdapterSpec) else AdapterSpec.from_mapping(candidate)
    if not verify_adapter_bytes(spec, adapter_bytes):
        raise ValueError('adapter bytes differ from the declared digest')
    names = _artifacts(spec.adapter_id)
    before = {name: file_record(contained(project, name)) for name in names}
    deployed = (before[names[0]] or {}).get('sha256')
    if spec.previous_digest != deployed or spec.content_digest == deployed:
        raise ValueError('candidate does not follow the deployed adapter version')
    return spec, before


def stage(journal, authorized, authorization, candidate, adapter_bytes):
    """Prepare (never apply) the deployment of one candidate; any rejection happens before anything is journaled."""
    spec, before = check(journal.project, authorized, authorization, candidate, adapter_bytes)
    adapter, spec_file = before
    spec_bytes = (json.dumps(spec.as_mapping(), sort_keys=True, indent=2) + '\n').encode()
    after = {adapter: snapshot_record(adapter_bytes, ARTIFACT_MODE), spec_file: snapshot_record(spec_bytes, ARTIFACT_MODE)}
    metadata = dict(adapter=dict(adapter_id=spec.adapter_id, content_digest=spec.content_digest,
                                 previous_digest=spec.previous_digest, spec_digest=spec.digest,
                                 authorization_digest=authorization.digest))
    return journal.prepare(before, after, metadata)


def _restores_authorized(record, authorization):
    """Whether the state a rollback of record restores is covered by the authorization in force now; none is."""
    files = {name.rsplit('/', 1)[1]: snapshot for name, snapshot in record['before'].items()}
    if not any(files.values()):
        return True
    try:
        content = {name: base64.b64decode(files[name]['content'], validate=True) for name in ('adapter.py', 'spec.json')}
        spec = AdapterSpec.from_mapping(json.loads(content['spec.json']))
        return (spec.adapter_id in authorization.adapter_ids and set(spec.tools) <= set(authorization.tools)
                and verify_adapter_bytes(spec, content['adapter.py']))
    except (KeyError, TypeError, ValueError):  # a half-present or unreadable version fails closed
        return False


def preview_rollback(journal, deployment, authorization):
    """The journal's verdict, downgraded to a conflict when the restored version is no longer authorized."""
    result = journal.preview_rollback(deployment)
    if result['status'] == 'rollback_eligible' and not _restores_authorized(journal.load(deployment), authorization):
        return dict(status='conflict', reason='previous_version_not_authorized')
    return result


def rollback(journal, deployment, authorization):
    """Restore the exact previous state of one deployment as a new journal event, or raise without writing."""
    if preview_rollback(journal, deployment, authorization).get('reason') == 'previous_version_not_authorized':
        raise ValueError('rollback restores an adapter version the project no longer authorizes')
    return journal.rollback(deployment)
