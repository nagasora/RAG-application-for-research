"""Run the versioned CI-014 harness; live provider use is explicit opt-in."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.evaluation import (  # noqa: E402
    benchmark_retrieval_latency, build_offline_artifact,
    run_live_model_benchmark, write_evaluation_artifact,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture", type=Path,
        default=ROOT / "backend" / "evaluation" / "ci014_golden_cases.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--measure-latency", action="store_true")
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--live-model", action="store_true")
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    if args.live_model:
        payload = run_live_model_benchmark()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        return 0
    latency = benchmark_retrieval_latency(fixture, args.iterations) if args.measure_latency else None
    artifact = build_offline_artifact(fixture, latency_samples_ms=latency)
    write_evaluation_artifact(artifact, args.output)
    # Validation artifacts are useful even when an unresolved gate is false.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
