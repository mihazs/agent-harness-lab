---
name: pytest-patterns
description: Python testing with pytest fixtures, parametrize, markers, conftest, mocking and temporary paths. Use when writing, reviewing or refactoring the pytest tests in this repository.
license: MIT
metadata:
  author: thetestingacademy
  version: "1.0.0"
  source: https://github.com/PramodDutta/qaskills/blob/2f99c85516e03ae53a6db39da564027e4efe1e76/seed-skills/pytest-patterns/SKILL.md
---

# Pytest Patterns Skill

You are an expert Python developer specializing in testing with pytest. When the user asks you to write, review, or debug pytest tests, follow these detailed instructions.

## Core Principles

1. **Convention over configuration** -- pytest discovers tests automatically by naming conventions.
2. **Fixtures for setup** -- Use fixtures instead of setUp/tearDown methods.
3. **Parametrize for coverage** -- Use `@pytest.mark.parametrize` for data-driven tests.
4. **Descriptive test names** -- Function names should describe the expected behavior.
5. **Minimal test scope** -- Each test verifies one behavior.

## Project Structure

```
project/
  src/
    myapp/
      __init__.py
      services/
        user_service.py
        order_service.py
      models/
        user.py
      utils/
        validators.py
  tests/
    __init__.py
    conftest.py
    unit/
      __init__.py
      test_user_service.py
      test_validators.py
    integration/
      __init__.py
      conftest.py
      test_user_api.py
    fixtures/
      user_fixtures.py
  pyproject.toml
  pytest.ini
```

## Configuration

```ini
# pytest.ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -v --tb=short --strict-markers
markers =
    slow: marks tests as slow (deselect with '-m "not slow"')
    integration: marks integration tests
    smoke: marks smoke tests
    unit: marks unit tests
```

```toml
# pyproject.toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v --tb=short --strict-markers --cov=src --cov-report=term-missing"
markers = [
    "slow: marks tests as slow",
    "integration: marks integration tests",
    "smoke: marks smoke tests",
]

[tool.coverage.run]
source = ["src"]
omit = ["tests/*", "*/__init__.py"]

[tool.coverage.report]
fail_under = 80
show_missing = true
```

## Fixtures

### Basic Fixtures

```python
# conftest.py
import pytest
from myapp.services.user_service import UserService
from myapp.models.user import User


@pytest.fixture
def sample_user():
    """Create a sample user for testing."""
    return User(
        id="user-123",
        email="test@example.com",
        name="Test User",
        role="user",
    )


@pytest.fixture
def admin_user():
    """Create an admin user for testing."""
    return User(
        id="admin-123",
        email="admin@example.com",
        name="Admin User",
        role="admin",
    )


@pytest.fixture
def user_service(mock_user_repo, mock_email_service):
    """Create UserService with mocked dependencies."""
    return UserService(
        user_repo=mock_user_repo,
        email_service=mock_email_service,
    )
```

### Fixture Scopes

```python
@pytest.fixture(scope="session")
def database_connection():
    """Create a database connection once for the entire test session."""
    conn = create_connection("test_db")
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def test_data(database_connection):
    """Seed test data once per module."""
    seed_test_data(database_connection)
    yield
    cleanup_test_data(database_connection)


@pytest.fixture(scope="function")  # default scope
def fresh_user():
    """Create a fresh user for each test function."""
    return create_user(email=f"test-{uuid4()}@example.com")


@pytest.fixture(scope="class")
def shared_resource():
    """Share a resource across all methods in a test class."""
    resource = create_expensive_resource()
    yield resource
    resource.cleanup()
```

### Fixture Factories

```python
@pytest.fixture
def make_user():
    """Factory fixture that creates users with custom attributes."""
    created_users = []

    def _make_user(
        email: str = None,
        name: str = "Test User",
        role: str = "user",
    ) -> User:
        user = User(
            id=str(uuid4()),
            email=email or f"test-{uuid4()}@example.com",
            name=name,
            role=role,
        )
        created_users.append(user)
        return user

    yield _make_user

    # Cleanup
    for user in created_users:
        try:
            delete_user(user.id)
        except Exception:
            pass


# Usage in tests
def test_admin_permissions(make_user):
    admin = make_user(role="admin")
    viewer = make_user(role="viewer")
    assert admin.can_delete_users()
    assert not viewer.can_delete_users()
```

### Yield Fixtures (Setup/Teardown)

