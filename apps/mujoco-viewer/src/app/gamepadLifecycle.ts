import {
  createViewerGamepadPublicationController,
  sampleViewerGamepadSnapshot,
  type ViewerGamepadLike,
  type ViewerGamepadPublicationController,
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
  hasFocus(): boolean;
  addEventListener(type: "visibilitychange", listener: () => void): void;
  removeEventListener(type: "visibilitychange", listener: () => void): void;
}

export interface ViewerGamepadLifecycleOptions {
  neutralHeartbeat?: boolean;
  window: ViewerGamepadLifecycleWindowLike;
  document: ViewerGamepadLifecycleDocumentLike;
  getGamepads(): ArrayLike<ViewerGamepadLike | null | undefined> | null;
  publish(snapshot: ViewerGamepadSnapshot): void;
  heartbeatIntervalMs?: number;
  setTimeoutFn?: (callback: () => void, delayMs: number) => ReturnType<typeof setTimeout>;
  clearTimeoutFn?: (timeoutId: ReturnType<typeof setTimeout>) => void;
}

export interface ViewerGamepadLifecycle {
  start(): void;
  dispose(): void;
}

export function createViewerGamepadLifecycle(options: ViewerGamepadLifecycleOptions): ViewerGamepadLifecycle {
  const publication = createViewerGamepadPublicationController({
    publish: options.publish,
    neutralHeartbeat: options.neutralHeartbeat,
    heartbeatIntervalMs: options.heartbeatIntervalMs,
    setTimeoutFn: options.setTimeoutFn,
    clearTimeoutFn: options.clearTimeoutFn,
  });
  let disposed = false;
  let started = false;
  let lifecycleActive = options.document.visibilityState === "visible" && options.document.hasFocus();
  let animationFrameId = 0;

  const publishGamepadState = (): void => {
    const gamepads = options.getGamepads();
    if (!lifecycleActive) {
      return;
    }

    publication.update(sampleViewerGamepadSnapshot(gamepads, { deadzone: 0.1 }));
  };

  const setLifecycleActive = (nextActive: boolean): void => {
    if (disposed || nextActive === lifecycleActive) {
      return;
    }

    lifecycleActive = nextActive;
    if (!nextActive) {
      publication.update(sampleViewerGamepadSnapshot(null));
      publication.suspend();
      return;
    }

    publication.resume();
    publishGamepadState();
  };

  const schedulePoll = (): void => {
    if (disposed) {
      return;
    }

    publishGamepadState();
    animationFrameId = options.window.requestAnimationFrame(schedulePoll);
  };

  const onGamepadConnected = (): void => {
    publishGamepadState();
  };
  const onGamepadDisconnected = (): void => {
    publishGamepadState();
  };
  const onWindowBlur = (): void => {
    setLifecycleActive(false);
  };
  const onWindowFocus = (): void => {
    setLifecycleActive(options.document.visibilityState === "visible" && options.document.hasFocus());
  };
  const onVisibilityChange = (): void => {
    setLifecycleActive(options.document.visibilityState === "visible" && options.document.hasFocus());
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
    animationFrameId = options.window.requestAnimationFrame(schedulePoll);
    options.window.addEventListener("gamepadconnected", onGamepadConnected);
    options.window.addEventListener("gamepaddisconnected", onGamepadDisconnected);
    options.window.addEventListener("blur", onWindowBlur);
    options.window.addEventListener("focus", onWindowFocus);
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

    options.window.cancelAnimationFrame(animationFrameId);
    publication.dispose();
    options.window.removeEventListener("gamepadconnected", onGamepadConnected);
    options.window.removeEventListener("gamepaddisconnected", onGamepadDisconnected);
    options.window.removeEventListener("blur", onWindowBlur);
    options.window.removeEventListener("focus", onWindowFocus);
    options.document.removeEventListener("visibilitychange", onVisibilityChange);
  };

  return { start, dispose };
}
