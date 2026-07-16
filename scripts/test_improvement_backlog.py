from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from scripts.improvement_backlog import (
    eligible_items, load_decisions, load_focus, load_items, validate,
)


def document(rows: str, focus: str = "- CI-001: first") -> str:
    return f"""# Test
<!-- FOCUS_START -->
{focus}
<!-- FOCUS_END -->
<!-- BACKLOG_TABLE_START -->
| ID | Priority | State | Area | Outcome | Depends on | Evidence reviewed | Decision | Next action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
{rows}
<!-- BACKLOG_TABLE_END -->
"""


class ImprovementBacklogTest(unittest.TestCase):
    def write(self, content: str) -> Path:
        temporary = tempfile.NamedTemporaryFile(mode="w", suffix=".md", encoding="utf-8", delete=False)
        with temporary:
            temporary.write(content)
        self.addCleanup(Path(temporary.name).unlink, missing_ok=True)
        return Path(temporary.name)

    def test_load_validate_and_focus_order(self) -> None:
        path = self.write(document("\n".join((
            "| CI-001 | P0 | ready | evidence | exact quote | - | 2026-07-16 | D-20260716-01 | implement |",
            "| CI-002 | P1 | intake | ui | later | CI-001 | 2026-07-16 | - | design |",
        ))))
        items = load_items(path)
        focus = load_focus(path)
        self.assertEqual([], validate(items, focus, dt.date(2026, 7, 16)))
        self.assertEqual(["CI-001"], [item.item_id for item in eligible_items(items)])

    def test_unknown_dependency_and_cycle_are_rejected(self) -> None:
        path = self.write(document("\n".join((
            "| CI-001 | P0 | intake | evidence | first | CI-002, CI-999 | 2026-07-16 | - | implement |",
            "| CI-002 | P0 | intake | evidence | second | CI-001 | 2026-07-16 | - | implement |",
        ))))
        errors = validate(load_items(path), load_focus(path), dt.date(2026, 7, 16))
        self.assertTrue(any("unknown dependency CI-999" in error for error in errors))
        self.assertTrue(any("dependency cycle" in error for error in errors))

    def test_malformed_row_is_rejected(self) -> None:
        path = self.write(document(
            "| CI-001 | P0 | ready | evidence | missing columns | - | 2026-07-16 |"
        ))
        with self.assertRaisesRegex(ValueError, "malformed backlog row"):
            load_items(path)

    def test_escaped_pipe_is_supported(self) -> None:
        path = self.write(document(
            r"| CI-001 | P0 | ready | evidence | A \| B | - | 2026-07-16 | - | implement |"
        ))
        self.assertEqual("A | B", load_items(path)[0].outcome)

    def test_focus_limit_future_date_and_wip_are_rejected(self) -> None:
        rows = "\n".join(
            f"| CI-00{index} | P0 | in_progress | area | outcome {index} | - | 2026-07-17 | - | next |"
            for index in range(1, 5)
        )
        focus = "\n".join(f"- CI-00{index}: item" for index in range(1, 5))
        path = self.write(document(rows, focus))
        errors = validate(load_items(path), load_focus(path), dt.date(2026, 7, 16))
        self.assertTrue(any("WIP limit exceeded" in error for error in errors))
        self.assertTrue(any("Focus limit exceeded" in error for error in errors))
        self.assertTrue(any("future" in error for error in errors))

    def test_decisions_must_exist_and_link_both_directions(self) -> None:
        backlog = self.write(document(
            "| CI-001 | P0 | ready | evidence | exact quote | - | 2026-07-16 | D-20260716-01, D-20260716-99 | implement |"
        ))
        decisions_path = self.write("""# Decisions
## D-20260716-01 Existing decision

- Linked items: CI-002
""")
        decisions = load_decisions(decisions_path)
        errors = validate(
            load_items(backlog), load_focus(backlog), dt.date(2026, 7, 16), decisions,
        )
        self.assertTrue(any("does not link back" in error for error in errors))
        self.assertTrue(any("unknown Decision reference D-20260716-99" in error for error in errors))
        self.assertTrue(any("unknown linked item CI-002" in error for error in errors))

    def test_global_decision_link_is_valid(self) -> None:
        backlog = self.write(document(
            "| CI-001 | P0 | ready | evidence | exact quote | - | 2026-07-16 | D-20260716-01 | implement |"
        ))
        decisions_path = self.write("""# Decisions
## D-20260716-01 Global decision

- Linked items: *
""")
        errors = validate(
            load_items(backlog), load_focus(backlog), dt.date(2026, 7, 16),
            load_decisions(decisions_path),
        )
        self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()
