import {
  createViewerGamepadPublicationController,
  sampleViewerGamepadSnapshot,
  type ViewerGamepadLike,
  type ViewerGamepadSnapshot,
} from "../input/gamepadInput.js";

type GamepadLifecycleEvent = "gamepadconnected" | "gamepaddisconnected" | "blur" | "focus";

export interface ViewerGamepadLifecycleWindowLike {
  requestAnimationFrame(callback: () => void): number;
  cancelAnimationFrame(id: number): void;
  addEventListener(type: GamepadLifecycleEvent, listener: () => void): void;
  removeEventListener(type: GamepadLifecycleEvent, listener: () => void): void;
}

export interface ViewerGamepadLifecycleDocumentLike {
  visibilityState: "visible" | "hidden";
  addEventListener(type: "visibilitychange", listener: () => void): void;
  removeEventListener(type: "visibilitychange", listener: () => void): void;
}

export interface ViewerGamepadLifecycleOptions<T extends ViewerGamepadLike = ViewerGamepadLike> {
  neutralHeartbeat?: boolean;
  window: ViewerGamepadLifecycleWindowLike;
  document: ViewerGamepadLifecycleDocumentLike;
  getGamepads(): ArrayLike<T | null | undefined> | null;
  publish?(snapshot: ViewerGamepadSnapshot): void;
  onSample?(pads: ArrayLike<T | null | undefined> | null): void;
  pollIntervalMs?: number;
  nowMs?: () => number;
  heartbeatIntervalMs?: number;
  setTimeoutFn?: (callback: () => void, delayMs: number) => ReturnType<typeof setTimeout>;
  clearTimeoutFn?: (timeoutId: ReturnType<typeof setTimeout>) => void;
}

export interface ViewerGamepadLifecycle {
  start(): void;
  dispose(): void;
}

export function createViewerGamepadLifecycle<T extends ViewerGamepadLike>(options: ViewerGamepadLifecycleOptions<T>): ViewerGamepadLifecycle {
  const publication = createViewerGamepadPublicationController({
    publish: options.publish ?? (() => {}),
    neutralHeartbeat: options.neutralHeartbeat,
    heartbeatIntervalMs: options.heartbeatIntervalMs,
    setTimeoutFn: options.setTimeoutFn,
    clearTimeoutFn: options.clearTimeoutFn,
  });
  let disposed = false;
  let started = false;
  let lifecycleActive = options.document.visibilityState === "visible";
  let animationFrameId = 0;
  let pollTimeoutId: ReturnType<typeof setTimeout> | null = null;
  let lastPollMs = -Infinity;
  const nowMs = options.nowMs ?? (() => performance.now());
  const setTimeoutFn = options.setTimeoutFn ?? setTimeout;
  const clearTimeoutFn = options.clearTimeoutFn ?? clearTimeout;
  const timedPoll = (options.pollIntervalMs ?? 0) > 0;

  const cancelPoll = (): void => {
    options.window.cancelAnimationFrame(animationFrameId);
    animationFrameId = 0;
    if (pollTimeoutId !== null) clearTimeoutFn(pollTimeoutId);
    pollTimeoutId = null;
  };

  // 取得間隔を指定したconsumerは描画を待たない。従来ViewerはrAFを維持する。
  const queuePoll = (): void => {
    if (disposed || !started || !lifecycleActive) return;
    cancelPoll();
    if (timedPoll) {
      pollTimeoutId = setTimeoutFn(() => {
        pollTimeoutId = null;
        schedulePoll();
      }, Math.max(0, options.pollIntervalMs! - (nowMs() - lastPollMs)));
    } else animationFrameId = options.window.requestAnimationFrame(schedulePoll);
  };

  const publishGamepadState = (): void => {
    if (disposed || !lifecycleActive) {
      return;
    }

    const gamepads = options.getGamepads();
    lastPollMs = nowMs();
    if (options.onSample) options.onSample(gamepads);
    else publication.update(sampleViewerGamepadSnapshot(gamepads, { deadzone: 0.1 }));
  };

  const setLifecycleActive = (nextActive: boolean): void => {
    if (disposed || nextActive === lifecycleActive) {
      return;
    }

    lifecycleActive = nextActive;
    if (!nextActive) {
      cancelPoll();
      if (options.onSample) options.onSample(null);
      else publication.update(sampleViewerGamepadSnapshot(null));
      publication.suspend();
      return;
    }

    publication.resume();
    publishGamepadState();
    queuePoll();
  };

  const schedulePoll = (): void => {
    if (disposed || !lifecycleActive) {
      return;
    }

    publishGamepadState();
    queuePoll();
  };

  const onGamepadConnected = (): void => {
    publishGamepadState();
    queuePoll();
  };
  const onGamepadDisconnected = (): void => {
    publishGamepadState();
    queuePoll();
  };
  const onVisibilityChange = (): void => {
    setLifecycleActive(options.document.visibilityState === "visible");
  };

  const start = (): void => {
    if (disposed || started) {
      return;
    }

    started = true;
    if (lifecycleActive) {
      publishGamepadState();
    } else {
      publication.update(sampleViewerGamepadSnapshot(null));
      publication.suspend();
    }
    queuePoll();
    options.window.addEventListener("gamepadconnected", onGamepadConnected);
    options.window.addEventListener("gamepaddisconnected", onGamepadDisconnected);
    options.document.addEventListener("visibilitychange", onVisibilityChange);
  };

  const dispose = (): void => {
    if (disposed) {
      return;
    }

    disposed = true;
    if (!started) {
      publication.dispose();
      return;
    }

    cancelPoll();
    publication.dispose();
    options.window.removeEventListener("gamepadconnected", onGamepadConnected);
    options.window.removeEventListener("gamepaddisconnected", onGamepadDisconnected);
    options.document.removeEventListener("visibilitychange", onVisibilityChange);
  };

  return { start, dispose };
}
