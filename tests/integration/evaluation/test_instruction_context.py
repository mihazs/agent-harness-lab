"""Controller-loaded AGENTS.md chain for a task directory; synthetic harness and wire only."""
import contextlib
import io
import json
import sys
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation import instruction_context, responses
from tests.integration.sandbox import test_sandbox_inference as inference_fixtures


@pytest.fixture
def chain(ctx):
    (ctx.harness / 'AGENTS.md').write_text('SYNTHETIC_ROOT_GUIDANCE')
    (ctx.harness / 'app').mkdir()
    (ctx.harness / 'app/AGENTS.md').write_text('SYNTHETIC_APP_GUIDANCE')
    return ctx


def test_task_cwd_under_app_loads_root_then_app_instructions(chain):
    chain.run_cwd(chain.client(), 'app')
    content = json.loads(chain.bodies[0])['input'][0]['content']
    assert instruction_context.CHAIN_MARKER in content
    assert content.index('SYNTHETIC_ROOT_GUIDANCE') < content.index('SYNTHETIC_APP_GUIDANCE')
    packet = chain.receipts[-1]['request_binding']['instruction_binding']['instruction_file']
    assert [row['path'] for row in packet['chain']] == ['AGENTS.md', 'app/AGENTS.md']
    assert [row['membership'] for row in packet['chain']] == ['present', 'present']
    assert packet['task_cwd'] == 'app'
    assert packet['chain_budget'] == 32 * 1024
    assert 'SYNTHETIC_APP_GUIDANCE' not in json.dumps(chain.receipts)


def test_missing_intermediate_instructions_are_explicitly_absent(chain):
    (chain.harness / 'app/api').mkdir()
    chain.run_cwd(chain.client(), 'app/api')
    packet = chain.receipts[-1]['request_binding']['instruction_binding']['instruction_file']
    assert [(row['path'], row['membership']) for row in packet['chain']] == \
        [('AGENTS.md', 'present'), ('app/AGENTS.md', 'present'), ('app/api/AGENTS.md', 'absent')]


def test_chain_over_the_budget_blocks_before_any_request(chain):
    (chain.harness / 'app/AGENTS.md').write_text('x' * (32 * 1024))
    client = chain.client()
    with pytest.raises(ValueError, match='32 KiB budget'):
        chain.run_cwd(client, 'app')
    assert chain.bodies == []
    assert client.requests == 0


def test_root_only_default_is_unchanged(chain):
    chain.run_role(chain.client())
    packet = chain.receipts[-1]['request_binding']['instruction_binding']['instruction_file']
    assert set(packet) == {'path', 'membership', 'mode', 'sha256'}
    content = json.loads(chain.bodies[0])['input'][0]['content']
    assert instruction_context.MARKER in content
    assert 'SYNTHETIC_APP_GUIDANCE' not in content


@pytest.mark.parametrize('value', [pytest.param(value, id=repr(value)) for value in
                                   ('', '/app', '../app', 'app/', 'app//api', './app', '.git', 'app\\api', 7)])
def test_unsafe_task_cwd_values_are_rejected(value):
    with pytest.raises(ValueError):
        instruction_context.chain_paths(value)


def test_safe_task_cwd_lists_every_chain_member():
    assert instruction_context.chain_paths('app/api') == ('AGENTS.md', 'app/AGENTS.md', 'app/api/AGENTS.md')


def test_task_cwd_requires_the_selected_protocol(chain):
    del chain.settings['instruction_protocol']
    with pytest.raises(ValueError):
        chain.run_cwd(chain.client(), 'app')
    assert chain.bodies == []


class ManifestCwd:
    """Task cwd configured through the runner manifest, not a direct run_role call."""

    def __init__(self, f):
        self.f = f
        self.fixture, self.runner, self.args = f.fixture, f.runner, f.args
        self.fixture.manifest['tasks'][0]['cwd'] = 'app'; self.fixture.write_manifest()

    def protocol(self):
        self.f.settings['instruction_protocol'] = instruction_context.PROFILE
        self.args.inference_settings.write_text(json.dumps(self.f.settings))

    def blocked_before_inference(self, message):
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=self.f.backend), \
                patch.object(responses, 'read_credential', side_effect=AssertionError('no key before setup')), \
                patch.object(responses, 'request', side_effect=AssertionError('no inference before setup')), \
                pytest.raises(ValueError, match=message):
            self.runner.evaluate(self.args)
        assert not self.args.output.exists()
        assert self.f.requests == 0


@pytest.fixture
def manifest():
    f = inference_fixtures.ResponsesEvaluationTests(); f.setUp()
    try:
        yield ManifestCwd(f)
    finally:
        f.doCleanups()


@pytest.mark.parametrize('agent', [pytest.param('codex', id='codex'), pytest.param('offline', id='offline')])
def test_cooperative_and_offline_executors_reject_cwd_as_a_setup_error(manifest, agent):
    args = manifest.args
    args.agent, args.inference_settings = agent, None
    for name in ('cgroup_root', 'image_root', 'image_sha256', 'disk_bytes'): setattr(args, name, None)
    argv = ['run_evaluation.py', '--agent', agent, '--manifest', str(args.manifest),
            '--baseline', str(args.baseline), '--candidate', str(args.candidate),
            '--output', str(args.output)]
    stderr = io.StringIO()
    with patch.object(sys, 'argv', argv), contextlib.redirect_stderr(stderr), \
            patch.object(manifest.runner, 'execute', side_effect=AssertionError('no native CLI')), \
            pytest.raises(SystemExit) as stopped:
        manifest.runner.main()
    assert stopped.value.code == 2
    assert 'task cwd is supported only by the controlled Responses executor' in stderr.getvalue()
    assert not args.output.exists()


def test_cwd_without_instruction_protocol_blocks_before_any_trial(manifest):
    manifest.blocked_before_inference('task cwd requires the controller instruction protocol')


def test_frozen_chain_over_budget_blocks_before_any_trial(manifest):
    manifest.protocol()
    (manifest.args.candidate / 'AGENTS.md').write_text('x' * (32 * 1024 + 1))
    manifest.blocked_before_inference('32 KiB budget')


def test_irregular_chain_member_blocks_before_any_trial(manifest):
    manifest.protocol()
    (manifest.args.baseline / 'app/AGENTS.md').mkdir(parents=True)
    manifest.blocked_before_inference('regular files')


def test_manifest_cwd_reaches_run_trial_only_for_its_task(manifest):
    manifest.protocol(); calls = []
    def stop(*args, **kwargs):
        calls.append((args[4], kwargs.get('task_cwd', 'unset')))
        raise ValueError('synthetic stop after capture')
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=manifest.f.backend), \
            patch.object(responses, 'request', side_effect=AssertionError('run_trial is replaced')), \
            patch.object(responses.Client, 'abort'), patch.object(responses, 'run_trial', side_effect=stop):
        manifest.runner.evaluate(manifest.args)
    cwd_prompt = manifest.fixture.manifest['tasks'][0]['prompt']
    assert len(calls) == 2 * len(manifest.fixture.manifest['tasks'])
    for prompt, task_cwd in calls:
        assert task_cwd == ('app' if prompt.endswith(cwd_prompt) else 'unset')
    assert sum(task_cwd == 'app' for _, task_cwd in calls) == 2
