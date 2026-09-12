"""Server-owned generation model catalog and resolution rules."""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Any

DEFAULT_PROVIDER="openai"; DEFAULT_MODEL="gpt-5.6-luna"
CATALOG={"openai": ("gpt-5.6-luna",), "gemini": ("gemini-3.5-flash-lite",)}

@dataclass(frozen=True)
class GenerationSelection:
    provider: str
    model: str

def catalog() -> list[dict]:
    return [
        {"provider":"openai","model":"gpt-5.6-luna","available":bool(os.getenv("OPENAI_API_KEY")),"reason":None if os.getenv("OPENAI_API_KEY") else "api_key_missing"},
        {"provider":"gemini","model":"gemini-3.5-flash-lite","available":bool(os.getenv("GEMINI_API_KEY")),"reason":None if os.getenv("GEMINI_API_KEY") else "api_key_missing"},
    ]

def validate(provider: str, model: str, *, require_available: bool = False) -> GenerationSelection:
    normalized_provider=provider.strip().casefold(); normalized_model=model.strip()
    if normalized_provider not in CATALOG or normalized_model not in CATALOG[normalized_provider]: raise ValueError("generation model is not allowlisted")
    selection=GenerationSelection(normalized_provider, normalized_model)
    if require_available and not any(item["provider"]==selection.provider and item["model"]==selection.model and item["available"] for item in catalog()): raise ValueError("generation model is unavailable")
    return selection

def defaults() -> dict:
    return {scope:{"provider":DEFAULT_PROVIDER,"model":DEFAULT_MODEL} for scope in ("ask","discovery","analysis","mind_map")}


def migrate_persisted_scopes(scopes: Any) -> dict:
    """Return a complete settings mapping with retired OpenAI defaults replaced.

    This deliberately applies only to the mutable workspace settings record.
    Generation audits and research-run snapshots are historical facts and must
    keep the model that actually ran.
    """
    current = dict(scopes) if isinstance(scopes, dict) else {}
    migrated = dict(current)
    for scope, default in defaults().items():
        choice = current.get(scope)
        if not isinstance(choice, dict):
            migrated[scope] = dict(default)
            continue
        provider = str(choice.get("provider") or "").strip().casefold()
        model = str(choice.get("model") or "").strip()
        if provider == "openai" and model == "gpt-5.4-nano":
            migrated[scope] = {"provider": DEFAULT_PROVIDER, "model": DEFAULT_MODEL}
    return migrated


def resolve(settings: dict, scope: str, provider: str | None, model: str | None) -> GenerationSelection:
    if bool(provider) != bool(model): raise ValueError("generation_provider and generation_model must be supplied together")
    choice={"provider":provider,"model":model} if provider else dict((settings.get(scope) or defaults()[scope]))
    return validate(str(choice.get("provider") or ""), str(choice.get("model") or ""))
