import assert from "node:assert/strict";
import { readViewerInputStartup, createViewerInputLifecycle, type ViewerInputLifecycleDocumentLike, type ViewerInputLifecycleWindowLike, type ViewerKeyboardEventLike } from "../src/app/viewerInputLifecycle.js";
import {
  createViewerKeyboardCapture,
  DEFAULT_VIEWER_KEYBOARD_CAPTURE_KEYS,
  type ViewerKeyboardControlSocketLike,
} from "../src/input/keyboardInput.js";
import type { ViewerGamepadControlSocketLike, ViewerGamepadLike } from "../src/input/gamepadInput.js";
import { parseViewerControlMessageJson, type ViewerControlMessage } from "../src/transport/viewerControlMessage.js";
import {
  applyProductViewerRendererStatePatch,
  createInitialProductViewerState,
  isProductViewerLiveInputEnabled,
} from "../src/wasm-scene/productViewerState.js";

type LifecycleWindowEvent = "gamepadconnected" | "gamepaddisconnected" | "blur" | "focus";

class FakeTimer {
  private nextId = 1;
  private readonly callbacks = new Map<number, () => void>();

  readonly setTimeoutFn = (callback: () => void): ReturnType<typeof setTimeout> => {
    const id = this.nextId++;
    this.callbacks.set(id, callback);
    return id as unknown as ReturnType<typeof setTimeout>;
  };

  readonly clearTimeoutFn = (timeoutId: ReturnType<typeof setTimeout>): void => {
    this.callbacks.delete(timeoutId as unknown as number);
  };

  runNext(): void {
    const entry = this.callbacks.entries().next().value as [number, () => void] | undefined;
    if (entry === undefined) {
      throw new Error("expected pending timer");
    }
    this.callbacks.delete(entry[0]);
    entry[1]();
  }

  runNextIfPending(): boolean {
    const entry = this.callbacks.entries().next().value as [number, () => void] | undefined;
    if (entry === undefined) {
      return false;
    }
    this.callbacks.delete(entry[0]);
    entry[1]();
    return true;
  }
}

class FakeBrowser {
  visibilityState: "visible" | "hidden" = "visible";
  focused = true;
  currentGamepads: ArrayLike<ViewerGamepadLike | null | undefined> = [];

  private nextAnimationFrameId = 1;
  private readonly animationFrames = new Map<number, () => void>();
  private readonly keyboardListeners = new Map<"keydown" | "keyup", Set<(event: ViewerKeyboardEventLike) => void>>();
  private readonly windowListeners = new Map<LifecycleWindowEvent, Set<() => void>>();
  private readonly visibilityListeners = new Set<() => void>();

  readonly document: ViewerInputLifecycleDocumentLike = {
    get visibilityState() {
      return "visible" as const;
    },
    hasFocus: () => this.focused,
    addEventListener: (type, listener) => {
      if (type === "visibilitychange") {
        this.visibilityListeners.add(listener);
      }
    },
    removeEventListener: (type, listener) => {
      if (type === "visibilitychange") {
        this.visibilityListeners.delete(listener);
      }
    },
  };

  readonly window: ViewerInputLifecycleWindowLike = {
    requestAnimationFrame: (callback) => {
      const id = this.nextAnimationFrameId++;
      this.animationFrames.set(id, callback);
      return id;
    },
    cancelAnimationFrame: (id) => {
      this.animationFrames.delete(id);
    },
    addEventListener: (type: string, listener: ((event: ViewerKeyboardEventLike) => void) | (() => void)) => {
      if (type === "keydown" || type === "keyup") {
        const listeners = this.keyboardListeners.get(type) ?? new Set<(event: ViewerKeyboardEventLike) => void>();
        listeners.add(listener as (event: ViewerKeyboardEventLike) => void);
        this.keyboardListeners.set(type, listeners);
        return;
      }
      const listeners = this.windowListeners.get(type as LifecycleWindowEvent) ?? new Set<() => void>();
      listeners.add(listener as () => void);
      this.windowListeners.set(type as LifecycleWindowEvent, listeners);
    },
    removeEventListener: (type: string, listener: ((event: ViewerKeyboardEventLike) => void) | (() => void)) => {
      if (type === "keydown" || type === "keyup") {
        this.keyboardListeners.get(type)?.delete(listener as (event: ViewerKeyboardEventLike) => void);
        return;
      }
      this.windowListeners.get(type as LifecycleWindowEvent)?.delete(listener as () => void);
    },
  };

  getGamepads = (): ArrayLike<ViewerGamepadLike | null | undefined> => this.currentGamepads;

