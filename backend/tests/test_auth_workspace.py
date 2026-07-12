from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app import auth
from app.database import Base
from app.models import Principal
from app.storage import LocalOriginalStorage
from app.store import PaperStore


def setup_app(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'auth.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    store = PaperStore(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    originals = LocalOriginalStorage(tmp_path / "originals")
    main.app.dependency_overrides[main.get_store] = lambda: store
    main.app.dependency_overrides[main.get_original_storage] = lambda: originals
    return store


def test_auth_mode_and_identity_are_explicit(tmp_path, monkeypatch):
    setup_app(tmp_path)
    try:
        with TestClient(main.app) as client:
            missing_identity = client.get("/api/me")
            monkeypatch.delenv("AUTH_MODE", raising=False)
            missing_mode = client.get("/api/me", headers={"X-Dev-User": "alice"})
        assert missing_identity.status_code == 401
        assert missing_mode.status_code == 503
    finally:
        main.app.dependency_overrides.clear()


def test_first_identity_gets_personal_workspace_and_can_create_more(tmp_path):
    setup_app(tmp_path)
    try:
        with TestClient(main.app) as client:
            me = client.get("/api/me", headers={"X-Dev-User": "alice"})
            created = client.post(
                "/api/workspaces", headers={"X-Dev-User": "alice"}, json={"name": "Lab"}
            )
            listed = client.get("/api/workspaces", headers={"X-Dev-User": "alice"})
        assert me.status_code == 200
        assert me.json()["personal_workspace"]["role"] == "owner"
        assert created.status_code == 201
        assert {item["name"] for item in listed.json()} == {
            "alice のワークスペース", "Lab"
        }
    finally:
        main.app.dependency_overrides.clear()


def test_request_user_id_is_ignored_and_membership_prevents_idor(tmp_path):
    store = setup_app(tmp_path)
    try:
        with TestClient(main.app) as client:
            uploaded = client.post(
                "/api/papers/upload",
                headers={"X-Dev-User": "alice"},
                data={"user_id": "mallory"},
                files={"files": ("paper.txt", b"workspace evidence", "text/plain")},
            ).json()[0]
            paper_id = uploaded["paper"]["id"]
            forged = client.get(
                f"/api/papers/{paper_id}?user_id=alice", headers={"X-Dev-User": "bob"}
            )

            alice, alice_workspace = store.ensure_user(
                Principal(issuer="paperpilot-dev", subject="alice")
            )
            bob, _ = store.ensure_user(Principal(issuer="paperpilot-dev", subject="bob"))
            store.add_workspace_member(alice_workspace.id, bob.id, "viewer")
            shared = client.get(
                f"/api/papers/{paper_id}",
                headers={"X-Dev-User": "bob", "X-Workspace-ID": alice_workspace.id},
            )
            forbidden_delete = client.delete(
                f"/api/papers/{paper_id}",
                headers={"X-Dev-User": "bob", "X-Workspace-ID": alice_workspace.id},
            )
        assert forged.status_code == 404
        assert shared.status_code == 200
        assert forbidden_delete.status_code == 403
        saved = store.get_owned(alice_workspace.id, paper_id)
        assert saved.created_by == alice.id
        assert saved.user_id == "alice"
    finally:
        main.app.dependency_overrides.clear()


def test_static_dev_bearer_requires_explicit_user_and_token(tmp_path, monkeypatch):
    setup_app(tmp_path)
    monkeypatch.setenv("DEV_AUTH_USER", "token-user")
    monkeypatch.setenv("DEV_AUTH_TOKEN", "a-long-local-test-secret")
    try:
        with TestClient(main.app) as client:
            denied = client.get("/api/me", headers={"Authorization": "Bearer wrong"})
            allowed = client.get(
                "/api/me", headers={"Authorization": "Bearer a-long-local-test-secret"}
            )
        assert denied.status_code == 401
        assert allowed.status_code == 200
        assert allowed.json()["user"]["subject"] == "token-user"
    finally:
        main.app.dependency_overrides.clear()


def test_oidc_verifies_signature_issuer_audience_expiry_and_subject(tmp_path, monkeypatch):
    setup_app(tmp_path)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    class FakeJwks:
        def get_signing_key_from_jwt(self, token):
            return SimpleNamespace(key=public_key)

    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.setenv("OIDC_ISSUER", "https://issuer.example")
    monkeypatch.setenv("OIDC_AUDIENCE", "paperpilot-api")
    monkeypatch.setenv("OIDC_JWKS_URL", "https://issuer.example/jwks")
    monkeypatch.setattr(auth, "_jwk_client", lambda _: FakeJwks())
    now = datetime.now(timezone.utc)

    def token(**overrides):
        claims = {
            "iss": "https://issuer.example",
            "aud": "paperpilot-api",
            "sub": "oidc-user",
            "exp": now + timedelta(minutes=5),
        }
        claims.update(overrides)
        return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test"})

    try:
        with TestClient(main.app) as client:
            valid = client.get("/api/me", headers={"Authorization": f"Bearer {token()}"})
            wrong_audience = client.get(
                "/api/me", headers={"Authorization": f"Bearer {token(aud='other')}"}
            )
            expired = client.get(
                "/api/me",
                headers={"Authorization": f"Bearer {token(exp=now - timedelta(seconds=1))}"},
            )
            missing_subject = client.get(
                "/api/me", headers={"Authorization": f"Bearer {token(sub=None)}"}
            )
        assert valid.status_code == 200
        assert valid.json()["user"]["subject"] == "oidc-user"
        assert wrong_audience.status_code == 401
        assert expired.status_code == 401
        assert missing_subject.status_code == 401
    finally:
        main.app.dependency_overrides.clear()


@pytest.mark.parametrize("url", ["http://localhost:8080/jwks", "http://127.0.0.1/jwks", "http://[::1]/jwks"])
def test_oidc_allows_insecure_jwks_only_for_explicit_loopback(monkeypatch, url):
    monkeypatch.setenv("OIDC_ALLOW_INSECURE_HTTP", "true")
    assert auth._validate_jwks_url(url) == url


def test_oidc_rejects_http_jwks_by_default_and_non_loopback_even_when_enabled(monkeypatch):
    with pytest.raises(HTTPException) as default_denied:
        auth._validate_jwks_url("http://localhost/jwks")
    assert default_denied.value.status_code == 503
    monkeypatch.setenv("OIDC_ALLOW_INSECURE_HTTP", "true")
    with pytest.raises(HTTPException):
        auth._validate_jwks_url("http://issuer.example/jwks")
    assert auth._validate_jwks_url("https://issuer.example/jwks").startswith("https://")
