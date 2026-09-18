# dune1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a project-local Python CLI that executes raw DuneSQL through one mandatory HTTP proxy without telemetry, identity lookup, credential caching, or direct-network fallback.

**Architecture:** A Click command loads one atomic key/proxy pair from `config.local.toml`, constructs the only `httpx.Client` with an explicit proxy and `trust_env=False`, and delegates submission/polling/result retrieval to a synchronous query service. Network, orchestration, formatting, and CLI parsing remain separate so every security boundary can be tested offline.

**Tech Stack:** Python 3.12, uv, Click 8.x, httpx 0.28.x, pytest, Ruff, mypy.

## Global Constraints

- Project directory, package, and command are all named `dune1`.
- The only business command is `dune1 query` for raw DuneSQL.
- API base URL is fixed to `https://api.dune.com` and cannot be configured.
- `config.local.toml` is project-local, ignored by Git, and must have POSIX mode `0600`.
- API key and authenticated HTTP proxy are one atomic config block with no flag or environment override.
- All requests use the explicit proxy with `trust_env=False`, TLS verification enabled, and redirects disabled.
- Any proxy, transport, TLS, authentication, quota, or polling request error stops; there is no direct fallback, account rotation, or POST retry.
- No telemetry, `whoami`, usage lookup, cache, persistent logs, query history, or automatic result files.
- Default poll interval is 2 seconds and default total wait timeout is 300 seconds.
- Default tests are entirely offline and must not call suspended Dune accounts.

## File Map

- `pyproject.toml`: package metadata, `dune1` entry point, dependencies, Ruff and mypy settings.
- `.gitignore`: ignores `.venv`, caches, build output, and `config.local.toml`.
- `config.example.toml`: credential-free example of the one supported config block.
- `src/dune1/errors.py`: domain exceptions and stable exit codes.
- `src/dune1/config.py`: project-root resolution, TOML parsing, permission and proxy validation.
- `src/dune1/api.py`: fixed-host HTTP client, three Dune endpoints, pagination and HTTP error classification.
- `src/dune1/query.py`: submit/no-wait/poll/final-result state machine.
- `src/dune1/output.py`: deterministic text table and JSON rendering.
- `src/dune1/cli.py`: Click command contract, SQL/file/param parsing, service wiring and stderr errors.
- `tests/`: offline unit and CLI tests matching each module boundary.
- `README.md`: setup, config, command examples, privacy boundary, and validation commands.

---

### Task 1: Project Skeleton, Error Contract, and Local Configuration

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `config.example.toml`
- Create: `src/dune1/__init__.py`
- Create: `src/dune1/errors.py`
- Create: `src/dune1/config.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces: `DuneConfig(api_key: str, proxy_url: str)`, `load_config(path: Path | None = None) -> DuneConfig`, `Dune1Error(message: str)` with class-level `exit_code`, and specific error subclasses used by later tasks.
- Consumes: no application interfaces.

- [ ] **Step 1: Add the uv package metadata and ignored local-secret paths**

Create `pyproject.toml` with `click>=8.1,<9`, `httpx>=0.28,<0.29`, a `dune1 = "dune1.cli:main"` script, `uv_build`, Python `>=3.12`, and dev dependencies `pytest`, `ruff`, and `mypy`. Configure Ruff for Python 3.12 and mypy for package `dune1`.

Create `.gitignore` containing at minimum:

```gitignore
.venv/
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
dist/
build/
*.egg-info/
config.local.toml
```

Create `config.example.toml` with placeholders only:

```toml
[dune]
api_key = "replace-with-an-active-authorized-key"
proxy_url = "http://username:password@host:port"
```

Run: `uv lock`

Expected: `uv.lock` is created without dependency-resolution errors.

- [ ] **Step 2: Write failing configuration and permission tests**

Create `tests/test_config.py` covering these exact cases:

```python
def test_load_config_reads_atomic_pair(tmp_path: Path) -> None:
    path = write_config(tmp_path, "secret", "http://user:pass@127.0.0.1:8080")
    assert load_config(path) == DuneConfig(
        api_key="secret",
        proxy_url="http://user:pass@127.0.0.1:8080",
    )