  dispatchKey(type: "keydown" | "keyup", code: string, repeat = false): void {
    const listeners = [...(this.keyboardListeners.get(type) ?? [])];
    if (listeners.length === 0) {
      return;
    }
    let prevented = false;
    const event: ViewerKeyboardEventLike = {
      code,
      repeat,
      preventDefault: () => {
        prevented = true;
      },
    };
    for (const listener of listeners) {
      listener(event);
    }
    assert.equal(prevented, true, `${type} should prevent the bound key default`);
  }

  runAnimationFrame(): void {
    const entry = this.animationFrames.entries().next().value as [number, () => void] | undefined;
    if (entry === undefined) {
      throw new Error("expected pending animation frame");
    }
    this.animationFrames.delete(entry[0]);
    entry[1]();
  }

  runAnimationFrameIfPending(): boolean {
    const entry = this.animationFrames.entries().next().value as [number, () => void] | undefined;
    if (entry === undefined) {
      return false;
    }
    this.animationFrames.delete(entry[0]);
    entry[1]();
    return true;
  }

  dispatchWindow(type: LifecycleWindowEvent): void {
    for (const listener of [...(this.windowListeners.get(type) ?? [])]) {
      listener();
    }
  }

  dispatchVisibilityChange(): void {
    for (const listener of [...this.visibilityListeners]) {
      listener();
    }
  }
}

class FakeControlSocket implements ViewerKeyboardControlSocketLike, ViewerGamepadControlSocketLike {
  readonly sentMessages: string[] = [];
  private readonly listeners = new Map<"open" | "close" | "error", Set<(event: Event) => void>>();
  readyState = 0;
  closed = false;

  constructor(readonly url: string) {}

  addEventListener(type: "open" | "close" | "error", listener: (event: Event) => void): void {
    const listeners = this.listeners.get(type) ?? new Set<(event: Event) => void>();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: "open" | "close" | "error", listener: (event: Event) => void): void {
    this.listeners.get(type)?.delete(listener);
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
    for (const listener of [...(this.listeners.get("close") ?? [])]) {
      listener(new Event("close"));
    }
  }

  emitOpen(): void {
    this.readyState = 1;
    for (const listener of [...(this.listeners.get("open") ?? [])]) {
      listener(new Event("open"));
    }
  }
}

function activePad(axis: number): ViewerGamepadLike {
  return {
    connected: true,
    index: 0,
    id: "test-pad",
    axes: [axis],
    buttons: [{ pressed: false, value: 0 }],
  };
}

function parseMessages(sockets: FakeControlSocket[]): ViewerControlMessage[] {
  return sockets.flatMap((socket) => socket.sentMessages.map((message) => parseViewerControlMessageJson(message)));
}

function createInputLifecycleHarness() {
  const browser = new FakeBrowser();
  const timer = new FakeTimer();
  const sockets: FakeControlSocket[] = [];
  class InjectedSocket extends FakeControlSocket {
    constructor(url: string) {
      super(url);
      sockets.push(this);
    }
  }

  const lifecycle = createViewerInputLifecycle({
    window: browser.window,
    document: browser.document,
    url: "ws://example.test/viewer",
    keyboardCapture: createViewerKeyboardCapture(DEFAULT_VIEWER_KEYBOARD_CAPTURE_KEYS, "focused"),
    getGamepads: browser.getGamepads,
    keyboardWebSocketCtor: InjectedSocket,
    gamepadWebSocketCtor: InjectedSocket,
    gamepadSetTimeoutFn: timer.setTimeoutFn,
    gamepadClearTimeoutFn: timer.clearTimeoutFn,
  });

  return { browser, timer, sockets, lifecycle };
}

function testPayloadFirstBootstrapKeepsOpenConnectionDuringModelLifecycle(): void {
  const initial = createInitialProductViewerState();
  const connecting = { ...initial, connectionStatus: "connecting" as const };
  const open = { ...connecting, connectionStatus: "open" as const };
  const loading = applyProductViewerRendererStatePatch(open, {
    status: "loading",
    sourceLabel: "declaration fetch",
  });
  const ready = applyProductViewerRendererStatePatch(loading, {
    status: "ready",
    sourceLabel: "runtime payload",
  });

  assert.deepEqual(
    [connecting.connectionStatus, open.connectionStatus, loading.connectionStatus, ready.connectionStatus],
    ["connecting", "open", "open", "open"],
  );
  assert.equal(loading.connectionStatus, "open");
  assert.equal(loading.status, "loading");
  assert.equal(ready.connectionStatus, "open");
  assert.equal(ready.status, "ready");
}

