"""A line a review called unmeasured is accepted only when it is a verified correction of a cited contradiction."""
from agent_harness_lab.evaluation import verified_correction
from tests.support.profile_fixture import build, package

FILES = {'package.json': package({'test': 'vitest run'}), 'pnpm-lock.yaml': '',
         'AGENTS.md': '# Project\nUse pnpm. Run `pnpm test`, then `pnpm run deploy`.\nKeep changes small.\n'}


def candidate(tmp_path, line):
    return build(tmp_path / 'c', {'AGENTS.md': '# Project\n' + line + '\nKeep changes small.\n'})


# fixed-in: a confirmed gain was not installed because the review called an accompanying dead-reference correction unmeasured
def test_an_unmeasured_line_that_only_corrects_a_cited_contradiction_is_proven(tmp_path):
    project, baseline = build(tmp_path / 'p', FILES), build(tmp_path / 'b', {'AGENTS.md': FILES['AGENTS.md']})
    fixed = candidate(tmp_path, 'Use pnpm. Run `pnpm test`.')
    assert verified_correction.proven_lines(project, baseline, fixed, ['Run `pnpm test`.'])
    assert not verified_correction.proven_lines(project, baseline, fixed, ['Keep changes small.', 'Run `pnpm test`.'])  # an uncited line
    assert not verified_correction.proven_lines(project, baseline, fixed, [])
    swapped = candidate(tmp_path / 'x', 'Use pnpm. Run `pnpm test`, then `pnpm run lint`.')  # a new reference in the corrected line
    assert not verified_correction.proven_lines(project, baseline, swapped, ['Run `pnpm test`, then `pnpm run lint`.'])
