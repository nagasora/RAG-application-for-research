# PaperPilot backend

The backend requires an explicit PostgreSQL connection. It never silently falls back to SQLite.
Authentication is also explicit: `AUTH_MODE` must be `dev` or `oidc`; there is no
production `demo-user` fallback.

```powershell
docker compose up -d postgres
Copy-Item .env.example .env
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --port 8000
```

For local development, `.env.example` selects `AUTH_MODE=dev`. Send an identity on
every protected request with `X-Dev-User: your-name`, or configure both
`DEV_AUTH_USER` and a long random `DEV_AUTH_TOKEN` and send that token as Bearer.
These mechanisms are accepted only in dev mode.

For deployed environments use `AUTH_MODE=oidc` and configure `OIDC_ISSUER`,
`OIDC_AUDIENCE`, and `OIDC_JWKS_URL`. The API verifies the JWT signature through
JWKS and requires matching `iss`, `aud`, `exp`, and `sub` claims. Symmetric JWT
algorithms are rejected. Put the frontend and API behind TLS and do not store access
tokens in local storage.
`OIDC_JWKS_URL` must use HTTPS. Insecure HTTP is accepted only when
`OIDC_ALLOW_INSECURE_HTTP=true` and the host is exactly loopback
(`localhost`, `127.0.0.1`, or `::1`) for local development.

The first authenticated request provisions a user and personal workspace. Use
`GET /api/me`, `GET /api/workspaces`, and `POST /api/workspaces`. Paper, search,
analysis, original-file, page, and chunk endpoints use the personal workspace by
default. Send `X-Workspace-ID` to select another workspace; membership is checked
server-side. Legacy `user_id` fields are ignored and marked deprecated where OpenAPI
supports that marker.

Upload limits are configured with `MAX_UPLOAD_FILES`, `MAX_UPLOAD_BYTES`, and
`MAX_PDF_PAGES`. `POST /api/papers/upload` returns one result per file. A mixed
batch may contain successful and failed items while retaining HTTP 200.
Duplicate uploads return `success=true`, `status="duplicate"`, and the existing paper.
Original files are stored immutably below `PAPER_ORIGINAL_STORAGE_DIR`; derived
figures are isolated below `PAPER_ASSET_STORAGE_DIR`. The legacy `PAPER_STORAGE_DIR`
remains a single-root compatibility setting. The initial Alembic
migrations create authenticated users, workspaces, memberships, `papers`, and
`chunks`. Papers are deduplicated by `(workspace_id, content_hash)` and record their
creator. `python -m app.init_db` remains available for disposable development databases.

Research workspace assets are introduced by revision `20260712_0003`. Search history
stores citations by default; set `SEARCH_HISTORY_STORE_ANSWER=true` to retain full answers.
Paper exports are available as BibTeX, RIS, and formula-injection-safe CSV.

## Ingestion workers and optional OCR

Revision `20260712_0004` adds ingestion jobs, per-page text provenance/quality,
and structured document elements. `INGESTION_MODE=inline` preserves the simple
development flow. For production set `INGESTION_MODE=celery`, start Redis and the
worker from `docker-compose.yml`, and poll `GET /api/jobs/{id}`. Queue messages
contain only `paper_id` and `job_id`; workers reload the immutable original.
Celery beat runs a periodic lease reaper. Fresh running jobs are left untouched;
stale jobs below the attempt limit are queued again, while exhausted jobs and their
papers are moved to a terminal failed state. Attempt fencing prevents an older worker
from heartbeating or committing after a replacement worker has reclaimed its lease.
Element metadata is listed at `GET /api/papers/{id}/assets`; figure bytes are served
through the workspace-authorized `/api/papers/{id}/assets/{element_id}/file` route.

OCR is feature-off by default. `ENABLE_OCR=true` applies Tesseract only to pages
below `OCR_DENSITY_THRESHOLD`; `OCR_FAILURE_POLICY=native` falls back to native
text when the CLI/language data is unavailable, while `fail` marks the job failed.
Page, CPU, wall-time, input byte, extracted asset count, asset byte, OCR timeout,
and retry limits are configured in `.env.example`. The worker image installs
Japanese and English Tesseract language data. Tables use pdfplumber; figure images
use pypdf. The extraction adapters intentionally leave a boundary for future engines.

The worker runs as UID/GID `10001`, with a read-only root filesystem, a bounded
`/tmp`, no Linux capabilities, no-new-privileges, and process/memory/CPU limits.
The worker mounts `data/originals` read-only and only `data/assets` read-write;
ensure `data/assets` is writable by UID 10001 before starting it. Its Compose
network is internal, so runtime egress is denied while PostgreSQL and Redis remain
reachable. OCR is local and needs no internet access. Do not attach the worker to a
public-egress network unless a reviewed extraction adapter explicitly requires it.

The default Compose bootstrap service runs Alembic as the database owner and then
creates/updates `paperpilot_worker` with DML only on the five ingestion tables.
Production must likewise use separate database roles: a migration owner runs Alembic and
holds DDL privileges; the API runtime role receives only required DML; the worker
role should be narrower still (`SELECT, UPDATE` on `papers`/`ingestion_jobs`, and
the required `SELECT, INSERT, UPDATE, DELETE` on `chunks`, `paper_pages`, and
`document_elements`). Do not grant the API or worker `CREATE`, `ALTER`, `DROP`,
superuser, role-management, or database-owner privileges. Set `WORKER_DATABASE_URL`
to that least-privilege worker role; never pass OpenAI or OIDC secrets to the worker.
To rotate the worker password, set a new `WORKER_DB_PASSWORD` and matching
percent-encoded `WORKER_DATABASE_URL`, rerun `docker compose run --rm bootstrap`,
then restart worker and beat. Keep owner credentials available only to the one-shot
bootstrap service, never to worker or beat.

Tests explicitly inject a temporary SQLAlchemy database:

```powershell
python -m pytest tests -q
```
