"""Agent-facing help remains discoverable before any execution or private write."""
import argparse
import ast
from contextlib import ExitStack,redirect_stderr,redirect_stdout
import importlib
import io
from pathlib import Path
import runpy
import sys
from unittest.mock import patch

import pytest

from agent_harness_lab import cli

ROOT=Path(__file__).resolve().parents[3]
ROUTES=((),('rollback',),('update',),('sources',),('sources','lineage'),('sources','inventory'),('sources','claude','pin-check'),('sources','claude-mem','tool-check'),('sources','mempalace','capabilities'),('sources','github','quota'),('memory',),('config',),
    ('campaign',),('regression-review',),('bank-review',),('bank-interchange',),('experiment-history',),('grader-correction',),('annotation-plan',),('apply',),('compare',),
    ('doctor',),('audit',),('research-simulate',),('bank',),('mine',),('evaluate',),('evaluate','hooks'),('diagnose',),('benchmark',),('skills',),('skills','activation-receipt'))
SCRIPTS=('scripts/sync_project.py','scripts/build_release.py','scripts/publish_pending_releases.py','scripts/check_commit_message.py','scripts/check_pins.py','scripts/isolated_project_check.py','scripts/build_bayes_fixtures.py','install.py','tools/perf_bench.py')
PARSER_FILES={
    'agent_harness_lab/'+name for name in ('cli.py','onboarding.py','one_call.py','updater.py','experiments/cli.py',
        'sources/cli.py','sources/claude_mem_tools.py','sources/mempalace_capabilities.py','sources/lineage.py','sources/harness_inventory.py','sources/github_quota.py','telemetry/claude_contract.py','evaluation/cli.py','user_config.py','evaluation/regression_review.py',
        'evaluation/bank_qualification.py','evaluation/oracle_history.py','evaluation/grader_correction.py',
        'diagnostics/annotation_plan.py','evaluation/compare.py','plan_review.py','evaluation/interchange.py','evaluation/image.py','evaluation/bayes_calibration.py','evaluation/skills.py','audit/cli.py','campaigns/cli.py','memory/cli.py')}
PARSER_FILES.update(('tools/diagnose.py','tools/benchmark_models.py','tools/link_skills.py',
    'skills/harness-evaluation/scripts/mine_traces.py','skills/harness-evaluation/scripts/run_evaluation.py',*SCRIPTS))


class Captured(BaseException):
    def __init__(self,parser,source):self.parser=parser;self.source=source


def capture(call):
    def stop(parser,*args,**kwargs):raise Captured(parser,sys._getframe(1).f_code.co_filename)
    with patch.object(argparse.ArgumentParser,'parse_args',stop):
        try:call()
        except Captured as result:
            result.parser._help_test_source=Path(result.source).as_posix()
            return result.parser
    raise AssertionError('entrypoint never constructed its argument parser')


def walk(parser,path=()):
    yield path,parser
    for action in parser._actions:
        if isinstance(action,argparse._SubParsersAction):
            for name,child in action.choices.items():yield from walk(child,(*path,name))


def parsers():
    for route in ROUTES:
        yield 'ahl '+' '.join(route),capture(lambda:cli.main(list(route)))
    image=importlib.import_module('agent_harness_lab.evaluation.image')
    yield 'runtime image builder',capture(image.main)
    calibration=importlib.import_module('agent_harness_lab.evaluation.bayes_calibration')
    yield 'bayes calibration',capture(lambda:calibration.main([]))
    for relative in SCRIPTS:
        with patch.object(sys,'argv',[relative]):
            yield relative,capture(lambda:runpy.run_path(str(ROOT/relative),run_name='__main__'))


def routed_help():
    routes=set()
    for route in ROUTES:
        root=capture(lambda:cli.main(list(route)))
        prefix=tuple(root.prog.split()[1:]) if root.prog=='ahl' or root.prog.startswith('ahl ') else route
        routes.update((*prefix,*path) for path,_ in walk(root))
    return sorted(routes)


def inventory(build):
    """Build a case inventory at collection time without letting a broken entrypoint abort collection."""
    try:return build()
    except (Exception,SystemExit) as error:return error


def checked(built):
    """Re-raise an inventory failure inside the test that consumes it."""
    if isinstance(built,BaseException):raise built
    return built


def cases(built,param):
    """One case per inventory item, or a single failing case that carries the inventory error."""
    if isinstance(built,BaseException):return [pytest.param(built,id='inventory-error')]
    return [param(item) for item in built]


# Collected once so that every recursive parser and every routed command is its own case.
PARSERS=inventory(lambda:[(entry,path,parser) for entry,root in parsers() for path,parser in walk(root)])
ROUTED=inventory(routed_help)