```python
@pytest.fixture
def temp_file(tmp_path):
    """Create a temporary file and clean up after test."""
    file_path = tmp_path / "test_data.json"
    file_path.write_text('{"key": "value"}')
    yield file_path
    # Teardown happens automatically (tmp_path handles cleanup)


@pytest.fixture
def mock_server():
    """Start a mock HTTP server for testing."""
    server = MockServer(port=8089)
    server.start()
    yield server
    server.stop()


@pytest.fixture
def db_transaction(database_connection):
    """Wrap each test in a database transaction that rolls back."""
    transaction = database_connection.begin()
    yield database_connection
    transaction.rollback()
```

## Parametrize

### Basic Parametrize

```python
@pytest.mark.parametrize("email,expected", [
    ("user@example.com", True),
    ("first.last@domain.co.uk", True),
    ("user+tag@example.com", True),
    ("", False),
    ("not-an-email", False),
    ("@missing-local.com", False),
    ("missing-at.com", False),
])
def test_is_valid_email(email, expected):
    assert is_valid_email(email) == expected
```

### Multiple Parameters

```python
@pytest.mark.parametrize("a,b,expected", [
    (1, 2, 3),
    (0, 0, 0),
    (-1, 1, 0),
    (100, 200, 300),
])
def test_add(a, b, expected):
    assert add(a, b) == expected
```

### Parametrize with IDs

```python
@pytest.mark.parametrize(
    "password,is_valid",
    [
        pytest.param("SecurePass1!", True, id="strong-password"),
        pytest.param("short", False, id="too-short"),
        pytest.param("nouppercase1!", False, id="no-uppercase"),
        pytest.param("NOLOWERCASE1!", False, id="no-lowercase"),
        pytest.param("NoSpecialChar1", False, id="no-special-char"),
    ],
)
def test_password_validation(password, is_valid):
    assert validate_password(password) == is_valid
```

### Combining Parametrize Decorators

```python
@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
@pytest.mark.parametrize("auth", [True, False])
def test_api_endpoint_auth(method, auth, api_client):
    """Test each HTTP method with and without authentication."""
    response = api_client.request(method, "/protected", authenticated=auth)
    if auth:
        assert response.status_code != 401
    else:
        assert response.status_code == 401
```

## Markers

```python
# Define custom markers in conftest.py or pytest.ini

@pytest.mark.slow
def test_large_file_processing():
    """This test takes a long time to run."""
    result = process_large_file("100mb_dataset.csv")
    assert result.row_count == 1_000_000


@pytest.mark.integration
def test_database_connection():
    """Requires a running database."""
    conn = connect_to_db()
    assert conn.is_connected()


@pytest.mark.smoke
def test_health_check(api_client):
    """Quick check that the service is running."""
    response = api_client.get("/health")
    assert response.status_code == 200


@pytest.mark.skip(reason="Feature not yet implemented")
def test_future_feature():
    pass


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Not supported on Windows"
)
def test_unix_specific():
    pass


@pytest.mark.xfail(reason="Known bug #1234")
def test_known_issue():
    assert buggy_function() == expected_value
```

## Mocking with pytest-mock

```python
# Install: pip install pytest-mock

def test_create_user_sends_email(mocker, user_service):
    """Verify that creating a user sends a welcome email."""
    mock_send = mocker.patch.object(
        user_service.email_service,
        "send_welcome_email",
    )
    mocker.patch.object(
        user_service.user_repo,
        "find_by_email",
        return_value=None,
    )
    mocker.patch.object(
        user_service.user_repo,
        "create",
        return_value=User(id="1", email="new@example.com", name="New"),
    )

    user_service.create_user("new@example.com", "New")

    mock_send.assert_called_once_with("new@example.com", "New")


def test_api_call_with_retry(mocker):
    """Test that the function retries on failure."""
    mock_get = mocker.patch("requests.get")
    mock_get.side_effect = [
        ConnectionError("Failed"),
        ConnectionError("Failed"),
        mocker.Mock(status_code=200, json=lambda: {"data": "success"}),
    ]

    result = fetch_with_retry("/api/data", max_retries=3)

    assert result == {"data": "success"}
    assert mock_get.call_count == 3


def test_datetime_mocking(mocker):
    """Mock the current time for deterministic testing."""
    fixed_now = datetime(2024, 6, 15, 12, 0, 0)
    mocker.patch("myapp.services.datetime")
    mocker.patch("myapp.services.datetime.now", return_value=fixed_now)

    result = get_greeting()
    assert result == "Good afternoon"
```

## Conftest Patterns

```python
# tests/conftest.py -- shared across all tests

import pytest
from unittest.mock import MagicMock


@pytest.fixture
def mock_user_repo():
    """Create a mock UserRepository."""
    repo = MagicMock()
    repo.find_by_email.return_value = None
    repo.find_by_id.return_value = None
    repo.create.side_effect = lambda data: {**data, "id": "generated-id"}
    return repo


@pytest.fixture
def mock_email_service():
    """Create a mock EmailService."""
    return MagicMock()


@pytest.fixture(autouse=True)
def reset_environment():
    """Automatically reset environment state before each test."""
    import os
    original_env = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(original_env)
```