@pytest.mark.parametrize(
    "proxy_url",
    [
        "",
        "https://user:pass@127.0.0.1:8080",
        "http://127.0.0.1:8080",
        "http://user@127.0.0.1:8080",
        "http://user:pass@127.0.0.1",
    ],
)
def test_load_config_rejects_proxy_without_required_parts(
    tmp_path: Path, proxy_url: str
) -> None:
    path = write_config(tmp_path, "secret", proxy_url)
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_rejects_group_or_other_permissions(tmp_path: Path) -> None:
    path = write_config(tmp_path, "secret", "http://user:pass@127.0.0.1:8080")
    path.chmod(0o644)
    with pytest.raises(ConfigError, match="chmod 600"):
        load_config(path)
```

Also test missing file, malformed TOML, missing `[dune]`, missing/empty key, missing/empty proxy, and an unknown extra key. The test helper must write mode `0600` before calling `load_config`.

- [ ] **Step 3: Run configuration tests and confirm they fail**

Run: `uv run pytest tests/test_config.py -v`

Expected: FAIL because `dune1.config` and its types do not exist.

- [ ] **Step 4: Implement the stable error types and strict config loader**

Implement `src/dune1/errors.py` with:

```python
class Dune1Error(Exception):
    exit_code = 8


class ConfigError(Dune1Error):
    exit_code = 2


class TransportError(Dune1Error):
    exit_code = 3


class AuthenticationError(Dune1Error):
    exit_code = 4


class QuotaError(Dune1Error):
    exit_code = 5


class QueryExecutionError(Dune1Error):
    exit_code = 6


class QueryTimeoutError(Dune1Error):
    exit_code = 7


class ApiProtocolError(Dune1Error):
    exit_code = 8
```

Implement `src/dune1/config.py` around this public contract:

```python
@dataclass(frozen=True, slots=True)
class DuneConfig:
    api_key: str
    proxy_url: str


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.local.toml"


def load_config(path: Path | None = None) -> DuneConfig:
    config_path = path or DEFAULT_CONFIG_PATH
    # Require a regular file, POSIX mode with no 0o077 bits, exact [dune]
    # keys api_key/proxy_url, and non-empty stripped strings.
    # Parse proxy_url with urllib.parse.urlsplit and require:
    # scheme == "http", hostname, port, username, and password.
```

Reject unknown top-level sections and unknown `[dune]` fields so misspellings cannot be ignored. Convert file, TOML, type, port, and permission failures into `ConfigError` without including credential contents.

- [ ] **Step 5: Run tests and static checks**

Run:

```bash
uv run pytest tests/test_config.py -v
uv run ruff check src/dune1/config.py src/dune1/errors.py tests/test_config.py
uv run mypy src/dune1/config.py src/dune1/errors.py
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit the project foundation**

```bash
git add pyproject.toml uv.lock .gitignore config.example.toml src/dune1 tests/test_config.py
git commit -m "feat: add strict local Dune configuration"
```

### Task 2: Proxy-Bound Dune API Client

**Files:**
- Create: `src/dune1/api.py`
- Create: `tests/test_api.py`

**Interfaces:**
- Consumes: `DuneConfig` and errors from Task 1.
- Produces: `DuneApiClient.submit_sql(sql, parameters, performance)`, `get_status(execution_id)`, `get_results(execution_id, limit)`, context-manager close behavior, and `create_http_client(config) -> httpx.Client`.

- [ ] **Step 1: Write failing client-factory and request tests**

Create tests that monkeypatch `httpx.Client` and assert the factory is called exactly once with an explicit proxy, `trust_env=False`, `verify=True`, `follow_redirects=False`, and fixed `base_url="https://api.dune.com"`:

