import assert from "node:assert/strict";
import { describeWorkbenchConnection, formatWorkbenchAge } from "../src/app/workbenchPresentation.js";
import { cameraPresentation } from "../src/wasm-scene/cameraPresentation.js";
import { createInitialProductViewerState } from "../src/wasm-scene/productViewerState.js";
import { createViewerFrameTiming } from "../src/wasm-scene/viewerFrameTiming.js";
import { isInputEditingTarget, createDefaultViewerInputProviderRegistry } from "../src/input/viewerInputProvider.js";
import { createViewerKeyboardCapture } from "../src/input/keyboardInput.js";

const state = createInitialProductViewerState();
state.status = "ready";
state.qposStatus = "ready";
assert.equal(describeWorkbenchConnection(state, 0).label, "オフライン表示");
state.connectionStatus = "closed";
assert.equal(describeWorkbenchConnection(state, 0).label, "配信終了");
state.viewerTiming = { ...createViewerFrameTiming(() => 0).snapshot(), latestReceivedAtMs: 100 };
assert.equal(describeWorkbenchConnection(state, 200).label, "配信終了",
  "current closed state must not be overwritten by a previously received frame");
assert.doesNotMatch(describeWorkbenchConnection(state, 200).detail, /open/);
state.connectionStatus = "open";
assert.equal(describeWorkbenchConnection(state, 0).label, "接続済み・受信待ち");
assert.equal(describeWorkbenchConnection(state, 200).label, "受信中");
assert.equal(describeWorkbenchConnection(state, 1100).label, "受信中");
assert.equal(describeWorkbenchConnection(state, 1101).label, "更新停止");
assert.equal(describeWorkbenchConnection(state, 99).ageMs, null);
state.viewerTiming.latestIngressStatus = "parse_error";
assert.equal(describeWorkbenchConnection(state, 200).tone, "danger");
assert.equal(formatWorkbenchAge(null), "受信時刻なし");
assert.equal(formatWorkbenchAge(1234), "1.2 s");

const positions = [0, 0, 0, 0, 0, 0.5, 0.6, 0, 0.7];
const original = [...positions];
const iso = cameraPresentation(positions, "iso");
assert.ok(iso);
assert.deepEqual(iso.target, [0.3, 0, 0.6]);
const operator = cameraPresentation(positions, "operator");
assert.ok(operator);
assert.ok(operator.position[0] < operator.target[0], "operator camera must stay behind body +X forward");
assert.equal(operator.position[1], operator.target[1]);
assert.ok(operator.position[2] > operator.target[2], "operator camera should be slightly above the robot");
assert.deepEqual(operator.up, [0, 0, 1]);
const forward = operator.target.map((value, axis) => value - operator.position[axis]);
const screenRight = [
  forward[1] * operator.up[2] - forward[2] * operator.up[1],
  forward[2] * operator.up[0] - forward[0] * operator.up[2],
  forward[0] * operator.up[1] - forward[1] * operator.up[0],
];
assert.ok(Math.abs(screenRight[0]) < 1e-12 && screenRight[1] < 0 && Math.abs(screenRight[2]) < 1e-12,
  "operator screen-right must align with body/world -Y");
assert.deepEqual(cameraPresentation(positions, "top")?.up, [0, 1, 0]);
assert.deepEqual(positions, original);
assert.equal(cameraPresentation([0, 0, 0], "iso"), null);
assert.equal(cameraPresentation([0, 0, 0, NaN, 0, 0], "iso"), null);
for (const tagName of ["INPUT", "TEXTAREA", "SELECT", "BUTTON", "SUMMARY"]) assert.ok(isInputEditingTarget({ tagName }));
assert.ok(isInputEditingTarget({ isContentEditable: true }));
assert.ok(isInputEditingTarget({ closest: () => ({ tagName: "INPUT" }) }));
assert.equal(isInputEditingTarget({ tagName: "CANVAS" }), false);

const messages: any[] = [];
class FakeSocket {
  readyState = 1;
  addEventListener() {}
  removeEventListener() {}
  send(message: string) { messages.push(JSON.parse(message)); }
  close() { this.readyState = 3; }
}
const listeners = new Map<string, Function>();
let scheduled: (() => void) | null = null;
const documentLike = { activeElement: null as unknown, hasFocus: () => true, visibilityState: "visible",
  addEventListener: () => {}, removeEventListener: () => {} };
const provider = createDefaultViewerInputProviderRegistry().create("keyboard/v1", {
  window: {
    addEventListener: (name: string, callback: Function) => { listeners.set(name, callback); },
    removeEventListener: (name: string) => { listeners.delete(name); },
    requestAnimationFrame: (callback: FrameRequestCallback) => { scheduled = () => callback(0); return 1; },
    cancelAnimationFrame: () => { scheduled = null; },
  },
  document: documentLike,
  keyboardCapture: createViewerKeyboardCapture(),
  getGamepads: () => [], url: "ws://127.0.0.1:8766", keyboardWebSocketCtor: FakeSocket,
});
provider.start();
listeners.get("keydown")!({ code: "KeyW", repeat: false, target: { tagName: "CANVAS" }, preventDefault() {} });
assert.deepEqual(messages.at(-1).keyboard.active_key_codes, ["KeyW"]);
documentLike.activeElement = { tagName: "INPUT" };
(scheduled as unknown as Function)();
assert.deepEqual(messages.at(-1).keyboard.active_key_codes, []);
let prevented = false;
listeners.get("keydown")!({ code: "KeyA", repeat: false, target: documentLike.activeElement, preventDefault() { prevented = true; } });
assert.equal(prevented, false);
assert.deepEqual(messages.at(-1).keyboard.active_key_codes, []);
documentLike.activeElement = null;
(scheduled as unknown as Function)();
listeners.get("keydown")!({ code: "KeyD", repeat: false, target: { tagName: "CANVAS" }, preventDefault() {} });
assert.deepEqual(messages.at(-1).keyboard.active_key_codes, ["KeyD"]);
const beforeDispose = messages.length;
provider.dispose();
assert.equal(messages.length, beforeDispose, "dispose must not publish after lifecycle closure");
assert.equal(listeners.has("keydown"), false);
console.log("workbench presentation, camera and editing isolation tests passed");
