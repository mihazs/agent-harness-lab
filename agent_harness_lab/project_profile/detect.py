"""What a checkout declares: technologies, package managers, declared and CI commands, layout.

Read-only, bounded and lexical. Only manifests, lockfiles, task-runner files and CI configs are read, through the no-follow
bounded reader; nothing is executed, written or sent. A fact the reader cannot settle (an unparsable manifest, an included
Makefile, a bound reached) goes to `unknown` and the runner it affects is listed in `incomplete`, so a later comparison
stays silent about it instead of treating absence as a contradiction. The profile describes the repository; it is evidence
for comparisons with the harness, never text to paste into one.
"""
import json
import os
import re
import stat
import tomllib

from agent_harness_lab.evaluation.integrity import read_regular
from agent_harness_lab.project_profile import versions
from agent_harness_lab.project_profile.commands import ALIASES, KIND, category, category_of, simple_commands, tools_in

PROFILE = 'ahl-project-profile-v1'
MAX_BYTES = 1024 * 1024
MAX_DIRS = 2000
MAX_DEPTH = 3
MAX_PACKAGES = 200
MAX_COMMANDS = 400
MAX_COMMAND_CHARS = 200
NESTED = 'nested package.json'
CONVENTIONAL_DIRS = frozenset('src lib app apps packages cmd internal crates pkg services libs modules tests test spec __tests__ e2e '
                              'docs scripts examples tools infra migrations alembic db prisma config'.split())
CONVENTIONAL_SUBDIRS = frozenset('unit integration e2e fixtures support functional acceptance smoke contract helpers'.split())
SKIP_DIRS = frozenset({'node_modules', 'vendor', 'dist', 'build', 'target', 'venv', 'env', '__pycache__', 'site-packages'})

PY_TABLES = ('pytest', 'ruff', 'black', 'isort', 'mypy', 'pyright')
PY_FILES = {'pytest.ini': 'pytest', 'tox.ini': 'tox', 'noxfile.py': 'nox', 'ruff.toml': 'ruff', '.ruff.toml': 'ruff',
            'mypy.ini': 'mypy', '.mypy.ini': 'mypy', 'pyrightconfig.json': 'pyright', '.flake8': 'flake8',
            '.pre-commit-config.yaml': 'pre-commit', 'lefthook.yml': 'lefthook'}
PY_LOCKS = {'uv.lock': 'uv', 'poetry.lock': 'poetry', 'pdm.lock': 'pdm', 'Pipfile.lock': 'pipenv', 'Pipfile': 'pipenv'}
NODE_LOCKS = {'pnpm-lock.yaml': 'pnpm', 'yarn.lock': 'yarn', 'bun.lockb': 'bun', 'bun.lock': 'bun',
              'package-lock.json': 'npm', 'npm-shrinkwrap.json': 'npm'}
NODE_CONFIGS = {'.yarnrc.yml': 'yarn', '.yarnrc': 'yarn', '.pnp.cjs': 'yarn', 'bunfig.toml': 'bun'}
NODE_MONOREPO = {'pnpm-workspace.yaml': 'pnpm-workspaces', 'lerna.json': 'lerna', 'nx.json': 'nx', 'turbo.json': 'turbo'}
CI_FILES = {'.gitlab-ci.yml': 'gitlab-ci', '.circleci/config.yml': 'circleci', 'azure-pipelines.yml': 'azure-pipelines',
            'Jenkinsfile': 'jenkins'}
OTHER_MANIFESTS = {'pom.xml': ('java', 'maven'), 'build.gradle': ('java', 'gradle'), 'build.gradle.kts': ('kotlin', 'gradle'),
                   'Gemfile': ('ruby', 'bundler'), 'composer.json': ('php', 'composer'), 'mix.exs': ('elixir', 'mix')}
_REQUIREMENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
_MAKE_TARGET = re.compile(r'^([A-Za-z0-9_][A-Za-z0-9_.-]*)[ \t]*:(?![=:])')
_JUST_RECIPE = re.compile(r'^@?([A-Za-z_][A-Za-z0-9_-]*)(?:[ \t]+[^:\n]*)?:(?![=])')
_MAKE_DYNAMIC = re.compile(r'^[^\s#:=]*[%$][^\s:=]*[ \t]*:(?![=])')
_RUN_KEY = re.compile(r'^\s*(?:-\s*)?run:\s*(.*)$')
_BLOCK = re.compile(r'^[|>][+-]?\d*$')


