import assert from "node:assert/strict";
import {
  createDefaultViewerInputProviderRegistry,
  ViewerInputProviderRegistry,
  type ViewerInputProvider,
  type ViewerInputProviderOptions,
} from "../src/input/viewerInputProvider.js";
import { createViewerInputLifecycle, readViewerInputSelection, type ViewerInputLifecycleOptions } from "../src/app/viewerInputLifecycle.js";

function testDefaultRegistryIsKnownAndVersioned(): void {
  const registry = createDefaultViewerInputProviderRegistry();
  assert.deepEqual(registry.ids(), ["keyboard/v1", "gamepad/v1"]);
  assert.equal(registry.resolve("keyboard/v1").rawSampleSchema, "viewer_keyboard_sample/v1");
  assert.equal(registry.resolve("gamepad/v1").rawSampleSchema, "viewer_gamepad_sample/v1");
}

function testRegistryFailsClosedForUnknownAndDuplicateIds(): void {
  const registry = createDefaultViewerInputProviderRegistry();
  assert.throws(() => registry.resolve("unknown/v1" as never), /unknown viewer input provider id/);
  assert.throws(
    () => new ViewerInputProviderRegistry([
      registry.resolve("keyboard/v1"),
      registry.resolve("keyboard/v1"),
    ]),
    /duplicate viewer input provider id/,
  );
}

function testLifecycleActivatesAndDisposesSelectedProvider(): void {
  const calls: string[] = [];
  const provider: ViewerInputProvider = {
    id: "keyboard/v1",
    rawSampleSchema: "viewer_keyboard_sample/v1",
    start: () => calls.push("start"),
    dispose: () => calls.push("dispose"),
  };
  const registry = new ViewerInputProviderRegistry([
    {
      id: "keyboard/v1",
      rawSampleSchema: "viewer_keyboard_sample/v1",
      create: (_options: ViewerInputProviderOptions) => provider,
    },
  ]);
  const lifecycle = createViewerInputLifecycle({
    providerRegistry: registry,
    providerIds: ["keyboard/v1"],
  } as unknown as ViewerInputLifecycleOptions);

  lifecycle.setLiveInputEnabled(true);
  lifecycle.setLiveInputEnabled(false);
  lifecycle.dispose();
  assert.deepEqual(calls, ["start", "dispose"]);
}

function testLifecycleFailsClosedForDuplicateSelection(): void {
  const lifecycle = createViewerInputLifecycle({
    providerRegistry: createDefaultViewerInputProviderRegistry(),
    providerIds: ["keyboard/v1", "keyboard/v1"],
  } as unknown as ViewerInputLifecycleOptions);
  assert.throws(() => lifecycle.setLiveInputEnabled(true), /duplicate viewer input provider selection/);
  lifecycle.dispose();
}

testDefaultRegistryIsKnownAndVersioned();
testRegistryFailsClosedForUnknownAndDuplicateIds();
testLifecycleActivatesAndDisposesSelectedProvider();
testLifecycleFailsClosedForDuplicateSelection();

console.log("viewer input provider registry and lifecycle tests passed");

assert.deepEqual(readViewerInputSelection("?inputProvider=gamepad%2Fv1").providerIds, ["gamepad/v1"]);
assert.deepEqual(readViewerInputSelection("?inputProvider=keyboard%2Fv1").providerIds, ["keyboard/v1"]);
assert.deepEqual(readViewerInputSelection("?inputProvider=none").providerIds, []);
assert.equal(readViewerInputSelection("").providerIds.length, 2);
for (const search of ["?inputProvider=unknown", "?inputProvider=", "?inputProvider=keyboard/v1&inputProvider=gamepad/v1"]) {
  const result = readViewerInputSelection(search);
  assert.deepEqual(result.providerIds, []);
  assert.notEqual(result.error, null);
}

// mode表示前の初回送信にもsession IDが必要。neutralHeartbeat=falseでも付与する。
{
  const messages: Array<Record<string, any>> = [];
  class Socket {
    readyState = 1;
    constructor(_url: string) {}
    addEventListener(_type: string, _listener: (event: Event) => void): void {}
    removeEventListener(_type: string, _listener: (event: Event) => void): void {}
    send(value: string): void { messages.push(JSON.parse(value)); }
    close(): void { this.readyState = 3; }
  }
  const options = {
    url: "ws://127.0.0.1:8766", gamepadWebSocketCtor: Socket,
    gamepadNeutralHeartbeat: false,
    window: { requestAnimationFrame: () => 1, cancelAnimationFrame: () => {},
              addEventListener: () => {}, removeEventListener: () => {} },
    document: { visibilityState: "visible", hasFocus: () => true,
                addEventListener: () => {}, removeEventListener: () => {} },
    getGamepads: () => [{ connected: true, id: "synthetic", index: 0, axes: [0,0,0,0],
                         buttons: Array.from({length:6}, () => ({pressed:false,value:0})) }],
  } as unknown as ViewerInputProviderOptions;
  const registry = createDefaultViewerInputProviderRegistry();
  const first = registry.create("gamepad/v1", options);
  first.start();
  assert.equal(messages.length, 1);
  assert.equal(messages[0].sequence, 0);
  const id = messages[0].metadata.viewer_provider_session_id;
  assert.match(id, /^[a-zA-Z0-9_-]{1,128}$/);
  first.dispose();
  const second = registry.create("gamepad/v1", options);
  second.start();
  assert.equal(messages.length, 2);
  assert.equal(messages[1].sequence, 0);
  assert.notEqual(messages[1].metadata.viewer_provider_session_id, id);
  second.dispose();
}
