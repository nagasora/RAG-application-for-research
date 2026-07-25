import assert from "node:assert/strict";
import test from "node:test";

import { mindMapLayout } from "../lib/graph/mindmap-layout.mjs";

test("mind map layout safely handles empty nodes", () => {
  assert.deepEqual(mindMapLayout([], [], "missing"), []);
});

test("mind map layout falls back to the first node for an unknown root", () => {
  const nodes = [{ id: "first" }, { id: "second" }];
  const positioned = mindMapLayout(nodes, [{ source: "first", target: "second" }], "missing");

  assert.equal(positioned[0].id, "first");
  assert.equal(positioned[0].x, 465);
  assert.ok(positioned.every(node => Number.isFinite(node.x) && Number.isFinite(node.y)));
});

test("mind map layout assigns finite coordinates to disconnected nodes", () => {
  const positioned = mindMapLayout(
    [{ id: "root" }, { id: "linked" }, { id: "disconnected" }],
    [{ source: "root", target: "linked" }],
    "root",
  );

  assert.equal(positioned.length, 3);
  assert.ok(positioned.every(node => Number.isFinite(node.x) && Number.isFinite(node.y)));
  assert.ok(positioned.some(node => node.id === "disconnected"));
});
