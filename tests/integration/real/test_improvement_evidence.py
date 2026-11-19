"""Opt-in regressions over existing private runs; never create fixture histories."""
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from agent_harness_lab.diagnostics.improve_coverage import citable
from agent_harness_lab.evaluation.construction import views
from agent_harness_lab.evaluation.integrity import relative
from agent_harness_lab.loop.improve import mechanisms
from agent_harness_lab.monitoring.state import exit_code
from agent_harness_lab.workflow import file_state, harness_files


HOST_HOME = Path.home()
RUNS = json.loads(os.environ.get('REAL_IMPROVE_RUNS', '[]'))
CODE_ROOT = Path(os.environ.get('REAL_IMPROVE_CODE_ROOT', Path(__file__).resolve().parents[3])).resolve()


@pytest.fixture(params=RUNS or [None], ids=lambda _: 'private-run')
def real_run(request):
    if request.param is None:
        pytest.skip('set REAL_IMPROVE_RUNS to existing private run directories')
    run = Path(request.param).resolve(strict=True)
    checkpoint = json.loads((run/'recovery.json').read_text())
    project = Path(checkpoint['project'])
    if not project.is_absolute() or project.resolve(strict=True) != project or run.parent.parent.name != 'runs':
        raise ValueError('private run must name a canonical project and run directory')
    worktrees = HOST_HOME/'dev/.worktrees'
    worktrees.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ahl-real-regression-', dir=worktrees) as folder:
        base = Path(folder)
        checkout = base/'project'
        git = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'index.threads=1', '-C', str(project)]
        added = False
        try:
            subprocess.run([*git, 'worktree', 'add', '-q', '--detach', str(checkout), 'HEAD'], check=True)
            added = True
            allowed, expected = checkpoint['allowed_paths'], checkpoint['original']
            for name in [*expected, *(allowed or [])]:
                if relative(name) != name:
                    raise ValueError('noncanonical harness path')
            for path in harness_files(checkout, allowed):
                if any(part.is_symlink() for part in (path, *path.parents) if part.is_relative_to(checkout)):
                    raise ValueError('worktree harness path crosses a symlink')
                if str(path.relative_to(checkout)) not in expected:
                    path.unlink()
            for name in expected:
                target = checkout/name
                source = project/name
                if any(path.is_symlink() for root, leaf in ((project, source), (checkout, target))
                       for path in (leaf, *leaf.parents) if path.is_relative_to(root)):
                    raise ValueError('harness path crosses a symlink')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            def states(root):
                return {str(path.relative_to(root)):list(file_state(path)) for path in harness_files(root, allowed)}
            assert states(project) == states(checkout) == expected
            yield SimpleNamespace(run=run, project=project, checkout=checkout, base=base, states=states, expected=expected)
            assert states(project) == states(checkout) == expected
        finally:
            if added:
                subprocess.run([*git, 'worktree', 'remove', '--force', str(checkout)], check=True)


def test_real_mechanism_summaries_reach_only_the_proposer(real_run):
    run, base = real_run.run, real_run.base
    data = json.loads((run/'analysis.json').read_text())
    brief = json.loads((run/'analysis-brief.json').read_text())
    summaries = json.loads((run/'mechanism-summaries.json').read_text())
    options = dict(brief=brief)
    if 'mechanisms' in inspect.signature(views).parameters:
        options['mechanisms'] = summaries
    role = views(data, CODE_ROOT, base/'roles', time.monotonic()+120, **options)
    private = role['proposer']/'mechanism-summaries.json'
    assert private.is_file()
    assert json.loads(private.read_text()) == summaries and private.stat().st_mode & 0o077 == 0
    assert not (role['evaluator_author']/'mechanism-summaries.json').exists()
    from agent_harness_lab.diagnostics.brief import no_change_result
    disposition = no_change_result(data)
    assert (disposition['status'], disposition['reason'], exit_code(disposition['status'])) == (
        'inconclusive', 'no_change_evidence_insufficient', 3)
    ids = {row['mechanism_id'] for row in summaries['mechanisms']}
    if not ids - citable(brief):
        pytest.skip('this run has no mechanisms beyond the short brief')
    assert ids <= citable(brief, summaries)
    assert ids <= set(mechanisms(SimpleNamespace(_analysis_brief=brief, _mechanism_summaries=summaries, focus=None)))
    from agent_harness_lab.workflow import check_mechanism_summaries
    copied = base/'mechanism-summaries.json'
    shutil.copy2(run/'mechanism-summaries.json', copied)
    args = SimpleNamespace(_mechanism_summaries_digest=hashlib.sha256(copied.read_bytes()).hexdigest())
    check_mechanism_summaries(args, base)
    with copied.open('ab') as stream:
        stream.write(b' ')
    with pytest.raises(ValueError, match='changed during preparation'):
        check_mechanism_summaries(args, base)


