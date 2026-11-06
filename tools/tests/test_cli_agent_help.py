"""Agent-facing help remains discoverable before any execution or private write."""
import argparse
import ast
from contextlib import ExitStack,redirect_stderr,redirect_stdout
import importlib
import io
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch

from agent_harness_lab import cli

ROOT=Path(__file__).resolve().parents[2]
ROUTES=((),('rollback',),('update',),('sources',),('sources','lineage'),('sources','inventory'),
    ('regression-review',),('bank-review',),('bank-interchange',),('experiment-history',),('grader-correction',),
    ('doctor',),('research-simulate',),('bank',),('mine',),('evaluate',),('evaluate','hooks'),('diagnose',),('benchmark',),('skills',))
SCRIPTS=('scripts/sync_project.py','scripts/build_release.py','scripts/check_commit_message.py','install.py')
PARSER_FILES={
    'agent_harness_lab/'+name for name in ('cli.py','onboarding.py','one_call.py','updater.py','experiments/cli.py',
        'sources/cli.py','sources/lineage.py','sources/harness_inventory.py','evaluation/cli.py','evaluation/regression_review.py',
        'evaluation/bank_qualification.py','evaluation/oracle_history.py','evaluation/grader_correction.py',
        'evaluation/interchange.py','evaluation/image.py')}
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


class CLIHelpTests(unittest.TestCase):
    def parsers(self):
        for route in ROUTES:
            yield 'ahl '+' '.join(route),capture(lambda:cli.main(list(route)))
        image=importlib.import_module('agent_harness_lab.evaluation.image')
        yield 'runtime image builder',capture(image.main)
        for relative in SCRIPTS:
            with patch.object(sys,'argv',[relative]):
                yield relative,capture(lambda:runpy.run_path(str(ROOT/relative),run_name='__main__'))

    def test_every_recursive_action_and_command_has_explicit_useful_help(self):
        commands=actions=0
        for entry,root in self.parsers():
            for path,parser in walk(root):
                commands+=1
                with self.subTest(entry=entry,path=path):
                    self.assertIsInstance(parser.description,str)
                    self.assertGreaterEqual(len(parser.description.strip()),20)
                    for action in parser._actions:
                        actions+=1
                        self.assertIsInstance(action.help,str,msg=str(action.option_strings or action.dest))
                        self.assertNotEqual(action.help,argparse.SUPPRESS)
                        self.assertGreaterEqual(len(action.help.strip()),15,msg=str(action.option_strings or action.dest))
                        if isinstance(action,argparse._SubParsersAction):
                            # Argparse groups aliases into their canonical command's help row.
                            documented={id(action.choices[row.dest]) for row in action._choices_actions}
                            self.assertEqual(documented,{id(child) for child in action.choices.values()})
                            for name in action.choices:self.assertIn(name,parser.format_help())
                            for row in action._choices_actions:self.assertGreaterEqual(len(row.help.strip()),15)
                    self.assertIn('usage:',parser.format_help())
        self.assertGreater(commands,50);self.assertGreater(actions,250)

    def test_all_routed_help_is_available_without_operations_or_input(self):
        routes=set()
        for route in ROUTES:
            root=capture(lambda:cli.main(list(route)))
            prefix=tuple(root.prog.split()[1:]) if root.prog=='ahl' or root.prog.startswith('ahl ') else route
            routes.update((*prefix,*path) for path,_ in walk(root))
        self.assertGreater(len(routes),50)
        for route in sorted(routes):
            with self.subTest(route=route),ExitStack() as denied:
                for target in ('subprocess.Popen','subprocess.run','urllib.request.urlopen','builtins.input',
                        'pathlib.Path.mkdir','pathlib.Path.write_text','pathlib.Path.write_bytes'):
                    denied.enter_context(patch(target,side_effect=AssertionError('help must not perform an operation')))
                output=io.StringIO();error=io.StringIO()
                with redirect_stdout(output),redirect_stderr(error),self.assertRaises(SystemExit) as stopped:
                    cli.main([*route,'--help'])
                self.assertEqual(stopped.exception.code,0);self.assertEqual(error.getvalue(),'')
                self.assertIn('usage:',output.getvalue());self.assertIn('--help',output.getvalue())

    def test_parser_module_inventory_has_no_unreviewed_or_unexercised_entrypoint(self):
        discovered=set()
        for directory in ('agent_harness_lab','tools','scripts','skills/harness-evaluation/scripts'):
            for path in (ROOT/directory).rglob('*.py'):
                if 'tests' in path.parts:continue
                raw=path.read_text()
                if 'ArgumentParser' not in raw:continue
                if any(isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='ArgumentParser'
                        for node in ast.walk(ast.parse(raw))):discovered.add(path.relative_to(ROOT).as_posix())
        discovered.add('install.py')
        self.assertEqual(discovered,PARSER_FILES)
        captured={parser._help_test_source for _,parser in self.parsers()}
        for relative in PARSER_FILES:self.assertTrue(any(path.endswith('/'+relative) for path in captured),relative)

    def test_workflow_options_preserve_runtime_defaults_and_unlimited_analysis(self):
        parser=capture(lambda:cli.main([]))
        args=parser.parse_args(['improve','.'])
        self.assertEqual((args.agent,args.codex,args.claude,args.timeout),('auto','codex','claude',None))
        self.assertEqual((args.max_captures,args.max_capture_scan,args.max_events),(None,None,None))
        self.assertFalse(args.repair_contracts);self.assertEqual(args.format,'normalized')
        explicit=parser.parse_args(['improve','.','--agent','responses','--inference-settings','settings.json','--timeout','600'])
        self.assertEqual((explicit.agent,explicit.inference_settings,explicit.timeout),('responses',Path('settings.json'),600))
        watch=parser.parse_args(['watch','.','--once'])
        self.assertEqual((watch.interval,watch.max_attempts,watch.retry_delay,watch.watch_budget),(300,3,60,None))
        self.assertTrue(watch.once)

    def test_representative_help_explains_automation_scope_and_side_effects(self):
        for route,terms in ((('analyze',),('JSON','private','--events','--source-root','--max-events')),
                (('improve',),('2700','--repair-contracts','--inference-settings','configuration')),
                (('init',),('--write','--non-interactive','JSON','existing')),
                (('rollback',),('32-hex','JSON','foreign','No model'))):
            with self.subTest(route=route),redirect_stdout(io.StringIO()) as output,self.assertRaises(SystemExit):
                cli.main([*route,'--help'])
            for term in terms:self.assertIn(term,output.getvalue())


if __name__=='__main__':unittest.main()