```python
def test_create_http_client_is_proxy_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_client(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(httpx, "Client", fake_client)
    create_http_client(DuneConfig("secret", "http://user:pass@proxy:8080"))
    assert captured["proxy"] == "http://user:pass@proxy:8080"
    assert captured["trust_env"] is False
    assert captured["verify"] is True
    assert captured["follow_redirects"] is False
    assert captured["base_url"] == "https://api.dune.com"
```

Use `httpx.MockTransport` for endpoint tests. Assert `submit_sql` sends only `sql`, optional `query_parameters`, and optional `performance`, with `X-DUNE-API-KEY`. Test status and results paths, a positive server-side limit, and multi-page aggregation when limit is zero or greater than 32,000.

- [ ] **Step 2: Write failing security and error-classification tests**

Cover:

```python
@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_are_not_called_suspensions_without_explicit_text(status: int) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, json={"error": "invalid credentials"})
    )
    raw_client = httpx.Client(base_url=BASE_URL, transport=transport)
    client = DuneApiClient(CONFIG, client=raw_client)
    with pytest.raises(AuthenticationError, match="authentication or permission"):
        client.submit_sql("SELECT 1", {}, None)


def test_explicit_suspension_message_is_classified() -> None:
    response = httpx.Response(
        403,
        json={"error": "Your Dune account has been suspended for violating our Terms of Service."},
    )
    transport = httpx.MockTransport(lambda request: response)
    raw_client = httpx.Client(base_url=BASE_URL, transport=transport)
    client = DuneApiClient(CONFIG, client=raw_client)
    with pytest.raises(AuthenticationError, match="may be suspended"):
        client.submit_sql("SELECT 1", {}, None)


def test_proxy_failure_does_not_construct_a_direct_client() -> None:
    # Inject one client whose send raises httpx.ProxyError and assert the
    # factory/constructor call count remains one after the command fails.
```

Also cover 402 and quota text as `QuotaError`, 429 as `QuotaError`, query-related 400 as `QueryExecutionError`, 5xx/non-JSON/malformed success as `ApiProtocolError`, and all `httpx.TransportError` subclasses as sanitized `TransportError`. Assert error strings contain neither API key nor proxy password.

- [ ] **Step 3: Run API tests and confirm they fail**

Run: `uv run pytest tests/test_api.py -v`

Expected: FAIL because `dune1.api` does not exist.

- [ ] **Step 4: Implement the fixed-host API client**

Implement these signatures in `src/dune1/api.py`:

```python
BASE_URL = "https://api.dune.com"
MAX_PAGE_SIZE = 32_000


def create_http_client(config: DuneConfig) -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        headers={"X-DUNE-API-KEY": config.api_key},
        proxy=config.proxy_url,
        trust_env=False,
        verify=True,
        follow_redirects=False,
        timeout=httpx.Timeout(30.0, connect=15.0),
    )


class DuneApiClient:
    """Own one proxy-bound HTTP client and expose only the three V1 endpoints."""
```

The class constructor is `__init__(config: DuneConfig, client: httpx.Client | None = None)`. It implements `__enter__() -> Self`, `__exit__(*args: object) -> None`, `submit_sql(sql: str, parameters: Mapping[str, str], performance: str | None) -> dict[str, Any]`, `get_status(execution_id: str) -> dict[str, Any]`, and `get_results(execution_id: str, limit: int) -> dict[str, Any]`. The injected-client path is test-only and must never construct a second client.

All requests go through one injected or factory-created client. A private `_request_json` must validate 2xx JSON objects and classify non-2xx responses. Read at most 2,048 display characters from error messages, detect explicit suspension phrases case-insensitively, and sanitize secrets before raising. Do not retry any method.

For pagination, request `limit=min(remaining, 32_000)` and `offset`; append `result.rows` in order until `next_offset` is absent or the requested positive limit is reached. Preserve first-page envelope/metadata, replace rows with the aggregate, and set returned `metadata.row_count` to the emitted row count.

- [ ] **Step 5: Run API tests and checks**

Run:

