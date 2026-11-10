"""Synthetic staged skill assets require an actual filename mention."""
import pytest

from tests.integration.workflow.test_skill_bundle_lifecycle import ALPHA, ENTRY, Stage
from tests.integration.workflow.test_skill_bundle_lifecycle import project as project


def test_filename_substring_cannot_authorize_an_orphan_asset(project):
    stage = Stage(project)
    stage.write(f'{ALPHA}/SKILL.md',ENTRY.format(name='alpha')+'Use data.py for the synthetic example.\n')
    stage.write(f'{ALPHA}/scripts/a.py','print("synthetic")\n')
    review = stage.review()
    assert review.verdict == 'blocked' and 'orphan_asset' in review.findings[0].reasons
    with pytest.raises(ValueError,match='skill bundle is blocked'): stage.adopt()
    assert not (project/ALPHA/'scripts/a.py').exists()


@pytest.mark.parametrize('mention',['Use `a.py` as an example.','Read scripts/a.py for details.','Read ./scripts/a.py for details.','Run (a.py) when appropriate.'])
def test_exact_filename_or_path_mentions_remain_allowed(project,mention):
    stage = Stage(project)
    stage.write(f'{ALPHA}/SKILL.md',ENTRY.format(name='alpha')+mention+'\n')
    stage.write(f'{ALPHA}/scripts/a.py','print("synthetic")\n')
    assert stage.review().verdict == 'adoptable'


def test_ambiguous_basename_requires_new_asset_path(project):
    stage = Stage(project)
    stage.write(f'{ALPHA}/examples/a.py','print("example")\n')
    stage.write(f'{ALPHA}/scripts/a.py','print("script")\n')
    stage.write(f'{ALPHA}/SKILL.md',ENTRY.format(name='alpha')+'Use examples/a.py.\n')
    assert 'orphan_asset' in stage.review().findings[0].reasons
    stage.write(f'{ALPHA}/SKILL.md',ENTRY.format(name='alpha')+'Use examples/a.py and scripts/a.py.\n')
    assert stage.review().verdict == 'adoptable'


@pytest.mark.parametrize('other',['data.py','a.py.bak','a.py-old','a.py_extra','examples/a.py','other/scripts/a.py'])
def test_longer_filename_mentions_do_not_count_as_new_asset(project,other):
    stage = Stage(project)
    stage.write(f'{ALPHA}/SKILL.md',ENTRY.format(name='alpha')+f'Use {other} in the synthetic example.\n')
    stage.write(f'{ALPHA}/scripts/a.py','print("synthetic")\n')
    assert 'orphan_asset' in stage.review().findings[0].reasons
