from collections.abc import Mapping
from typing import Any

import pytest

from dune1.errors import ApiProtocolError, QueryExecutionError, QueryTimeoutError
from dune1.query import QueryService


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeApi:
    execution_id = "01ABCDEFGHIJKLMNOPQRSTUV"

    def __init__(self, states: list[str]) -> None:
        self.states = states
        self.status_calls = 0
        self.result_calls = 0
        self.submission: dict[str, Any] | None = None

    def submit_sql(
        self,
        sql: str,
        parameters: Mapping[str, str],
        performance: str | None,
    ) -> dict[str, Any]:
        self.submission = {
            "sql": sql,
            "parameters": dict(parameters),
            "performance": performance,
        }
        return {"execution_id": self.execution_id, "state": "QUERY_STATE_PENDING"}

    def get_status(self, execution_id: str) -> dict[str, Any]:
        assert execution_id == self.execution_id
        index = min(self.status_calls, len(self.states) - 1)
        self.status_calls += 1
        state = self.states[index]
        response: dict[str, Any] = {"state": state}
        if state == "QUERY_STATE_FAILED":
            response["error"] = {"message": "syntax error at line 1"}
        return response

    def get_results(self, execution_id: str, limit: int) -> dict[str, Any]:
        assert execution_id == self.execution_id
        self.result_calls += 1
        return {
            "state": "QUERY_STATE_COMPLETED",
            "result": {
                "metadata": {"column_names": ["value"], "row_count": 1},
                "rows": [{"value": 1}],
            },
            "requested_limit": limit,
        }


def test_run_polls_pending_and_executing_then_returns_results() -> None:
    clock = FakeClock()
    api = FakeApi(
        states=[
            "QUERY_STATE_PENDING",
            "QUERY_STATE_EXECUTING",
            "QUERY_STATE_COMPLETED",
        ]
    )
    result = QueryService(api, sleep=clock.sleep, monotonic=clock.monotonic).run(
        sql="SELECT 1",
        parameters={"days": "30"},
        performance="small",
        no_wait=False,
        timeout=300,
        limit=10,
    )

    assert result["result"]["rows"] == [{"value": 1}]
    assert result["requested_limit"] == 10
    assert clock.sleeps == [2.0, 2.0]
    assert api.submission == {
        "sql": "SELECT 1",
        "parameters": {"days": "30"},
        "performance": "small",
    }


def test_no_wait_never_calls_status_or_results() -> None:
    api = FakeApi(states=[])
    result = QueryService(api).run(
        sql="SELECT 1",
        parameters={},
        performance=None,
        no_wait=True,
        timeout=300,
        limit=0,
    )

    assert result["execution_id"] == api.execution_id
    assert api.status_calls == 0
    assert api.result_calls == 0


def test_failed_state_uses_returned_query_message() -> None:
    api = FakeApi(states=["QUERY_STATE_FAILED"])
    with pytest.raises(QueryExecutionError, match="syntax error at line 1"):
        QueryService(api).run(
            sql="SELECT BAD",
            parameters={},
            performance=None,
            no_wait=False,
            timeout=300,
            limit=0,
        )
    assert api.result_calls == 0


def test_cancelled_state_is_query_error() -> None:
    api = FakeApi(states=["QUERY_STATE_CANCELLED"])
    with pytest.raises(QueryExecutionError, match="cancelled"):
        QueryService(api).run(
            sql="SELECT 1",
            parameters={},
            performance=None,
            no_wait=False,
            timeout=300,
            limit=0,
        )


def test_unknown_state_is_protocol_error() -> None:
    api = FakeApi(states=["QUERY_STATE_UNKNOWN"])
    with pytest.raises(ApiProtocolError, match="unexpected execution state"):
        QueryService(api).run(
            sql="SELECT 1",
            parameters={},
            performance=None,
            no_wait=False,
            timeout=300,
            limit=0,
        )


def test_total_deadline_raises_timeout() -> None:
    clock = FakeClock()
    api = FakeApi(states=["QUERY_STATE_PENDING"])
    with pytest.raises(QueryTimeoutError, match="3 seconds"):
        QueryService(api, sleep=clock.sleep, monotonic=clock.monotonic).run(
            sql="SELECT 1",
            parameters={},
            performance=None,
            no_wait=False,
            timeout=3,
            limit=0,
        )
    assert clock.now >= 3


@pytest.mark.parametrize(
    "submission",
    [
        {"execution_id": "", "state": "QUERY_STATE_PENDING"},
        {"execution_id": "01ABC", "state": "PENDING"},
    ],
)
def test_invalid_submission_is_protocol_error(submission: dict[str, str]) -> None:
    api = FakeApi(states=[])
    api.submit_sql = lambda sql, parameters, performance: submission  # type: ignore[method-assign]
    with pytest.raises(ApiProtocolError):
        QueryService(api).run(
            sql="SELECT 1",
            parameters={},
            performance=None,
            no_wait=True,
            timeout=300,
            limit=0,
        )