class Repo:
    """Bounded no-follow reads of one checkout; whatever cannot be read or parsed is recorded, never guessed."""

    def __init__(self, root):
        self.root, self.unknown, self.incomplete, self.tree = os.path.abspath(root), set(), set(), None

    def kind(self, rel):
        try: return stat.S_IFMT(os.lstat(os.path.join(self.root, rel)).st_mode)
        except OSError: return None

    def is_file(self, rel): return self.kind(rel) == stat.S_IFREG
    def is_dir(self, rel): return self.kind(rel) == stat.S_IFDIR

    def read(self, rel):
        if not self.is_file(rel): return None
        try: return read_regular(os.path.join(self.root, rel), MAX_BYTES).decode('utf-8')
        except (OSError, ValueError, UnicodeError):
            self.unknown.add('unreadable:' + rel)
            return None

    def parse(self, rel, loader):
        text = self.read(rel)
        if text is None: return None
        try: value = loader(text)
        except (ValueError, RecursionError):  # JSON and TOML decode errors both derive from ValueError
            value = None
        if not isinstance(value, dict): self.unknown.add('unparsed:' + rel)
        return value if isinstance(value, dict) else None

    def listing(self, rel=''):
        try:
            with os.scandir(os.path.join(self.root, rel) if rel else self.root) as entries:
                return sorted(entries, key=lambda entry: entry.name)
        except OSError: return []

    def find(self, names):
        """Relative paths of files named in `names`, breadth first to MAX_DEPTH, never through a link or a generated folder."""
        if self.tree is None:
            self.tree, queue, visited = [], [('', 0)], 0
            while queue:
                rel, depth = queue.pop(0)
                visited += 1
                if visited > MAX_DIRS:
                    self.unknown.add('walk_bound')
                    break
                for entry in self.listing(rel):
                    path = f'{rel}/{entry.name}' if rel else entry.name
                    if entry.is_symlink(): continue
                    if entry.is_dir(follow_symlinks=False):
                        if depth < MAX_DEPTH and entry.name not in SKIP_DIRS and not entry.name.startswith('.'): queue.append((path, depth + 1))
                    else: self.tree.append((path, entry.name))
        return [path for path, name in self.tree if name in names]


class Facts:
    def __init__(self):
        self.tech, self.managers, self.commands, self.ci, self.scripts, self.nested = {}, {}, [], [], {}, {}
        self.runtimes = []

    def add(self, kind, name, evidence, version=None):
        row = self.tech.setdefault((kind, name), dict(kind=kind, name=name, version=None, evidence=set()))
        row['evidence'].add(evidence)
        if version and not row['version']: row['version'] = str(version)[:64]

    def runtime(self, name, source, text, pinned=False):
        """A runtime version a manifest declares, as a minimum or an exact pin; an unparsable constraint adds nothing."""
        if not isinstance(text, str): return
        lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith('#')]
        parsed = versions.constraint(lines[0]) if len(lines) == 1 else None
        if parsed and (parsed[0] == 'pin' or not pinned):
            self.runtimes.append(dict(runtime=name, source=source, kind=parsed[0], version=versions.text(parsed[1])))

    def manager(self, ecosystem, name, evidence):
        self.managers.setdefault((ecosystem, name), set()).add(evidence)
        self.add('package_manager', name, evidence)

    def deps(self, names, evidence):
        for name in names:
            name = ALIASES.get(name, name)
            if name in KIND: self.add(KIND[name], name, evidence)

    def command(self, runner, target, source, text='', root=True):
        text = ' '.join(text.split())[:MAX_COMMAND_CHARS]
        (self.scripts if root else self.nested).setdefault(runner, set()).add(target)
        if len(self.commands) < MAX_COMMANDS:
            self.commands.append(dict(runner=runner, target=target, category=category(target, text), source=source,
                                      tools=tools_in(text)))


def _requirement_names(items):
    return {re.sub(r'[-_.]+', '-', match.group()).lower() for item in items
            if isinstance(item, str) and (match := _REQUIREMENT.match(item))}


