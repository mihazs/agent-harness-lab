"""Reported-failure scenarios run against an INSTALLED release launcher, never the source tree.

Shared by the tests that build the archive from this checkout and by the post-publication verification of the
downloaded archive. Everything is synthetic: a temporary HOME and state directory, stub `claude`/`codex`
executables on an exclusive PATH, no network and no model call.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile

from tests.integration.diagnostics.test_reported_actionable_analysis import history
from tests.integration.workflow.test_workflow import FAKE

HOST_MARKERS = ('CLAUDECODE', 'CODEX_THREAD_ID', 'CODEX_CI', 'CODEX_SANDBOX', 'CODEX_SANDBOX_NETWORK_DISABLED')
EXIT_ZERO = '#!/bin/sh\nexit 0\n'
EXIT_ONE = '#!/bin/sh\nexit 1\n'


HAND_WRITTEN = b'# hand-written rules\nkeep this line\n<!-- other-tool:start -->\nanother tool\n<!-- other-tool:end -->\n'
REGION = re.compile(rb'<!-- agent-harness-lab:start.*?<!-- agent-harness-lab:end -->\n?', re.S)


def install(installer, archive, checksums, root, update=False):
    """Run the bundled installer offline into `root`/{data,bin} with a temporary HOME; return the Installed layout.

    The first run finds a Claude directory with a hand-written file and an empty Codex directory, as a user's machine would."""
    home = root / 'install-home'
    if not update:
        (home / '.claude').mkdir(parents=True)
        (home / '.codex').mkdir()
        (home / '.claude/CLAUDE.md').write_bytes(HAND_WRITTEN)
    env = {'PATH': str(Path(sys.executable).parent), 'HOME': str(home)}
    done = subprocess.run([sys.executable, '-I', str(installer), '--archive', str(archive), '--checksums', str(checksums),
                           '--root', str(root / 'data'), '--bin-dir', str(root / 'bin')],
                          env=env, check=True, capture_output=True, text=True, timeout=120)
    return Installed(root=root, home=home, archive=Path(archive), checksums=Path(checksums), output=done.stdout)


def reversioned(archive, version, folder):
    """A copy of `archive` that claims `version`, with its own SHA256SUMS; it stands in for the next release."""
    folder.mkdir(parents=True)
    target = folder / f'agent-harness-lab-{version}.tar.gz'
    with tarfile.open(archive) as source, tarfile.open(target, 'w:gz') as output:
        for member in source.getmembers():
            data = source.extractfile(member).read()
            member.name = f'agent-harness-lab-{version}/' + member.name.split('/', 1)[1]
            if member.name.endswith('/package.json') and member.name.count('/') == 1:
                data = json.dumps({**json.loads(data), 'version': version}).encode()
            member.size, member.pax_headers = len(data), {}  # a long member name also lives in its pax header
            output.addfile(member, io.BytesIO(data))
    checksums = folder / 'SHA256SUMS'
    checksums.write_text(f'{hashlib.sha256(target.read_bytes()).hexdigest()}  {target.name}\n')
    return target, checksums


class Installed:
    """An archive installed in a temporary prefix: launcher, release directory and the global skill links."""

    def __init__(self, root, home, archive, checksums, output=''):
        self.root, self.home, self.archive, self.checksums, self.output = root, home, archive, checksums, output
        self.launcher = root / 'bin/ahl'
        self.release = self.launcher.resolve().parent
        self.version = self.release.name
        self.skill_links = sorted(home.glob('.*/skills/harness-*'))


