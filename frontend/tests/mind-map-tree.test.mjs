import assert from "node:assert/strict";
import test from "node:test";

import {
  mindMapDescendantIds,
  mindMapKeyboardTarget,
  visibleMindMap,
} from "../lib/mind-map-tree.mjs";

const nodes = [
  { id:"root", parent_id:null, order_index:0 },
  { id:"a", parent_id:"root", order_index:0 },
  { id:"b", parent_id:"root", order_index:1 },
  { id:"a1", parent_id:"a", order_index:0 },
  { id:"orphan", parent_id:"missing", order_index:0 },
];

test("visibleMindMap is total for empty and dangling trees", () => {
  assert.deepEqual(visibleMindMap([]), { nodes:[], edges:[] });
  const visible = visibleMindMap(nodes);
  assert.equal(visible.nodes.length, 5);
  assert.equal(visible.edges.length, 3);
  assert.ok(visible.nodes.some(node => node.id === "orphan"));
});

test("collapsed nodes hide every descendant but keep siblings", () => {
  assert.deepEqual(mindMapDescendantIds(nodes, "a"), ["a1"]);
  const visible = visibleMindMap(nodes, ["a"]);
  assert.deepEqual(visible.nodes.map(node => node.id), ["root", "a", "b", "orphan"]);
  assert.ok(visible.edges.every(edge => edge.target !== "a1"));
});

test("keyboard navigation follows parent, first child and sibling order", () => {
  assert.equal(mindMapKeyboardTarget(nodes, "a", "ArrowLeft"), "root");
  assert.equal(mindMapKeyboardTarget(nodes, "a", "ArrowRight"), "a1");
  assert.equal(mindMapKeyboardTarget(nodes, "a", "ArrowDown"), "b");
  assert.equal(mindMapKeyboardTarget(nodes, "b", "ArrowUp"), "a");
  assert.equal(mindMapKeyboardTarget(nodes, "orphan", "ArrowLeft"), null);
});
