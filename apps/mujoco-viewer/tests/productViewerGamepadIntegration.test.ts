import assert from "node:assert/strict";
import {createWorkbenchGamepadLifecycle} from "../src/app/workbenchLifecycle.js";
import {
  createViewerGamepadLifecycle,
  type ViewerGamepadLifecycleDocumentLike,
  type ViewerGamepadLifecycleWindowLike,
} from "../src/app/gamepadLifecycle.js";
import {
  sampleViewerGamepadSnapshot,
  type ViewerGamepadLike,
  type ViewerGamepadSnapshot,
} from "../src/input/gamepadInput.js";

type WindowEvent = "gamepadconnected" | "gamepaddisconnected" | "blur" | "focus";

class FakeTimer {
  private nextId = 1;
  private readonly callbacks = new Map<number, () => void>();

  public readonly setTimeoutFn = (callback: () => void): ReturnType<typeof setTimeout> => {
    const id = this.nextId;
    this.nextId += 1;
    this.callbacks.set(id, callback);
    return id as unknown as ReturnType<typeof setTimeout>;
  };

  public readonly clearTimeoutFn = (timeoutId: ReturnType<typeof setTimeout>): void => {
    this.callbacks.delete(timeoutId as unknown as number);
  };

  get pendingCount(): number {
    return this.callbacks.size;
  }

  runNext(): void {
    const entry = this.callbacks.entries().next().value as [number, () => void] | undefined;
    if (entry === undefined) {
      throw new Error("expected a pending timer");
    }

    this.callbacks.delete(entry[0]);
    entry[1]();
  }
}

class FakeBrowser {
  public visibilityState: "visible" | "hidden" = "visible";
  public currentGamepads: ArrayLike<ViewerGamepadLike | null | undefined> = [];
  public getGamepadsCalls = 0;

  private nextAnimationFrameId = 1;
  private readonly animationFrames = new Map<number, () => void>();
  private readonly windowListeners = new Map<WindowEvent, Set<() => void>>();
  private readonly documentListeners = new Set<() => void>();

  public readonly document: ViewerGamepadLifecycleDocumentLike;

  constructor() {
    const browser = this;
    this.document = {
      get visibilityState() {
        return browser.visibilityState;
      },
      addEventListener: (_type, listener) => {
        browser.documentListeners.add(listener);
      },
      removeEventListener: (_type, listener) => {
        browser.documentListeners.delete(listener);
      },
    };
  }

  public readonly window: ViewerGamepadLifecycleWindowLike = {
    requestAnimationFrame: (callback) => {
      const id = this.nextAnimationFrameId;
      this.nextAnimationFrameId += 1;
      this.animationFrames.set(id, callback);
      return id;
    },
    cancelAnimationFrame: (id) => {
      this.animationFrames.delete(id);
    },
    addEventListener: (type, listener) => {
      const listeners = this.windowListeners.get(type) ?? new Set<() => void>();
      listeners.add(listener);
      this.windowListeners.set(type, listeners);
    },
    removeEventListener: (type, listener) => {
      this.windowListeners.get(type)?.delete(listener);
    },
  };

  getGamepads = (): ArrayLike<ViewerGamepadLike | null | undefined> => {
    this.getGamepadsCalls += 1;
    return this.currentGamepads;
  };

  get pendingAnimationFrameCount(): number {
    return this.animationFrames.size;
  }

  dispatchWindow(type: WindowEvent): void {
    for (const listener of [...(this.windowListeners.get(type) ?? [])]) {
      listener();
    }
  }

  dispatchVisibilityChange(): void {
    for (const listener of [...this.documentListeners]) {
      listener();
    }
  }

  runAnimationFrame(): void {
    const entry = this.animationFrames.entries().next().value as [number, () => void] | undefined;
    if (entry === undefined) {
      throw new Error("expected a pending animation frame");
    }

    this.animationFrames.delete(entry[0]);
    entry[1]();
  }
}

function activePad(axis: number): ViewerGamepadLike {
  return {
    connected: true,
    index: 0,
    id: "Pad",
    axes: [axis],
    buttons: [{ pressed: false, value: 0 }],
  };
}

function createTestLifecycle(browser: FakeBrowser, timer: FakeTimer, published: ViewerGamepadSnapshot[]) {
  return createViewerGamepadLifecycle({
    window: browser.window,
    document: browser.document,
    getGamepads: browser.getGamepads,
    publish: (snapshot) => published.push(snapshot),
    setTimeoutFn: timer.setTimeoutFn,
    clearTimeoutFn: timer.clearTimeoutFn,
  });
}