@pytest.mark.parametrize('entry', [('improve',), ('skills', 'invoke')], ids=['cli', 'skill'])
def test_real_no_change_receipt_resumes_as_inconclusive(real_run, entry):
    run, base = real_run.run, real_run.base
    state_root = run.parents[2]
    destination = base/'state'/run.relative_to(state_root)
    (destination/'preparation/proposal-0').mkdir(parents=True, mode=0o700)
    destination.chmod(0o700)
    shutil.copy2(run/'recovery.json', destination/'recovery.json')
    shutil.copy2(run/'preparation/proposal-0/proposal.json', destination/'preparation/proposal-0/proposal.json')
    environment = dict(os.environ, AHL_STATE_HOME=str(state_root), PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(CODE_ROOT))
    command = ['unshare', '--user', '--map-root-user', '--mount', 'bash', '-c',
        'set -eu; mount --make-rprivate /; mount --bind "$1" "$2"; mount --bind "$3" "$4"; '
        'exec "$5" -m agent_harness_lab "${@:7}" "$4" --resume "$6"',
        '_', str(base/'state'), str(state_root), str(real_run.checkout), str(real_run.project), sys.executable, run.name, *entry]
    completed = subprocess.run(command, env=environment, cwd=base, capture_output=True, text=True, timeout=30)
    result = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert (result['status'], result['reason']) == ('inconclusive', 'no_change_evidence_insufficient')
    assert result['changed_files'] == [] and result['exit_receipt']
    original = json.loads((run/'recovery.json').read_text())['receipt']
    assert original['spend_label'] == 'spent_without_evaluation'
    for field in ('spend_label', 'spent_without_evaluation', 'findings_not_candidates'):
        assert result[field] == original[field]


