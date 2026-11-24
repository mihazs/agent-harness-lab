"""The project profile reads manifests only and names what it could not settle (#1114); synthetic checkouts only."""
import json
import os

from agent_harness_lab.project_profile import detect
from tests.support.profile_fixture import build, package


def names(profile, kind):
    return {row['name'] for row in profile['technologies'] if row['kind'] == kind}


def commands_of(profile):
    return {(row['runner'], row['target']): row['category'] for row in profile['commands']}


def test_python_project_with_uv(tmp_path):
    root = build(tmp_path, {'pyproject.toml': '[project]\nname = "x"\nrequires-python = ">=3.11"\ndependencies = ["fastapi>=0.1"]\n'
                            '[project.optional-dependencies]\ntest = ["pytest==9", "pytest-xdist"]\nlint = ["ruff"]\n'
                            '[tool.ruff]\nline-length = 100\n', 'uv.lock': '', 'mypy.ini': ''})
    profile = detect.detect(root)
    assert [row['version'] for row in profile['technologies'] if row['name'] == 'python'] == ['>=3.11']
    assert names(profile, 'test_runner') == {'pytest'} and names(profile, 'linter') == {'ruff'}
    assert names(profile, 'type_checker') == {'mypy'} and 'fastapi' in names(profile, 'framework')
    assert profile['package_managers'] == [dict(ecosystem='python', name='uv', evidence=['uv.lock'])]
    assert profile['status'] == 'observed' and profile['unknown'] == [] and profile['publication_allowed'] is False


def test_poetry_is_found_in_the_table_and_the_lockfile_alike(tmp_path):
    root = build(tmp_path, {'pyproject.toml': '[tool.poetry]\nname = "x"\n[tool.poetry.dependencies]\nrequests = "*"\n'
                            '[tool.poetry.group.dev.dependencies]\npytest = "*"\n'})
    profile = detect.detect(root)
    assert [row['name'] for row in profile['package_managers']] == ['poetry'] and names(profile, 'test_runner') == {'pytest'}


def test_node_project_scripts_get_categories_and_the_manager_comes_from_the_lockfile_and_the_field(tmp_path):
    scripts = {'test': 'vitest run', 'lint': 'eslint .', 'check': 'tsc --noEmit', 'format': 'prettier -w .', 'build': 'vite build',
               'dev': 'vite', 'release': 'node release.js'}
    root = build(tmp_path, {'package.json': package(scripts, packageManager='pnpm@9.1.0', devDependencies={'vitest': '1', 'eslint': '9', 'typescript': '5'}),
                            'pnpm-lock.yaml': '', 'tsconfig.json': '{}'})
    profile = detect.detect(root)
    assert commands_of(profile) == {('package.json', 'test'): 'test', ('package.json', 'lint'): 'lint', ('package.json', 'check'): 'typecheck',
                                    ('package.json', 'format'): 'format', ('package.json', 'build'): 'build', ('package.json', 'dev'): 'run',
                                    ('package.json', 'release'): None}
    assert [(row['name'], sorted(row['evidence'])) for row in profile['package_managers']] == [('pnpm', ['package.json', 'pnpm-lock.yaml'])]
    assert {'vitest', 'eslint', 'tsc'} <= {row['name'] for row in profile['technologies']}
    assert 'typescript' in names(profile, 'language')
    assert profile['runners']['package.json']['targets'] == sorted(scripts)


def test_a_nested_package_declares_its_own_targets_apart_from_the_root(tmp_path):
    root = build(tmp_path, {'package.json': package({'test': 'jest'}, workspaces=['apps/*']),
                            'apps/web/package.json': package({'dev': 'vite', 'storybook': 'sb'})})
    runner = detect.detect(root)['runners']['package.json']
    assert runner['targets'] == ['test'] and runner['nested'] and runner['nested_targets'] == ['dev', 'storybook']


def test_rust_go_and_container_files(tmp_path):
    root = build(tmp_path, {'Cargo.toml': '[workspace]\nmembers = ["a"]\n', 'go.mod': 'module x\n\ngo 1.22\n', 'Dockerfile': 'FROM scratch\n'})
    profile = detect.detect(root)
    assert names(profile, 'language') == {'rust', 'go'} and names(profile, 'container') == {'docker'}
    assert {('rust', 'cargo'), ('go', 'go')} <= {(row['ecosystem'], row['name']) for row in profile['package_managers']}
    assert [row['version'] for row in profile['technologies'] if row['kind'] == 'language' and row['name'] == 'go'] == ['1.22']
    assert 'cargo-workspace' in names(profile, 'monorepo')


def test_makefile_taskfile_and_justfile_targets_with_categories(tmp_path):
    root = build(tmp_path, {
        'Makefile': '.PHONY: test\ntest:\n\tpytest -q\nlint:\n\truff check .\nclean:\n\trm -rf build\nVAR := x\n',
        'Taskfile.yml': 'version: "3"\ntasks:\n  build:\n    cmds:\n      - go build ./...\n  fmt:\n    cmds:\n      - gofmt -w .\n',
        'justfile': 'set shell := ["bash", "-c"]\ntest:\n  cargo test\n\nlint arg="x":\n  cargo clippy\n'})
    profile = detect.detect(root)
    assert commands_of(profile) == {('make', 'test'): 'test', ('make', 'lint'): 'lint', ('make', 'clean'): None, ('task', 'build'): 'build',
                                    ('task', 'fmt'): 'format', ('just', 'test'): 'test', ('just', 'lint'): 'lint'}
    assert profile['incomplete'] == [] and all(profile['runners'][name]['complete'] for name in ('make', 'task', 'just'))