function testKeyboardAndGamepadStayLiveAcrossPayloadBootstrap(): void {
  const { browser, sockets, lifecycle } = createInputLifecycleHarness();
  browser.currentGamepads = [activePad(0.5)];
  const openState = { ...createInitialProductViewerState(), connectionStatus: "open" as const };
  const bootstrappingState = applyProductViewerRendererStatePatch(openState, { status: "loading" });
  assert.equal(bootstrappingState.connectionStatus, "open");
  assert.equal(isProductViewerLiveInputEnabled(bootstrappingState), true);
  lifecycle.setLiveInputEnabled(isProductViewerLiveInputEnabled(bootstrappingState));
  assert.equal(sockets.length, 2, "open/loading must create keyboard and gamepad senders");
  for (const socket of sockets) {
    socket.emitOpen();
  }
  browser.dispatchKey("keydown", "KeyW");
  browser.dispatchKey("keyup", "KeyW");

  const messagesAfterKeyboard = parseMessages(sockets).filter((message) => message.source_kind === "keyboard");
  assert.ok(messagesAfterKeyboard.length >= 3, "keyboard sender must publish bootstrap, press, and release states");
  assert.ok((messagesAfterKeyboard.at(-2)?.sequence ?? -1) > (messagesAfterKeyboard.at(-3)?.sequence ?? -1));
  assert.deepEqual(messagesAfterKeyboard.at(-2)?.keyboard?.active_key_codes, ["KeyW"]);
  assert.equal(messagesAfterKeyboard.at(-1)?.keyboard?.zero_state, true);

  const gamepadCountBeforeModelReady = parseMessages(sockets).filter((message) => message.source_kind === "gamepad").length;
  browser.currentGamepads = [activePad(-0.8)];
  browser.runAnimationFrame();
  const gamepadMessages = parseMessages(sockets).filter((message) => message.source_kind === "gamepad");
  assert.ok(gamepadMessages.length > gamepadCountBeforeModelReady, "gamepad polling must continue during model bootstrap");
  assert.ok((gamepadMessages.at(-1)?.sequence ?? -1) > (gamepadMessages.at(-2)?.sequence ?? -1));

  const messageCountBeforeClose = parseMessages(sockets).length;
  lifecycle.setLiveInputEnabled(false);
  assert.ok(sockets.every((socket) => socket.closed), "close must dispose both input senders");
  browser.dispatchKey("keydown", "KeyW");
  assert.equal(parseMessages(sockets).length, messageCountBeforeClose, "closed lifecycle must stop keyboard publication");

  lifecycle.setLiveInputEnabled(true);
  const reopenedSockets = sockets.slice(sockets.length - 2);
  for (const socket of reopenedSockets) {
    socket.emitOpen();
  }
  lifecycle.setLiveInputEnabled(false);
  assert.ok(reopenedSockets.every((socket) => socket.closed), "error must dispose both input senders");

  lifecycle.dispose();
}

function testRendererErrorStopsLiveInputWithoutChangingOpenConnection(): void {
  const { browser, timer, sockets, lifecycle } = createInputLifecycleHarness();
  browser.currentGamepads = [activePad(0.25)];

  const openLoading = {
    ...createInitialProductViewerState(),
    connectionStatus: "open" as const,
    status: "loading" as const,
  };
  assert.equal(isProductViewerLiveInputEnabled(openLoading), true);
  lifecycle.setLiveInputEnabled(isProductViewerLiveInputEnabled(openLoading));
  for (const socket of sockets) {
    socket.emitOpen();
  }
  const socketsAfterOpen = [...sockets];

  const ready = applyProductViewerRendererStatePatch(openLoading, { status: "ready" });
  assert.equal(isProductViewerLiveInputEnabled(ready), true);
  lifecycle.setLiveInputEnabled(isProductViewerLiveInputEnabled(ready));
  assert.deepEqual(sockets, socketsAfterOpen, "loading -> ready must not recreate input senders");

  browser.currentGamepads = [activePad(-0.65)];
  browser.runAnimationFrame();
  assert.equal(timer.runNextIfPending(), true, "active gamepad publication must schedule a heartbeat");
  const messageCountBeforeError = parseMessages(sockets).length;

  const rendererError = applyProductViewerRendererStatePatch(ready, {
    status: "error",
    qposError: "failed to compile viewer model",
  });
  assert.equal(rendererError.connectionStatus, "open", "renderer error must not rewrite connection status");
  assert.equal(isProductViewerLiveInputEnabled(rendererError), false);
  lifecycle.setLiveInputEnabled(isProductViewerLiveInputEnabled(rendererError));
  assert.ok(sockets.every((socket) => socket.closed), "renderer error must dispose keyboard and gamepad senders");

  const messageCountAfterError = parseMessages(sockets).length;
  assert.equal(messageCountAfterError, messageCountBeforeError);
  browser.dispatchKey("keydown", "KeyW");
  browser.dispatchKey("keyup", "KeyW");
  assert.equal(browser.runAnimationFrameIfPending(), false, "renderer error must cancel input polling RAF");
  assert.equal(timer.runNextIfPending(), false, "renderer error must cancel gamepad heartbeat timers");
  assert.equal(parseMessages(sockets).length, messageCountAfterError, "renderer error must block new keyboard/gamepad messages");

  const reconnectLoading = applyProductViewerRendererStatePatch(rendererError, { status: "loading" });
  assert.equal(reconnectLoading.connectionStatus, "open");
  assert.equal(isProductViewerLiveInputEnabled(reconnectLoading), true);
  lifecycle.setLiveInputEnabled(isProductViewerLiveInputEnabled(reconnectLoading));
  assert.equal(sockets.length, socketsAfterOpen.length + 2, "new open/loading session must create fresh senders");
  for (const socket of sockets.slice(-2)) {
    socket.emitOpen();
  }
  browser.dispatchKey("keydown", "KeyW");
  const reconnectKeyboardMessages = parseMessages(sockets).filter((message) => message.source_kind === "keyboard");
  assert.ok(reconnectKeyboardMessages.length > 0, "reconnect must resume keyboard publication");

  const closed = { ...rendererError, connectionStatus: "closed" as const };
  assert.equal(isProductViewerLiveInputEnabled(closed), false, "closed connection must remain input-disabled");
  lifecycle.setLiveInputEnabled(isProductViewerLiveInputEnabled(closed));
  lifecycle.dispose();
}

