"""The benchmark runs end to end on a tiny corpus, writes its report and enforces budgets with its exit status."""
import argparse
import json
from pathlib import Path
from unittest.mock import patch

from agent_harness_lab import cli
from tools import perf_bench

TINY = ['--runs', '1', '--repeats', '2', '--sizes', '300']


def test_report_covers_startup_and_diagnose_with_identical_outputs(tmp_path, capsys):
    output = tmp_path / 'report.json'
    assert perf_bench.main([*TINY, '--output', str(output)]) == 0
    report = json.loads(output.read_text())
    assert set(report['startup']) == {'(bare interpreter)', '(no subcommand)', *perf_bench.cli_commands()}
    assert 'improve' in report['startup'] and 'bank-review' in report['startup']
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


def test_committed_startup_budgets_cover_every_command_the_cli_lists():
    budgets = json.loads((Path(__file__).resolve().parents[3] / 'tools/perf_budgets.json').read_text())
    assert set(budgets['startup_ratio_max']) == {'(no subcommand)', *perf_bench.cli_commands()}


def test_measured_commands_are_exactly_the_parsers_top_level_commands():
    # Independent of the benchmark's own `--help` parsing: a hand-picked or truncated list must fail here.
    class Captured(BaseException):
        pass

    def stop(parser, *args, **kwargs):
        raise Captured(parser)

    with patch.object(argparse.ArgumentParser, 'parse_args', stop):
        try:
            cli.main([])
        except Captured as captured:
            parser = captured.args[0]
    commands = next(action.choices for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    assert len(commands) > 30
    assert sorted(perf_bench.cli_commands()) == sorted(commands)
