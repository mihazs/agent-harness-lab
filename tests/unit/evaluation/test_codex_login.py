"""The saved Codex subscription login: borrowed into a temporary CODEX_HOME for one run, never printed, written back only when safe.

Synthetic homes only; the process HOME and the real CODEX_HOME are never read."""
import json
from pathlib import Path

import pytest

from agent_harness_lab.evaluation import trial_surfaces

# fixed-in: 0.233.0. A Codex trial's temporary CODEX_HOME held no login, so every run needed a paid CODEX_API_KEY.
CANARY = 'synthetic-login-canary'
BANNER = 'codex-cli 0.166.0\n'


def login(token='one'):
    return json.dumps({'tokens': {'refresh_token': CANARY + token}}).encode()


@pytest.fixture
def real(tmp_path):
    home = tmp_path / 'real-codex'
    home.mkdir()
    (home / 'auth.json').write_bytes(login())
    (home / 'auth.json').chmod(0o600)
    return home


@pytest.fixture
def environ(tmp_path, real):
    return {'HOME': str(tmp_path / 'home'), 'CODEX_HOME': str(real), 'CODEX_API_KEY': 'synthetic-key', 'PATH': '/synthetic/bin'}


@pytest.fixture
def session(tmp_path, environ):
    """The child environment of one session whose temporary CODEX_HOME may borrow the login."""
    (tmp_path / 'session').mkdir()
    return trial_surfaces.codex_environment(tmp_path / 'session', tmp_path / 'workspace', {}, environ)


def test_login_is_found_under_the_named_codex_home(real, environ):
    assert trial_surfaces.codex_login(environ) == real / 'auth.json'


def test_login_defaults_to_the_dot_codex_folder_of_the_named_home(tmp_path):
    (tmp_path / '.codex').mkdir()
    (tmp_path / '.codex/auth.json').write_bytes(login())
    assert trial_surfaces.codex_login({'HOME': str(tmp_path)}) == tmp_path / '.codex/auth.json'


def test_login_is_never_looked_up_in_the_process_home(tmp_path, monkeypatch):
    (tmp_path / '.codex').mkdir()
    (tmp_path / '.codex/auth.json').write_bytes(login())
    monkeypatch.setenv('HOME', str(tmp_path))
    assert trial_surfaces.codex_login({}) is None


@pytest.mark.parametrize('with_login, key, banner, expected', [
    pytest.param(True, None, BANNER, 'subscription_login', id='login'),
    pytest.param(True, 'synthetic-key', BANNER, 'subscription_login', id='login-beats-key'),
    pytest.param(False, 'synthetic-key', BANNER, 'environment_variable_unobserved', id='key-alternative'),
    pytest.param(False, '', BANNER, 'unavailable', id='empty-key-blocks'),
    pytest.param(False, None, BANNER, 'unavailable', id='nothing-blocks'),
    pytest.param(False, None, 'codex-test 1.0', 'unknown', id='unrecognised-binary'),
])
def test_authentication_reports_the_subscription_login_before_any_key(real, environ, with_login, key, banner, expected):
    if not with_login:
        (real / 'auth.json').unlink()
    environ.pop('CODEX_API_KEY')
    if key is not None:
        environ['CODEX_API_KEY'] = key
    assert trial_surfaces.codex_authentication(banner, environ) == expected


def test_user_harness_authentication_is_not_blocked_by_a_login_kept_elsewhere(real, environ):
    (real / 'auth.json').unlink()  # e.g. stored in the keyring of the real CODEX_HOME
    assert trial_surfaces.codex_authentication(BANNER, {k: v for k, v in environ.items() if k != 'CODEX_API_KEY'},
                                               user_harness=True) == 'inherited'


def test_a_borrowable_login_removes_the_api_key_from_the_session(session, tmp_path):
    assert 'CODEX_API_KEY' not in session
    assert session['CODEX_HOME'] == str(tmp_path / 'session/codex-home')
    assert not (Path(session['CODEX_HOME']) / 'auth.json').exists()  # nothing is copied until a run starts


def test_without_a_login_the_api_key_stays_the_alternative(tmp_path, environ, real):
    (real / 'auth.json').unlink()
    (tmp_path / 'session').mkdir()
    child = trial_surfaces.codex_environment(tmp_path / 'session', tmp_path / 'workspace', {}, environ)
    assert child['CODEX_API_KEY'] == 'synthetic-key'
    assert trial_surfaces.stage_codex_login(child) == 'not_staged'


def test_hook_validation_style_homes_never_borrow_a_login(tmp_path, monkeypatch, real):
    monkeypatch.setenv('HOME', str(real.parent))
    monkeypatch.setenv('CODEX_HOME', str(real))
    (tmp_path / 'session').mkdir()
    child = trial_surfaces.codex_environment(tmp_path / 'session', tmp_path / 'workspace', {}, {})
    assert trial_surfaces.stage_codex_login(child) == 'not_staged'
    assert not (tmp_path / 'session/codex-home/auth.json').exists()


