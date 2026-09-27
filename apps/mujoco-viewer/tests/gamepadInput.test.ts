import assert from "node:assert/strict";
import {
  buildViewerGamepadControlMessage,
  createViewerGamepadPublicationController,
  createViewerGamepadControlSender,
  DEFAULT_VIEWER_GAMEPAD_HEARTBEAT_INTERVAL_MS,
  normalizeViewerGamepadAxis,
  sampleViewerGamepadSnapshot,
  type ViewerGamepadControlSender,
  type ViewerGamepadControlSocketLike,
  type ViewerGamepadLike,
} from "../src/input/gamepadInput.js";
import { parseViewerControlMessageJson } from "../src/transport/viewerControlMessage.js";

class FakeWebSocket implements ViewerGamepadControlSocketLike {
  public readonly sentMessages: string[] = [];
  public readonly openListeners: Array<(event: Event) => void> = [];
  public readonly closeListeners: Array<(event: Event) => void> = [];
  public readonly errorListeners: Array<(event: Event) => void> = [];
  public readyState = 0;
  public closed = false;

  constructor(public readonly url: string) {}

  addEventListener(type: "open", listener: (event: Event) => void): void;
  addEventListener(type: "close", listener: (event: Event) => void): void;
  addEventListener(type: "error", listener: (event: Event) => void): void;
  addEventListener(type: "open" | "close" | "error", listener: (event: Event) => void): void {
    if (type === "open") {
      this.openListeners.push(listener);
      return;
    }
    if (type === "close") {
      this.closeListeners.push(listener);
      return;
    }

    this.errorListeners.push(listener);
  }

  removeEventListener(type: "open", listener: (event: Event) => void): void;
  removeEventListener(type: "close", listener: (event: Event) => void): void;
  removeEventListener(type: "error", listener: (event: Event) => void): void;
  removeEventListener(type: "open" | "close" | "error", listener: (event: Event) => void): void {
    const listeners =
      type === "open" ? this.openListeners : type === "close" ? this.closeListeners : this.errorListeners;
    const index = listeners.indexOf(listener);
    if (index >= 0) {
      listeners.splice(index, 1);
    }
  }

  send(message: string): void {
    if (this.readyState !== 1) {
      throw new Error("socket is not open");
    }

    this.sentMessages.push(message);
  }

  close(): void {
    this.closed = true;
    this.readyState = 3;
    for (const listener of [...this.closeListeners]) {
      listener(new Event("close"));
    }
  }

  emitOpen(): void {
    this.readyState = 1;
    for (const listener of [...this.openListeners]) {
      listener(new Event("open"));
    }
  }
}

class FakeTimer {
  private nextId = 1;
  private readonly callbacks = new Map<number, { callback: () => void; delayMs: number }>();

  public readonly setTimeoutFn = (callback: () => void, delayMs: number): ReturnType<typeof setTimeout> => {
    const id = this.nextId;
    this.nextId += 1;
    this.callbacks.set(id, { callback, delayMs });
    return id as unknown as ReturnType<typeof setTimeout>;
  };

  public readonly clearTimeoutFn = (timeoutId: ReturnType<typeof setTimeout>): void => {
    this.callbacks.delete(timeoutId as unknown as number);
  };

  get pendingCount(): number {
    return this.callbacks.size;
  }

  get nextDelayMs(): number | null {
    const entry = this.callbacks.values().next().value as { callback: () => void; delayMs: number } | undefined;
    return entry?.delayMs ?? null;
  }

  runNext(): void {
    const entry = this.callbacks.entries().next().value as [number, { callback: () => void; delayMs: number }] | undefined;
    if (entry === undefined) {
      throw new Error("expected a pending timer");
    }

    this.callbacks.delete(entry[0]);
    entry[1].callback();
  }
}

const ACTIVE_SNAPSHOT: ViewerGamepadLike = {
  connected: true,
  index: 0,
  id: "Pad",
  axes: [0.5],
  buttons: [{ pressed: false, value: 0 }],
};

function activeSnapshot() {
  return sampleViewerGamepadSnapshot([ACTIVE_SNAPSHOT]);
}

