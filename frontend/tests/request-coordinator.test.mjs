import assert from "node:assert/strict";
import test from "node:test";

import { LatestRequestCoordinator } from "../lib/api/request-coordinator.mjs";

test("a newer request aborts and invalidates an older workspace request", () => {
  const coordinator = new LatestRequestCoordinator();
  const oldWorkspace = coordinator.begin();
  const newWorkspace = coordinator.begin();

  assert.equal(oldWorkspace.signal.aborted, true);
  assert.equal(oldWorkspace.isCurrent(), false);
  assert.equal(newWorkspace.signal.aborted, false);
  assert.equal(newWorkspace.isCurrent(), true);
});

test("a late old response cannot overwrite the latest workspace state", async () => {
  const coordinator = new LatestRequestCoordinator();
  const commits = [];
  let releaseOld;
  const oldResponse = new Promise(resolve => { releaseOld = resolve; });
  const oldRequest = coordinator.begin();
  const oldCommit = oldResponse.then(value => { if (oldRequest.isCurrent()) commits.push(value); });

  const newRequest = coordinator.begin();
  if (newRequest.isCurrent()) commits.push("new-workspace");
  releaseOld("old-workspace");
  await oldCommit;

  assert.deepEqual(commits, ["new-workspace"]);
});

test("cancel invalidates an in-flight request during unmount or retry", () => {
  const coordinator = new LatestRequestCoordinator();
  const request = coordinator.begin();
  coordinator.cancel();

  assert.equal(request.signal.aborted, true);
  assert.equal(request.isCurrent(), false);
});
