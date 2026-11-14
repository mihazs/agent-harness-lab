"""Role views copy the methodology skills without bytecode caches, which a concurrent importer may write (#758)."""
import time

import pytest

from agent_harness_lab.evaluation import construction

SKILLS = ('harness-evaluation', 'harness-self-improvement')


@pytest.fixture
def assets(tmp_path):
    root = tmp_path / 'assets'
    for skill in SKILLS:
        scripts = root / 'skills' / skill / 'scripts'
        scripts.mkdir(parents=True)
        (root / 'skills' / skill / 'SKILL.md').write_text('Synthetic skill.')
        (scripts / 'helper.py').write_text('VALUE = 1\n')
    return root


def build_views(assets, tmp_path):
    data = dict(analysis=dict(events=0, cases=0))
    return construction.views(data, assets, tmp_path / 'views', time.monotonic() + 30)


@pytest.mark.parametrize('cache', [
    pytest.param('scripts/__pycache__/helper.cpython-314.pyc', id='pycache-directory'),
    pytest.param('scripts/legacy.pyc', id='loose-pyc')])
def test_views_leave_bytecode_caches_out_and_keep_the_sources(assets, tmp_path, cache):
    for skill in SKILLS:
        path = assets / 'skills' / skill / cache
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'synthetic bytecode')
    views = build_views(assets, tmp_path)
    for role, folder in views.items():
        assert not list(folder.rglob('*.pyc')) and not list(folder.rglob('__pycache__')), role
        assert (folder / 'methodology/harness-evaluation/scripts/helper.py').read_text() == 'VALUE = 1\n'