def _python(repo, facts):
    project = repo.parse('pyproject.toml', tomllib.loads)
    pipfile = repo.parse('Pipfile', tomllib.loads)
    requirements = [name for name in ('requirements.txt', 'requirements-dev.txt', 'dev-requirements.txt') if repo.is_file(name)]
    if not any((project, pipfile, requirements, repo.is_file('setup.py'), repo.is_file('setup.cfg'))): return
    tables, deps = {}, set()
    meta = project.get('project', {}) if project else {}
    tool = project.get('tool', {}) if project else {}
    facts.add('language', 'python', 'pyproject.toml' if project else 'python manifest', meta.get('requires-python') or None)
    groups = [meta.get('dependencies', []), *meta.get('optional-dependencies', {}).values(),
              *(group for group in (project or {}).get('dependency-groups', {}).values() if isinstance(group, list))]
    poetry = tool.get('poetry', {})
    legacy = [poetry.get('dependencies', {}), poetry.get('dev-dependencies', {}),
              *(group.get('dependencies', {}) for group in poetry.get('group', {}).values())]
    deps |= _requirement_names(item for group in groups if isinstance(group, list) for item in group)
    deps |= _requirement_names(name for group in legacy if isinstance(group, dict) for name in group)
    deps |= _requirement_names(tool.get('uv', {}).get('dev-dependencies', []))
    for name in requirements:
        deps |= _requirement_names(line.strip() for line in (repo.read(name) or '').splitlines() if line.strip() and line[0] not in '#-')
    deps |= _requirement_names([*(pipfile or {}).get('packages', {}), *(pipfile or {}).get('dev-packages', {})])
    facts.deps(deps, 'pyproject.toml' if project else 'requirements')
    for name in PY_TABLES:
        if name in tool: tables[name] = 'pyproject.toml'
    for name, tool_name in PY_FILES.items():
        if repo.is_file(name): tables[tool_name] = name
    for tool_name, evidence in tables.items():
        facts.add(KIND.get(tool_name, 'tool'), tool_name, evidence)
    for name, manager in PY_LOCKS.items():
        if repo.is_file(name): facts.manager('python', manager, name)
    for manager in ('poetry', 'uv', 'pdm'):
        if manager in tool: facts.manager('python', manager, 'pyproject.toml')
    text = repo.read('.python-version')
    if text and text.strip(): facts.add('language', 'python', '.python-version', text.split()[0])
    facts.runtime('python', 'pyproject.toml', meta.get('requires-python') or '')
    facts.runtime('python', '.python-version', text or '', pinned=True)


def _node(repo, facts):
    root = repo.parse('package.json', json.loads)
    packages = repo.find({'package.json'})[:MAX_PACKAGES]
    if root is None and not packages: return
    facts.add('language', 'javascript', 'package.json')
    for rel in packages:
        data = root if rel == 'package.json' else repo.parse(rel, json.loads)
        if not isinstance(data, dict): continue
        rel = rel if rel == 'package.json' else NESTED  # a nested package's folder name is not part of the profile
        facts.deps([*(data.get('dependencies') or {}), *(data.get('devDependencies') or {}), *(data.get('peerDependencies') or {})], rel)
        scripts = data.get('scripts') if isinstance(data.get('scripts'), dict) else {}
        for name, text in sorted(scripts.items()):
            if isinstance(name, str) and isinstance(text, str): facts.command('package.json', name, rel, text, root=rel == 'package.json')
    if isinstance(root, dict):
        facts.scripts.setdefault('package.json', set())
        engines = root.get('engines') if isinstance(root.get('engines'), dict) else {}
        if isinstance(engines.get('node'), str): facts.add('language', 'javascript', 'package.json', 'node ' + engines['node'])
        facts.runtime('node', 'package.json', engines.get('node') or '')
        field = root.get('packageManager')
        if isinstance(field, str) and (name := field.partition('@')[0]) in ('npm', 'pnpm', 'yarn', 'bun'):
            facts.manager('node', name, 'package.json')
        if root.get('workspaces'): facts.add('monorepo', 'workspaces', 'package.json')
    for name, manager in NODE_LOCKS.items():
        if repo.is_file(name): facts.manager('node', manager, name)
    for name, manager in NODE_CONFIGS.items():
        if repo.is_file(name): facts.manager('node', manager, name)
    for name, label in NODE_MONOREPO.items():
        if repo.is_file(name): facts.add('monorepo', label, name)
    if repo.is_file('tsconfig.json') or ('type_checker', 'tsc') in facts.tech:
        facts.add('language', 'typescript', 'tsconfig.json' if repo.is_file('tsconfig.json') else 'package.json')
    for name in ('.nvmrc', '.node-version'):
        text = repo.read(name)
        if text and text.strip(): facts.add('language', 'javascript', name, 'node ' + text.split()[0])
        facts.runtime('node', name, text or '', pinned=True)
    if repo.is_file('deno.json') or repo.is_file('deno.jsonc'): facts.add('language', 'typescript', 'deno.json', 'deno')


