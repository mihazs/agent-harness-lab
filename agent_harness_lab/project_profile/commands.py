"""Shell command lines as simple commands, and the closed vocabularies that name and classify them.

One reader serves harness text (what an instruction file tells the agent to run) and session history (what the agent ran),
so both sides of a comparison normalize the same way. A command is reduced to a program, its arguments and whether a `cd`
came before it on the line; nothing is executed or expanded. Only vocabulary names and names the project itself declares
survive into a key, so a path, URL, secret or one-off argument never becomes part of a count.
"""
import os
import re
import shlex

NODE_MANAGERS = ('npm', 'pnpm', 'yarn', 'bun')
PYTHON_MANAGERS = ('uv', 'poetry', 'pipenv', 'pdm')
ECOSYSTEMS = dict(node=NODE_MANAGERS, python=PYTHON_MANAGERS)
RUNNERS = NODE_MANAGERS + ('make', 'task', 'just')
# Script names conventionally written without `run`; any other bare `<pm> name` could be a binary, so it is never judged.
BARE_SCRIPTS = frozenset('test lint build dev start format fmt typecheck check verify ci e2e'.split())
NPM_ALIASES = frozenset('test start stop restart t'.split())
SEPARATORS = frozenset({'&&', '||', ';', '|', '&', '|&', ';;'})
WRAPPERS = frozenset('sudo time nohup command exec env nice timeout stdbuf'.split())
RUN_WRAPPERS = frozenset({('uv', 'run'), ('poetry', 'run'), ('pipenv', 'run'), ('pdm', 'run'), ('pnpm', 'exec'), ('pnpm', 'dlx'),
                          ('yarn', 'dlx'), ('npm', 'exec'), ('bun', 'x'), ('python', '-m'), ('python3', '-m'), ('npx',), ('bunx',)})
INTERPRETERS = frozenset('python python3 node bash sh zsh ruby perl deno'.split())
SETUP = frozenset('setup install bootstrap deps init'.split())
CATEGORIES = ('test', 'lint', 'typecheck', 'format', 'build', 'run')
VERIFICATION = ('test', 'lint', 'typecheck', 'build')
NAME_CATEGORY = (('typecheck', 'typecheck type types tsc mypy pyright'), ('test', 'test tests spec e2e unit integration coverage'),
                 ('lint', 'lint eslint clippy'), ('format', 'format fmt prettier'), ('build', 'build compile dist bundle'),
                 ('run', 'dev start serve watch'))
TEXT_CATEGORY = (
    ('typecheck', r'\b(?:tsc|mypy|pyright|cargo check|go vet)\b'),
    ('format', r'\b(?:prettier|black|isort|gofmt|rustfmt|cargo fmt|ruff format|biome format|dprint)\b'),
    ('lint', r'\b(?:eslint|ruff|flake8|pylint|clippy|golangci-lint|biome (?:lint|check)|oxlint|rubocop)\b'),
    ('test', r'\b(?:pytest|jest|vitest|mocha|playwright|cypress|ava|tox|nox|unittest|cargo test|cargo nextest|go test|rspec|phpunit)\b'),
    ('build', r'\b(?:webpack|rollup|esbuild|vite build|cargo build|go build|tsc -b|docker build|python -m build|maturin)\b'))
# tool name -> kind; dependency names map onto these through ALIASES (PEP 503 or npm names).
KIND = {name: kind for kind, names in (
    ('test_runner', 'pytest tox nox jest vitest mocha ava playwright cypress'),
    ('linter', 'ruff flake8 pylint eslint oxlint biome'),
    ('formatter', 'black isort prettier'),
    ('type_checker', 'mypy pyright tsc'),
    ('hook_runner', 'pre-commit lefthook husky'),
    ('framework', 'django flask fastapi starlette celery sqlalchemy alembic pydantic pandas numpy torch tensorflow scikit-learn '
                  'react next vue nuxt svelte angular express nestjs vite webpack electron astro remix tailwindcss'),
) for name in names.split()}
ALIASES = {'@biomejs/biome': 'biome', 'typescript': 'tsc', '@playwright/test': 'playwright', '@angular/core': 'angular',
           '@nestjs/core': 'nestjs', '@remix-run/react': 'remix', 'sklearn': 'scikit-learn'}