function zeroSnapshot() {
  return sampleViewerGamepadSnapshot([
    {
      ...ACTIVE_SNAPSHOT,
      axes: [0],
    },
  ]);
}

function testNormalizeViewerGamepadAxis(): void {
  assert.equal(normalizeViewerGamepadAxis(0.05), 0);
  assert.equal(normalizeViewerGamepadAxis(0.5), 0.4444444444444445);
  assert.equal(normalizeViewerGamepadAxis(-1.25), -1);
}

function testSampleViewerGamepadSnapshotReturnsZeroSnapshotWhenNoPad(): void {
  const snapshot = sampleViewerGamepadSnapshot(null);

  assert.deepEqual(snapshot, {
    connected: false,
    axes: [],
    buttons: [],
    stale: true,
    zero_state: true,
  });
}

function testSampleViewerGamepadSnapshotUsesFirstConnectedPad(): void {
  const connectedPad: ViewerGamepadLike = {
    connected: true,
    index: 2,
    id: "Browser Gamepad",
    axes: [0.05, 0.8, -1.2],
    buttons: [
      { pressed: true, value: 0.75 },
      { pressed: false },
    ],
  };
  const disconnectedPad: ViewerGamepadLike = {
    connected: false,
    index: 0,
    id: "Ignored",
    axes: [1],
    buttons: [{ pressed: true, value: 1 }],
  };

  const snapshot = sampleViewerGamepadSnapshot([disconnectedPad, connectedPad], { deadzone: 0.1 });

  assert.deepEqual(snapshot, {
    connected: true,
    index: 2,
    id: "Browser Gamepad",
    raw_axes: [0.05, 0.8, -1.2],
    axes: [0, 0.7777777777777778, -1],
    buttons: [
      { pressed: true, value: 0.75 },
      { pressed: false, value: null },
    ],
    stale: false,
    zero_state: false,
  });
}

function testRawAxisBelowLegacyDeadzonePublishesZeroStateWithoutHeartbeat(): void {
  const snapshot = sampleViewerGamepadSnapshot([
    {
      connected: true,
      index: 0,
      id: "Small-axis pad",
      axes: [0.05],
      buttons: [{ pressed: false, value: 0 }],
    },
  ]);

  assert.deepEqual(snapshot.raw_axes, [0.05]);
  assert.deepEqual(snapshot.axes, [0]);
  assert.equal(snapshot.zero_state, true);

  const timer = new FakeTimer();
  const published: ReturnType<typeof activeSnapshot>[] = [];
  const controller = createViewerGamepadPublicationController({
    publish: (sample) => published.push(sample),
    setTimeoutFn: timer.setTimeoutFn,
    clearTimeoutFn: timer.clearTimeoutFn,
  });
  controller.update(snapshot);
  assert.equal(published.length, 1, "the initial zero-state transition must be published");
  assert.equal(timer.pendingCount, 0, "legacy zero state must stop the heartbeat");
  controller.dispose();
}

function testButtonOnlyInputRemainsAProviderActivitySample(): void {
  const snapshot = sampleViewerGamepadSnapshot([
    {
      connected: true,
      index: 0,
      id: "Button-only pad",
      axes: [0, 0, 0],
      buttons: [{ pressed: true, value: 1 }],
    },
  ]);

  assert.deepEqual(snapshot.raw_axes, [0, 0, 0]);
  assert.equal(snapshot.zero_state, false);

  const timer = new FakeTimer();
  const published: ReturnType<typeof activeSnapshot>[] = [];
  const controller = createViewerGamepadPublicationController({
    publish: (sample) => published.push(sample),
    setTimeoutFn: timer.setTimeoutFn,
    clearTimeoutFn: timer.clearTimeoutFn,
  });
  controller.update(snapshot);
  assert.equal(published.length, 1);
  assert.equal(timer.pendingCount, 1);
  controller.dispose();
}

