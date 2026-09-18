from pathlib import Path

import pytest

from dune1.config import DuneConfig, load_config
from dune1.errors import ConfigError


def write_raw_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.local.toml"
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


def write_accounts_config(tmp_path: Path, *, active: str = "main") -> Path:
    return write_raw_config(
        tmp_path,
        (
            f'[dune]\nactive = "{active}"\n\n'
            '[dune.accounts.main]\napi_key = "main-key"\n\n'
            '[dune.accounts.backup]\napi_key = "backup-key"\n'
            'proxy_url = "http://backup.test:8080"\n'
        ),
    )


def test_load_config_returns_only_active_direct_account(tmp_path: Path) -> None:
    path = write_accounts_config(tmp_path, active="main")
    assert load_config(path) == DuneConfig(api_key="main-key", proxy_url=None)


def test_load_config_returns_active_account_proxy(tmp_path: Path) -> None:
    path = write_accounts_config(tmp_path, active="backup")
    assert load_config(path) == DuneConfig(
        api_key="backup-key",
        proxy_url="http://backup.test:8080",
    )


def test_load_config_rejects_group_or_other_permissions(tmp_path: Path) -> None:
    path = write_accounts_config(tmp_path)
    path.chmod(0o644)
    with pytest.raises(ConfigError, match="chmod 600"):
        load_config(path)


def test_load_config_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.toml")


@pytest.mark.parametrize(
    "content",
    [
        "not valid toml = [",
        'api_key = "secret"\n',
        '[dune]\napi_key = "legacy-key"\n',
        '[dune]\nactive = "main"\n',
        '[dune]\nactive = ""\naccounts = { main = { api_key = "key" } }\n',
        '[dune]\nactive = 1\naccounts = { main = { api_key = "key" } }\n',
        '[dune]\nactive = "main"\naccounts = {}\n',
        '[dune]\nactive = "main"\naccounts = 1\n',
        '[dune]\naccounts = { main = { api_key = "key" } }\n',
        '[dune]\nactive = "missing"\naccounts = { main = { api_key = "key" } }\n',
        (
            '[dune]\nactive = "main"\nextra = true\n'
            'accounts = { main = { api_key = "key" } }\n'
        ),
        '[dune]\nactive = "main"\naccounts = { "" = { api_key = "key" } }\n',
        '[dune]\nactive = "main"\naccounts = { main = "not-a-table" }\n',
        (
            '[dune]\nactive = "main"\n'
            'accounts = { main = { proxy_url = "http://proxy:8080" } }\n'
        ),
        '[dune]\nactive = "main"\naccounts = { main = { api_key = "" } }\n',
        '[dune]\nactive = "main"\naccounts = { main = { api_key = 1 } }\n',
        (
            '[dune]\nactive = "main"\n'
            'accounts = { main = { api_key = "key", extra = true } }\n'
        ),
        (
            '[dune]\nactive = "main"\n\n'
            '[dune.accounts.main]\napi_key = "main-key"\n\n'
            '[dune.accounts.backup]\napi_key = "backup-key"\n'
            'proxy_url = "https://invalid-proxy:8080"\n'
        ),
        (
            '[dune]\nactive = "main"\naccounts = { main = { api_key = "key" } }\n'
            "[other]\nvalue = 1\n"
        ),
    ],
)
def test_load_config_rejects_invalid_structure(tmp_path: Path, content: str) -> None:
    path = write_raw_config(tmp_path, content)
    with pytest.raises(ConfigError):
        load_config(path)
