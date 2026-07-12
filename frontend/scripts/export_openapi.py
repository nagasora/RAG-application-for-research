"""Export the backend FastAPI schema deterministically for frontend codegen."""

from __future__ import annotations

import json
import sys
from pathlib import Path


FRONTEND_ROOT = Path(__file__).resolve().parent.parent
REPOSITORY_ROOT = FRONTEND_ROOT.parent
OUTPUT_PATH = FRONTEND_ROOT / "openapi" / "paperpilot.json"


def main() -> None:
    sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))
    from app.main import app  # pylint: disable=import-outside-toplevel

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT_PATH.relative_to(FRONTEND_ROOT)}")


if __name__ == "__main__":
    main()