function testBuildViewerGamepadControlMessageBuildsSchemaPayload(): void {
  const message = buildViewerGamepadControlMessage(
    {
      connected: true,
      index: 0,
      id: "Pad",
      raw_axes: [0.25, 0.5],
      axes: [0, 0.5],
      buttons: [
        { pressed: true, value: 1 },
        { pressed: false, value: null },
      ],
      stale: false,
      zero_state: false,
    },
    42.5,
    { sequence: 3 },
  );

  assert.deepEqual(message, {
    type: "viewer_control_message",
    timestamp_s: 42.5,
    source_kind: "gamepad",
    provider_id: "gamepad/v1",
    provider_schema: "viewer_gamepad_sample/v1",
    metadata: {
      intent_kind: "local_endpoint_velocity",
      input_continuity: "continuous",
      source_kind: "viewer_gamepad",
      control_frame: "world",
      local_endpoint_speed_m_s: 0.1,
      local_endpoint_max_delta_m: 0.03,
    },
    sequence: 3,
    gamepad: {
      connected: true,
      index: 0,
      id: "Pad",
      raw_axes: [0.25, 0.5],
      axes: [0, 0.5],
      buttons: [
        { pressed: true, value: 1 },
        { pressed: false },
      ],
      stale: false,
      zero_state: false,
    },
  });

  const parsed = parseViewerControlMessageJson(JSON.stringify(message));
  assert.deepEqual(parsed, message);
}

function testViewerGamepadControlSenderQueuesUntilOpen(): void {
  let createdSocket: FakeWebSocket | null = null;
  const sender: ViewerGamepadControlSender = createViewerGamepadControlSender({
    url: "ws://127.0.0.1:8766",
    WebSocketCtor: class extends FakeWebSocket {
      constructor(url: string) {
        super(url);
        createdSocket = this;
      }
    },
  });

  sender.publish(
    {
      connected: true,
      axes: [0.1],
      buttons: [{ pressed: true, value: 1 }],
      stale: false,
      zero_state: false,
    },
    7.5,
  );

  if (createdSocket === null) {
    throw new Error("expected a socket to be created");
  }

  const socket = createdSocket as FakeWebSocket;
  assert.equal(socket.sentMessages.length, 0);

  socket.emitOpen();
  assert.equal(socket.sentMessages.length, 1);
  assert.deepEqual(parseViewerControlMessageJson(socket.sentMessages[0]), {
    type: "viewer_control_message",
    timestamp_s: 7.5,
    source_kind: "gamepad",
    provider_id: "gamepad/v1",
    provider_schema: "viewer_gamepad_sample/v1",
    metadata: {
      intent_kind: "local_endpoint_velocity",
      input_continuity: "continuous",
      source_kind: "viewer_gamepad",
      control_frame: "world",
      local_endpoint_speed_m_s: 0.1,
      local_endpoint_max_delta_m: 0.03,
    },
    sequence: 0,
    gamepad: {
      connected: true,
      axes: [0.1],
      buttons: [{ pressed: true, value: 1 }],
      stale: false,
      zero_state: false,
    },
  });
}

function testViewerGamepadControlSenderHandlesMissingBackendGracefully(): void {
  const sender = createViewerGamepadControlSender({
    url: null,
  });

  sender.publish({
    connected: false,
    axes: [],
    buttons: [],
    stale: true,
    zero_state: true,
  });

  assert.deepEqual(sender.getLatestMessage()?.source_kind, "gamepad");
}

