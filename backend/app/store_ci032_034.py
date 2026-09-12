"""SQLAlchemy persistence mixins for CI-032 through CI-034.

The mixins deliberately contain persistence-only operations.  Workflows and
FastAPI routes pass already-validated values and never receive a Session.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from .database import (
    DiscoverySearchCandidateRecord, DiscoverySearchSessionRecord,
    ExperimentProfileRecord, GenerationAuditRecord, WorkspaceGenerationSettingsRecord,
)


class DiscoverySessionStoreMixin:
    def _insert_discovery_session(self, session, workspace_id, created_by, *, criteria, query_plan, generation_provider, generation_model, candidates):
        now = datetime.now(timezone.utc); expires = now + timedelta(hours=1)
        record = DiscoverySearchSessionRecord(id=str(uuid4()), workspace_id=workspace_id, created_by=created_by, criteria=dict(criteria), query_plan=list(query_plan), generation_provider=generation_provider, generation_model=generation_model, expires_at=expires, created_at=now)
        session.add(record)
        # Candidate rows only carry the scalar session_id and have no ORM
        # relationship that lets SQLAlchemy order these INSERTs reliably.
        # Persist the parent first so PostgreSQL never observes orphan FKs.
        session.flush()
        persisted=[]
        for rank, candidate in enumerate(candidates, 1):
            row = DiscoverySearchCandidateRecord(id=str(uuid4()), session_id=record.id, canonical_key=str(candidate["canonical_key"]), provider=str(candidate["provider"]), provider_paper_id=str(candidate["provider_paper_id"]), provider_ids=dict(candidate.get("provider_ids") or {}), snapshot=dict(candidate.get("snapshot") or {}), rank=rank, imported_paper_id=None, created_at=now)
            session.add(row); persisted.append({**candidate, "candidate_id": row.id, "search_session_id": record.id, "expires_at": expires.isoformat()})
        session.flush(); return persisted


class GenerationSettingsStoreMixin:
    def _generation_settings_row(self, session, workspace_id):
        return session.get(WorkspaceGenerationSettingsRecord, workspace_id)

    def _write_generation_audit(self, session, **values):
        session.add(GenerationAuditRecord(id=str(uuid4()), created_at=datetime.now(timezone.utc), **values))


class ExperimentProfileStoreMixin:
    def _find_experiment_profile(self, session, *, workspace_id, paper_id, source_version_id, content_hash, provider, model, prompt_version, cache_key):
        return session.scalar(select(ExperimentProfileRecord).where(ExperimentProfileRecord.workspace_id==workspace_id, ExperimentProfileRecord.paper_id==paper_id, ExperimentProfileRecord.source_version_id==source_version_id, ExperimentProfileRecord.content_hash==content_hash, ExperimentProfileRecord.provider==provider, ExperimentProfileRecord.model==model, ExperimentProfileRecord.prompt_version==prompt_version, ExperimentProfileRecord.cache_key==cache_key))
