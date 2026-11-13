"""The synthetic corpus is deterministic and analyzable, and the budget comparison reports what it should."""
import hashlib
import json
from pathlib import Path

import pytest

from agent_harness_lab.telemetry.schema import event
from tools import perf_bench
from tools.diagnose import analyze


def sample(count, seed=perf_bench.SEED):
    return list(perf_bench.synthetic_events(count, seed))


def test_generator_yields_exactly_the_requested_count_of_valid_events():
    rows = sample(1234)
    assert len(rows) == 1234
    assert all(event(row)['event_id'] == row['event_id'] for row in rows)


def test_same_seed_gives_identical_bytes_and_another_seed_does_not(tmp_path):
    first, again, other = (tmp_path / name for name in ('first', 'again', 'other'))
    assert perf_bench.write_events(first, 600) == perf_bench.write_events(again, 600)
    assert first.read_bytes() == again.read_bytes()
    assert perf_bench.write_events(other, 600, seed=2) != perf_bench.write_events(first, 600)


def test_prefix_of_a_longer_corpus_matches_the_shorter_one():
    assert sample(2000)[:500] == sample(500)


def test_corpus_is_analyzable_and_exercises_the_observation_path():
    result = analyze(iter(sample(3000)))
    kinds = {finding['kind'] for finding in result['findings']}
    assert 'duplicate_observation' in kinds
    assert result['events'] == 3000


def test_digest_is_the_file_content_hash(tmp_path):
    path = tmp_path / 'events.jsonl'
    assert perf_bench.write_events(path, 50) == perf_bench.digest(path) == hashlib.sha256(path.read_bytes()).hexdigest()


def test_spread_reports_median_min_max_and_run_count():
    assert perf_bench.spread([3, 1, 2], 1000, 0) == dict(median=2000, min=1000, max=3000, runs=3)


@pytest.mark.parametrize('contents, repeats, expected', [
    (['same', 'same', 'same'], 3, True),
    (['same', 'other'], 2, False),
    (['same', 'same', 'other'], 3, False),
    (['same'], 1, None),
])
def test_diagnose_compares_output_hashes_across_runs(monkeypatch, contents, repeats, expected):
    runs = iter(contents)

    def fake_run(argv):
        out = Path(argv[argv.index('--output') + 1])
        out.mkdir()
        (out / 'findings.json').write_text(next(runs))
        (out / 'report.md').write_text('report')
        return 0.1, 50e6, 0

    monkeypatch.setattr(perf_bench, 'run', fake_run)
    measured = perf_bench.diagnose((60,), repeats)['60']
    assert measured['outputs_identical_across_runs'] is expected
    assert measured['findings_sha256'] == hashlib.sha256(contents[0].encode()).hexdigest()


def report(startup_ms, seconds, peak_mb, identical=True):
    return dict(startup={'(bare interpreter)': dict(median=10.0), 'diagnose': dict(median=startup_ms)},
                diagnose={'80000': dict(seconds=dict(median=seconds), peak_rss_mb=dict(median=peak_mb),
                                        outputs_identical_across_runs=identical)})


BUDGETS = dict(startup_ratio_max={'diagnose': 6.0}, diagnose={'80000': dict(seconds_max=5.0, peak_rss_mb_max=400)})


def test_check_accepts_a_result_within_every_budget():
    assert perf_bench.check(report(50, 2.0, 165), BUDGETS) == []


@pytest.mark.parametrize('measured, fragment', [
    (report(70, 2.0, 165), 'startup diagnose: 7.0x'),
    (report(50, 6.0, 165), 'median seconds 6.0 exceeds 5.0'),
    (report(50, 2.0, 450), 'median peak_rss_mb 450 exceeds 400'),
    (report(50, 2.0, 165, identical=False), 'outputs differ between runs'),
])
def test_check_names_each_violated_budget(measured, fragment):
    problems = perf_bench.check(measured, BUDGETS)
    assert len(problems) == 1 and fragment in problems[0]


def test_check_treats_uncompared_outputs_as_unmeasured_not_as_a_pass_or_a_difference():
    problems = perf_bench.check(report(50, 2.0, 165, identical=None), BUDGETS)
    assert len(problems) == 1 and 'output stability was not measured' in problems[0]


def test_render_shows_uncompared_outputs_as_not_compared():
    measured = dict(events=60, seconds=dict(median=1.0), peak_rss_mb=dict(median=50.0), input_mb_per_second=3.0)
    shown = perf_bench.render(dict(startup={}, diagnose={'60': dict(measured, outputs_identical_across_runs=None)}))
    assert shown.endswith('not compared')


def test_check_reports_a_startup_command_that_was_not_measured():
    problems = perf_bench.check(report(50, 2.0, 165), dict(startup_ratio_max={'gone': 5.0}))
    assert problems == ['startup gone was not measured']


def test_check_reports_a_size_that_was_not_measured():
    problems = perf_bench.check(dict(startup=report(50, 2, 1)['startup'], diagnose={}), BUDGETS)
    assert problems == ['diagnose 80000 events was not measured']


def test_committed_budgets_are_well_formed():
    # Anchored on this file, not on the imported module: an installed package has no budgets file, the checkout does.
    budgets = json.loads((Path(__file__).resolve().parents[3] / 'tools/perf_budgets.json').read_text())
    assert set(budgets) <= {'note', 'startup_ratio_max', 'diagnose'}
    assert all(limit > 0 for limit in budgets['startup_ratio_max'].values())
    assert all(set(limits) == {'seconds_max', 'peak_rss_mb_max'} for limits in budgets['diagnose'].values())