def test_staging_copies_the_login_with_mode_0600_and_leaves_the_real_file_alone(session, real):
    before = (real / 'auth.json').stat()
    assert trial_surfaces.stage_codex_login(session) == 'subscription_login_copied'
    copy = Path(session['CODEX_HOME']) / 'auth.json'
    assert (copy.read_bytes(), copy.stat().st_mode & 0o777) == (login(), 0o600)
    assert ((real / 'auth.json').read_bytes(), (real / 'auth.json').stat().st_mtime_ns) == (login(), before.st_mtime_ns)


def test_staging_an_unreadable_login_reports_not_staged_and_leaves_no_copy(session, real):
    (real / 'auth.json').unlink()
    assert trial_surfaces.stage_codex_login(session) == 'not_staged'
    assert not (Path(session['CODEX_HOME']) / 'auth.json').exists()
    assert trial_surfaces.settle_codex_login(session) == 'not_staged'


def test_an_unchanged_copy_is_deleted_without_touching_the_real_login(session, real):
    trial_surfaces.stage_codex_login(session)
    before = (real / 'auth.json').stat().st_mtime_ns
    assert trial_surfaces.settle_codex_login(session) == 'subscription_login_copied'
    assert not (Path(session['CODEX_HOME']) / 'auth.json').exists()
    assert ((real / 'auth.json').read_bytes(), (real / 'auth.json').stat().st_mtime_ns) == (login(), before)


def test_a_refreshed_copy_replaces_an_unchanged_real_login_atomically(session, real):
    trial_surfaces.stage_codex_login(session)
    (Path(session['CODEX_HOME']) / 'auth.json').write_bytes(login('two'))
    assert trial_surfaces.settle_codex_login(session) == 'subscription_login_refreshed'
    assert ((real / 'auth.json').read_bytes(), (real / 'auth.json').stat().st_mode & 0o777) == (login('two'), 0o600)
    assert sorted(path.name for path in real.iterdir()) == ['auth.json']  # no sidecar left
    assert not (Path(session['CODEX_HOME']) / 'auth.json').exists()


def test_a_refreshed_copy_never_overwrites_a_real_login_that_changed_meanwhile(session, real):
    trial_surfaces.stage_codex_login(session)
    (Path(session['CODEX_HOME']) / 'auth.json').write_bytes(login('two'))
    (real / 'auth.json').write_bytes(login('three'))  # the user logged in again, or another Codex refreshed it
    assert trial_surfaces.settle_codex_login(session) == 'subscription_login_copied'
    assert (real / 'auth.json').read_bytes() == login('three')
    assert not (Path(session['CODEX_HOME']) / 'auth.json').exists()


@pytest.mark.parametrize('damage', [pytest.param(b'not json', id='text'), pytest.param(b'[1]', id='not-an-object'),
                                    pytest.param(b'\xff\xfe', id='undecodable'), pytest.param(b'', id='empty')])
def test_a_damaged_refreshed_copy_is_never_written_back(session, real, damage):
    trial_surfaces.stage_codex_login(session)
    (Path(session['CODEX_HOME']) / 'auth.json').write_bytes(damage)
    assert trial_surfaces.settle_codex_login(session) == 'subscription_login_copied'
    assert (real / 'auth.json').read_bytes() == login()


def test_a_symlinked_real_login_is_refreshed_in_place_and_stays_a_link(tmp_path, real):
    target = tmp_path / 'dotfiles-auth.json'
    target.write_bytes(login())
    (real / 'auth.json').unlink()
    (real / 'auth.json').symlink_to(target)
    environ = {'HOME': str(tmp_path / 'home'), 'CODEX_HOME': str(real)}
    (tmp_path / 'linked').mkdir()
    linked = trial_surfaces.codex_environment(tmp_path / 'linked', tmp_path / 'workspace', {}, environ)
    trial_surfaces.stage_codex_login(linked)
    (Path(linked['CODEX_HOME']) / 'auth.json').write_bytes(login('two'))
    assert trial_surfaces.settle_codex_login(linked) == 'subscription_login_refreshed'
    assert ((real / 'auth.json').is_symlink(), target.read_bytes()) == (True, login('two'))


def test_settling_twice_is_a_no_op(session, real):
    trial_surfaces.stage_codex_login(session)
    assert trial_surfaces.settle_codex_login(session) == 'subscription_login_copied'
    assert trial_surfaces.settle_codex_login(session) == 'not_staged'


def test_the_real_home_of_a_user_harness_run_is_never_copied_or_deleted(real, environ):
    child = {key: value for key, value in environ.items() if key != 'CODEX_API_KEY'}  # CODEX_HOME is the real one
    assert trial_surfaces.stage_codex_login(child) == 'not_staged'
    assert trial_surfaces.settle_codex_login(child) == 'not_staged'
    assert (real / 'auth.json').read_bytes() == login()


def test_no_status_or_descriptor_carries_login_content(session):
    statuses = [trial_surfaces.stage_codex_login(session), trial_surfaces.settle_codex_login(session)]
    text = json.dumps([statuses, trial_surfaces.descriptor('codex', credentials=statuses[0]),
                       trial_surfaces.descriptor('codex', user_harness=True)])
    assert CANARY not in text
    assert {'subscription_login_copied'} == set(statuses)