def _rust_go_other(repo, facts):
    cargo = repo.parse('Cargo.toml', tomllib.loads)
    if cargo is not None:
        package = cargo.get('package', {})
        facts.add('language', 'rust', 'Cargo.toml', package.get('rust-version') or package.get('edition'))
        if isinstance(package.get('rust-version'), str): facts.runtime('rust', 'Cargo.toml', '>=' + package['rust-version'])
        facts.manager('rust', 'cargo', 'Cargo.toml')
        if 'workspace' in cargo: facts.add('monorepo', 'cargo-workspace', 'Cargo.toml')
    text = repo.read('go.mod')
    if text is not None:
        version = re.search(r'^go\s+(\S+)', text, re.M)
        facts.add('language', 'go', 'go.mod', version.group(1) if version else None)
        if version: facts.runtime('go', 'go.mod', '>=' + version.group(1))
        facts.manager('go', 'go', 'go.mod')
        if repo.is_file('go.work'): facts.add('monorepo', 'go-workspace', 'go.work')
    for name, (language, manager) in OTHER_MANIFESTS.items():
        if repo.is_file(name):
            facts.add('language', language, name)
            facts.manager(language, manager, name)
    names = {entry.name for entry in repo.listing()}
    for name in sorted(names):
        if name.startswith('Dockerfile') or name in ('docker-compose.yml', 'docker-compose.yaml', 'compose.yaml', 'compose.yml'):
            facts.add('container', 'docker', name)
        if name.endswith(('.csproj', '.sln')):
            facts.add('language', 'csharp', name)
            facts.manager('csharp', 'dotnet', name)
    for rel, label in CI_FILES.items():
        if repo.is_file(rel): facts.add('ci', label, rel)


def _task_names(raw, facts, source):
    """Declare the tasks (keys one level below `tasks:`) with their command lines; the count found, 0 when none were read."""
    lines = raw.splitlines()
    start = next((i for i, line in enumerate(lines) if re.match(r'tasks:\s*(?:#.*)?$', line)), None)
    tasks, indent = [], None
    for line in lines[start + 1:] if start is not None else []:
        if not line.strip() or line.lstrip().startswith('#'): continue
        width = len(line) - len(line.lstrip())
        if width == 0: break
        indent = width if indent is None else indent
        key = re.match(r'(["\']?)([A-Za-z0-9_][\w.:-]*)\1:', line.lstrip()) if width == indent else None
        if key: tasks.append((key.group(2), []))
        elif tasks and width > indent: tasks[-1][1].append(line.strip().lstrip('- ').strip())
    for task, body in tasks: facts.command('task', task, source, ' '.join(body))
    return len(tasks)


def _runners(repo, facts):
    """Declared Makefile, Taskfile and justfile targets; an include or a dynamic name leaves that runner incomplete."""
    for name in ('Makefile', 'makefile', 'GNUmakefile'):
        text = repo.read(name)
        if text is None: continue
        facts.add('task_runner', 'make', name)
        facts.scripts.setdefault('make', set())
        lines = text.splitlines()
        if any(re.match(r'-?include\b', line) or _MAKE_DYNAMIC.match(line) for line in lines): repo.incomplete.add('make')
        for index, line in enumerate(lines):
            found = _MAKE_TARGET.match(line)
            if found and not found.group(1).startswith('.'):
                body = ' '.join(row.strip() for row in lines[index + 1:index + 4] if row.startswith(('\t', '  ')))
                facts.command('make', found.group(1), name, body)
        break
    for name in ('Taskfile.yml', 'Taskfile.yaml'):
        raw = repo.read(name)
        if raw is None: continue
        facts.add('task_runner', 'task', name)
        facts.scripts.setdefault('task', set())
        if re.search(r'^includes:', raw, re.M) or not _task_names(raw, facts, name): repo.incomplete.add('task')
        break
    for name in ('justfile', 'Justfile', '.justfile'):
        text = repo.read(name)
        if text is None: continue
        facts.add('task_runner', 'just', name)
        facts.scripts.setdefault('just', set())
        if re.search(r'^(?:import|mod)\b', text, re.M): repo.incomplete.add('just')
        for line in text.splitlines():
            found = _JUST_RECIPE.match(line)
            if found: facts.command('just', found.group(1), name)
        break


