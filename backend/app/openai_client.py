"""Bounded, observable OpenAI SDK calls without logging prompt content.

The application deliberately keeps provider calls behind this small synchronous
adapter.  It uses one SDK client per process/API key, disables the SDK's own
retries, and applies a single deadline-aware retry only for transient failures.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
import logging
import os
import threading
import time
from typing import Any, Callable, TypeVar


logger = logging.getLogger(__name__)
_T = TypeVar("_T")


@dataclass(frozen=True)
class OpenAIRequestMetric:
    """Safe provider-call telemetry; deliberately contains no prompt content."""

    operation: str
    model: str
    outcome: str
    latency_ms: int
    retries: int
    error_category: str | None = None
    retry_category: str | None = None
    request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None


class OpenAIDeadlineExceeded(TimeoutError):
    """The local operation deadline elapsed before another provider attempt."""


def classify_openai_error(exc: BaseException) -> str:
    """Map SDK/provider failures to stable, content-free categories."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status == 401:
        return "authentication_failed"
    if status == 403:
        return "permission_denied"
    if status == 404:
        return "model_not_found"
    if status == 408:
        return "request_timeout"
    if status == 409:
        return "conflict"
    if status == 422 or status == 400:
        return "invalid_request"
    if status == 429:
        return "rate_limited"
    if isinstance(status, int) and 500 <= status <= 599:
        return "server_error"
    name = exc.__class__.__name__.lower()
    if "timeout" in name or isinstance(exc, TimeoutError):
        return "timeout"
    if "connection" in name or isinstance(exc, ConnectionError):
        return "connection_error"
    return "provider_error"


def is_retryable_openai_error(exc: BaseException) -> bool:
    """Retry only rate limits, temporary server errors, and connection errors."""
    return classify_openai_error(exc) in {"rate_limited", "server_error", "connection_error"}


def retry_after_seconds(exc: BaseException, *, now: Callable[[], float] = time.time) -> float | None:
    """Extract a non-negative Retry-After delay from common SDK exception shapes."""
    headers = getattr(exc, "headers", None)
    if headers is None:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get("retry-after") or headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(str(value))
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, retry_at.timestamp() - now())
        except (TypeError, ValueError, IndexError, OverflowError):
            return None


def _usage_value(usage: Any, *names: str) -> int | None:
    for name in names:
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        if isinstance(value, int):
            return value
    return None


def _response_metric(
    *, operation: str, model: str, outcome: str, latency_ms: int, retries: int,
    response: Any | None = None, error_category: str | None = None,
    retry_category: str | None = None,
) -> OpenAIRequestMetric:
    # LangChain structured output optionally returns {raw, parsed,
    # parsing_error}; measure the raw AIMessage rather than the parsed model.
    if isinstance(response, dict) and response.get("raw") is not None:
        response = response["raw"]
    usage = getattr(response, "usage", None)
    if usage is None:
        usage = getattr(response, "usage_metadata", None)
    input_details = usage.get("input_tokens_details") if isinstance(usage, dict) else getattr(usage, "input_tokens_details", None)
    output_details = usage.get("output_tokens_details") if isinstance(usage, dict) else getattr(usage, "output_tokens_details", None)
    return OpenAIRequestMetric(
        operation=operation,
        model=model,
        outcome=outcome,
        latency_ms=latency_ms,
        retries=retries,
        error_category=error_category,
        retry_category=retry_category,
        request_id=(
            getattr(response, "_request_id", None)
            or getattr(response, "request_id", None)
            or getattr(getattr(response, "response_metadata", None), "get", lambda *_: None)("request_id")
        ),
        input_tokens=_usage_value(usage, "input_tokens", "prompt_tokens"),
        output_tokens=_usage_value(usage, "output_tokens", "completion_tokens"),
        total_tokens=_usage_value(usage, "total_tokens"),
        cached_tokens=_usage_value(input_details, "cached_tokens"),
        reasoning_tokens=_usage_value(output_details, "reasoning_tokens"),
    )