```python
# tests/integration/conftest.py -- shared only within integration tests

import pytest
import httpx


@pytest.fixture(scope="session")
def api_base_url():
    return os.getenv("API_BASE_URL", "http://localhost:3000")


@pytest.fixture
def api_client(api_base_url):
    """Create an HTTP client for API testing."""
    with httpx.Client(base_url=api_base_url) as client:
        yield client


@pytest.fixture
def auth_client(api_client, api_base_url):
    """Create an authenticated HTTP client."""
    response = api_client.post("/api/auth/login", json={
        "email": "admin@example.com",
        "password": "AdminPass123!",
    })
    token = response.json()["token"]

    with httpx.Client(
        base_url=api_base_url,
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client
```

## Exception Testing

```python
def test_raises_value_error():
    with pytest.raises(ValueError, match="Invalid email"):
        validate_email("")


def test_raises_not_found():
    with pytest.raises(NotFoundError) as exc_info:
        get_user("nonexistent")

    assert exc_info.value.resource == "User"
    assert exc_info.value.id == "nonexistent"


def test_does_not_raise():
    # This should not raise any exception
    result = safe_divide(10, 2)
    assert result == 5.0
```

## Temporary Files and Directories

```python
def test_file_processing(tmp_path):
    """Use tmp_path for temporary file operations."""
    input_file = tmp_path / "input.csv"
    input_file.write_text("name,email\nJohn,john@example.com\n")

    output_file = tmp_path / "output.json"
    convert_csv_to_json(input_file, output_file)

    result = json.loads(output_file.read_text())
    assert len(result) == 1
    assert result[0]["name"] == "John"


def test_config_loading(tmp_path):
    """Test configuration file loading."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("database:\n  host: localhost\n  port: 5432\n")

    config = load_config(str(config_file))
    assert config["database"]["host"] == "localhost"
    assert config["database"]["port"] == 5432
```

## Best Practices

1. **Use fixtures for shared setup** -- Avoid duplicating setup code across tests.
2. **Use `conftest.py` at the right level** -- Place fixtures where they are needed.
3. **Name tests descriptively** -- `test_create_user_with_duplicate_email_raises_conflict`.
4. **Use `parametrize` for data-driven tests** -- Reduces code duplication significantly.
5. **Use markers for test categorization** -- Run subsets with `-m "smoke"` or `-m "not slow"`.
6. **Use `tmp_path` for file operations** -- Built-in fixture handles cleanup automatically.
7. **Use `mocker` from pytest-mock** -- Cleaner mocking than `unittest.mock` directly.
8. **Set `--strict-markers`** -- Catch typos in marker names.
9. **Use `autouse` fixtures sparingly** -- Only for truly universal setup like environment reset.
10. **Keep conftest files small** -- Split large conftest files into separate fixture modules.

## Anti-Patterns to Avoid

1. **Using `unittest.TestCase` with pytest** -- You lose pytest fixtures and parametrize.
2. **Global state in test modules** -- Use fixtures, not module-level variables.
3. **Overly broad fixtures** -- A `setup_everything` fixture makes tests coupled and slow.
4. **Testing internal details** -- Test public interfaces, not private methods.
5. **Fixtures that do too much** -- Each fixture should do one thing.
6. **Not using `yield` for teardown** -- Ensures cleanup runs even when tests fail.
7. **Ignoring fixture scope** -- Using `session` scope when `function` scope is needed causes coupling.
8. **Hardcoded file paths** -- Use `tmp_path` or `importlib.resources` instead.
9. **Mocking everything** -- If you mock all dependencies, you test nothing real.
10. **Not running tests in random order** -- Install `pytest-randomly` to catch hidden dependencies.

## Running Tests

```bash
# Run all tests
pytest

# Run specific file
pytest tests/unit/test_user_service.py

# Run specific test
pytest tests/unit/test_user_service.py::test_create_user

# Run by marker
pytest -m smoke
pytest -m "not slow"
pytest -m "unit and not integration"

# Run with coverage
pytest --cov=src --cov-report=html

# Run in parallel (requires pytest-xdist)
pytest -n auto

# Run with verbose output
pytest -v

# Run and stop on first failure
pytest -x

# Run last failed tests
pytest --lf

# Run with specific log level
pytest --log-cli-level=DEBUG
```

## Repository Adaptation

This section is a local addition and is not part of the upstream skill. It takes precedence over the examples above when they conflict.

