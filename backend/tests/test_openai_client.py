from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.openai_client import OpenAIClientAdapter, OpenAIDeadlineExceeded, classify_openai_error, retry_after_seconds


class FakeOpenAIError(Exception):
    def __init__(self, status_code: int, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.headers = headers or {}
        super().__init__("provider details must not reach telemetry")


@dataclass
class FakeEmbedding:
    index: int
    embedding: list[float]


@dataclass
class FakeResponse:
    data: list[FakeEmbedding]
    _request_id: str = "req_test"


class FakeEmbeddingsEndpoint:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, list[str]]] = []

    def create(self, *, model: str, input: list[str]):
        self.calls.append((model, input))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, outcomes):
        self.embeddings = FakeEmbeddingsEndpoint(outcomes)
        self.options: list[dict[str, object]] = []

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        return self


class FakeMessage:
    usage_metadata = {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}
    response_metadata = {"request_id": "req_langchain"}


def test_embedding_retry_honors_retry_after_preserves_index_order_and_emits_safe_metric():
    client = FakeClient([
        FakeOpenAIError(429, {"Retry-After": "2"}),
        FakeResponse([FakeEmbedding(1, [2.0]), FakeEmbedding(0, [1.0])]),
    ])
    delays: list[float] = []
    metrics = []
    adapter = OpenAIClientAdapter(
        client, sleeper=delays.append, metric_sink=metrics.append,
    )

    result = adapter.create_embeddings(["confidential source text", "second"], model="test-embedding", timeout_seconds=5)

    assert result == [[1.0], [2.0]]
    assert delays == [2.0]
    assert len(client.embeddings.calls) == 2
    assert metrics[0].outcome == "success"
    assert metrics[0].retries == 1
    assert metrics[0].retry_category == "rate_limited"
    assert "confidential source text" not in str(metrics)
    assert adapter.snapshot_metrics() == {
        "embeddings.create:test-embedding": {
            "calls": 1, "errors": 0, "retries": 1, "latency_ms": metrics[0].latency_ms,
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            "cached_tokens": 0, "reasoning_tokens": 0,
        }
    }


def test_non_retryable_invalid_request_is_raised_and_classified_without_retry():
    client = FakeClient([FakeOpenAIError(400)])
    metrics = []
    adapter = OpenAIClientAdapter(client, sleeper=lambda _: pytest.fail("must not retry"), metric_sink=metrics.append)

    with pytest.raises(FakeOpenAIError):
        adapter.create_embeddings(["input"], model="test-embedding")

    assert len(client.embeddings.calls) == 1
    assert metrics[0].outcome == "error"
    assert metrics[0].error_category == "invalid_request"
    assert classify_openai_error(FakeOpenAIError(400)) == "invalid_request"


def test_retry_after_http_date_and_unrecognized_error_are_safe():
    assert retry_after_seconds(FakeOpenAIError(429, {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}), now=lambda: 1445412479.0) == 1.0
    assert classify_openai_error(RuntimeError("provider says: user prompt")) == "provider_error"


def test_late_success_is_rejected_at_the_operation_deadline():
    ticks = iter([0.0, 0.0, 0.0, 2.0, 2.0])
    metrics = []
    adapter = OpenAIClientAdapter(
        object(), monotonic=lambda: next(ticks), metric_sink=metrics.append,
    )

    with pytest.raises(OpenAIDeadlineExceeded):
        adapter.call(
            operation="responses.create", model="test", deadline_monotonic=1.0,
            request=lambda _: object(),
        )

    assert len(metrics) == 1
    assert metrics[0].error_category == "deadline_exceeded"
    assert adapter.snapshot_metrics()["responses.create:test"]["errors"] == 1


def test_structured_langchain_raw_response_contributes_usage_metrics():
    metrics = []
    adapter = OpenAIClientAdapter(object(), metric_sink=metrics.append)

    result = adapter.call(
        operation="langchain.invoke.generation", model="test",
        request=lambda _: {"raw": FakeMessage(), "parsed": {"answer": "ok"}, "parsing_error": None},
    )

    assert result["parsed"] == {"answer": "ok"}
    assert metrics[0].input_tokens == 12
    assert metrics[0].output_tokens == 3
    assert metrics[0].request_id == "req_langchain"
