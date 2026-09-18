"""Resolve one active account from the project-local configuration."""

from __future__ import annotations

import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dune1.errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.local.toml"


@dataclass(frozen=True, slots=True)
class DuneConfig:
    api_key: str
    proxy_url: str | None = None


def load_config(path: Path | None = None) -> DuneConfig:
    config_path = path or DEFAULT_CONFIG_PATH
    _validate_file(config_path)

    try:
        with config_path.open("rb") as config_file:
            document = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError("local config is unreadable or invalid TOML") from exc

    if set(document) != {"dune"} or not isinstance(document["dune"], dict):
        raise ConfigError("local config must contain only a [dune] section")

    dune = document["dune"]
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


def _validate_file(path: Path) -> None:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError as exc:
        raise ConfigError(f"local config not found: {path}") from exc
    except OSError as exc:
        raise ConfigError("local config cannot be inspected") from exc

    if not stat.S_ISREG(mode):
        raise ConfigError("local config must be a regular file")
    if mode & 0o077:
        raise ConfigError("local config permissions are too broad; run chmod 600")


def _required_string(values: dict[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"[dune].{key} must be a non-empty string")
    return value.strip()


def _parse_account(name: str, value: object) -> DuneConfig:
    if not name:
        raise ConfigError("account name must not be empty")
    if not isinstance(value, dict):
        raise ConfigError(f"account must be a table: {name}")
    if not {"api_key"} <= set(value) <= {"api_key", "proxy_url"}:
        raise ConfigError(
            f"account must contain api_key and optional proxy_url: {name}"
        )

    api_key = _required_string(value, "api_key")
    proxy_url = None
    if "proxy_url" in value:
        proxy_url = _required_string(value, "proxy_url")
        _validate_proxy(proxy_url)
    return DuneConfig(api_key=api_key, proxy_url=proxy_url)


def _validate_proxy(proxy_url: str) -> None:
    try:
        parsed = urlsplit(proxy_url)
        port = parsed.port
    except ValueError as exc:
        raise ConfigError("proxy_url has an invalid port") from exc

    if parsed.scheme != "http":
        raise ConfigError("proxy_url must use http://")
    if not parsed.hostname or port is None:
        raise ConfigError("proxy_url must include a hostname and port")
    has_username = parsed.username is not None
    has_password = parsed.password is not None
    if has_username != has_password or (
        has_username and (not parsed.username or not parsed.password)
    ):
        raise ConfigError(
            "proxy_url authentication must include both username and password"
        )
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConfigError("proxy_url must not include a path, query, or fragment")