class OpenAIClientAdapter:
    """A shared-client call boundary with bounded concurrency and one retry."""

    def __init__(
        self, client: Any, *, max_in_flight: int | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        metric_sink: Callable[[OpenAIRequestMetric], None] | None = None,
    ) -> None:
        configured_limit = max_in_flight or int(os.getenv("OPENAI_MAX_IN_FLIGHT", "4"))
        self._client = client
        self._semaphore = threading.BoundedSemaphore(max(1, configured_limit))
        self._sleeper = sleeper
        self._monotonic = monotonic
        self._metric_sink = metric_sink or self._log_metric
        self._metrics_lock = threading.Lock()
        self._metrics: dict[tuple[str, str], dict[str, int]] = {}

    @staticmethod
    def _log_metric(metric: OpenAIRequestMetric) -> None:
        logger.info(
            "openai_request operation=%s model=%s outcome=%s latency_ms=%s retries=%s "
            "error_category=%s retry_category=%s request_id=%s input_tokens=%s output_tokens=%s "
            "total_tokens=%s cached_tokens=%s reasoning_tokens=%s",
            metric.operation, metric.model, metric.outcome, metric.latency_ms,
            metric.retries, metric.error_category, metric.retry_category, metric.request_id,
            metric.input_tokens, metric.output_tokens, metric.total_tokens,
            metric.cached_tokens, metric.reasoning_tokens,
        )

    def _emit(self, metric: OpenAIRequestMetric) -> None:
        # Keep an in-process aggregate for health/evaluation artifacts.  It is
        # intentionally numeric only: prompts, excerpts, errors, and request
        # IDs are never retained here.
        with self._metrics_lock:
            aggregate = self._metrics.setdefault((metric.operation, metric.model), {
                "calls": 0, "errors": 0, "retries": 0, "latency_ms": 0,
                "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                "cached_tokens": 0, "reasoning_tokens": 0,
            })
            aggregate["calls"] += 1
            aggregate["errors"] += int(metric.outcome == "error")
            aggregate["retries"] += metric.retries
            aggregate["latency_ms"] += metric.latency_ms
            for field in ("input_tokens", "output_tokens", "total_tokens", "cached_tokens", "reasoning_tokens"):
                aggregate[field] += getattr(metric, field) or 0
        try:
            self._metric_sink(metric)
        except Exception:
            # Provider calls must never fail because optional observability failed.
            logger.warning("openai_request_metric_failed")

    def snapshot_metrics(self) -> dict[str, dict[str, int]]:
        """Return safe numeric aggregates, keyed by ``operation:model``."""
        with self._metrics_lock:
            return {
                f"{operation}:{model}": values.copy()
                for (operation, model), values in self._metrics.items()
            }

    @property
    def sdk_client(self) -> Any:
        """Expose the process-shared SDK root for compatible framework clients."""
        return self._client

    def call(
        self, *, operation: str, model: str, request: Callable[[Any], _T],
        timeout_seconds: float | None = None, deadline_monotonic: float | None = None,
        max_retries: int = 1,
    ) -> _T:
        """Run a request while respecting a total deadline and retry policy."""
        configured_timeout = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "15"))
        timeout = max(0.1, timeout_seconds if timeout_seconds is not None else configured_timeout)
        deadline = deadline_monotonic if deadline_monotonic is not None else self._monotonic() + timeout
        started = self._monotonic()
        retries = 0
        last_retry_category: str | None = None
        acquired = False
        try:
            remaining = deadline - self._monotonic()
            if remaining <= 0 or not self._semaphore.acquire(timeout=remaining):
                raise OpenAIDeadlineExceeded("OpenAI operation deadline elapsed before dispatch")
            acquired = True
            while True:
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    raise OpenAIDeadlineExceeded("OpenAI operation deadline elapsed before dispatch")
                request_client = self._client
                with_options = getattr(self._client, "with_options", None)
                if callable(with_options):
                    request_client = with_options(timeout=remaining, max_retries=0)
                try:
                    response = request(request_client)
                    # A synchronous SDK can return just after its per-request
                    # timeout. Do not accept or persist that late success.
                    if self._monotonic() >= deadline:
                        raise OpenAIDeadlineExceeded(
                            "OpenAI operation deadline elapsed after response",
                        )
                    self._emit(_response_metric(
                        operation=operation, model=model, outcome="success",
                        latency_ms=int((self._monotonic() - started) * 1000), retries=retries,
                        response=response, retry_category=last_retry_category,
                    ))
                    return response
                except BaseException as exc:
                    if isinstance(exc, OpenAIDeadlineExceeded):
                        raise
                    category = classify_openai_error(exc)
                    if retries >= min(max(0, max_retries), 1) or not is_retryable_openai_error(exc):
                        self._emit(_response_metric(
                            operation=operation, model=model, outcome="error",
                            latency_ms=int((self._monotonic() - started) * 1000), retries=retries,
                            error_category=category,
                        ))
                        raise
                    retries += 1
                    last_retry_category = category
                    retry_after = retry_after_seconds(exc)
                    # Server-directed Retry-After takes precedence; absent a
                    # header, use a short bounded retry delay.
                    delay = retry_after if retry_after is not None else 0.05
                    if delay >= deadline - self._monotonic():
                        raise OpenAIDeadlineExceeded("OpenAI operation deadline elapsed before retry") from exc
                    self._sleeper(delay)
        except BaseException as exc:
            if isinstance(exc, OpenAIDeadlineExceeded):
                self._emit(_response_metric(
                    operation=operation, model=model, outcome="error",
                    latency_ms=int((self._monotonic() - started) * 1000), retries=retries,
                    error_category="deadline_exceeded",
                ))
            raise
        finally:
            if acquired:
                self._semaphore.release()

    def create_embeddings(
        self, texts: list[str], *, model: str, timeout_seconds: float | None = None,
        deadline_monotonic: float | None = None, max_retries: int = 1,
    ) -> list[list[float]]:
        """Create embeddings in provider index order, independent of response order."""
        if not texts:
            return []
        response = self.call(
            operation="embeddings.create", model=model,
            request=lambda client: client.embeddings.create(model=model, input=texts),
            timeout_seconds=timeout_seconds, deadline_monotonic=deadline_monotonic,
            max_retries=max_retries,
        )
        return [list(item.embedding) for item in sorted(response.data, key=lambda item: item.index)]


_shared_lock = threading.Lock()
_shared_adapter: OpenAIClientAdapter | None = None
_shared_api_key: str | None = None


def get_openai_adapter(api_key: str | None = None) -> OpenAIClientAdapter:
    """Return a process-wide adapter; never include API keys in telemetry."""
    global _shared_adapter, _shared_api_key
    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    with _shared_lock:
        if _shared_adapter is None or _shared_api_key != key:
            from openai import OpenAI

            _shared_adapter = OpenAIClientAdapter(OpenAI(
                api_key=key,
                timeout=max(0.1, float(os.getenv("OPENAI_TIMEOUT_SECONDS", "15"))),
                max_retries=0,
            ))
            _shared_api_key = key
        return _shared_adapter


def reset_openai_adapter_for_tests() -> None:
    """Clear the process singleton for test isolation without exposing secrets."""
    global _shared_adapter, _shared_api_key
    with _shared_lock:
        _shared_adapter = None
        _shared_api_key = None


def openai_metrics_snapshot() -> dict[str, dict[str, int]]:
    """Safe aggregate telemetry for CI artifacts and anonymous health views."""
    with _shared_lock:
        adapter = _shared_adapter
    return adapter.snapshot_metrics() if adapter is not None else {}
