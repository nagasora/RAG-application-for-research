from types import SimpleNamespace

import pytest

from app import ingestion


def test_heartbeat_is_joined_when_paper_load_fails(monkeypatch):
    lifecycle = {"started": False, "joined": False}

    class FakeThread:
        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon

        def start(self):
            lifecycle["started"] = True

        def join(self, timeout=None):
            lifecycle["joined"] = True
            assert timeout == 1

    class FakeStore:
        def claim_ingestion_job(self, *args):
            return SimpleNamespace(attempts=1)

        def get(self, paper_id):
            raise RuntimeError("paper load failed")

        def fail_ingestion(self, job_id, paper_id, error, expected_attempt):
            assert (job_id, paper_id, expected_attempt) == ("job-1", "paper-1", 1)
            assert error == "paper load failed"

    extractor = SimpleNamespace(config=SimpleNamespace(), created_asset_keys=[])
    monkeypatch.setattr(ingestion.threading, "Thread", FakeThread)

    with pytest.raises(RuntimeError, match="paper load failed"):
        ingestion.process_ingestion_job(
            FakeStore(), SimpleNamespace(), "job-1", "paper-1", extractor=extractor,
        )

    assert lifecycle == {"started": True, "joined": True}
