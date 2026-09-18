from collections.abc import Callable
from typing import Any

import httpx
import pytest

from dune1.api import BASE_URL, DuneApiClient, create_http_client
from dune1.config import DuneConfig
from dune1.errors import (
    ApiProtocolError,
    AuthenticationError,
    QueryExecutionError,
    QuotaError,
    TransportError,
)

CONFIG = DuneConfig(
    api_key="secret-key",
    proxy_url="http://proxy-user:proxy-pass@proxy.test:8080",
)
DIRECT_CONFIG = DuneConfig(api_key="direct-key", proxy_url=None)


def make_client(handler: Callable[[httpx.Request], httpx.Response]) -> DuneApiClient:
    raw_client = httpx.Client(
        base_url=BASE_URL,
        transport=httpx.MockTransport(handler),
    )
    return DuneApiClient(CONFIG, client=raw_client)


def test_create_http_client_is_proxy_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    create_http_client(CONFIG)

    assert captured["proxy"] == CONFIG.proxy_url
    assert captured["trust_env"] is False
    assert captured["verify"] is True
    assert captured["follow_redirects"] is False
    assert captured["base_url"] == BASE_URL


def test_create_http_client_defaults_to_direct_without_environment_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    create_http_client(DIRECT_CONFIG)

    assert captured["proxy"] is None
    assert captured["trust_env"] is False


def test_submit_sql_sends_only_supported_fields_and_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/sql/execute"
        assert request.headers["X-DUNE-API-KEY"] == CONFIG.api_key
        assert request.read()
        assert request.headers["content-type"] == "application/json"
        assert request.content == (
            b'{"sql":"SELECT {{days}}","query_parameters":{"days":"30"},'
            b'"performance":"small"}'
        )
        return httpx.Response(
            200,
            json={
                "execution_id": "01ABCDEFGHIJKLMNOPQRSTUV",
                "state": "QUERY_STATE_PENDING",
            },
        )

    result = make_client(handler).submit_sql("SELECT {{days}}", {"days": "30"}, "small")
    assert result["state"] == "QUERY_STATE_PENDING"


def test_submit_sql_omits_empty_optional_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.content == b'{"sql":"SELECT 1"}'
        return httpx.Response(
            200,
            json={
                "execution_id": "01ABCDEFGHIJKLMNOPQRSTUV",
                "state": "QUERY_STATE_PENDING",
            },
        )

    make_client(handler).submit_sql("SELECT 1", {}, None)


def test_get_status_uses_execution_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/execution/01ABC/status"
        return httpx.Response(200, json={"state": "QUERY_STATE_EXECUTING"})

    assert make_client(handler).get_status("01ABC")["state"] == "QUERY_STATE_EXECUTING"


def test_get_results_paginates_and_preserves_row_order() -> None:
    offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset", "0"))
        offsets.append(offset)
        if offset == 0:
            return httpx.Response(
                200,
                json={
                    "state": "QUERY_STATE_COMPLETED",
                    "next_offset": 2,
                    "result": {
                        "metadata": {"column_names": ["value"], "row_count": 2},
                        "rows": [{"value": 1}, {"value": 2}],
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "state": "QUERY_STATE_COMPLETED",
                "result": {
                    "metadata": {"column_names": ["value"], "row_count": 1},
                    "rows": [{"value": 3}],
                },
            },
        )

    result = make_client(handler).get_results("01ABC", limit=0)
    assert offsets == [0, 2]
    assert result["result"]["rows"] == [{"value": 1}, {"value": 2}, {"value": 3}]
    assert result["result"]["metadata"]["row_count"] == 3


def test_get_results_stops_at_positive_limit() -> None:
    requested: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "state": "QUERY_STATE_COMPLETED",
                "next_offset": 2,
                "result": {
                    "metadata": {"column_names": ["value"], "row_count": 2},
                    "rows": [{"value": 1}, {"value": 2}],
                },
            },
        )

    result = make_client(handler).get_results("01ABC", limit=2)
    assert requested == [{"limit": "2", "offset": "0"}]
    assert len(result["result"]["rows"]) == 2


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_are_not_called_suspensions_without_explicit_text(
    status: int,
) -> None:
    client = make_client(
        lambda request: httpx.Response(status, json={"error": "invalid credentials"})
    )
    with pytest.raises(
        AuthenticationError, match="authentication or permission"
    ) as error:
        client.submit_sql("SELECT 1", {}, None)
    assert "suspend" not in str(error.value).lower()


def test_explicit_suspension_message_is_classified() -> None:
    client = make_client(
        lambda request: httpx.Response(
            403,
            json={
                "error": (
                    "Your Dune account has been suspended for violating "
                    "our Terms of Service."
                )
            },
        )
    )
    with pytest.raises(AuthenticationError, match="may be suspended"):
        client.submit_sql("SELECT 1", {}, None)


@pytest.mark.parametrize("status", [402, 429])
def test_quota_and_rate_limit_have_dedicated_error(status: int) -> None:
    client = make_client(
        lambda request: httpx.Response(status, json={"error": "credits exhausted"})
    )
    with pytest.raises(QuotaError):
        client.submit_sql("SELECT 1", {}, None)


def test_bad_query_is_query_execution_error() -> None:
    client = make_client(
        lambda request: httpx.Response(400, json={"error": "syntax error"})
    )
    with pytest.raises(QueryExecutionError, match="syntax error"):
        client.submit_sql("SELECT BAD", {}, None)


def test_direct_connection_error_is_sanitized() -> None:
    raw_client = httpx.Client(
        base_url=BASE_URL,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                400,
                json={"error": f"bad query using {DIRECT_CONFIG.api_key}"},
            )
        ),
    )
    client = DuneApiClient(DIRECT_CONFIG, client=raw_client)
    with pytest.raises(QueryExecutionError) as error:
        client.submit_sql("SELECT BAD", {}, None)
    assert DIRECT_CONFIG.api_key not in str(error.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"error": "server unavailable"}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=["not", "an", "object"]),
    ],
)
def test_protocol_failures_are_classified(response: httpx.Response) -> None:
    client = make_client(lambda request: response)
    with pytest.raises(ApiProtocolError):
        client.submit_sql("SELECT 1", {}, None)


def test_proxy_failure_is_sanitized_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructions = 0

    class FailingClient:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        def request(self, *args: object, **kwargs: object) -> httpx.Response:
            raise httpx.ProxyError(
                f"failed via {CONFIG.proxy_url} using {CONFIG.api_key}"
            )

        def close(self) -> None:
            return None

    def factory(config: DuneConfig) -> Any:
        nonlocal constructions
        constructions += 1
        return FailingClient()

    monkeypatch.setattr("dune1.api.create_http_client", factory)
    client = DuneApiClient(CONFIG)
    with pytest.raises(TransportError) as error:
        client.submit_sql("SELECT 1", {}, None)

    assert constructions == 1
    assert CONFIG.api_key not in str(error.value)
    assert "proxy-pass" not in str(error.value)