class Sandbox:
    """One scenario's isolated world: project, stub runtimes, state, session stores and a clean environment."""

    def __init__(self, installed, root):
        self.installed, self.root = installed, Path(root)
        self.home, self.bin = self.root / 'home', self.root / 'bin'
        self.project = self.root / 'project'
        self.state, self.codex_home = self.root / 'state', self.root / 'codex'
        for folder in (self.home, self.bin, self.project):
            folder.mkdir(parents=True)
        (self.project / 'AGENTS.md').write_text('synthetic\n')
        (self.bin / 'python3').symlink_to(sys.executable)  # the launcher and Python stubs use `env python3`

    def stub(self, name, text=EXIT_ZERO):
        path = self.bin / name
        path.write_text(text)
        path.chmod(0o755)

    def env(self, **extra):
        base = dict(PATH=f'{self.bin}{os.pathsep}{self.installed.root / "bin"}', HOME=str(self.home), XDG_STATE_HOME=str(self.state),
                    CODEX_HOME=str(self.codex_home), CLAUDE_CONFIG_DIR=str(self.root / 'claude'), TMPDIR=str(self.root))
        return {**base, **extra}

    def ahl(self, *args, **env):
        """Run the installed launcher; return (exit code, parsed stdout JSON or None, stderr)."""
        result = subprocess.run([str(self.installed.launcher), *map(str, args)], env=self.env(**env), cwd=self.root,
                                capture_output=True, text=True, timeout=120)
        try:
            receipt = json.loads(result.stdout)
        except ValueError:
            receipt = None
        return result.returncode, receipt, result.stderr

    def invoke(self, *args, **env):
        code, receipt, err = self.ahl('skills', 'invoke', self.project, '--timeout', '30', *args, **env)
        assert receipt is not None, err
        return code, receipt, Path(receipt['output']), err

    def codex_session(self, name='s1'):
        path = self.codex_home / 'sessions' / f'{name}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        cwd = str(self.project)
        rows = [dict(type='session_meta', cwd=cwd, payload=dict(id=name, cwd=cwd)),
                dict(type='response_item', payload=dict(type='function_call', name='read', call_id='c1')),
                dict(type='response_item', payload=dict(type='function_call_output', call_id='c1', output=dict(exit_code=0)))]
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))

    def tree_digest(self, folder):
        return hashlib.sha256(b''.join(str(p).encode() + p.read_bytes() for p in sorted(Path(folder).rglob('*')) if p.is_file())).hexdigest()

    def assert_isolated(self):
        """Nothing resolved the default state path under the temporary HOME, and HOME is not the real one."""
        assert str(self.home) != os.path.expanduser('~')
        assert not (self.home / '.local').exists(), 'the default state path was used instead of the override'


def scenario_zero_config_host_marker_proceeds(installed, sb):
    sb.stub('claude'); sb.stub('codex')
    for marker, host in (('CLAUDECODE', 'claude'), ('CODEX_THREAD_ID', 'codex')):
        code, receipt, run, _ = sb.invoke(**{marker: '1'})
        assert receipt['status'] != 'blocked' and receipt.get('reason') != 'select_one_runtime_with_agent', (host, receipt)
        selection = json.loads((run / 'invocation.json').read_text())['runtime_selection']
        assert selection == dict(policy='invoking_host', runtime=host)
    sb.assert_isolated()


def scenario_no_marker_blocks_once_then_the_chosen_runtime_passes(installed, sb):
    sb.stub('claude'); sb.stub('codex')
    code, receipt, run, _ = sb.invoke()
    assert (code, receipt['status'], receipt['reason']) == (3, 'blocked', 'select_one_runtime_with_agent')
    plan = receipt['preflight_plan']
    assert plan['choice'] == dict(option='--agent', values=['codex', 'claude'], remember_option='--remember-runtime')
    assert plan['runtime_selection'] == dict(policy='ask_once', runtime=None, host=None)
    assert 'authorizes the bounded default workflow' in plan['authorization']
    assert 'nothing was dispatched' in plan['authorization'] and plan['execution_authority'] is False
    for command in ('rerun the same command', '--agent codex', '--agent claude'):
        assert command in receipt['remediation']
    assert (plan['candidate_prepared'], plan['evidence_prepared']) == (False, False)
    assert {path.name for path in run.iterdir()} <= {'workflow.json', 'invocation.json'}
    code, receipt, run, _ = sb.invoke('--agent', 'codex')
    assert receipt['status'] != 'blocked' and receipt.get('reason') != 'select_one_runtime_with_agent'
    assert json.loads((run / 'invocation.json').read_text())['runtime'] == 'codex'
    sb.assert_isolated()


def scenario_history_is_accepted_read_only_and_reports_unknown_capture(installed, sb):
    sb.stub('codex', EXIT_ONE)
    sb.codex_session('recent')
    before = sb.tree_digest(sb.codex_home)
    code, receipt, run, _ = sb.invoke('--agent', 'codex', '--history', '--since', 'first')
    assert code != 2, 'the installed launcher rejects --history'
    recorded = receipt['history']
    assert recorded['sessions_captured'] == 1 and recorded['read_only'] and recorded['scope'] == 'project'
    assert 'capture completeness' in recorded['unknown']
    assert sb.tree_digest(sb.codex_home) == before
    sb.assert_isolated()


def scenario_large_history_gives_a_size_capped_brief(installed, sb):
    events = sb.root / 'events.jsonl'
    events.write_text(''.join(json.dumps(row) + '\n' for row in history(300)))
    code, receipt, _ = sb.ahl('analyze', sb.project, '--events', events, '--output', sb.root / 'out')
    assert code == 0, receipt
    out = sb.root / 'out'
    brief = json.loads((out / 'analysis-brief.json').read_text())
    size = (out / 'analysis-brief.json').stat().st_size
    assert 0 < size <= brief['bounds']['max_bytes'] <= 64 * 1024
    assert (out / 'analysis.json').stat().st_size > 20 * size, 'the raw artifact must dwarf the brief the proposer reads'
    top = brief['mechanisms'][0]
    assert (top['tool_class'], top['error_category'], top['status']) == ('Bash', 'tool_reported_error', 'observed_pattern')
    sb.assert_isolated()


