"""The benchmark runs end to end on a tiny corpus, writes its report and enforces budgets with its exit status."""
import json

from tools import perf_bench

TINY = ['--runs', '1', '--repeats', '2', '--sizes', '300']


def test_report_covers_startup_and_diagnose_with_identical_outputs(tmp_path, capsys):
    output = tmp_path / 'report.json'
    assert perf_bench.main([*TINY, '--output', str(output)]) == 0
    report = json.loads(output.read_text())
    assert {'(bare interpreter)', '(no subcommand)', 'diagnose'} <= set(report['startup'])
    run = report['diagnose']['300']
    assert run['events'] == 300 and run['outputs_identical_across_runs'] and run['seconds']['runs'] == 2
    assert run['peak_rss_mb']['median'] > 0
    assert report['environment']['generator_seed'] == perf_bench.SEED
    assert 'report:' in capsys.readouterr().out


def test_an_exceeded_budget_fails_the_command(tmp_path, capsys):
    budgets = tmp_path / 'budgets.json'
    budgets.write_text(json.dumps(dict(diagnose={'300': dict(seconds_max=0.000001, peak_rss_mb_max=1000)})))
    assert perf_bench.main([*TINY, '--output', str(tmp_path / 'report.json'), '--check', str(budgets)]) == 1
    assert 'BUDGET EXCEEDED' in capsys.readouterr().err
