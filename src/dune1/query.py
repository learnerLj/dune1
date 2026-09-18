"""Synchronous orchestration for one raw DuneSQL execution."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from dune1.errors import ApiProtocolError, QueryExecutionError, QueryTimeoutError

POLL_INTERVAL_SECONDS = 2.0
PENDING_STATES = {"QUERY_STATE_PENDING", "QUERY_STATE_EXECUTING"}


class QueryApi(Protocol):
    def submit_sql(
        self,
        sql: str,
        parameters: Mapping[str, str],
        performance: str | None,
    ) -> dict[str, Any]: ...

    def get_status(self, execution_id: str) -> dict[str, Any]: ...

    def get_results(self, execution_id: str, limit: int) -> dict[str, Any]: ...


class QueryService:
    def __init__(
        self,
        api: QueryApi,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api = api
        self._sleep = sleep
        self._monotonic = monotonic

    def run(
        self,
        *,
        sql: str,
        parameters: Mapping[str, str],
        performance: str | None,
        no_wait: bool,
        timeout: int,
        limit: int,
    ) -> dict[str, Any]:
        submission = self._api.submit_sql(sql, parameters, performance)
        execution_id, state = _submission_parts(submission)
        if no_wait:
            return submission

        deadline = self._monotonic() + timeout
        if state == "QUERY_STATE_COMPLETED":
            return self._api.get_results(execution_id, limit)
        _raise_for_terminal_state(state, submission)

        while True:
            if self._monotonic() >= deadline:
                raise QueryTimeoutError(
                    f"Dune execution did not complete within {timeout} seconds"
                )

            status = self._api.get_status(execution_id)
            state = _state_from(status)
            if state == "QUERY_STATE_COMPLETED":
                return self._api.get_results(execution_id, limit)
            _raise_for_terminal_state(state, status)

            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise QueryTimeoutError(
                    f"Dune execution did not complete within {timeout} seconds"
                )
            self._sleep(min(POLL_INTERVAL_SECONDS, remaining))


def _submission_parts(payload: Mapping[str, Any]) -> tuple[str, str]:
    execution_id = payload.get("execution_id")
    if not isinstance(execution_id, str) or not execution_id:
        raise ApiProtocolError("Dune API returned an invalid execution_id")
    return execution_id, _state_from(payload)


def _state_from(payload: Mapping[str, Any]) -> str:
    state = payload.get("state")
    if not isinstance(state, str) or not state.startswith("QUERY_STATE_"):
        raise ApiProtocolError("Dune API returned an invalid execution state")
    return state


def _raise_for_terminal_state(state: str, payload: Mapping[str, Any]) -> None:
    if state in PENDING_STATES:
        return
    if state == "QUERY_STATE_FAILED":
        raise QueryExecutionError(_query_error_message(payload))
    if state == "QUERY_STATE_CANCELLED":
        raise QueryExecutionError("Dune query execution was cancelled")
    if state != "QUERY_STATE_COMPLETED":
        raise ApiProtocolError(f"unexpected execution state: {state}")


def _query_error_message(payload: Mapping[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, Mapping):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    return "Dune query execution failed"