function testViewerGamepadPublicationControllerPublishesChangesAndHeldHeartbeat(): void {
  const timer = new FakeTimer();
  const published: ReturnType<typeof activeSnapshot>[] = [];
  const controller = createViewerGamepadPublicationController({
    publish(snapshot) {
      published.push(snapshot);
    },
    setTimeoutFn: timer.setTimeoutFn,
    clearTimeoutFn: timer.clearTimeoutFn,
  });

  const first = activeSnapshot();
  controller.update(first);
  assert.equal(published.length, 1, "first active sample must publish immediately");
  assert.equal(timer.nextDelayMs, DEFAULT_VIEWER_GAMEPAD_HEARTBEAT_INTERVAL_MS);
  assert.ok(DEFAULT_VIEWER_GAMEPAD_HEARTBEAT_INTERVAL_MS <= 125);

  controller.update(first);
  assert.equal(published.length, 1, "unchanged active sample must wait for heartbeat");
  timer.runNext();
  assert.equal(published.length, 2, "held active state must publish on heartbeat");
  assert.equal(timer.nextDelayMs, DEFAULT_VIEWER_GAMEPAD_HEARTBEAT_INTERVAL_MS);

  const changed = sampleViewerGamepadSnapshot([{ ...ACTIVE_SNAPSHOT, axes: [-0.5] }]);
  controller.update(changed);
  assert.equal(published.length, 3, "changed active sample must publish immediately");
  assert.deepEqual(published.at(-1), changed);
  assert.equal(timer.pendingCount, 1, "change must restart one heartbeat timer");
}

function testViewerGamepadPublicationControllerSeparatesReleaseFromDisconnectHeartbeat(): void {
  const timer = new FakeTimer();
  const published: ReturnType<typeof activeSnapshot>[] = [];
  const controller = createViewerGamepadPublicationController({
    publish(snapshot) {
      published.push(snapshot);
    },
    setTimeoutFn: timer.setTimeoutFn,
    clearTimeoutFn: timer.clearTimeoutFn,
  });

  controller.update(activeSnapshot());
  controller.update(zeroSnapshot());
  assert.equal(published.length, 2, "zero/release transition must publish immediately");
  assert.equal(published.at(-1)?.zero_state, true);
  assert.equal(timer.pendingCount, 0, "legacy zero state must stop the heartbeat");

  controller.update(zeroSnapshot());
  assert.equal(published.length, 2, "unchanged neutral sample must stay suppressed");

  controller.update(activeSnapshot());
  controller.update(sampleViewerGamepadSnapshot(null));
  assert.equal(published.length, 4, "disconnect transition must publish immediately");
  assert.equal(published.at(-1)?.connected, false);
  assert.equal(timer.pendingCount, 0, "disconnect must stop heartbeat");
}

function testViewerGamepadPublicationControllerSuspendsPollingUpdatesUntilFocusedResume(): void {
  const timer = new FakeTimer();
  const published: ReturnType<typeof activeSnapshot>[] = [];
  const controller = createViewerGamepadPublicationController({
    publish(snapshot) {
      published.push(snapshot);
    },
    setTimeoutFn: timer.setTimeoutFn,
    clearTimeoutFn: timer.clearTimeoutFn,
  });

  controller.update(activeSnapshot());
  controller.update(zeroSnapshot());
  controller.suspend();
  const countAtSuspend = published.length;

  controller.update(activeSnapshot());
  assert.equal(published.length, countAtSuspend, "inactive polling must not publish an active sample");
  assert.equal(timer.pendingCount, 0, "inactive lifecycle must stop the heartbeat");

  controller.resume();
  controller.update(activeSnapshot());
  assert.equal(published.length, countAtSuspend + 1, "resume must publish only the fresh active sample");
  assert.equal(timer.pendingCount, 1, "resume must own exactly one heartbeat");
  controller.dispose();
}

function testViewerGamepadPublicationControllerDoesNotReviveAfterDispose(): void {
  const timer = new FakeTimer();
  const published: ReturnType<typeof activeSnapshot>[] = [];
  const controller = createViewerGamepadPublicationController({
    publish(snapshot) {
      published.push(snapshot);
    },
    setTimeoutFn: timer.setTimeoutFn,
    clearTimeoutFn: timer.clearTimeoutFn,
  });

  controller.update(activeSnapshot());
  controller.suspend();
  controller.dispose();
  controller.resume();
  controller.update(activeSnapshot());
  assert.equal(published.length, 1, "dispose must prevent publication from resuming");
  assert.equal(timer.pendingCount, 0, "dispose must prevent heartbeat revival");
}

