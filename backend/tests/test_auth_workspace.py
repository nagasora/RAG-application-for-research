from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
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
    event.listen(
        engine,
        "connect",
        lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"),
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
            selected = client.get(
                f"/api/workspaces/{created.json()['id']}",
                headers={"X-Dev-User": "alice"},
            )
            hidden = client.get(
                f"/api/workspaces/{created.json()['id']}",
                headers={"X-Dev-User": "mallory"},
            )
        assert me.status_code == 200
        assert me.json()["personal_workspace"]["role"] == "owner"
        assert created.status_code == 201
        assert {item["name"] for item in listed.json()} == {
            "alice のワークスペース", "Lab"
        }
        assert selected.status_code == 200
        assert selected.json()["id"] == created.json()["id"]
        assert selected.json()["role"] == "owner"
        assert hidden.status_code == 404
    finally:
        main.app.dependency_overrides.clear()


def test_llm_status_is_authenticated_and_never_exposes_credentials(tmp_path, monkeypatch):
    setup_app(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setattr(main, "_agentic_dependencies_available", lambda: True)
    main._set_last_llm_failure("api_key_missing")
    try:
        with TestClient(main.app) as client:
            denied = client.get("/api/llm/status")
            allowed = client.get("/api/llm/status", headers={"X-Dev-User": "alice"})

        assert denied.status_code == 401
        assert allowed.status_code == 200
        assert allowed.json() == {
            "configured": False,
            "model": "gpt-5.4-nano",
            "embedding_model": "local-hash-v1",
            "agentic_dependencies_available": True,
            "last_failure_code": "api_key_missing",
        }
    finally:
        main._set_last_llm_failure(None)
        main.app.dependency_overrides.clear()


def test_owner_can_rename_workspace_but_viewer_and_outsider_cannot(tmp_path):
    store = setup_app(tmp_path)
    try:
        with TestClient(main.app) as client:
            personal = client.get("/api/me", headers={"X-Dev-User": "alice"}).json()[
                "personal_workspace"
            ]
            personal_renamed = client.patch(
                f"/api/workspaces/{personal['id']}",
                headers={"X-Dev-User": "alice"},
                json={"name": "Alice Research"},
            )
            refreshed_me = client.get("/api/me", headers={"X-Dev-User": "alice"})
            created = client.post(
                "/api/workspaces", headers={"X-Dev-User": "alice"}, json={"name": "Lab"}
            ).json()
            renamed = client.patch(
                f"/api/workspaces/{created['id']}",
                headers={"X-Dev-User": "alice"},
                json={"name": "  Evidence Lab  "},
            )
            bob, _ = store.ensure_user(Principal(issuer="paperpilot-dev", subject="bob"))
            store.add_workspace_member(created["id"], bob.id, "viewer")
            charlie, _ = store.ensure_user(
                Principal(issuer="paperpilot-dev", subject="charlie")
            )
            store.add_workspace_member(created["id"], charlie.id, "editor")
            viewer = client.patch(
                f"/api/workspaces/{created['id']}",
                headers={"X-Dev-User": "bob"},
                json={"name": "Viewer rename"},
            )
            editor = client.patch(
                f"/api/workspaces/{created['id']}",
                headers={"X-Dev-User": "charlie"},
                json={"name": "Editor rename"},
            )
            outsider = client.patch(
                f"/api/workspaces/{created['id']}",
                headers={"X-Dev-User": "mallory"},
                json={"name": "Outsider rename"},
            )
            blank = client.patch(
                f"/api/workspaces/{created['id']}",
                headers={"X-Dev-User": "alice"},
                json={"name": "   "},
            )
            listed = client.get("/api/workspaces", headers={"X-Dev-User": "alice"})

        assert personal_renamed.status_code == 200
        assert refreshed_me.json()["personal_workspace"]["name"] == "Alice Research"
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "Evidence Lab"
        assert renamed.json()["role"] == "owner"
        assert viewer.status_code == 403
        assert editor.status_code == 403
        assert outsider.status_code == 404
        assert blank.status_code == 422
        assert {workspace["name"] for workspace in listed.json()} == {
            "Alice Research", "Evidence Lab"
        }
        assert store.resolve_workspace(
            store.ensure_user(Principal(issuer="paperpilot-dev", subject="alice"))[0].id,
            created["id"],
        ).name == "Evidence Lab"
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


def test_workspace_owner_can_manage_members_without_leaking_orphans(tmp_path):
    setup_app(tmp_path)
    try:
        with TestClient(main.app) as client:
            workspace = client.post(
                "/api/workspaces", headers={"X-Dev-User": "alice"}, json={"name": "Shared Lab"},
            ).json()
            # A collaborator is discoverable only after their identity is provisioned.
            client.get("/api/me", headers={"X-Dev-User": "bob"})
            missing = client.post(
                f"/api/workspaces/{workspace['id']}/members",
                headers={"X-Dev-User": "alice"},
                json={"subject": "not-signed-in", "role": "editor"},
            )
            created = client.post(
                f"/api/workspaces/{workspace['id']}/members",
                headers={"X-Dev-User": "alice"},
                json={"subject": "bob", "role": "editor"},
            )
            duplicate = client.post(
                f"/api/workspaces/{workspace['id']}/members",
                headers={"X-Dev-User": "alice"},
                json={"subject": "bob", "role": "viewer"},
            )
            members_for_bob = client.get(
                f"/api/workspaces/{workspace['id']}/members", headers={"X-Dev-User": "bob"},
            )
            editor_add = client.post(
                f"/api/workspaces/{workspace['id']}/members",
                headers={"X-Dev-User": "bob"},
                json={"subject": "alice", "role": "viewer"},
            )
            promoted = client.patch(
                f"/api/workspaces/{workspace['id']}/members/{created.json()['user']['id']}",
                headers={"X-Dev-User": "alice"}, json={"role": "owner"},
            )
            demoted = client.patch(
                f"/api/workspaces/{workspace['id']}/members/{created.json()['user']['id']}",
                headers={"X-Dev-User": "alice"}, json={"role": "viewer"},
            )
            removed = client.delete(
                f"/api/workspaces/{workspace['id']}/members/{created.json()['user']['id']}",
                headers={"X-Dev-User": "alice"},
            )
            after_remove = client.get(
                f"/api/workspaces/{workspace['id']}/members", headers={"X-Dev-User": "alice"},
            )

        assert missing.status_code == 404
        assert created.status_code == 201
        assert created.json()["user"]["subject"] == "bob"
        assert created.json()["role"] == "editor"
        assert duplicate.status_code == 409
        assert members_for_bob.status_code == 200
        assert {member["user"]["subject"] for member in members_for_bob.json()} == {"alice", "bob"}
        assert editor_add.status_code == 403
        assert promoted.status_code == 200
        assert promoted.json()["role"] == "owner"
        assert demoted.status_code == 200
        assert demoted.json()["role"] == "viewer"
        assert removed.status_code == 204
        assert [member["user"]["subject"] for member in after_remove.json()] == ["alice"]
    finally:
        main.app.dependency_overrides.clear()


def test_member_administration_requires_an_owner_and_preserves_an_owner(tmp_path):
    setup_app(tmp_path)
    try:
        with TestClient(main.app) as client:
            workspace = client.post(
                "/api/workspaces", headers={"X-Dev-User": "alice"}, json={"name": "Shared Lab"},
            ).json()
            client.get("/api/me", headers={"X-Dev-User": "bob"})
            bob = client.post(
                f"/api/workspaces/{workspace['id']}/members",
                headers={"X-Dev-User": "alice"}, json={"subject": "bob", "role": "viewer"},
            ).json()
            viewer_change = client.patch(
                f"/api/workspaces/{workspace['id']}/members/{bob['user']['id']}",
                headers={"X-Dev-User": "bob"}, json={"role": "editor"},
            )
            # Obtain Alice's stable ID from the member list rather than treating a workspace field as a user ID.
            members = client.get(
                f"/api/workspaces/{workspace['id']}/members", headers={"X-Dev-User": "alice"},
            ).json()
            alice_id = next(item["user"]["id"] for item in members if item["user"]["subject"] == "alice")
            protected_demote = client.patch(
                f"/api/workspaces/{workspace['id']}/members/{alice_id}",
                headers={"X-Dev-User": "alice"}, json={"role": "viewer"},
            )
            invalid_target = client.post(
                f"/api/workspaces/{workspace['id']}/members",
                headers={"X-Dev-User": "alice"}, json={"subject": "bob", "email": "bob@example.test"},
            )

        assert viewer_change.status_code == 403
        assert protected_demote.status_code == 403
        assert invalid_target.status_code == 422
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
