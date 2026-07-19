import pytest


@pytest.fixture(autouse=True)
def explicit_dev_auth(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
    # Most API tests assert the completed upload state. Keep that deterministic
    # even when a developer or CI host exports a production worker mode; tests
    # for asynchronous ingestion explicitly override this fixture value.
    monkeypatch.setenv("INGESTION_MODE", "inline")
