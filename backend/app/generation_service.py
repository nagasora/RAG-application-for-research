"""Provider-selection application service.

This module deliberately has no FastAPI dependency. Routes translate its
validation errors to HTTP responses, while workers can use the same governed
selection and audit rules without importing ``app.main``.
"""
from __future__ import annotations

from typing import Protocol

from .generation_settings import GenerationSelection, resolve


class GenerationStore(Protocol):
    def generation_settings(self, workspace_id: str) -> tuple[dict, str | None, str | None]: ...

    def record_generation_audit(
        self, workspace_id: str, user_id: str, *, scope: str, provider: str,
        model: str, outcome: str, reference_id: str | None = None,
        prompt_version: str = "", usage: dict | None = None,
    ) -> None: ...


def resolve_workspace_generation(
    store: GenerationStore, workspace_id: str, scope: str, provider: str | None, model: str | None,
) -> GenerationSelection:
    settings, _, _ = store.generation_settings(workspace_id)
    return resolve(settings, scope, provider, model)


def record_generation_outcome(
    store: GenerationStore, workspace_id: str, user_id: str, *, scope: str,
    selection: GenerationSelection, outcome: str, reference_id: str | None = None,
    prompt_version: str = "", usage: dict | None = None,
) -> None:
    """Persist only operational metadata at the store boundary."""
    store.record_generation_audit(
        workspace_id, user_id, scope=scope, provider=selection.provider,
        model=selection.model, outcome=outcome, reference_id=reference_id,
        prompt_version=prompt_version, usage=usage,
    )
