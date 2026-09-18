import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from dune1.cli import cli
from dune1.errors import ConfigError


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def successful_execution(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_run_query(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        if kwargs["no_wait"]:
            return {
                "execution_id": "01ABCDEFGHIJKLMNOPQRSTUV",
                "state": "QUERY_STATE_PENDING",
            }
        return {
            "state": "QUERY_STATE_COMPLETED",
            "result": {
                "metadata": {"column_names": ["value"], "row_count": 1},
                "rows": [{"value": 1}],
            },
        }

    monkeypatch.setattr("dune1.cli._run_query", fake_run_query)
    return captured


def test_query_requires_exactly_one_sql_source(runner: CliRunner) -> None:
    missing = runner.invoke(cli, ["query"])
    both = runner.invoke(
        cli,
        ["query", "--sql", "SELECT 1", "--file", "query.sql"],
    )
    assert missing.exit_code == 2
    assert "exactly one of --sql or --file" in missing.stderr
    assert both.exit_code == 2
    assert "exactly one of --sql or --file" in both.stderr


def test_query_parses_options(
    runner: CliRunner,
    successful_execution: dict[str, Any],
) -> None:
    result = runner.invoke(
        cli,
        [
            "query",
            "--sql",
            "SELECT {{days}}",
            "--param",
            "days=30",
            "--param",
            "wallet=0xabc",
            "--performance",
            "small",
            "--limit",
            "10",
            "--timeout",
            "60",
        ],
    )
    assert result.exit_code == 0
    assert successful_execution == {
        "sql": "SELECT {{days}}",
        "parameters": {"days": "30", "wallet": "0xabc"},
        "performance": "small",
        "no_wait": False,
        "timeout": 60,
        "limit": 10,
    }
    assert "1 rows" in result.stdout


def test_query_reads_utf8_file_without_changing_sql(
    runner: CliRunner,
    successful_execution: dict[str, Any],
) -> None:
    with runner.isolated_filesystem():
        path = Path("query.sql")
        path.write_text("SELECT '中文'\n", encoding="utf-8")
        result = runner.invoke(cli, ["query", "--file", str(path)])
    assert result.exit_code == 0
    assert successful_execution["sql"] == "SELECT '中文'\n"


@pytest.mark.parametrize("args", [["--sql", ""], ["--sql", "   \n"]])
def test_query_rejects_empty_sql(runner: CliRunner, args: list[str]) -> None:
    result = runner.invoke(cli, ["query", *args])
    assert result.exit_code == 2
    assert "SQL must not be empty" in result.stderr


@pytest.mark.parametrize("raw_param", ["missing-equals", "=value"])
def test_query_rejects_invalid_parameter(
    runner: CliRunner,
    raw_param: str,
) -> None:
    result = runner.invoke(cli, ["query", "--sql", "SELECT 1", "--param", raw_param])
    assert result.exit_code == 2
    assert "KEY=VALUE" in result.stderr


def test_query_rejects_duplicate_parameter(runner: CliRunner) -> None:
    result = runner.invoke(
        cli,
        [
            "query",
            "--sql",
            "SELECT 1",
            "--param",
            "days=30",
            "--param",
            "days=60",
        ],
    )
    assert result.exit_code == 2
    assert "duplicate parameter: days" in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["--performance", "xlarge"],
        ["--limit", "-1"],
        ["--timeout", "0"],
        ["--output", "csv"],
    ],
)
def test_click_rejects_invalid_option_values(
    runner: CliRunner, args: list[str]
) -> None:
    result = runner.invoke(cli, ["query", "--sql", "SELECT 1", *args])
    assert result.exit_code == 2


def test_no_wait_json_is_machine_readable(
    runner: CliRunner,
    successful_execution: dict[str, Any],
) -> None:
    result = runner.invoke(
        cli,
        ["query", "--sql", "SELECT 1", "--no-wait", "-o", "json"],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["state"] == "QUERY_STATE_PENDING"
    assert result.stderr == ""
    assert successful_execution["no_wait"] is True


def test_domain_error_uses_stable_exit_code_and_stderr(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(**kwargs: Any) -> dict[str, Any]:
        raise ConfigError("invalid local config")

    monkeypatch.setattr("dune1.cli._run_query", fail)
    result = runner.invoke(cli, ["query", "--sql", "SELECT 1", "-o", "json"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "Error: invalid local config\n"


def test_query_defaults(
    runner: CliRunner,
    successful_execution: dict[str, Any],
) -> None:
    result = runner.invoke(cli, ["query", "--sql", "SELECT 1"])
    assert result.exit_code == 0
    assert successful_execution["performance"] is None
    assert successful_execution["limit"] == 0
    assert successful_execution["timeout"] == 300
    assert successful_execution["no_wait"] is False
