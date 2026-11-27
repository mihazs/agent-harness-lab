"""A temporary CODEX_HOME keeps only its configuration after a session; any other home is never touched."""
from agent_harness_lab.evaluation import trial_surfaces


# fixed-in: every trial and preparation session left about 50 MB of plugin downloads and caches in its temporary CODEX_HOME
def test_a_temporary_codex_home_keeps_only_its_configuration_and_a_real_home_is_untouched(tmp_path):
    (tmp_path / 'trial').mkdir()
    env = trial_surfaces.codex_environment(tmp_path / 'trial', tmp_path / 'workspace', {'model': 'm'}, {'HOME': str(tmp_path / 'none')})
    home = tmp_path / 'trial' / 'codex-home'
    (home / 'plugins' / 'x').mkdir(parents=True); (home / 'plugins' / 'x' / 'big').write_bytes(b'0' * 1024)
    (home / 'models_cache.json').write_text('{}'); (home / 'hooks.json').write_text('{}')
    real = tmp_path / 'real'; (real / 'plugins').mkdir(parents=True)
    trial_surfaces.prune_codex_home({'CODEX_HOME': str(real)})
    assert (real / 'plugins').is_dir()
    trial_surfaces.prune_codex_home(env)
    assert sorted(p.name for p in home.iterdir()) == ['config.toml', 'hooks.json']
    trial_surfaces.prune_codex_home(env)  # a second call is a no-op