function testBlurAndFocusEventsDoNotGateVisibleGamepadPublication(): void {
  const browser = new FakeBrowser();
  browser.currentGamepads = [activePad(0.5)];
  const timer = new FakeTimer();
  const published: ViewerGamepadSnapshot[] = [];
  const lifecycle = createTestLifecycle(browser, timer, published);

  lifecycle.start();
  assert.equal(published.length, 1, "visible gamepad sample must publish on start");
  assert.equal(timer.pendingCount, 1);

  browser.dispatchWindow("blur");
  browser.currentGamepads = [activePad(-0.8)];
  browser.runAnimationFrame();
  assert.equal(published.length, 2, "window blur must not suppress visible gamepad input");
  assert.deepEqual(
    published.at(-1),
    sampleViewerGamepadSnapshot([activePad(-0.8)], { deadzone: 0.1 }),
  );
  assert.equal(timer.pendingCount, 1, "visible blur must keep one heartbeat");

  browser.dispatchWindow("focus");
  browser.currentGamepads = [activePad(0.7)];
  browser.runAnimationFrame();
  assert.equal(published.length, 3, "window focus must not create a separate activation boundary");
  assert.deepEqual(
    published.at(-1),
    sampleViewerGamepadSnapshot([activePad(0.7)], { deadzone: 0.1 }),
  );

  lifecycle.dispose();
}

function testHiddenTransitionStopsAndVisibleResumeUsesFreshSample(): void {
  const browser = new FakeBrowser();
  browser.currentGamepads = [activePad(0.5)];
  const timer = new FakeTimer();
  const published: ViewerGamepadSnapshot[] = [];
  const lifecycle = createTestLifecycle(browser, timer, published);

  lifecycle.start();
  browser.visibilityState = "hidden";
  browser.dispatchVisibilityChange();
  assert.equal(published.length, 2, "hidden transition must publish zero immediately");
  assert.equal(published.at(-1)?.zero_state, true);
  assert.equal(timer.pendingCount, 0, "hidden transition must stop heartbeat");

  const callsBeforeHiddenPoll = browser.getGamepadsCalls;
  browser.currentGamepads = [activePad(-0.8)];
  assert.equal(browser.pendingAnimationFrameCount, 0);
  assert.equal(published.length, 2, "hidden polling must not publish active input");
  assert.equal(browser.getGamepadsCalls, callsBeforeHiddenPoll, "hiddenでは取得自体を停止する");

  browser.visibilityState = "visible";
  browser.dispatchVisibilityChange();
  assert.equal(published.length, 3, "visible resume must publish the current fresh sample immediately");
  assert.deepEqual(
    published.at(-1),
    sampleViewerGamepadSnapshot([activePad(-0.8)], { deadzone: 0.1 }),
  );
  assert.equal(timer.pendingCount, 1, "visible resume must restore one heartbeat");

  lifecycle.dispose();
}

function testRepeatedVisibilityEventsAndDisposeCannotRevivePublication(): void {
  const browser = new FakeBrowser();
  browser.currentGamepads = [activePad(0.5)];
  const timer = new FakeTimer();
  const published: ViewerGamepadSnapshot[] = [];
  const lifecycle = createTestLifecycle(browser, timer, published);

  lifecycle.start();
  browser.visibilityState = "hidden";
  browser.dispatchVisibilityChange();
  browser.dispatchWindow("blur");
  browser.dispatchVisibilityChange();
  assert.equal(published.length, 2, "repeated hidden/blur events must not duplicate zero publication");

  lifecycle.dispose();
  browser.visibilityState = "visible";
  browser.dispatchWindow("focus");
  browser.dispatchVisibilityChange();
  assert.equal(browser.pendingAnimationFrameCount, 0, "dispose must cancel animation-frame polling");
  assert.equal(published.length, 2, "dispose must block event and polling publication");
  assert.equal(timer.pendingCount, 0, "dispose must block heartbeat revival");
}

testBlurAndFocusEventsDoNotGateVisibleGamepadPublication();
testHiddenTransitionStopsAndVisibleResumeUsesFreshSample();
testRepeatedVisibilityEventsAndDisposeCannotRevivePublication();

