import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ACTIVITY_REFRESH_INTERVAL_MS,
  applyActivityRefresh,
  startActivityRefresh,
} from "../src/activityRefresh.js";

class FakeTarget {
  listeners = new Map();

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  removeEventListener(type, listener) {
    if (this.listeners.get(type) === listener) this.listeners.delete(type);
  }

  dispatch(type) {
    this.listeners.get(type)?.();
  }
}

const flushRefresh = () => new Promise((resolve) => setImmediate(resolve));

test("new activity becomes visible on the next timer refresh and focus", async () => {
  const windowTarget = new FakeTarget();
  const documentTarget = new FakeTarget();
  documentTarget.visibilityState = "visible";
  const previousActivity = [{ push_id: 1 }];
  let availableActivity = previousActivity;
  let visibleActivity = previousActivity;
  let intervalCallback;
  let intervalMs;

  const stop = startActivityRefresh({
    repository: "owner/repo",
    windowTarget,
    documentTarget,
    setIntervalFn: (callback, delay) => {
      intervalCallback = callback;
      intervalMs = delay;
      return 9;
    },
    clearIntervalFn: (id) => assert.equal(id, 9),
    refresh: async (repository) => {
      assert.equal(repository, "owner/repo");
      visibleActivity = availableActivity;
    },
  });

  assert.equal(intervalMs, ACTIVITY_REFRESH_INTERVAL_MS);
  availableActivity = [...previousActivity, { push_id: 2 }];
  intervalCallback();
  await flushRefresh();
  assert.deepEqual(visibleActivity, [{ push_id: 1 }, { push_id: 2 }]);

  availableActivity = [...availableActivity, { push_id: 3 }];
  windowTarget.dispatch("focus");
  await flushRefresh();
  assert.deepEqual(visibleActivity, [
    { push_id: 1 },
    { push_id: 2 },
    { push_id: 3 },
  ]);

  stop();
  assert.equal(windowTarget.listeners.has("focus"), false);
  assert.equal(documentTarget.listeners.has("visibilitychange"), false);
});

test("visibility refresh runs only when the tab becomes visible", async () => {
  const windowTarget = new FakeTarget();
  const documentTarget = new FakeTarget();
  documentTarget.visibilityState = "hidden";
  let refreshCount = 0;
  const stop = startActivityRefresh({
    repository: "owner/repo",
    windowTarget,
    documentTarget,
    setIntervalFn: () => 1,
    clearIntervalFn: () => {},
    refresh: async () => {
      refreshCount += 1;
    },
  });

  documentTarget.dispatch("visibilitychange");
  await flushRefresh();
  assert.equal(refreshCount, 0);
  documentTarget.visibilityState = "visible";
  documentTarget.dispatch("visibilitychange");
  await flushRefresh();
  assert.equal(refreshCount, 1);
  stop();
});

test("refresh preserves the selected activity item while it remains available", () => {
  const rows = [{ push_id: 3 }, { push_id: 2 }, { push_id: 1 }];
  assert.deepEqual(applyActivityRefresh(rows, 1), {
    pushes: rows,
    selectedPushId: 1,
  });
  assert.equal(applyActivityRefresh(rows, 99).selectedPushId, 3);
});

test("overlapping timer and focus refreshes are coalesced", async () => {
  const windowTarget = new FakeTarget();
  const documentTarget = new FakeTarget();
  documentTarget.visibilityState = "visible";
  let intervalCallback;
  let resolveRefresh;
  let refreshCount = 0;
  const stop = startActivityRefresh({
    repository: "owner/repo",
    windowTarget,
    documentTarget,
    setIntervalFn: (callback) => {
      intervalCallback = callback;
      return 1;
    },
    clearIntervalFn: () => {},
    refresh: () => {
      refreshCount += 1;
      return new Promise((resolve) => {
        resolveRefresh = resolve;
      });
    },
  });

  intervalCallback();
  await Promise.resolve();
  windowTarget.dispatch("focus");
  intervalCallback();
  assert.equal(refreshCount, 1);
  resolveRefresh();
  await flushRefresh();
  stop();
});
