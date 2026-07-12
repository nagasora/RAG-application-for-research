from __future__ import annotations

import os
from functools import lru_cache
from urllib.parse import urlparse

import jwt
from fastapi import HTTPException, Request, status
from jwt import PyJWKClient

from .models import Principal


def _bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("Authorization", "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise HTTPException(status_code=503, detail=f"{name} is required")
    return value


def _validate_jwks_url(jwks_url: str) -> str:
    parsed = urlparse(jwks_url)
    if not parsed.hostname:
        raise HTTPException(status_code=503, detail="OIDC_JWKS_URL must include a host")
    if parsed.scheme.lower() == "https":
        return jwks_url
    allow_insecure = os.getenv("OIDC_ALLOW_INSECURE_HTTP", "false").lower() in {"1", "true", "yes"}
    if parsed.scheme.lower() == "http" and allow_insecure and parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}:
        return jwks_url
    raise HTTPException(status_code=503, detail="OIDC_JWKS_URL must use HTTPS; insecure HTTP is limited to explicitly enabled loopback development")


@lru_cache(maxsize=8)
def _jwk_client(jwks_url: str) -> PyJWKClient:
    return PyJWKClient(jwks_url, cache_keys=True)


def _dev_principal(request: Request) -> Principal:
    header_user = request.headers.get("X-Dev-User", "").strip()
    if header_user:
        if len(header_user) > 255:
            raise HTTPException(status_code=400, detail="X-Dev-User is too long")
        return Principal(issuer="paperpilot-dev", subject=header_user, display_name=header_user)

    token = _bearer_token(request)
    expected = os.getenv("DEV_AUTH_TOKEN", "").strip()
    configured_user = os.getenv("DEV_AUTH_USER", "").strip()
    if token and expected and configured_user:
        import secrets

        if secrets.compare_digest(token, expected):
            return Principal(
                issuer="paperpilot-dev", subject=configured_user, display_name=configured_user
            )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="development identity is required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _oidc_principal(request: Request) -> Principal:
    issuer = _required_env("OIDC_ISSUER").rstrip("/")
    audience = _required_env("OIDC_AUDIENCE")
    jwks_url = _validate_jwks_url(_required_env("OIDC_JWKS_URL"))
    algorithms = [item.strip() for item in os.getenv("OIDC_ALGORITHMS", "RS256").split(",") if item.strip()]
    if not algorithms or any(item.lower() == "none" or item.upper().startswith("HS") for item in algorithms):
        raise HTTPException(status_code=503, detail="OIDC_ALGORITHMS must use asymmetric signatures")

    token = _bearer_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="bearer token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        signing_key = _jwk_client(jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=algorithms,
            audience=audience,
            issuer=issuer,
            options={"require": ["exp", "iss", "sub", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise HTTPException(status_code=401, detail="token subject is invalid")
    return Principal(
        issuer=issuer,
        subject=subject,
        email=claims.get("email") if isinstance(claims.get("email"), str) else None,
        display_name=claims.get("name") if isinstance(claims.get("name"), str) else None,
    )


def get_current_principal(request: Request) -> Principal:
    mode = os.getenv("AUTH_MODE", "").strip().lower()
    if mode == "dev":
        return _dev_principal(request)
    if mode == "oidc":
        return _oidc_principal(request)
    if not mode:
        raise HTTPException(status_code=503, detail="AUTH_MODE must be explicitly configured")
    raise HTTPException(status_code=503, detail="AUTH_MODE must be dev or oidc")