def _ci(repo, facts):
    """Command lines of GitHub Actions `run:` steps and GitLab `script:` lists that work in a known category."""
    sources = [f'.github/workflows/{entry.name}' for entry in repo.listing('.github/workflows')
               if entry.name.endswith(('.yml', '.yaml'))][:64]
    if sources: facts.add('ci', 'github-actions', '.github/workflows')
    sources += ['.gitlab-ci.yml'] if repo.is_file('.gitlab-ci.yml') else []
    for rel in sources:
        lines, index = (repo.read(rel) or '').splitlines(), 0
        for index, line in enumerate(lines):
            found, commands = _RUN_KEY.match(line), []
            if found and _BLOCK.match(found.group(1).strip()):
                indent = len(line) - len(line.lstrip())
                for row in lines[index + 1:index + 40]:
                    if row.strip() and len(row) - len(row.lstrip()) <= indent: break
                    commands.append(row.strip())
            elif found: commands.append(found.group(1).strip().strip('"\''))
            elif rel == '.gitlab-ci.yml' and line.lstrip().startswith('- '): commands.append(line.lstrip()[2:].strip().strip('"\''))
            for command in commands:
                kinds = {category_of(one) for one in simple_commands(command)} - {None}
                if kinds and len(facts.ci) < MAX_COMMANDS:
                    facts.ci.append(dict(command=command[:MAX_COMMAND_CHARS], category=sorted(kinds)[0], source=rel, tools=tools_in(command)))


def _layout(repo, facts):
    """Source, test and migration folders by their conventional names; any other folder name is counted, never listed."""
    top = [entry.name for entry in repo.listing() if entry.is_dir(follow_symlinks=False) and not entry.name.startswith('.')
           and entry.name not in SKIP_DIRS][:40]
    inside = lambda name: sorted(entry.name for entry in repo.listing(name) if entry.is_dir(follow_symlinks=False)
                                 and entry.name in CONVENTIONAL_SUBDIRS)
    tests = [name for name in top if name in ('tests', 'test', 'spec', '__tests__', 'e2e')]
    migrations = [path for path in ('migrations', 'alembic', 'db/migrate', 'prisma/migrations', 'db/migrations') if repo.is_dir(path)]
    return dict(top_level=[name for name in top if name in CONVENTIONAL_DIRS], other_top_level=sum(name not in CONVENTIONAL_DIRS for name in top),
                source_dirs=[name for name in top if name in ('src', 'lib', 'app', 'packages', 'apps', 'cmd', 'internal', 'crates')],
                test_dirs=tests, test_subdirs={name: inside(name) for name in tests}, migration_dirs=migrations,
                docs_dir='docs' in top, src_layout=repo.is_dir('src'), monorepo=sorted(name for kind, name in facts.tech if kind == 'monorepo'))


def detect(project):
    """The deterministic profile of one checkout (a path or a Repo); the checkout is only read."""
    repo, facts = project if isinstance(project, Repo) else Repo(project), Facts()
    for step in (_python, _node, _rust_go_other, _runners, _ci): step(repo, facts)
    if any(item in ('unparsed:package.json', 'unreadable:package.json') for item in repo.unknown): repo.incomplete.add('package.json')
    if 'walk_bound' in repo.unknown: repo.incomplete.add('package.json')
    managers = [dict(ecosystem=ecosystem, name=name, evidence=sorted(evidence)) for (ecosystem, name), evidence in sorted(facts.managers.items())]
    runners = {key: dict(present=key in facts.scripts, targets=sorted(facts.scripts.get(key, ())), nested=key in facts.nested,
                         nested_targets=sorted(facts.nested.get(key, ())), complete=key not in repo.incomplete)
               for key in ('package.json', 'make', 'task', 'just')}
    return dict(profile=PROFILE, status='observed', publication_allowed=False,
                technologies=[dict(row, evidence=sorted(row['evidence'])) for _, row in sorted(facts.tech.items())],
                package_managers=managers, runtimes=sorted(facts.runtimes, key=lambda row: (row['runtime'], row['source'])), commands=facts.commands, ci_commands=facts.ci,
                runners=runners,
                incomplete=sorted(repo.incomplete), layout=_layout(repo, facts),
                unknown=sorted({re.sub(r'^(\w+):.+/(package\.json)$', r'\1:nested \2', item) for item in repo.unknown}))