def scenario_no_change_dispositions_are_honest(installed, sb):
    sb.stub('codex', FAKE)
    (sb.project / 'mode').write_text('no_change')
    events = sb.root / 'events.jsonl'
    events.write_text(''.join(json.dumps(row) + '\n' for row in history(3)))
    code, result, _ = sb.ahl('improve', sb.project, '--events', events, '--agent', 'codex', '--timeout', '60',
                         '--focus', 'synthetic hypothesis')  # findings without outcomes reach the proposer only with a hypothesis source
    assert (result['status'], result['reason']) == ('no_change', 'no_change_evidence_insufficient')
    steps = result['next_steps']
    assert [row['rank'] for row in steps] == list(range(1, len(steps) + 1)) and all(row['command'] for row in steps)
    # Positive verified evidence: a complete native capture plus a valid executable-grader report, nothing contradicting it.
    sb.codex_session()
    trial = dict(version='baseline', status='passed', execution=dict(exit_code=0, timeout=False),
                 grader=dict(exit_code=0, timeout=False, valid=True, outcome=True, grader_id='synthetic', grader_version='1'))
    report = sb.project / 'artifacts/synthetic/results.json'
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps(dict(inputs_unchanged=True, trials=[dict(trial, task='t1'), dict(trial, task='t2')])))
    code, result, _ = sb.ahl('improve', sb.project, '--agent', 'codex', '--timeout', '60')
    assert (result['status'], result['reason']) == ('no_change', 'no_change_harness_supported')
    assert 'next_steps' not in result
    sb.assert_isolated()


def scenario_doctor_finds_a_consistent_installation(installed, sb):
    code, report, _ = sb.ahl('doctor', sb.project, '--json')
    state = report['installation']
    assert code == 0 and state['warnings'] == [] and state['network_used'] is False
    assert state['version'] == state['launcher_release'] == installed.version
    assert Path(state['launcher_resolves_to']) == installed.release / 'ahl'
    assert len(state['skill_links']) == 4 and {link['release'] for link in state['skill_links']} == {installed.version}
    assert all(link['minimum_cli'] is None or link['minimum_cli'] <= installed.version for link in state['skill_links'])  # same-width X.Y.Z
    assert next(c['status'] for c in report['checks'] if c['check_id'] == 'installation') == 'pass'
    assert next(c['status'] for c in report['checks'] if c['check_id'] == 'authentication') == 'unknown'
    sb.assert_isolated()


def scenario_global_instruction_regions_are_written_once_and_doctor_reports_them(installed, sb):
    claude, codex = installed.home / '.claude/CLAUDE.md', installed.home / '.codex/AGENTS.md'
    after = claude.read_bytes()
    assert REGION.sub(b'', after) == HAND_WRITTEN and f'version={installed.version} sha256='.encode() in after
    assert after.startswith(HAND_WRITTEN) and REGION.sub(b'', codex.read_bytes()) == b'' and codex.stat().st_mode & 0o777 == 0o600
    assert (installed.release / 'global-instructions.md').stat().st_size <= 1024
    assert 'Instructions updated' in installed.output and 'Instructions created' in installed.output
    where = dict(HOME=str(installed.home), CLAUDE_CONFIG_DIR=str(installed.home / '.claude'), CODEX_HOME=str(installed.home / '.codex'))
    code, report, _ = sb.ahl('doctor', sb.project, '--json', **where)
    rows = {row['agent']: row['state'] for row in report['installation']['instructions']}
    assert code == 0 and rows == {'claude': 'current', 'codex': 'current'} and report['installation']['warnings'] == []
    sb.assert_isolated()


def scenario_run_directories_honor_the_state_home_override(installed, sb):
    sb.stub('codex', EXIT_ONE)
    code, receipt, _ = sb.ahl('analyze', sb.project)
    assert Path(receipt['output']).is_relative_to(sb.state)
    override = sb.root / 'override-state'
    code, receipt, _ = sb.ahl('analyze', sb.project, AHL_STATE_HOME=str(override))
    assert Path(receipt['output']).is_relative_to(override)
    sb.assert_isolated()


SCENARIOS = [value for name, value in sorted(globals().items()) if name.startswith('scenario_')]