```bash
uv run pytest tests/test_api.py -v
uv run ruff check src/dune1/api.py tests/test_api.py
uv run mypy src/dune1/api.py
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit the audited network boundary**

```bash
git add src/dune1/api.py tests/test_api.py
git commit -m "feat: add proxy-bound Dune API client"
```

### Task 3: Query State Machine and Deterministic Output

**Files:**
- Create: `src/dune1/query.py`
- Create: `src/dune1/output.py`
- Create: `tests/test_query.py`
- Create: `tests/test_output.py`

**Interfaces:**
- Consumes: `DuneApiClient` methods from Task 2 and domain errors from Task 1.
- Produces: `QueryService.run(sql, parameters, performance, no_wait, timeout, limit) -> dict[str, Any]`, `render_text(payload, no_wait=False) -> str`, and `render_json(payload) -> str`.

- [ ] **Step 1: Write failing query-state tests with a fake API**

Define a small fake implementing `submit_sql`, `get_status`, and `get_results`, then cover:

```python
def test_run_polls_pending_and_executing_then_returns_results() -> None:
    clock = FakeClock()
    api = FakeApi(states=["QUERY_STATE_PENDING", "QUERY_STATE_EXECUTING", "QUERY_STATE_COMPLETED"])
    result = QueryService(api, sleep=clock.sleep, monotonic=clock.monotonic).run(
        sql="SELECT 1",
        parameters={},
        performance=None,
        no_wait=False,
        timeout=300,
        limit=0,
    )
    assert result["result"]["rows"] == [{"value": 1}]
    assert clock.sleeps == [2.0, 2.0]


def test_no_wait_never_calls_status_or_results() -> None:
    api = FakeApi(states=[])
    result = QueryService(api).run(
        sql="SELECT 1", parameters={}, performance=None,
        no_wait=True, timeout=300, limit=0,
    )
    assert result["execution_id"] == api.execution_id
    assert api.status_calls == 0
    assert api.result_calls == 0


@pytest.mark.parametrize(
    ("state", "error_type"),
    [
        ("QUERY_STATE_FAILED", QueryExecutionError),
        ("QUERY_STATE_CANCELLED", QueryExecutionError),
        ("QUERY_STATE_UNKNOWN", ApiProtocolError),
    ],
)
def test_terminal_and_unknown_states_raise(state: str, error_type: type[Exception]) -> None:
    api = FakeApi(states=[state])
    with pytest.raises(error_type):
        QueryService(api).run(
            sql="SELECT 1", parameters={}, performance=None,
            no_wait=False, timeout=300, limit=0,
        )


def test_total_deadline_raises_query_timeout_error() -> None:
    clock = FakeClock()
    api = FakeApi(states=["QUERY_STATE_PENDING"] * 10)
    with pytest.raises(QueryTimeoutError):
        QueryService(api, sleep=clock.sleep, monotonic=clock.monotonic).run(
            sql="SELECT 1", parameters={}, performance=None,
            no_wait=False, timeout=3, limit=0,
        )
```

The fake clock must advance only when `sleep` is called, keeping tests instant.

- [ ] **Step 2: Write failing text and JSON output tests**

Cover column ordering from `result.metadata.column_names`, `None`, booleans, nested values via deterministic JSON, empty rows, row-count footer, no-wait text, UTF-8 JSON, and parseable stdout JSON:

```python
def test_render_text_uses_column_names_order() -> None:
    payload = completed_payload(
        columns=["b", "a"],
        rows=[{"a": 1, "b": 2}],
    )
    rendered = render_text(payload)
    assert rendered.index("b") < rendered.index("a")
    assert rendered.endswith("1 rows\n")


def test_render_json_round_trips_payload() -> None:
    payload = {"state": "QUERY_STATE_COMPLETED", "label": "中文"}
    assert json.loads(render_json(payload)) == payload
```

- [ ] **Step 3: Run state and output tests and confirm they fail**

Run: `uv run pytest tests/test_query.py tests/test_output.py -v`

Expected: FAIL because the modules do not exist.

- [ ] **Step 4: Implement the synchronous query state machine**

Implement:

```python
POLL_INTERVAL_SECONDS = 2.0


class QueryService:
    """Run one raw-SQL execution against a QueryApi-compatible object."""
