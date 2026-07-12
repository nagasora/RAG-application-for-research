from __future__ import annotations

import os

from dotenv import load_dotenv

from .store import PaperStore


def main() -> None:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    store = PaperStore(database_url)
    store.create_schema()


if __name__ == "__main__":
    main()
