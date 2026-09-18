"""Direct or explicitly proxied access to the Dune API."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Self

import httpx

from dune1.config import DuneConfig
from dune1.errors import (
    ApiProtocolError,
    AuthenticationError,
    QueryExecutionError,
    QuotaError,
    TransportError,
)

BASE_URL = "https://api.dune.com"
MAX_PAGE_SIZE = 32_000
MAX_ERROR_LENGTH = 2_048
SUSPENSION_PHRASES = (
    "account has been suspended",
    "violating our terms of service",
)


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
    """Own one HTTP client and expose only the V1 endpoints."""

    def __init__(
        self,
        config: DuneConfig,
        client: httpx.Client | None = None,
    ) -> None:
        self._config = config
        try:
            self._client = client or create_http_client(config)
        except httpx.HTTPError as exc:
            raise TransportError("failed to initialize the HTTP client") from exc
        self._client.headers["X-DUNE-API-KEY"] = config.api_key

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def submit_sql(
        self,
        sql: str,
        parameters: Mapping[str, str],
        performance: str | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"sql": sql}
        if parameters:
            body["query_parameters"] = dict(parameters)
        if performance:
            body["performance"] = performance

        payload = self._request_json("POST", "/api/v1/sql/execute", json=body)
        execution_id = payload.get("execution_id")
        state = payload.get("state")
        if not isinstance(execution_id, str) or not execution_id:
            raise ApiProtocolError("Dune API returned an invalid execution_id")
        if not isinstance(state, str) or not state.startswith("QUERY_STATE_"):
            raise ApiProtocolError("Dune API returned an invalid execution state")
        return payload

    def get_status(self, execution_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/api/v1/execution/{execution_id}/status")

    def get_results(self, execution_id: str, limit: int) -> dict[str, Any]:
        aggregate: dict[str, Any] | None = None
        rows: list[Any] = []
        offset = 0

        while True:
            remaining = limit - len(rows) if limit > 0 else MAX_PAGE_SIZE
            page_size = min(remaining, MAX_PAGE_SIZE)
            payload = self._request_json(
                "GET",
                f"/api/v1/execution/{execution_id}/results",
                params={"limit": page_size, "offset": offset},
            )
            page_rows, metadata = _result_parts(payload)
            if aggregate is None:
                aggregate = deepcopy(payload)
            rows.extend(page_rows)

            if limit > 0 and len(rows) >= limit:
                rows = rows[:limit]
                break

            next_offset = payload.get("next_offset")
            if next_offset is None:
                break
            if (
                not isinstance(next_offset, int)
                or isinstance(next_offset, bool)
                or next_offset <= offset
            ):
                raise ApiProtocolError("Dune API returned an invalid next_offset")
            offset = next_offset

            if not metadata and not page_rows:
                raise ApiProtocolError("Dune API returned an empty pagination page")

        if aggregate is None:
            raise ApiProtocolError("Dune API returned no result page")
        result = aggregate["result"]
        result["rows"] = rows
        result["metadata"]["row_count"] = len(rows)
        aggregate.pop("next_offset", None)
        aggregate.pop("next_uri", None)
        return aggregate

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, int] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._client.request(method, path, json=json, params=params)
        except httpx.HTTPError as exc:
            raise TransportError("network request failed") from exc

        if not 200 <= response.status_code < 300:
            self._raise_http_error(response)

        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiProtocolError("Dune API returned a non-JSON response") from exc
        if not isinstance(payload, dict):
            raise ApiProtocolError("Dune API returned a non-object JSON response")
        return payload

    def _raise_http_error(self, response: httpx.Response) -> None:
        message = _response_message(response)
        safe_message = self._sanitize(message)
        normalized = message.casefold()

        if any(phrase in normalized for phrase in SUSPENSION_PHRASES):
            raise AuthenticationError(
                "Dune account may be suspended; stop calling it and "
                "contact Dune support"
            )
        if response.status_code in {401, 403}:
            raise AuthenticationError("Dune authentication or permission failed")
        if response.status_code in {402, 429} or any(
            marker in normalized
            for marker in ("credits exhausted", "datapoint limit", "quota")
        ):
            raise QuotaError(f"Dune quota or rate limit error: {safe_message}")
        if response.status_code == 400:
            raise QueryExecutionError(f"Dune rejected the query: {safe_message}")
        raise ApiProtocolError(
            f"Dune API request failed with HTTP {response.status_code}: {safe_message}"
        )

    def _sanitize(self, message: str) -> str:
        safe = message.replace(self._config.api_key, "[redacted]")
        if self._config.proxy_url:
            safe = safe.replace(self._config.proxy_url, "[redacted-proxy]")
        return safe[:MAX_ERROR_LENGTH]


def _response_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"

    if isinstance(payload, dict):
        for key in ("error", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict):
                nested = value.get("message")
                if isinstance(nested, str) and nested:
                    return nested
    return f"HTTP {response.status_code}"


def _result_parts(payload: dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ApiProtocolError("Dune result response is missing result")
    rows = result.get("rows")
    metadata = result.get("metadata")
    if not isinstance(rows, list) or not isinstance(metadata, dict):
        raise ApiProtocolError("Dune result response has invalid rows or metadata")
    return rows, metadata