```

Define a `QueryApi` protocol with the same three signatures produced by Task 2. `QueryService.__init__` accepts `api: QueryApi` plus keyword-only injectable `sleep: Callable[[float], None] = time.sleep` and `monotonic: Callable[[], float] = time.monotonic`. Its keyword-only `run` accepts `sql: str`, `parameters: Mapping[str, str]`, `performance: str | None`, `no_wait: bool`, `timeout: int`, and `limit: int`, and returns `dict[str, Any]`.

Validate the submit response has a non-empty string `execution_id` and a `QUERY_STATE_*` state. Return it immediately for no-wait. Otherwise poll immediately, sleep 2 seconds only between pending/executing responses, and compare every cycle against one absolute deadline. Failed/cancelled messages come from `error.message` when available; no automatic resubmission occurs.

- [ ] **Step 5: Implement output formatting without a table dependency**

`render_text` must derive cells in server-provided column order, convert scalar values with `str`, convert list/dict values with sorted-key JSON, compute column widths, render a two-space-separated table, and append `N rows`. `render_json` uses `json.dumps(payload, ensure_ascii=False, indent=2) + "\n"`. No formatter writes files or stderr.

- [ ] **Step 6: Run tests and checks**

Run:

```bash
uv run pytest tests/test_query.py tests/test_output.py -v
uv run ruff check src/dune1/query.py src/dune1/output.py tests/test_query.py tests/test_output.py
uv run mypy src/dune1/query.py src/dune1/output.py
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit orchestration and output**

```bash
git add src/dune1/query.py src/dune1/output.py tests/test_query.py tests/test_output.py
git commit -m "feat: add DuneSQL execution workflow"
```

### Task 4: Click Command and End-to-End Offline Behavior

**Files:**
- Create: `src/dune1/cli.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: configuration, API client, query service, formatters, and domain errors from Tasks 1-3.
- Produces: installed `dune1` command and `main() -> None` entry point.

- [ ] **Step 1: Write failing Click contract tests**

Use `click.testing.CliRunner` and monkeypatch the service factory. Cover exact commands and validation:

```python
def test_query_requires_exactly_one_sql_source(runner: CliRunner) -> None:
    missing = runner.invoke(cli, ["query"])
    both = runner.invoke(cli, ["query", "--sql", "SELECT 1", "--file", "q.sql"])
    assert missing.exit_code == 2
    assert both.exit_code == 2


def test_query_parses_repeated_parameters(runner: CliRunner, fake_service: FakeService) -> None:
    result = runner.invoke(
        cli,
        ["query", "--sql", "SELECT {{days}}", "--param", "days=30", "--param", "wallet=0xabc"],
    )
    assert result.exit_code == 0
    assert fake_service.call["parameters"] == {"days": "30", "wallet": "0xabc"}


