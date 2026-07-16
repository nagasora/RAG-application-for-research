from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKLOG = ROOT / "docs" / "CONTINUOUS_IMPROVEMENT.md"
START = "<!-- BACKLOG_TABLE_START -->"
END = "<!-- BACKLOG_TABLE_END -->"
FOCUS_START = "<!-- FOCUS_START -->"
FOCUS_END = "<!-- FOCUS_END -->"
VALID_PRIORITIES = {"P0", "P1", "P2"}
VALID_STATES = {
    "intake", "ready", "in_progress", "validating", "blocked", "done", "retired",
}
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}
STATE_ORDER = {
    "in_progress": 0, "validating": 1, "ready": 2, "blocked": 3,
    "intake": 4, "done": 5, "retired": 6,
}

@dataclass(frozen=True)
class Item:
    item_id: str
    priority: str
    state: str
    area: str
    outcome: str
    dependencies: tuple[str, ...]
    evidence_reviewed: str
    decisions: tuple[str, ...]
    next_action: str


@dataclass(frozen=True)
class Decision:
    decision_id: str
    linked_items: tuple[str, ...]


def _cells(line: str) -> list[str]:
    return [
        cell.replace(r"\|", "|").strip()
        for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))
    ]


def load_focus(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if FOCUS_START not in text or FOCUS_END not in text:
        raise ValueError("focus markers are missing")
    section = text.split(FOCUS_START, 1)[1].split(FOCUS_END, 1)[0]
    return re.findall(r"(?m)^- (CI-\d{3}):", section)


def load_decisions(path: Path) -> dict[str, Decision]:
    text = path.read_text(encoding="utf-8")
    matches = list(re.finditer(r"(?m)^## (D-\d{8}-\d{2})\b.*$", text))
    decisions: dict[str, Decision] = {}
    for index, match in enumerate(matches):
        decision_id = match.group(1)
        if decision_id in decisions:
            raise ValueError(f"duplicate Decision ID: {decision_id}")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.end():end]
        linked_match = re.search(r"(?m)^- Linked items:\s*(.+?)\s*$", block)
        if linked_match is None:
            raise ValueError(f"{decision_id}: Linked items is missing")
        linked_items = tuple(
            value.strip() for value in linked_match.group(1).split(",") if value.strip()
        )
        if not linked_items:
            raise ValueError(f"{decision_id}: Linked items is empty")
        decisions[decision_id] = Decision(decision_id, linked_items)
    if not decisions:
        raise ValueError("no Decision entries were found")
    return decisions


def load_items(path: Path) -> list[Item]:
    text = path.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise ValueError("backlog table markers are missing")
    table = text.split(START, 1)[1].split(END, 1)[0]
    items: list[Item] = []
    for line in table.splitlines():
        if not line.lstrip().startswith("| CI-"):
            continue
        cells = _cells(line)
        if len(cells) != 9 or not re.fullmatch(r"CI-\d{3}", cells[0]):
            raise ValueError(f"malformed backlog row: {line.strip()}")
        dependencies = tuple(
            dependency.strip()
            for dependency in cells[5].split(",")
            if dependency.strip() and dependency.strip() != "-"
        )
        decisions = tuple(
            decision.strip()
            for decision in cells[7].split(",")
            if decision.strip() and decision.strip() != "-"
        )
        items.append(Item(
            item_id=cells[0], priority=cells[1], state=cells[2], area=cells[3],
            outcome=cells[4], dependencies=dependencies,
            evidence_reviewed=cells[6], decisions=decisions, next_action=cells[8],
        ))
    if not items:
        raise ValueError("no backlog items were found")
    return items


