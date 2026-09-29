/// <reference types="node" />

import assert from "node:assert/strict";
import { createViewerFrameTiming } from "../src/wasm-scene/viewerFrameTiming.js";
import type { TransportPayloadV0 } from "../src/types/transportPayload.js";

function payload(frameIndex: number): TransportPayloadV0 {
  return {
    version: 0,
    frame_index: frameIndex,
    time_s: frameIndex / 60,
    qpos: [0, 0, 0, 0],
    qvel: [],
    bodies: [],
    sites: [],
    target_position_m: null,
    metadata: {},
  };
}

let nowMs = 0;
const timing = createViewerFrameTiming(() => nowMs);
timing.receive(payload(1), { receivedAtMs: 0, parseDurationMs: 0.2 });
timing.acceptLatestCandidate(payload(1), { receivedAtMs: 0, parseDurationMs: 0.2 });
timing.receive(payload(2), { receivedAtMs: 1, parseDurationMs: 0.3 });
timing.acceptLatestCandidate(payload(2), { receivedAtMs: 1, parseDurationMs: 0.3 });
timing.receive(payload(3), { receivedAtMs: 2, parseDurationMs: 0.4 });
timing.acceptLatestCandidate(payload(3), { receivedAtMs: 2, parseDurationMs: 0.4 });

const latest = timing.takeLatestCandidate();
assert.equal(latest?.payload.frame_index, 3, "a burst should retain only the latest candidate");
assert.equal(timing.snapshot().coalescedFrameCount, 2);
assert.equal(timing.snapshot().sceneAppliedFrameCount, 0);

if (latest === null) {
  throw new Error("latest candidate should exist");
}
nowMs = 12;
timing.recordSceneApplied(latest, 1.5);
assert.equal(timing.takeLatestCandidate(), null, "the same payload must not be applied twice");

let snapshot = timing.snapshot();
assert.equal(snapshot.receivedFrameCount, 3);
assert.equal(snapshot.compatibilityAcceptedFrameCount, 3);
assert.equal(snapshot.sceneAppliedFrameCount, 1);
assert.equal(snapshot.latestReceivedFrameIndex, 3);
assert.equal(snapshot.latestCompatibilityAcceptedFrameIndex, 3);
assert.equal(snapshot.latestSceneAppliedFrameIndex, 3);
assert.equal(snapshot.receivedToAppliedFrameDistance, 0);
assert.equal(snapshot.receiveToApplyAgeMsP95, 10);
assert.equal(snapshot.sceneApplyDurationMsP95, 1.5);

timing.receive(payload(4), { receivedAtMs: 13, parseDurationMs: 0.2 });
timing.acceptLatestCandidate(payload(4), { receivedAtMs: 13, parseDurationMs: 0.2 });
timing.receive(payload(5), { receivedAtMs: 14, parseDurationMs: 0.2 });
timing.recordCompatibilityInvalidIngress();
// Invalid ingress is a barrier: the older compatible pending frame is not
// applied on the next render cadence, while the last applied scene stays 3.
assert.equal(timing.takeLatestCandidate(), null);
snapshot = timing.snapshot();
assert.equal(snapshot.latestCompatibilityAcceptedFrameIndex, 4);
assert.equal(snapshot.latestSceneAppliedFrameIndex, 3);
assert.equal(snapshot.receivedToAppliedFrameDistance, 2);
assert.equal(snapshot.compatibilityInvalidFrameCount, 1);
assert.equal(snapshot.latestIngressStatus, "compatibility_invalid");

timing.receive(payload(6), { receivedAtMs: 15, parseDurationMs: 0.1 });
timing.acceptLatestCandidate(payload(6), { receivedAtMs: 15, parseDurationMs: 0.1 });
timing.recordParseError();
assert.equal(timing.snapshot().parseErrorCount, 1);
assert.equal(timing.snapshot().latestIngressStatus, "parse_error");
assert.equal(timing.takeLatestCandidate(), null, "parse errors must invalidate pending valid frames");

timing.receive(payload(7), { receivedAtMs: 16, parseDurationMs: 0.1 });
timing.acceptLatestCandidate(payload(7), { receivedAtMs: 16, parseDurationMs: 0.1 });
const recovered = timing.takeLatestCandidate();
assert.equal(recovered?.payload.frame_index, 7, "only a new valid ingress may recover after invalid input");
if (recovered === null) {
  throw new Error("recovery candidate should exist");
}
nowMs = 18;
timing.recordSceneApplied(recovered, 0.5);
assert.equal(timing.snapshot().latestSceneAppliedFrameIndex, 7);

timing.dispose();
timing.receive(payload(8), { receivedAtMs: 19, parseDurationMs: 0.1 });
timing.acceptLatestCandidate(payload(8), { receivedAtMs: 19, parseDurationMs: 0.1 });
assert.equal(timing.takeLatestCandidate(), null, "dispose should clear and reject pending payloads");
assert.equal(timing.snapshot().latestReceivedFrameIndex, 7);

console.log("viewer frame timing tests passed");


// 集計は標本更新時だけ再計算する。通知回数の増加がsort回数を増やさない。
const cachedTiming = createViewerFrameTiming(() => nowMs);
const originalSort = Array.prototype.sort;
let sortCalls = 0;
try {
  Array.prototype.sort = function(compareFn) { sortCalls += 1; return originalSort.call(this, compareFn); };
  cachedTiming.receive(payload(1), { receivedAtMs: 0, parseDurationMs: 1 });
  const first = cachedTiming.snapshot();
  const beforeRepeat = sortCalls;
  for (let i = 0; i < 100; i += 1) {
    cachedTiming.recordUiStateUpdate();
    assert.equal(cachedTiming.snapshot().parseDurationMsP95, first.parseDurationMsP95);
  }
  assert.equal(sortCalls, beforeRepeat);
  cachedTiming.receive(payload(2), { receivedAtMs: 0, parseDurationMs: 2 });
  assert.equal(cachedTiming.snapshot().parseDurationMsP95, 2);
  assert.equal(sortCalls, beforeRepeat + 1);
} finally {
  Array.prototype.sort = originalSort;
}
for (let i = 0; i < 600; i += 1) cachedTiming.receive(payload(i + 3), { receivedAtMs: 0, parseDurationMs: i });
const bounded = cachedTiming.snapshot();
// 最後の512値 = 88..599。以前と同じnearest-rank、同じ保持件数。
assert.equal(bounded.parseDurationMsP50, 343);
assert.equal(bounded.parseDurationMsP95, 574);
assert.equal(bounded.parseDurationMsMax, 599);