def test_domain_error_uses_stable_exit_code(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dune1.cli.load_config", raising_config_error)
    result = runner.invoke(cli, ["query", "--sql", "SELECT 1", "-o", "json"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Error: invalid local config" in result.stderr
```

Also test UTF-8 `--file`, empty SQL, missing file, duplicate param, invalid performance, negative limit, zero timeout, defaults, `--no-wait`, `-o json`, and that configuration failure prevents API construction.

- [ ] **Step 2: Run CLI tests and confirm they fail**

Run: `uv run pytest tests/test_cli.py -v`

Expected: FAIL because `dune1.cli` does not exist.

- [ ] **Step 3: Implement the Click command and dependency seam**

Create a root group with one `query` subcommand. Use Click choices and ranges for simple values, then explicit callbacks/helpers for SQL source and parameters:

```python
@click.group()
def cli() -> None:
    """Execute raw DuneSQL through the project-configured proxy."""


@cli.command("query")
@click.option("--sql", type=str)
@click.option("--file", "sql_file", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--param", "raw_params", multiple=True)
@click.option("--performance", type=click.Choice(["small", "medium", "large"]))
@click.option("--limit", type=click.IntRange(min=0), default=0, show_default=True)
@click.option("--no-wait", is_flag=True)
@click.option("--timeout", type=click.IntRange(min=1), default=300, show_default=True)
@click.option("--output", "output_format", type=click.Choice(["text", "json"]), default="text", show_default=True)
```

The decorated `query_command` has typed parameters `sql: str | None`, `sql_file: Path | None`, `raw_params: tuple[str, ...]`, `performance: str | None`, `limit: int`, `no_wait: bool`, `timeout: int`, and `output_format: str`, and returns `None`.

`query_command` validates exactly one source, reads UTF-8, strips only for the empty check while preserving original SQL, rejects duplicate params, loads config, opens one `DuneApiClient`, invokes `QueryService`, and writes only the selected renderer to stdout. Catch `Dune1Error`, write one sanitized `Error: <message>` line to stderr, and raise `click.exceptions.Exit(error.exit_code)`.

Expose `main()` that invokes `cli(prog_name="dune1")`.

- [ ] **Step 4: Run all offline tests**

Run: `uv run pytest -v`

Expected: all tests pass without network access and without `config.local.toml`.

- [ ] **Step 5: Exercise installed CLI help and pre-network rejection**

Run:

```bash
uv run dune1 --help
uv run dune1 query --help
uv run dune1 query --sql "SELECT 1"
```

Expected: both help commands exit 0; the query exits 2 with a missing-local-config message before any network request.

- [ ] **Step 6: Commit the CLI**

```bash
git add src/dune1/cli.py tests/test_cli.py
git commit -m "feat: expose the dune1 query command"
```

### Task 5: Documentation, Security Audit, and Final Verification

**Files:**
- Create: `README.md`
- Modify: any implementation/test file only when a final verification exposes a concrete defect.

**Interfaces:**
- Consumes: complete CLI from Tasks 1-4.
- Produces: handoff-ready usage and verified repository state.

- [ ] **Step 1: Write README with exact setup and privacy limits**

Document:

```bash
cd dune1
uv sync
cp config.example.toml config.local.toml
chmod 600 config.local.toml
uv run dune1 query --sql "SELECT 1"
uv run dune1 query --file query.sql --param days=30 --output json
uv run dune1 query --sql "SELECT 1" --no-wait
```

State that `config.local.toml` contains plaintext secrets and is Git-ignored; suspended accounts must not be used. Explain that `dune1` sends no extra telemetry or identity requests, while Dune necessarily still receives the API key, proxy exit IP, SQL, parameters, request timing, and frequency.

- [ ] **Step 2: Run the complete quality gate**

Run:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

Expected: all commands exit 0.

- [ ] **Step 3: Audit telemetry, writes, secrets, and forbidden API surface**

Run:

```bash
rg -n -i 'amplitude|sentry|opentelemetry|telemetry|whoami|/api/v1/usage' src pyproject.toml uv.lock
git grep -n -E 'replace-with-a-real-secret-pattern-before-running'
git status --short
```

Expected: the telemetry/API search has no matches; the known-secret-pattern search has no matches; Git shows only the intended README change before its commit. Test-only words such as `telemetry` should be avoided so this audit remains exact.

- [ ] **Step 4: Verify project-local execution creates no runtime files**

Record the file list, run help and the missing-config rejection, then compare:

```bash
find . -path ./.git -prune -o -type f -print | sort > /tmp/dune1-files-before
uv run dune1 --help >/dev/null
uv run dune1 query --sql "SELECT 1" >/dev/null 2>/dev/null || test $? -eq 2
find . -path ./.git -prune -o -type f -print | sort > /tmp/dune1-files-after
diff -u /tmp/dune1-files-before /tmp/dune1-files-after
```

Expected: diff exits 0. Generated `.pyc` and tool caches must already exist from the quality gate or be excluded consistently; the application itself must create no files.

- [ ] **Step 5: Commit documentation and final verified state**

```bash
git add README.md
git commit -m "docs: document private DuneSQL workflow"
git status --short
```

Expected: commit succeeds and final status is empty.
