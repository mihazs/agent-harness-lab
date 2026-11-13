"""The package version is resolved only when --version asks for it, not on every invocation."""
import importlib.metadata
import os
from pathlib import Path
import subprocess
import sys

import pytest

import agent_harness_lab
from agent_harness_lab import cli

PACKAGE_PARENT = str(Path(agent_harness_lab.__file__).resolve().parents[1])


def probe(tmp_path, code):
    # A fresh interpreter, so modules loaded by earlier tests cannot hide an eager import.
    run = subprocess.run([sys.executable, '-c', code], cwd=tmp_path, capture_output=True, text=True,
                         env={**os.environ, 'PYTHONPATH': PACKAGE_PARENT})
    assert run.returncode == 0, run.stderr
    return run.stdout.splitlines()


def test_importing_the_cli_does_not_load_importlib_metadata(tmp_path):
    code = "import sys, agent_harness_lab.cli; print('importlib.metadata' in sys.modules)"
    assert probe(tmp_path, code) == ['False']


def test_help_does_not_load_importlib_metadata(tmp_path):
    code = ("import sys\nfrom agent_harness_lab import cli\n"
            "try:\n    cli.main(['--help'])\nexcept SystemExit as stop:\n    print('exit', stop.code)\n"
            "print('importlib.metadata' in sys.modules)")
    assert probe(tmp_path, code)[-2:] == ['exit 0', 'False']


def test_building_the_parser_does_not_resolve_the_version(monkeypatch):
    def fail():
        raise AssertionError('version resolved without --version')
    monkeypatch.setattr(cli, 'version', fail)
    with pytest.raises(SystemExit) as stop:
        cli.main(['--help'])
    assert stop.value.code == 0


def test_version_flag_prints_the_resolved_version_and_exits(monkeypatch, capsys):
    monkeypatch.setattr(cli, 'version', lambda: '9.8.7')
    with pytest.raises(SystemExit) as stop:
        cli.main(['--version'])
    assert stop.value.code == 0
    assert capsys.readouterr().out == '9.8.7\n'


@pytest.fixture
def installed_without_package_json(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'assets', lambda: tmp_path)


def test_installed_version_comes_from_package_metadata(installed_without_package_json, monkeypatch):
    monkeypatch.setattr(importlib.metadata, 'version', lambda name: '1.2.3' if name == 'agent-harness-lab' else '0')
    assert cli.version() == '1.2.3'


def test_version_falls_back_when_the_package_is_not_installed(installed_without_package_json, monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(importlib.metadata, 'version', missing)
    assert cli.version() == '0.1.0'