TOOLS = frozenset(KIND) | frozenset(RUNNERS) | frozenset(('cargo', 'go', 'uv', 'poetry', 'pipenv', 'pdm', 'pip', 'pnpm'))
_WORD = re.compile(r'[A-Za-z][\w.-]*')
_REDIRECT = re.compile(r'^\d*[<>]')
_NAME = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.:-]{0,63}')


def category(name, text=''):
    """The category a command name (a script or target) or its command line falls in; None when neither says."""
    words = re.split(r'[:_./\s-]+', name.lower())
    if words[0] in SETUP: return None  # `setup:test` installs the tooling; it does not verify anything
    found = next((cat for cat, names in NAME_CATEGORY if set(words) & set(names.split())), None)
    return found or next((cat for cat, shape in TEXT_CATEGORY if re.search(shape, text.lower())), None)


def tools_in(text):
    """The known tool names a command line contains."""
    return sorted({word for word in _WORD.findall(text.lower()) if word in TOOLS})


def _tokens(line):
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def simple_commands(line):
    """Simple commands of one shell line as dicts of program, path, args and cd; an unparsable line gives none."""
    try: tokens = _tokens(line)
    except ValueError: return []
    found, current, moved = [], [], False
    for token in [*tokens, ';']:
        if token not in SEPARATORS:
            current.append(token)
            continue
        words, current = current, []
        while words and (words[0] in WRAPPERS or re.fullmatch(r'\w+=\S*', words[0])):
            wrapper = words.pop(0)
            while words and (words[0].startswith('-') or re.fullmatch(r'\d+[smhd]?', words[0]) and wrapper == 'timeout'): words.pop(0)
        cut = next((i for i, word in enumerate(words) if _REDIRECT.match(word) or word in ('(', ')')), len(words))
        words = words[:cut]
        if not words: continue
        if words[0] == 'cd':
            moved = True
            continue
        found.append(dict(program=os.path.basename(words[0]), path=words[0] if '/' in words[0] else None, args=words[1:], cd=moved))
    return found


def unwrapped(command):
    """The command a run wrapper (`uv run`, `npx`, `python -m`) starts, else the command itself."""
    program, args = command['program'], command['args']
    for size in (2, 1):
        if tuple([program, *args[:size - 1]]) in RUN_WRAPPERS and len(args) >= size - 1:
            rest = args[size - 1:]
            while rest and rest[0].startswith('-'): rest = rest[1:]
            if rest: return dict(program=os.path.basename(rest[0]), path=rest[0] if '/' in rest[0] else None, args=rest[1:], cd=command['cd'])
    return command


def runner_target(command, *, any_directory=False):
    """(runner, target) when the command runs a declared script or target by its name, else None (an option or flag earlier
    on the line, a binary or a built-in could mean something this reader cannot see). After a `cd` the package is another
    one, so the pair is only returned to a caller that does not compare it with the root declarations."""
    program, args = command['program'], command['args']
    if program not in RUNNERS or command['cd'] and not any_directory: return None
    if program in NODE_MANAGERS and args[:1] in (['run'], ['run-script']): args = args[1:]
    elif program in NODE_MANAGERS and args[:1] and (args[0] in NPM_ALIASES if program == 'npm' else args[0] in BARE_SCRIPTS): pass
    elif program in NODE_MANAGERS: return None
    if not args or args[0].startswith('-') or not _NAME.fullmatch(args[0]): return None
    if program in ('make', 'task', 'just') and any(word.startswith('-') for word in args): return None
    return program, ('test' if args[0] == 't' and program == 'npm' else args[0])


def category_of(command):
    """The category one simple command works in: its declared name, or the tool it runs."""
    pair = runner_target(command, any_directory=True)
    if pair: return category(pair[1])
    inner = unwrapped(command)
    return category('', ' '.join([inner['program'], *inner['args'][:2]]))