def test_real_remote_selects_the_review_forge(real_run):
    remote = subprocess.check_output(['git', '-C', str(real_run.checkout), 'remote', 'get-url', 'origin'], text=True).strip()
    host = urlsplit(remote).hostname if '://' in remote else remote.split(':', 1)[0].rsplit('@', 1)[-1]
    expected = {'github.com':'github', 'gitlab.com':'gitlab'}.get((host or '').lower())
    if expected is None:
        pytest.skip('self-hosted remotes need an independently established provider')
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(CODE_ROOT))
    completed = subprocess.run([sys.executable, '-m', 'agent_harness_lab', 'sources', 'detect', str(real_run.checkout)],
                               env=environment, cwd=real_run.base, capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0 and json.loads(completed.stdout)['provider'] == expected


def test_real_legacy_source_journal_remains_readable(real_run):
    import tomllib
    from agent_harness_lab.evaluation.integrity import read_regular
    from agent_harness_lab.sources.schema import SourceSpec, digest
    from agent_harness_lab.sources.store import SourceStore
    selected = os.environ.get('REAL_SOURCE_JOURNAL')
    if not selected:
        pytest.skip('set REAL_SOURCE_JOURNAL to an existing private source journal')
    original = Path(selected)
    raw = read_regular(original, 4*1024*1024)
    data = json.loads(raw)
    scope = real_run.project/'.ai-memory.toml'
    if not scope.is_file():
        pytest.skip('selected project has no explicit memory scope')
    config = tomllib.loads(read_regular(scope, 65536).decode('utf-8'))
    if data['project_sha256'] != digest(config['project']):
        pytest.skip('journal project differs from the selected run')
    target = real_run.checkout/'private-source-journal.json'
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
    store = SourceStore(target, project_sha256=data['project_sha256'], scope_sha256=data['scope_sha256'], max_bytes=4*1024*1024)
    state, observed = store.read_snapshot()
    assert observed == raw
    sources = [SourceSpec.parse(value) for value in state['sources'].values()]
    assert sources and all(store.status(source)['source_sha256'] == source.source_key for source in sources)
    assert target.read_bytes() == raw and read_regular(original, 4*1024*1024) == raw


def test_real_native_and_memory_session_with_missing_scope_stays_unlinked(real_run):
    import secrets
    import tomllib
    from agent_harness_lab.sources.okf import concept
    from agent_harness_lab.evaluation.integrity import contained, read_regular

    selected = os.environ.get('REAL_SOURCE_JOURNAL')
    if not selected: pytest.skip('set REAL_SOURCE_JOURNAL to an existing private source journal')
    journal = Path(selected)
    scope_file = real_run.project/'.ai-memory.toml'
    if not scope_file.is_file(): pytest.skip('selected project has no explicit memory scope')
    scope = tomllib.loads(read_regular(scope_file, 65536).decode('utf-8'))
    from agent_harness_lab.sources.schema import digest
    if json.loads(read_regular(journal, 4*1024*1024))['project_sha256'] != digest(scope['project']):
        pytest.skip('journal project differs from the selected run')
    declaration = json.loads(read_regular(journal.parent/'manifest.json', 1024*1024))
    export = contained(journal.parent, declaration['data'])
    ids = set()
    for path in export.rglob('*.md'):
        metadata, _ = concept(read_regular(path, 1024*1024))
        if metadata.get('type') == 'Session Summary' and metadata.get('project') is None:
            if isinstance(metadata.get('session_id'), str): ids.add(metadata['session_id'])
    capture = None
    for directory, _, names in os.walk(HOST_HOME/'.codex/sessions'):
        for name in names:
            if not name.endswith('.jsonl') or not any(value in name for value in ids): continue
            candidate = Path(directory)/name
            if candidate.stat().st_size > 16*1024*1024: continue
            with candidate.open('rb') as stream:
                header = json.loads(stream.readline(1024*1024)).get('payload', {})
                if (header.get('id') not in ids or header.get('cwd') != str(real_run.project)
                        or header.get('cli_version') != '0.166.0'): continue
                if 1+sum(1 for _ in stream) <= 10000:
                    capture = candidate
                    break
        if capture: break
    if capture is None: pytest.skip('no bounded matching real native capture')
    root = real_run.base/'namespace'
    root.mkdir(mode=0o700)
    (root/real_run.project.name).mkdir()
    (root/'memory').mkdir()
    shutil.copytree(export, root/'memory/export', symlinks=True)
    shutil.copy2(capture, root/'capture.jsonl')
    (root/'scope.json').write_text(json.dumps([scope['project'], scope['workspace']]))
    (root/'key').write_bytes(secrets.token_bytes(32))
    code = root/'code'
    code.mkdir()
    shutil.copytree(CODE_ROOT/'agent_harness_lab', code/'agent_harness_lab',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(Path(__file__).with_name('native_correlation_check.py'), code/'check.py')
    command = ['unshare', '--user', '--map-root-user', '--mount', 'sh', '-ec',
               'mount --make-rprivate /; mount --bind "$1" "$2/$3"; '
               'mount --bind "$2" "$5"; '
               'PYTHONPATH="$5/code" exec "$4" "$5/code/check.py" "$3"',
               '_', str(real_run.checkout), str(root), real_run.project.name, sys.executable, str(real_run.project.parent)]
    result = subprocess.run(command, cwd='/', env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                                                        REAL_DEV_ROOT=str(real_run.project.parent)),
                            capture_output=True, text=True, timeout=240)
    assert result.returncode == 0, 'isolated real native/memory correlation failed'


def test_real_keyed_source_capture_and_provenance(real_run):
    import hmac
    import tomllib
    from agent_harness_lab.evaluation.integrity import contained, read_regular
    from agent_harness_lab.sources.memory_provenance import bind_provenance
    from agent_harness_lab.sources.identity_key import load as load_identity_key
    from agent_harness_lab.sources.lineage import LineageStore
    from agent_harness_lab.sources.okf import OKFExport
    from agent_harness_lab.sources.registry import Capability, Registry
    from agent_harness_lab.sources.schema import SourceSpec, digest
    from agent_harness_lab.sources.session_correlation import correlate_sessions
    from agent_harness_lab.sources.store import SourceStore
    selected = os.environ.get('REAL_SOURCE_JOURNAL')
    if not selected:
        pytest.skip('set REAL_SOURCE_JOURNAL to an existing private source journal')
    original = Path(selected)
    original_raw = read_regular(original, 4*1024*1024)
    journal = json.loads(original_raw)
    scope = real_run.project/'.ai-memory.toml'
    if not scope.is_file(): pytest.skip('selected project has no explicit memory scope')
    config = tomllib.loads(read_regular(scope, 65536).decode('utf-8'))
    if journal['project_sha256'] != digest(config['project']): pytest.skip('journal project differs from the selected run')
    declaration = json.loads(read_regular(original.parent/'manifest.json', 1024*1024))
    source_dir = real_run.checkout/'private-keyed-evidence'
    source_dir.mkdir(mode=0o700)
    shutil.copytree(contained(original.parent, declaration['data']), source_dir/'export', symlinks=True)
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(CODE_ROOT))
    cli = [sys.executable, '-m', 'agent_harness_lab', 'sources']
    key_file = source_dir/'identity.key'
    create = [*cli, 'identity-key', 'create', '--output', str(key_file)]
    created = subprocess.run(create, env=environment, cwd=real_run.base, capture_output=True, text=True, timeout=20)
    assert created.returncode == 0
    key = load_identity_key(key_file)
    assert json.loads(created.stdout)['identity_key_id'] == hashlib.sha256(key).hexdigest()
    assert key_file.stat().st_mode & 0o077 == 0
    assert subprocess.run(create, env=environment, cwd=real_run.base, capture_output=True, timeout=20).returncode != 0
    assert load_identity_key(key_file) == key
    key_file.chmod(0o644)
    with pytest.raises(ValueError): load_identity_key(key_file)
    key_file.chmod(0o600)
    linked = source_dir/'linked.key'
    os.link(key_file, linked)
    try:
        with pytest.raises(ValueError): load_identity_key(key_file)
    finally:
        linked.unlink()
    assert load_identity_key(key_file) == key
    wrong_file = source_dir/'wrong.key'
    assert subprocess.run([*cli, 'identity-key', 'create', '--output', str(wrong_file)],
                          env=environment, cwd=real_run.base, capture_output=True, timeout=20).returncode == 0
    def keyed(value):
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        return hmac.new(key, raw, hashlib.sha256).hexdigest()
    value_file = source_dir/'identity-value.json'
    value_file.write_text(json.dumps(config['project'])); value_file.chmod(0o600)
    fingerprinted = subprocess.run([*cli, 'identity-key', 'fingerprint', '--key-file', str(key_file),
                                   '--value-file', str(value_file)], env=environment, cwd=real_run.base,
                                  capture_output=True, text=True, timeout=20)
    assert fingerprinted.returncode == 0 and json.loads(fingerprinted.stdout)['fingerprint'] == keyed(config['project'])
    scope_file = source_dir/'scope-value.json'
    scope_file.write_text(json.dumps(config['workspace'])); scope_file.chmod(0o600)
    results = []
    for version in (1, 2):
        fingerprint = digest if version == 1 else keyed
        values = dict(declaration['source'], root_sha256=fingerprint(str(source_dir.resolve())),
                      project_sha256=fingerprint(config['project']), scope_sha256=fingerprint(config['workspace']),
                      enabled=True, schema_version=version)
        if version == 2: values.update(identity_scheme='hmac-sha256-v1', identity_key_id=hashlib.sha256(key).hexdigest())
        source = SourceSpec.parse(values)
        manifest = source_dir/f'manifest-{version}.json'
        if version == 1:
            manifest.write_text(json.dumps(dict(schema_version=1, source=source.to_dict(), data='export')))
            manifest.chmod(0o600)
        else:
            prepare = [*cli, 'identity-key', 'prepare-okf', '--manifest', str(source_dir/'manifest-1.json'),
                       '--scope-file', str(scope_file), '--key-file', str(key_file), '--output', str(manifest)]
            prepared = subprocess.run(prepare, env=environment, cwd=real_run.base, capture_output=True, text=True, timeout=30)
            assert prepared.returncode == 0 and json.loads(prepared.stdout)['source_sha256'] == source.source_key
            assert json.loads(manifest.read_text())['source'] == source.to_dict()
            assert subprocess.run(prepare, env=environment, cwd=real_run.base, capture_output=True, timeout=30).returncode != 0
            if config['project'] != config['workspace']:
                refused = source_dir/'refused-manifest.json'
                bad = [*cli, 'identity-key', 'prepare-okf', '--manifest', str(source_dir/'manifest-1.json'),
                       '--scope-file', str(value_file), '--key-file', str(key_file), '--output', str(refused)]
                assert subprocess.run(bad, env=environment, cwd=real_run.base, capture_output=True, timeout=30).returncode != 0
                assert not refused.exists()
        identity_key = key if version == 2 else None
        connector = OKFExport(manifest, identity_key=identity_key)
        scope_args = dict(project_sha256=source.project_sha256, scope_sha256=source.scope_sha256)
        store = SourceStore(source_dir/f'journal-{version}.json', identity_key=identity_key, **scope_args)
        registry = Registry()
        registry.register(Capability('local_file', 'ai-memory-okf', '0.2', ('contextual_assertion',)), connector)
        if version == 1:
            assert registry.ingest(source, store, **scope_args).source_sha256 == source.source_key
        else:
            command = [*cli, 'import', '--manifest', str(manifest), '--project-sha256', source.project_sha256,
                       '--scope-sha256', source.scope_sha256, '--cache', str(source_dir/f'journal-{version}.json'),
                       '--identity-key-file', str(key_file)]
            imported = subprocess.run(command, env=environment, cwd=real_run.base, capture_output=True, text=True, timeout=30)
            assert imported.returncode == 0 and json.loads(imported.stdout)['source_sha256'] == source.source_key
        report = bind_provenance(connector, source, store)
        sessions = correlate_sessions(connector, source, store, [])
        state, _ = store.read_snapshot()
        pages = connector.pages(source)
        assert state['records'] and pages and sessions['status'] == 'evaluated'
        assert sessions['counts']['memory_sessions'] > 0
        status = [*cli, 'status', '--manifest', str(manifest), '--project-sha256', source.project_sha256,
                  '--scope-sha256', source.scope_sha256, '--cache', str(source_dir/f'journal-{version}.json')]
        if version == 1:
            checked = subprocess.run(status, env=environment, cwd=real_run.base, capture_output=True, text=True, timeout=20)
            assert checked.returncode == 0 and json.loads(checked.stdout)['source_sha256'] == source.source_key
            assert subprocess.run([*status, '--identity-key-file', str(key_file)], env=environment, cwd=real_run.base,
                                  capture_output=True, timeout=20).returncode != 0
        if version == 2:
            serialized = json.dumps((state, report, sessions))
            private_identities = [config['project'], config['workspace'], str(source_dir.resolve()),
                                  *(page.path for page in pages)]
            for page in pages:
                metadata = page.metadata
                for field, kind in (('handoff_id', 'handoff'), ('workstream_id', 'workstream'),
                                    ('session_id', 'session'), ('from_session', 'session')):
                    if isinstance(metadata.get(field), str): private_identities.append((kind, metadata[field]))
                for field, kind in (('linked_sessions', 'session'), ('linked_pages', 'page')):
                    if isinstance(metadata.get(field), list):
                        private_identities.extend((kind, value) for value in metadata[field] if isinstance(value, str))
            assert all(digest(value) not in serialized for value in private_identities)
            with pytest.raises(ValueError): OKFExport(manifest).read(source)
            with pytest.raises(ValueError): SourceStore(source_dir/f'journal-{version}.json', **scope_args).status(source)
            with pytest.raises(ValueError): OKFExport(manifest, identity_key=load_identity_key(wrong_file)).read(source)
            checked = subprocess.run([*status, '--identity-key-file', str(key_file)], env=environment, cwd=real_run.base,
                                     capture_output=True, text=True, timeout=20)
            assert checked.returncode == 0 and json.loads(checked.stdout)['source_sha256'] == source.source_key
            assert subprocess.run(status, env=environment, cwd=real_run.base, capture_output=True, timeout=20).returncode != 0
            assert subprocess.run([*status, '--identity-key-file', str(wrong_file)], env=environment, cwd=real_run.base,
                                  capture_output=True, timeout=20).returncode != 0
            lineage_dir = source_dir/'lineage'
            lineage = LineageStore(lineage_dir, identity_key=key, **scope_args)
            with lineage.locked(create=True): pass
            coverage = lineage.coverage(source_dir/f'journal-{version}.json')
            lineage_cli = [*cli, 'lineage', '--store', str(lineage_dir), '--project-sha256', source.project_sha256,
                           '--scope-sha256', source.scope_sha256]
            action = ['coverage', '--cache', str(source_dir/f'journal-{version}.json')]
            checked = subprocess.run([*lineage_cli, '--identity-key-file', str(key_file), *action], env=environment,
                                     cwd=real_run.base, capture_output=True, text=True, timeout=30)
            assert checked.returncode == 0 and json.loads(checked.stdout)['record_types'] == coverage['record_types']
            assert subprocess.run([*lineage_cli, *action], env=environment, cwd=real_run.base,
                                  capture_output=True, timeout=30).returncode != 0
            assert subprocess.run([*lineage_cli, '--identity-key-file', str(wrong_file), *action], env=environment,
                                  cwd=real_run.base, capture_output=True, timeout=30).returncode != 0
        results.append((len(state['records']), report['status'], report['reason'], report['counts'], sessions['counts']))
    assert results[0] == results[1] and read_regular(original, 4*1024*1024) == original_raw
