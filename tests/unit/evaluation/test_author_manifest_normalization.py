"""Authored grader digests: dropped only where the controller recomputes them, refused on bank conversion.

Reported defect class (#729): the controller dropped authored digests on every author path, so bank conversion,
which must stay strict about controller-owned fields, silently accepted a manifest that claimed its own digest.
"""
import json
import time

import pytest

from agent_harness_lab.evaluation import construction
from agent_harness_lab.evaluation.working_state import WorkingState

DIGESTS = [pytest.param('0' * 64, id='wrong-hex'), pytest.param('', id='empty'), pytest.param(None, id='null')]


def author_snapshot(tmp_path, bundle):
    work = tmp_path / 'author'
    work.mkdir()
    manifest = dict(skills=[], harness_paths=[], tasks=[dict(id='one')], grader_bundles={'one': bundle})
    (work / 'manifest.json').write_text(json.dumps(manifest))
    return WorkingState(work, time.monotonic() + 30).snapshot


def normalized_manifest(snapshot):
    return json.loads(next(raw for name, _, raw in snapshot.files if name == 'manifest.json'))


@pytest.mark.parametrize('digest', DIGESTS)
def test_normalization_refuses_an_authored_bundle_digest_by_default(tmp_path, digest):
    snapshot = author_snapshot(tmp_path, dict(path='oracle', digest=digest))
    with pytest.raises(ValueError, match='controller owns bundle freezing'):
        construction.normalize_author_snapshot(snapshot, tmp_path / 'published')


@pytest.mark.parametrize('digest', DIGESTS)
def test_normalization_drops_an_authored_bundle_digest_when_the_caller_asks_for_it(tmp_path, digest):
    snapshot = author_snapshot(tmp_path, dict(path='oracle', digest=digest))
    after = construction.normalize_author_snapshot(snapshot, tmp_path / 'published', drop_digests=True)
    assert normalized_manifest(after)['grader_bundles'] == {'one': dict(path='oracle')}


def test_normalization_without_authored_digests_is_the_same_with_and_without_the_drop(tmp_path):
    snapshot = author_snapshot(tmp_path, dict(path='oracle'))
    kept = construction.normalize_author_snapshot(snapshot, tmp_path / 'published')
    dropped = construction.normalize_author_snapshot(snapshot, tmp_path / 'published', drop_digests=True)
    assert normalized_manifest(kept) == normalized_manifest(dropped)
