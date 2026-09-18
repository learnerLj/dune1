"""Render Dune responses without side effects."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from dune1.errors import ApiProtocolError


def render_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def render_text(payload: Mapping[str, Any], *, no_wait: bool = False) -> str:
    if no_wait:
        execution_id = payload.get("execution_id", "")
        state = payload.get("state", "")
        return f"Execution ID: {execution_id}\nState:        {state}\n"

    columns, rows = _result_table(payload)
    rendered_rows = [[_cell(row.get(column)) for column in columns] for row in rows]
    widths = [
        max([len(column), *(len(row[index]) for row in rendered_rows)])
        for index, column in enumerate(columns)
    ]

    lines = [
        _join_row(columns, widths),
        _join_row(["-" * width for width in widths], widths),
    ]
    lines.extend(_join_row(row, widths) for row in rendered_rows)
    lines.extend(["", f"{len(rows)} rows"])
    return "\n".join(lines) + "\n"


def _result_table(
    payload: Mapping[str, Any],
) -> tuple[list[str], list[Mapping[str, Any]]]:
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise ApiProtocolError("Dune result response is missing result")
    metadata = result.get("metadata")
    rows = result.get("rows")
    if not isinstance(metadata, Mapping) or not isinstance(rows, list):
        raise ApiProtocolError("Dune result response has invalid rows or metadata")
    columns = metadata.get("column_names")
    if not isinstance(columns, list) or not all(
        isinstance(column, str) for column in columns
    ):
        raise ApiProtocolError("Dune result response has invalid column_names")
    if not columns:
        raise ApiProtocolError("Dune result response has no columns")
    if not all(isinstance(row, Mapping) for row in rows):
        raise ApiProtocolError("Dune result response has invalid row objects")
    return columns, rows


def _cell(value: Any) -> str:
    if isinstance(value, (Mapping, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _join_row(values: Sequence[str], widths: Sequence[int]) -> str:
    return "  ".join(
        value.ljust(widths[index]) for index, value in enumerate(values)
    ).rstrip()