function testWorkbenchUsesSharedVisibilityLifetimeAndFreshEpochSamples(): void {
  const browser = new FakeBrowser();
  const timer = new FakeTimer();
  let now = 0;
  let enabled = true;
  let epoch = "trial-a";
  const messages: any[] = [];
  const lifecycle = createWorkbenchGamepadLifecycle({
    window: browser.window, document: browser.document, getGamepads: browser.getGamepads,
    nowMs: () => now, nowSeconds: () => now / 1000,
    setTimeoutFn: timer.setTimeoutFn, clearTimeoutFn: timer.clearTimeoutFn,
    context: () => ({epoch, enabled}), publish: message => messages.push(message),
  });
  lifecycle.start();
  assert.equal(messages.length, 0, "初回未取得は入力待ち");
  lifecycle.start();
  assert.equal(timer.pendingCount, 1, "取得schedulerは一つ");
  assert.equal(browser.pendingAnimationFrameCount, 0, "Workbench取得はrAFを使わない");
  browser.currentGamepads = [activePad(.5)];
  now = 40; browser.dispatchWindow("blur"); timer.runNext();
  assert.equal(messages.length, 1, "visibleならfocusに依存せず取得");
  assert.equal(messages[0].timestamp_s, .04);
  const session = messages[0].metadata.viewer_provider_session_id;
  const readsBeforeDisabled = browser.getGamepadsCalls;
  enabled = false; now = 80; timer.runNext();
  assert.equal(messages.length, 1, "STOP・fixture・claim gateで送信停止");
  assert.equal(browser.getGamepadsCalls, readsBeforeDisabled, "無効contextでは実デバイスの取得も行わない");
  enabled = true; epoch = "trial-b"; now = 120; timer.runNext();
  assert.equal(messages[1].sequence, 0);
  assert.notEqual(messages[1].metadata.viewer_provider_session_id, session);
  browser.visibilityState = "hidden"; browser.dispatchVisibilityChange();
  assert.equal(messages.at(-1).gamepad.stale, true);
  assert.equal(browser.pendingAnimationFrameCount, 0);
  const count = messages.length, reads = browser.getGamepadsCalls;
  browser.dispatchWindow("gamepadconnected");
  assert.equal(browser.getGamepadsCalls, reads);
  assert.equal(messages.length, count);
  browser.currentGamepads = [];
  browser.visibilityState = "visible"; now = 400; browser.dispatchVisibilityChange();
  assert.equal(messages.at(-1).gamepad.connected, false, "復帰で旧sampleを再送しない");
  assert.equal(messages.at(-1).timestamp_s, .4);
  browser.currentGamepads = [activePad(0)]; now = 440; timer.runNext();
  assert.equal(messages.at(-1).gamepad.zero_state, true);
  browser.currentGamepads = [];
  browser.dispatchWindow("gamepaddisconnected");
  assert.equal(messages.at(-1).gamepad.stale, true);
  assert.equal(timer.pendingCount, 1, "disconnectはschedulerを重複させない");
  browser.currentGamepads = [activePad(.2)];
  now = 1000; timer.runNext();
  assert.equal(messages.at(-1).timestamp_s, 1, "停止したevent loopからの復帰も実取得時刻を使う");
  lifecycle.dispose();
  const finalCount = messages.length;
  browser.dispatchWindow("gamepaddisconnected"); browser.dispatchVisibilityChange();
  assert.equal(messages.length, finalCount);
  assert.equal(browser.pendingAnimationFrameCount, 0);
  assert.equal(timer.pendingCount, 0);
}
testWorkbenchUsesSharedVisibilityLifetimeAndFreshEpochSamples();

function testWorkbenchInitiallyHiddenAndDisabled():void {
  const browser=new FakeBrowser(), timer=new FakeTimer();
  browser.visibilityState="hidden";
  browser.currentGamepads=[activePad(.5)];
  let enabled=false, count=0;
  const lifecycle=createWorkbenchGamepadLifecycle({window:browser.window,document:browser.document,
    getGamepads:browser.getGamepads,context:()=>({epoch:"hidden",enabled}),
    nowSeconds:()=>0,setTimeoutFn:timer.setTimeoutFn,clearTimeoutFn:timer.clearTimeoutFn,publish(){count++;}});
  lifecycle.start();
  assert.equal(browser.getGamepadsCalls,0);assert.equal(timer.pendingCount,0);
  browser.dispatchWindow("focus");browser.dispatchWindow("gamepadconnected");
  assert.equal(browser.getGamepadsCalls,0);
  browser.visibilityState="visible";browser.dispatchVisibilityChange();
  assert.equal(browser.getGamepadsCalls,0);assert.equal(timer.pendingCount,1);
  enabled=true;timer.runNext();assert.equal(count,1);assert.equal(browser.getGamepadsCalls,1);
  lifecycle.dispose();lifecycle.dispose();assert.equal(timer.pendingCount,0);
}
testWorkbenchInitiallyHiddenAndDisabled();

console.log("product viewer gamepad lifecycle integration tests passed");
