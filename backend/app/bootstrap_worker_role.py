from __future__ import annotations

import os

import psycopg
from psycopg import sql


WORKER_ROLE = "paperpilot_worker"
WORKER_GRANTS = {
    "papers": ("SELECT", "UPDATE"),
    "chunks": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "ingestion_jobs": ("SELECT", "UPDATE"),
    "paper_pages": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "document_elements": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "chunk_embeddings": ("SELECT", "INSERT", "UPDATE"),
    "embedding_jobs": ("SELECT", "INSERT", "UPDATE"),
}


def _postgres_url(value: str) -> str:
    if value.startswith("postgresql+psycopg://"):
        return "postgresql://" + value.removeprefix("postgresql+psycopg://")
    if value.startswith(("postgresql://", "postgres://")):
        return value
    raise RuntimeError("OWNER_DATABASE_URL must be PostgreSQL")


def main() -> None:
    owner_url = _postgres_url(os.getenv("OWNER_DATABASE_URL", os.getenv("DATABASE_URL", "")))
    password = os.environ.get("WORKER_DB_PASSWORD", "")
    if len(password) < 16:
        raise RuntimeError("WORKER_DB_PASSWORD must contain at least 16 characters")
    role = sql.Identifier(WORKER_ROLE)
    with psycopg.connect(owner_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (WORKER_ROLE,))
            if cursor.fetchone() is None:
                cursor.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT").format(role, sql.Literal(password)))
            else:
                cursor.execute(sql.SQL("ALTER ROLE {} PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT").format(role, sql.Literal(password)))
            cursor.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(role))
            cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
            cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {}").format(role))
            for table, privileges in WORKER_GRANTS.items():
                cursor.execute(
                    sql.SQL("GRANT {} ON TABLE {} TO {}").format(
                        sql.SQL(", ").join(map(sql.SQL, privileges)),
                        sql.Identifier(table),
                        role,
                    )
                )


if __name__ == "__main__":
    main()
