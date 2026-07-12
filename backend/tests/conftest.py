import pytest


@pytest.fixture(autouse=True)
def explicit_dev_auth(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