def test_an_included_or_patterned_runner_file_is_incomplete_not_empty(tmp_path):
    root = build(tmp_path, {'Makefile': 'include other.mk\ntest:\n\tpytest\n', 'Taskfile.yml': 'version: "3"\nincludes:\n  sub: ./sub\ntasks:\n  a:\n    cmds: []\n',
                            'justfile': 'import "other.just"\nbuild:\n  true\n'})
    profile = detect.detect(root)
    assert profile['incomplete'] == ['just', 'make', 'task']
    assert not any(profile['runners'][name]['complete'] for name in ('make', 'task', 'just'))


def test_a_make_pattern_rule_makes_the_target_set_incomplete(tmp_path):
    assert detect.detect(build(tmp_path, {'Makefile': '%.o: %.c\n\tcc -c $<\nall:\n\ttrue\n'}))['incomplete'] == ['make']


def test_ci_run_steps_keep_only_commands_that_verify_or_build(tmp_path):
    workflow = ('jobs:\n  t:\n    steps:\n      - run: pytest -q\n      - run: echo hello\n      - run: |\n          npm ci\n          npm run lint\n'
                '          cd web && npm test\n      - uses: actions/checkout@v4\n')
    profile = detect.detect(build(tmp_path, {'.github/workflows/ci.yml': workflow}))
    assert [(row['command'], row['category']) for row in profile['ci_commands']] == [('pytest -q', 'test'), ('npm run lint', 'lint'), ('cd web && npm test', 'test')]
    assert 'github-actions' in names(profile, 'ci')


def test_an_unparsable_manifest_is_unknown_and_never_a_guess(tmp_path):
    root = build(tmp_path, {'package.json': '{"scripts": ', 'pyproject.toml': 'not = [valid', 'Makefile': 'test:\n\ttrue\n'})
    profile = detect.detect(root)
    assert profile['unknown'] == ['unparsed:package.json', 'unparsed:pyproject.toml'] and 'package.json' in profile['incomplete']
    assert not profile['runners']['package.json']['complete'] and profile['runners']['make']['targets'] == ['test']
    assert names(profile, 'language') == {'javascript'} and profile['package_managers'] == []


def test_a_non_object_json_manifest_is_unparsed(tmp_path):
    assert detect.detect(build(tmp_path, {'package.json': '[1, 2]'}))['unknown'] == ['unparsed:package.json']


def test_an_oversized_manifest_is_unreadable(tmp_path, monkeypatch):
    monkeypatch.setattr(detect, 'MAX_BYTES', 64)
    profile = detect.detect(build(tmp_path, {'package.json': json.dumps(package({'a': 'x' * 200}))}))
    assert profile['unknown'] == ['unreadable:package.json'] and 'package.json' in profile['incomplete']


def test_a_linked_manifest_is_not_followed(tmp_path):
    outside = tmp_path / 'outside.json'
    outside.write_text(json.dumps(package({'test': 'jest'})))
    root = build(tmp_path, {'README.md': ''})
    os.symlink(outside, root / 'package.json')
    profile = detect.detect(root)
    assert profile['commands'] == [] and not profile['runners']['package.json']['present']


def test_layout_names_source_test_and_migration_folders(tmp_path):
    root = build(tmp_path, {'src/pkg/a.py': '', 'tests/unit/test_a.py': '', 'tests/integration/test_b.py': '', 'migrations/0001.sql': '', 'docs/a.md': ''})
    layout = detect.detect(root)['layout']
    assert layout['src_layout'] and layout['test_dirs'] == ['tests'] and layout['test_subdirs'] == {'tests': ['integration', 'unit']}
    assert layout['migration_dirs'] == ['migrations'] and layout['docs_dir'] and 'src' in layout['source_dirs']


def test_generated_and_hidden_folders_are_not_walked(tmp_path):
    root = build(tmp_path, {'node_modules/dep/package.json': package({'postinstall': 'x'}), '.hidden/package.json': package({'a': 'b'}),
                            'package.json': package({'test': 'jest'})})
    assert detect.detect(root)['runners']['package.json']['nested'] is False


def test_the_profile_is_deterministic_bounded_and_read_only(tmp_path, monkeypatch):
    monkeypatch.setattr(detect, 'MAX_COMMANDS', 5)
    root = build(tmp_path, {'package.json': package({f'test{index}': 'jest' for index in range(20)})})
    before = sorted((path.relative_to(root).as_posix(), path.stat().st_mtime_ns) for path in root.rglob('*'))
    first, second = detect.detect(root), detect.detect(root)
    assert first == second and len(first['commands']) == 5
    assert before == sorted((path.relative_to(root).as_posix(), path.stat().st_mtime_ns) for path in root.rglob('*'))
