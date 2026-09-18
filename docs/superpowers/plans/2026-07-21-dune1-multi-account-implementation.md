# dune1 Multi-Account Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store multiple named Dune accounts in `config.local.toml` while resolving exactly one manually selected active account for every command.

**Architecture:** `config.py` validates the full named-account document, then returns the existing `DuneConfig` for the active account only. API, query, CLI, editable installation, and skill invocation remain unchanged, so no downstream component can inspect or switch to another account.

**Tech Stack:** Python 3.12, standard-library `tomllib`, pytest, Ruff, mypy, uv.

## Global Constraints

- Configuration uses `[dune] active = "name"` and `[dune.accounts.<name>]` tables.
- At least one account exists and `active` matches exactly one account.
- Each account contains one non-empty `api_key` and an optional validated HTTP `proxy_url`.
- Every configured account is validated before network access.
- `load_config()` returns only the active account as `DuneConfig(api_key, proxy_url)`.
- Authentication, suspension, quota, transport, query, and timeout errors never select another account.
- No `--account`, account-management command, automatic rotation, random selection, or fallback.
- The old single-account TOML shape is rejected instead of silently migrated at runtime.
- The skill continues to call `dune1` directly and does not inspect configuration.

---

### Task 1: Resolve One Active Named Account

**Files:**
- Modify: `tests/test_config.py`
- Modify: `src/dune1/config.py`

**Interfaces:**
- Consumes: the existing `DuneConfig(api_key: str, proxy_url: str | None)` value type.
- Produces: unchanged `load_config(path: Path | None = None) -> DuneConfig`, now backed by named accounts.

- [ ] **Step 1: Replace single-account fixtures with named-account fixtures**

Add a helper that writes the approved structure with mode `0600`:

```python
def write_accounts_config(
    tmp_path: Path,
    *,
    active: str = "main",
    main_proxy: str | None = None,
) -> Path:
    proxy_line = f'proxy_url = "{main_proxy}"\n' if main_proxy else ""
    return write_raw_config(
        tmp_path,
        (
            f'[dune]\nactive = "{active}"\n\n'
            '[dune.accounts.main]\napi_key = "main-key"\n'
            f"{proxy_line}\n"
            '[dune.accounts.backup]\napi_key = "backup-key"\n'
            'proxy_url = "http://backup.test:8080"\n'
        ),
    )
```

Keep a raw-file helper for malformed TOML and permission tests.

- [ ] **Step 2: Write failing active-selection tests**

Add these behaviors:

```python
def test_load_config_returns_only_active_direct_account(tmp_path: Path) -> None:
    path = write_accounts_config(tmp_path, active="main")
    assert load_config(path) == DuneConfig(api_key="main-key", proxy_url=None)


def test_load_config_returns_active_account_proxy(tmp_path: Path) -> None:
    path = write_accounts_config(tmp_path, active="backup")
    assert load_config(path) == DuneConfig(
        api_key="backup-key",
        proxy_url="http://backup.test:8080",
    )
```

Add parameterized failures for missing/empty/non-string `active`, missing/empty/non-table `accounts`, unknown active name, empty account name, non-table account, missing/empty/non-string key, unknown account field, invalid proxy, unknown `[dune]` field, and the old `[dune] api_key = ...` structure. Include one invalid inactive account to prove the full document is validated.

- [ ] **Step 3: Run the configuration suite and verify RED**

Run: `uv run pytest tests/test_config.py -v`

Expected: active-selection tests fail because the current parser expects `api_key` directly under `[dune]`.

- [ ] **Step 4: Implement strict named-account parsing**

Replace the single-account section of `load_config` with this flow:

```python
if set(dune) != {"active", "accounts"}:
    raise ConfigError("[dune] must contain only active and accounts")

active = _required_string(dune, "active")
accounts = dune.get("accounts")
if not isinstance(accounts, dict) or not accounts:
    raise ConfigError("[dune].accounts must be a non-empty table")

parsed_accounts = {
    name: _parse_account(name, value) for name, value in accounts.items()
}
try:
    return parsed_accounts[active]
except KeyError as exc:
    raise ConfigError(f"active account does not exist: {active}") from exc
```

Implement the account parser with exact validation:

```python
def _parse_account(name: str, value: object) -> DuneConfig:
    if not name:
        raise ConfigError("account name must not be empty")
    if not isinstance(value, dict):
        raise ConfigError(f"account must be a table: {name}")
    if not {"api_key"} <= set(value) <= {"api_key", "proxy_url"}:
        raise ConfigError(f"account must contain api_key and optional proxy_url: {name}")

    api_key = _required_string(value, "api_key")
    proxy_url = None
    if "proxy_url" in value:
        proxy_url = _required_string(value, "proxy_url")
        _validate_proxy(proxy_url)
    return DuneConfig(api_key=api_key, proxy_url=proxy_url)
```

Do not expose the full account map beyond `config.py`.

- [ ] **Step 5: Verify GREEN and static quality**

Run:

```bash
uv run pytest tests/test_config.py -v
uv run ruff check src/dune1/config.py tests/test_config.py
uv run ruff format --check src/dune1/config.py tests/test_config.py
uv run mypy src/dune1/config.py
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit named-account resolution**

```bash
git add src/dune1/config.py tests/test_config.py
git commit -m "feat: resolve one active Dune account"
```

### Task 2: Document and Migrate the Multi-Account Shape

**Files:**
- Modify: `config.example.toml`
- Modify: `README.md`
- Local only after merge: `config.local.toml`

**Interfaces:**
- Consumes: named-account schema from Task 1.
- Produces: copyable example, user-facing documentation, and one valid local active-account configuration.

- [ ] **Step 1: Update the tracked example**

Replace `config.example.toml` with:

```toml
[dune]
active = "main"

[dune.accounts.main]
api_key = "replace-with-an-active-authorized-key"

[dune.accounts.backup]
api_key = "replace-with-another-active-authorized-key"
proxy_url = "http://host:port"
```

- [ ] **Step 2: Update README configuration semantics**

Document that multiple accounts can coexist, exactly one `active` name is required, switching means editing that field, and errors never rotate to another account. Keep direct `dune1 query ...` commands and editable installation unchanged.

- [ ] **Step 3: Run the complete offline quality gate**

Run:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

Expected: all commands exit 0.

- [ ] **Step 4: Commit tracked documentation**

```bash
git add config.example.toml README.md
git commit -m "docs: document named Dune accounts"
```

- [ ] **Step 5: Merge and migrate the ignored local config**

After merging the feature branch to `main`, rewrite the existing ignored `config.local.toml` without printing its key:

```toml
[dune]
active = "main"

[dune.accounts.main]
api_key = "<existing-local-key>"
```

Keep mode `0600`, confirm Git ignores the file, and run `dune1 query --help`. Do not send a live Dune query solely for migration verification.