@pytest.mark.parametrize('case',cases(PARSERS,lambda case:pytest.param(case,id=case[0].strip().replace(' ','_')+':'+'/'.join(case[1]))))
def test_every_recursive_action_and_command_has_explicit_useful_help(case):
    _,_,parser=checked(case)
    assert isinstance(parser.description,str)
    assert len(parser.description.strip())>=20
    for action in parser._actions:
        assert isinstance(action.help,str),str(action.option_strings or action.dest)
        assert action.help!=argparse.SUPPRESS
        assert len(action.help.strip())>=15,str(action.option_strings or action.dest)
        if isinstance(action,argparse._SubParsersAction):
            # Argparse groups aliases into their canonical command's help row.
            documented={id(action.choices[row.dest]) for row in action._choices_actions}
            assert documented=={id(child) for child in action.choices.values()}
            for name in action.choices:assert name in parser.format_help()
            for row in action._choices_actions:assert len(row.help.strip())>=15
    assert 'usage:' in parser.format_help()


def test_recursive_help_covers_every_command_and_action():
    found=checked(PARSERS)
    assert len(found)>50;assert sum(len(parser._actions) for _,_,parser in found)>250


def test_routed_help_covers_every_command():
    assert len(checked(ROUTED))>50


def test_broken_entrypoint_inventory_becomes_one_failing_case_instead_of_a_collection_error():
    built=inventory(lambda:capture(lambda:None))
    collected=cases(built,lambda item:pytest.param(item,id='unused'))
    assert [param.id for param in collected]==['inventory-error']
    with pytest.raises(AssertionError,match='never constructed its argument parser'):checked(collected[0].values[0])
    assert checked([('ahl',(),None)])==[('ahl',(),None)]


@pytest.mark.parametrize('route',cases(ROUTED,lambda route:pytest.param(route,id='/'.join(route))))
def test_all_routed_help_is_available_without_operations_or_input(route):
    route=checked(route)
    with ExitStack() as denied:
        for target in ('subprocess.Popen','subprocess.run','urllib.request.urlopen','builtins.input',
                'pathlib.Path.mkdir','pathlib.Path.write_text','pathlib.Path.write_bytes'):
            denied.enter_context(patch(target,side_effect=AssertionError('help must not perform an operation')))
        output=io.StringIO();error=io.StringIO()
        with redirect_stdout(output),redirect_stderr(error),pytest.raises(SystemExit) as stopped:
            cli.main([*route,'--help'])
        assert stopped.value.code==0;assert error.getvalue()==''
        assert 'usage:' in output.getvalue();assert '--help' in output.getvalue()


def test_parser_module_inventory_has_no_unreviewed_or_unexercised_entrypoint():
    discovered=set()
    for directory in ('agent_harness_lab','tools','scripts','skills/harness-evaluation/scripts'):
        for path in (ROOT/directory).rglob('*.py'):
            if 'tests' in path.parts:continue
            raw=path.read_text()
            if 'ArgumentParser' not in raw:continue
            if any(isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='ArgumentParser'
                    for node in ast.walk(ast.parse(raw))):discovered.add(path.relative_to(ROOT).as_posix())
    discovered.add('install.py')
    assert discovered==PARSER_FILES
    captured={parser._help_test_source for _,parser in parsers()}
    for relative in PARSER_FILES:assert any(path.endswith('/'+relative) for path in captured),relative


def test_workflow_options_preserve_runtime_defaults_and_unlimited_analysis():
    parser=capture(lambda:cli.main([]))
    args=parser.parse_args(['improve','.'])
    assert (args.agent,args.codex,args.claude,args.timeout)==('auto','codex','claude',None)
    assert (args.max_captures,args.max_capture_scan,args.max_events)==(None,None,None)
    assert not args.repair_contracts;assert args.format=='normalized'
    explicit=parser.parse_args(['improve','.','--agent','responses','--inference-settings','settings.json','--timeout','600'])
    assert (explicit.agent,explicit.inference_settings,explicit.timeout)==('responses',Path('settings.json'),600)
    watch=parser.parse_args(['watch','.','--once'])
    assert (watch.interval,watch.max_attempts,watch.retry_delay,watch.watch_budget)==(300,3,60,None)
    assert watch.once


@pytest.mark.parametrize('route,terms',[
    pytest.param(('analyze',),('JSON','private','--events','--source-root','--max-events'),id='analyze'),
    pytest.param(('improve',),('2700','--repair-contracts','--inference-settings','configuration'),id='improve'),
    pytest.param(('init',),('--write','--non-interactive','JSON','existing'),id='init'),
    pytest.param(('rollback',),('32-hex','JSON','foreign','No model'),id='rollback')])
def test_representative_help_explains_automation_scope_and_side_effects(route,terms):
    with redirect_stdout(io.StringIO()) as output,pytest.raises(SystemExit):
        cli.main([*route,'--help'])
    for term in terms:assert term in output.getvalue()
