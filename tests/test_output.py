import json

from dune1.output import render_json, render_text


def completed_payload() -> dict[str, object]:
    return {
        "state": "QUERY_STATE_COMPLETED",
        "result": {
            "metadata": {"column_names": ["b", "a", "nested"], "row_count": 1},
            "rows": [{"a": None, "b": True, "nested": {"z": 2, "a": 1}}],
        },
    }


def test_render_text_uses_server_column_order_and_deterministic_values() -> None:
    rendered = render_text(completed_payload())
    header = rendered.splitlines()[0]
    assert header.index("b") < header.index("a") < header.index("nested")
    assert "True" in rendered
    assert "None" in rendered
    assert '{"a": 1, "z": 2}' in rendered
    assert rendered.endswith("1 rows\n")


def test_render_text_handles_empty_results() -> None:
    payload = {
        "state": "QUERY_STATE_COMPLETED",
        "result": {
            "metadata": {"column_names": ["value"], "row_count": 0},
            "rows": [],
        },
    }
    rendered = render_text(payload)
    assert "value" in rendered
    assert rendered.endswith("0 rows\n")


def test_render_text_handles_no_wait_submission() -> None:
    payload = {
        "execution_id": "01ABCDEFGHIJKLMNOPQRSTUV",
        "state": "QUERY_STATE_PENDING",
    }
    assert render_text(payload, no_wait=True) == (
        "Execution ID: 01ABCDEFGHIJKLMNOPQRSTUV\nState:        QUERY_STATE_PENDING\n"
    )


def test_render_json_round_trips_utf8_payload() -> None:
    payload = {"state": "QUERY_STATE_COMPLETED", "label": "中文"}
    rendered = render_json(payload)
    assert json.loads(rendered) == payload
    assert "中文" in rendered
    assert rendered.endswith("\n")