def validate(
    items: list[Item], focus: list[str], today: dt.date | None = None,
    decisions: dict[str, Decision] | None = None,
) -> list[str]:
    errors: list[str] = []
    today = today or dt.date.today()
    ids = [item.item_id for item in items]
    known = set(ids)
    duplicates = sorted({item_id for item_id in ids if ids.count(item_id) > 1})
    if duplicates:
        errors.append(f"duplicate IDs: {', '.join(duplicates)}")

    for item in items:
        if item.priority not in VALID_PRIORITIES:
            errors.append(f"{item.item_id}: invalid priority {item.priority!r}")
        if item.state not in VALID_STATES:
            errors.append(f"{item.item_id}: invalid state {item.state!r}")
        if not item.area or not item.outcome or not item.next_action:
            errors.append(f"{item.item_id}: area, outcome, and next action are required")
        try:
            reviewed = dt.date.fromisoformat(item.evidence_reviewed)
            if reviewed > today:
                errors.append(f"{item.item_id}: Evidence reviewed date is in the future")
        except ValueError:
            errors.append(f"{item.item_id}: invalid Evidence reviewed date {item.evidence_reviewed!r}")
        for decision in item.decisions:
            if not re.fullmatch(r"D-\d{8}-\d{2}", decision):
                errors.append(f"{item.item_id}: invalid Decision reference {decision!r}")
            elif decisions is not None and decision not in decisions:
                errors.append(f"{item.item_id}: unknown Decision reference {decision}")
            elif decisions is not None:
                linked = decisions[decision].linked_items
                if "*" not in linked and item.item_id not in linked:
                    errors.append(f"{item.item_id}: {decision} does not link back to this item")
        for dependency in item.dependencies:
            if dependency not in known:
                errors.append(f"{item.item_id}: unknown dependency {dependency}")
            if dependency == item.item_id:
                errors.append(f"{item.item_id}: item cannot depend on itself")

    in_progress = [item.item_id for item in items if item.state == "in_progress"]
    if len(in_progress) > 3:
        errors.append(f"WIP limit exceeded: {', '.join(in_progress)}")
    if not focus:
        errors.append("Focus must contain at least one item")
    if len(focus) > 3:
        errors.append(f"Focus limit exceeded: {', '.join(focus)}")
    if len(focus) != len(set(focus)):
        errors.append("Focus contains duplicate IDs")
    for item_id in focus:
        if item_id not in known:
            errors.append(f"Focus contains unknown item {item_id}")

    if decisions is not None:
        items_by_id = {item.item_id: item for item in items}
        for decision in decisions.values():
            if "*" in decision.linked_items and decision.linked_items != ("*",):
                errors.append(f"{decision.decision_id}: '*' must be the only Linked items value")
            for item_id in decision.linked_items:
                if item_id == "*":
                    continue
                if not re.fullmatch(r"CI-\d{3}", item_id):
                    errors.append(f"{decision.decision_id}: invalid linked item {item_id!r}")
                elif item_id not in items_by_id:
                    errors.append(f"{decision.decision_id}: unknown linked item {item_id}")
                elif decision.decision_id not in items_by_id[item_id].decisions:
                    errors.append(f"{decision.decision_id}: {item_id} does not link back to this Decision")

    graph = {item.item_id: item.dependencies for item in items}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(item_id: str, path: tuple[str, ...]) -> None:
        if item_id in visiting:
            cycle_start = path.index(item_id) if item_id in path else 0
            cycle = path[cycle_start:] + (item_id,)
            errors.append(f"dependency cycle: {' -> '.join(cycle)}")
            return
        if item_id in visited:
            return
        visiting.add(item_id)
        for dependency in graph.get(item_id, ()):
            if dependency in graph:
                visit(dependency, path + (item_id,))
        visiting.remove(item_id)
        visited.add(item_id)

    for item_id in graph:
        visit(item_id, ())
    return errors


def eligible_items(items: list[Item]) -> list[Item]:
    states = {item.item_id: item.state for item in items}
    candidates = [
        item for item in items
        if item.state == "ready"
        and all(states.get(dependency) == "done" for dependency in item.dependencies)
    ]
    return sorted(candidates, key=lambda item: (PRIORITY_ORDER[item.priority], item.item_id))


def print_item(item: Item) -> None:
    dependencies = ", ".join(item.dependencies) or "-"
    print(f"{item.item_id} [{item.priority}/{item.state}] {item.area}")
    print(f"  Outcome: {item.outcome}")
    print(f"  Depends on: {dependencies}")
    print(f"  Next: {item.next_action}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Validate and inspect the PaperPilot improvement backlog.")
    parser.add_argument("command", choices=("check", "next", "list"))
    parser.add_argument("--file", type=Path, default=DEFAULT_BACKLOG)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--state", choices=sorted(VALID_STATES))
    parser.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    args = parser.parse_args()

    try:
        items = load_items(args.file)
        focus = load_focus(args.file)
        decision_path = args.decisions or args.file.with_name("DECISIONS.md")
        decisions = load_decisions(decision_path)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    errors = validate(items, focus, args.today, decisions)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if args.command == "check":
        counts = {state: sum(item.state == state for item in items) for state in VALID_STATES}
        summary = ", ".join(f"{state}={counts[state]}" for state in sorted(counts, key=STATE_ORDER.get))
        print(f"OK: {len(items)} items; {summary}")
        stale = [
            item.item_id for item in items
            if (args.today - dt.date.fromisoformat(item.evidence_reviewed)).days > 90
            and item.state not in {"done", "retired"}
        ]
        if stale:
            print(f"WARNING: evidence review is older than 90 days: {', '.join(stale)}")
        return 0

    if args.command == "next":
        in_progress = [item.item_id for item in items if item.state == "in_progress"]
        if len(in_progress) >= 3:
            print(f"WIP limit reached ({', '.join(in_progress)}). Finish, validate, or block existing work first.")
            return 0
        candidates = eligible_items(items)
        if not candidates:
            print("No ready items have all dependencies completed.")
            return 0
        focused = [item for item in candidates if item.item_id in focus]
        selected = focused or candidates
        print("Focus candidates, ordered by priority:" if focused else "No Focus item is ready; other ready candidates:")
        for item in selected:
            print_item(item)
        return 0

    selected = [item for item in items if args.state is None or item.state == args.state]
    for item in sorted(selected, key=lambda value: (
        STATE_ORDER[value.state], PRIORITY_ORDER[value.priority], value.item_id,
    )):
        print_item(item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