function testViewerGamepadPublicationControllerDisposeAndRecreateAvoidDuplicateHeartbeats(): void {
  const timer = new FakeTimer();
  const published: ReturnType<typeof activeSnapshot>[] = [];
  const makeController = () =>
    createViewerGamepadPublicationController({
      publish(snapshot) {
        published.push(snapshot);
      },
      setTimeoutFn: timer.setTimeoutFn,
      clearTimeoutFn: timer.clearTimeoutFn,
    });

  const firstController = makeController();
  firstController.update(activeSnapshot());
  firstController.dispose();
  assert.equal(timer.pendingCount, 0, "dispose must cancel active heartbeat");

  const reconnectedController = makeController();
  reconnectedController.update(activeSnapshot());
  assert.equal(timer.pendingCount, 1, "reconnect must own exactly one heartbeat loop");
  timer.runNext();
  assert.equal(published.length, 3, "only the reconnected loop may heartbeat");
  assert.equal(timer.pendingCount, 1);
  reconnectedController.dispose();
}

function testHeartbeatPublicationAdvancesSequenceAndTimestamp(): void {
  const timer = new FakeTimer();
  const sender = createViewerGamepadControlSender({ url: null });
  let timestampS = 1;
  const messages: Array<{ sequence: number | undefined; timestampS: number }> = [];
  const controller = createViewerGamepadPublicationController({
    publish(snapshot) {
      sender.publish(snapshot, timestampS);
      const message = sender.getLatestMessage();
      if (message === null) {
        throw new Error("expected latest gamepad message");
      }
      messages.push({ sequence: message.sequence, timestampS: message.timestamp_s });
      timestampS += 0.1;
    },
    setTimeoutFn: timer.setTimeoutFn,
    clearTimeoutFn: timer.clearTimeoutFn,
  });

  controller.update(activeSnapshot());
  timer.runNext();
  assert.deepEqual(messages, [
    { sequence: 0, timestampS: 1 },
    { sequence: 1, timestampS: 1.1 },
  ]);
  controller.dispose();
}

testNormalizeViewerGamepadAxis();
testSampleViewerGamepadSnapshotReturnsZeroSnapshotWhenNoPad();
testSampleViewerGamepadSnapshotUsesFirstConnectedPad();
testRawAxisBelowLegacyDeadzonePublishesZeroStateWithoutHeartbeat();
testButtonOnlyInputRemainsAProviderActivitySample();
testBuildViewerGamepadControlMessageBuildsSchemaPayload();
testViewerGamepadControlSenderQueuesUntilOpen();
testViewerGamepadControlSenderHandlesMissingBackendGracefully();
testViewerGamepadPublicationControllerPublishesChangesAndHeldHeartbeat();
testViewerGamepadPublicationControllerSeparatesReleaseFromDisconnectHeartbeat();
testViewerGamepadPublicationControllerSuspendsPollingUpdatesUntilFocusedResume();
testViewerGamepadPublicationControllerDoesNotReviveAfterDispose();
testViewerGamepadPublicationControllerDisposeAndRecreateAvoidDuplicateHeartbeats();
testHeartbeatPublicationAdvancesSequenceAndTimestamp();

console.log("gamepad input tests passed");

// 平面操作だけがopt-inする中立heartbeat。旧modeの既存検査は変更しない。
{
  const timer = new FakeTimer();
  const published: ReturnType<typeof zeroSnapshot>[] = [];
  const controller = createViewerGamepadPublicationController({
    neutralHeartbeat: true, publish: value => published.push(value),
    setTimeoutFn: timer.setTimeoutFn, clearTimeoutFn: timer.clearTimeoutFn,
  });
  controller.update(zeroSnapshot());
  assert.equal(timer.pendingCount, 1);
  timer.runNext();
  assert.equal(published.length, 2);
  assert.ok(published.every(value => value.zero_state && value.connected));
  controller.update(sampleViewerGamepadSnapshot(null));
  assert.equal(timer.pendingCount, 0);
  controller.update(zeroSnapshot());
  controller.suspend();
  assert.equal(timer.pendingCount, 0);
  controller.resume();
  controller.update(zeroSnapshot());
  assert.equal(timer.pendingCount, 1);
  controller.dispose();
  assert.equal(timer.pendingCount, 0);
}