- **No new test dependencies.** The `test` extra pins pytest and pytest-xdist only. Use the built-in `monkeypatch`, `tmp_path` and `capsys` fixtures and the standard library's `unittest.mock` in place of pytest-mock, pytest-randomly or pytest-cov.
- **Keep behaviour and strength.** A refactor keeps every assertion at least as strict as before. When a file moves to pytest style (see **Runners** for when it may), `self.assertEqual(a, b)` becomes `assert a == b`, `assertRaises` becomes `pytest.raises` with the same exception type and message check, and each `subTest` case becomes a `pytest.param` with a readable `id`. Collected test count and coverage of cases must not drop.
- **Held-out inputs stay frozen.** Never rewrite files under `agent_harness_lab/evaluation/fixtures`, grader bundles or other frozen evaluation inputs while refactoring tests.
- **Synthetic data only.** Tests build their inputs under `tmp_path` and never read a real home directory, agent history or memory store.
- **Layout.** Tests live in the `tests/` package, following the Project Structure above: `tests/unit/<area>/` and `tests/integration/<area>/`, where `<area>` mirrors the code under test (`sources`, `evaluation`, `sandbox`, `telemetry`, `diagnostics`, `cli`, `workflow`, `surfaces`, `optimization`, `monitoring`, `packaging`, `tools`, `docs` or `repository`). A test is integration when it spawns a subprocess or the CLI, runs bubblewrap, `systemd-run` or a sandbox, creates Git repositories, installs or builds packages, drives a controller or workflow end to end, or imports its fixtures from an integration module; otherwise it is unit. Every directory has an `__init__.py`, and test modules import each other absolutely, as in `from tests.integration.sandbox import test_sandbox_kernel as kernel`. Shared helper and data-builder modules that are not tests live in `tests/support/`; `tests/fixtures/` is not used because the ruff `fixtures` exclusion would skip it. `tests/conftest.py` holds only what every tree shares. pytest runs with `--import-mode=append` and the bank-qualification runner appends the checkout to `sys.path`, so an installed wheel or archive stays ahead of the checkout. Documented standard-library runs use `python -m unittest discover -s tests -t .`. pytest `-k` also matches package names, so a `-k` word that names an area, `unit` or `integration` selects that whole tree; prefer a module, class or test name. The one exception is the evaluator's own checks in `skills/harness-evaluation/scripts/`: `skills/harness-evaluation/references/execution.md` runs them with `python -m unittest discover` beside the scripts wherever the skill is installed, including the global skill directories that link to this checkout, and they import the scripts as top-level modules. `tests/unit/repository/test_layout.py` fails on a `test_*.py` module anywhere else.
- **Runners.** Tests run under pytest from `pyproject.toml`. This rule wins over the conversion above. Every `Taskfile.yml` test task runs `python -m pytest` with the selection its old unittest command had, except the runners listed under unittest-bound files. A task filter variable such as `KERNEL_TEST_FILTER` takes one pytest `-k` expression; pytest keeps only the last `-k` flag, so join alternatives with `or`. A file that a standard-library-only runner still executes with `python -m unittest` stays a `unittest.TestCase` and keeps its `subTest` and `assert*` methods until that runner moves to pytest. unittest collects only `TestCase` methods, so a converted module-level test disappears from that runner without an error, and `pytest.mark.parametrize` on a `TestCase` method fails under both runners. Convert such a file only in the same change that moves its runner to pytest, and check that the new runner collects at least as many cases as the old one did. `tests/integration/repository/test_runners.py` checks the list below against the Taskfile and the Markdown commands.
- **Unittest-bound files.** These files must stay `unittest.TestCase`-compatible, because a runner whose environment cannot have pytest executes them with `python -m unittest discover`:
  - `tests/unit/evaluation/test_bank_qualification.py`, `tests/integration/evaluation/test_bank_qualification_controls.py` and `tests/integration/evaluation/test_bank_qualification_final.py`: `task test:evaluation:bank-qualification`, which `test:evaluation:bank-qualification:wheel` runs from a wheel venv installed with `--no-index --no-deps`.
  - `tests/integration/tools/test_quickstart.py`: the reproduction in `docs/getting-started.md`, which needs only Git, a POSIX shell and Python 3.11+.
  - `skills/harness-evaluation/scripts/test_mine_traces.py`, `skills/harness-evaluation/scripts/test_run_evaluation.py` and `skills/harness-evaluation/scripts/test_run_evaluation_memory.py`: the helper checks in `skills/harness-evaluation/references/execution.md`, run wherever the skill is installed.
  - `tests/integration/evaluation/derivation_raw_assessment.py`: the retained falsifier in `docs/owned-development-variants.md`.