function testKeyboardReactivationStartsWithFreshZeroState(): void {
  const { browser, sockets, lifecycle } = createInputLifecycleHarness();
  lifecycle.setLiveInputEnabled(true);
  for (const socket of sockets) {
    socket.emitOpen();
  }

  browser.dispatchKey("keydown", "KeyW");
  lifecycle.setLiveInputEnabled(false);
  // The keyup is intentionally dispatched while the provider is disabled.
  browser.dispatchKey("keyup", "KeyW");

  lifecycle.setLiveInputEnabled(true);
  const reopenedSockets = sockets.slice(-2);
  for (const socket of reopenedSockets) {
    socket.emitOpen();
  }
  const reopenedKeyboardMessages = parseMessages(reopenedSockets).filter(
    (message) => message.source_kind === "keyboard",
  );
  assert.equal(reopenedKeyboardMessages.length, 1);
  assert.deepEqual(reopenedKeyboardMessages[0]?.keyboard?.active_key_codes, []);
  assert.equal(reopenedKeyboardMessages[0]?.keyboard?.zero_state, true);

  lifecycle.dispose();
}

function testBootstrapFailureVariantsShareRendererErrorFailSafe(): void {
  const openReady = {
    ...createInitialProductViewerState(),
    connectionStatus: "open" as const,
    status: "ready" as const,
  };
  const failures = [
    "malformed viewer declaration",
    "viewer declaration digest mismatch",
    "failed to fetch model",
    "failed to fetch VFS asset",
    "MuJoCo model compatibility failure",
  ];

  for (const failure of failures) {
    const rendererError = applyProductViewerRendererStatePatch(openReady, {
      status: "error",
      qposError: failure,
    });
    assert.equal(rendererError.connectionStatus, "open");
    assert.equal(isProductViewerLiveInputEnabled(rendererError), false, failure);
  }
}

testPayloadFirstBootstrapKeepsOpenConnectionDuringModelLifecycle();
testKeyboardAndGamepadStayLiveAcrossPayloadBootstrap();
testRendererErrorStopsLiveInputWithoutChangingOpenConnection();
testKeyboardReactivationStartsWithFreshZeroState();
testBootstrapFailureVariantsShareRendererErrorFailSafe();

console.log("product viewer payload-first and renderer-error input lifecycle tests passed");

// 双腕は明示したscene準備待ち。既存の接続直後入力は変更しない。
assert.equal(readViewerInputStartup(""), "connection");
assert.equal(readViewerInputStartup("?inputStartup=scene"), "scene");
assert.equal(readViewerInputStartup("?inputStartup=other"), "invalid");
assert.equal(readViewerInputStartup("?inputStartup=scene&inputStartup=scene"), "invalid");
const startupState = { ...createInitialProductViewerState(), connectionStatus: "open" as const };
assert.equal(isProductViewerLiveInputEnabled(startupState), true);
assert.equal(isProductViewerLiveInputEnabled(startupState, "scene"), false);
assert.equal(isProductViewerLiveInputEnabled({ ...startupState, qposStatus: "ready" }, "scene"), true);
assert.equal(isProductViewerLiveInputEnabled({ ...startupState, qposStatus: "ready" }, "invalid"), false);
assert.equal(isProductViewerLiveInputEnabled({ ...startupState, status: "error", qposStatus: "ready" }, "scene"), false);
