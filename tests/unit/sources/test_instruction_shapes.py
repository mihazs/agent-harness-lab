"""Procedure cues are lexical and never include captured text."""
from agent_harness_lab.sources.instruction_shapes import analyze


def test_only_shell_labelled_fences_are_counted_as_command_block_cues():
    examples='# Formats\n'+''.join(f'```json\n{{"item": {n}}}\n```\n' for n in range(3))
    assert analyze(examples)[2] is False
    shell='# Steps\n'+''.join('```bash\nsynthetic-command\n```\n' for _ in range(3))
    assert analyze(shell)[2] is True
    unsupported='# Steps\n'+''.join('```bash-like\nsynthetic\n```\n' for _ in range(3))
    assert analyze(unsupported)[2] is False
    unlabelled='# Steps\n'+''.join('```\nsynthetic\n```\n' for _ in range(3))
    assert analyze(unlabelled)[2] is False


def test_numbered_steps_and_separate_sections_keep_existing_behavior():
    assert analyze('# Steps\n'+''.join(f'{n}. step\n' for n in range(1,6)))[2] is True
    sections='# One\n```sh\ncmd\n```\n```sh\ncmd\n```\n# Two\n```sh\ncmd\n```\n'
    assert analyze(sections)[2] is False
    mixed='# Steps\n```sh\ncmd\n```\n```json\n{}\n```\n```zsh\ncmd\n```\n```shell\ncmd\n```\n'
    assert analyze(mixed)[2] is True
