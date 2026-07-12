import pytest

from app.bootstrap_worker_role import WORKER_GRANTS, _postgres_url


def test_worker_role_grants_only_ingestion_tables():
    assert set(WORKER_GRANTS) == {"papers", "chunks", "ingestion_jobs", "paper_pages", "document_elements"}
    assert "DELETE" not in WORKER_GRANTS["papers"]
    assert "INSERT" not in WORKER_GRANTS["ingestion_jobs"]
    assert _postgres_url("postgresql+psycopg://owner@db/app").startswith("postgresql://")
    with pytest.raises(RuntimeError, match="must be PostgreSQL"):
        _postgres_url("sqlite:///unsafe.db")
