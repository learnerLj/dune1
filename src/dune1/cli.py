"""Click entry point for the dune1 command."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from dune1.api import DuneApiClient
from dune1.config import load_config
from dune1.errors import Dune1Error
from dune1.output import render_json, render_text
from dune1.query import QueryService


@click.group()
def cli() -> None:
    """Execute raw DuneSQL through the project-configured proxy."""


@cli.command("query")
@click.option("--sql", type=str)
@click.option(
    "--file",
    "sql_file",
    type=click.Path(path_type=Path, dir_okay=False),
)
@click.option("--param", "raw_params", multiple=True)
@click.option(
    "--performance",
    type=click.Choice(["small", "medium", "large"], case_sensitive=True),
)
@click.option(
    "--limit",
    type=click.IntRange(min=0),
    default=0,
    show_default=True,
)
@click.option("--no-wait", is_flag=True)
@click.option(
    "--timeout",
    type=click.IntRange(min=1),
    default=300,
    show_default=True,
)
@click.option(
    "--output",
    "-o",
    "output_format",
    type=click.Choice(["text", "json"], case_sensitive=True),
    default="text",
    show_default=True,
)
def query_command(
    sql: str | None,
    sql_file: Path | None,
    raw_params: tuple[str, ...],
    performance: str | None,
    limit: int,
    no_wait: bool,
    timeout: int,
    output_format: str,
) -> None:
    """Execute inline SQL or one UTF-8 SQL file."""

    sql_text = _read_sql(sql, sql_file)
    parameters = _parse_params(raw_params)

    try:
        payload = _run_query(
            sql=sql_text,
            parameters=parameters,
            performance=performance,
            no_wait=no_wait,
            timeout=timeout,
            limit=limit,
        )
    except Dune1Error as exc:
        click.echo(f"Error: {exc}", err=True)
        raise click.exceptions.Exit(exc.exit_code) from exc

    rendered = (
        render_json(payload)
        if output_format == "json"
        else render_text(payload, no_wait=no_wait)
    )
    click.echo(rendered, nl=False)


def _read_sql(sql: str | None, sql_file: Path | None) -> str:
    if (sql is None) == (sql_file is None):
        raise click.UsageError("exactly one of --sql or --file is required")

    if sql_file is not None:
        try:
            value = sql_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise click.UsageError("--file must be a readable UTF-8 SQL file") from exc
    else:
        value = sql or ""

    if not value.strip():
        raise click.UsageError("SQL must not be empty")
    return value


def _parse_params(raw_params: tuple[str, ...]) -> dict[str, str]:
    parameters: dict[str, str] = {}
    for raw in raw_params:
        key, separator, value = raw.partition("=")
        if not separator or not key:
            raise click.BadParameter(
                "expected KEY=VALUE with a non-empty key",
                param_hint="--param",
            )
        if key in parameters:
            raise click.BadParameter(
                f"duplicate parameter: {key}",
                param_hint="--param",
            )
        parameters[key] = value
    return parameters


def _run_query(
    *,
    sql: str,
    parameters: dict[str, str],
    performance: str | None,
    no_wait: bool,
    timeout: int,
    limit: int,
) -> dict[str, Any]:
    config = load_config()
    with DuneApiClient(config) as api:
        return QueryService(api).run(
            sql=sql,
            parameters=parameters,
            performance=performance,
            no_wait=no_wait,
            timeout=timeout,
            limit=limit,
        )


def main() -> None:
    cli(prog_name="dune1")
