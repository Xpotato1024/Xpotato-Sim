import type { ViewerControlGamepadButtonMessage, ViewerControlGamepadMessage, ViewerControlMessage } from "../transport/viewerControlMessage.js";

export interface ViewerGamepadButtonSnapshot {
  pressed: boolean;
  value: number | null;
}

/** browser poll 1回分。raw_axesはMapping前のfinite値である。 */
export interface ViewerGamepadSnapshot {
  connected: boolean;
  index?: number;
  id?: string;
  /** Raw finite browser axes; mapping must use this field when present. */
  raw_axes?: number[];
  axes: number[];
  buttons: ViewerGamepadButtonSnapshot[];
  stale: boolean;
  zero_state: boolean;
}

export interface ViewerGamepadControlMessageOptions {
  sequence?: number;
  metadata?: Record<string, unknown>;
}

export interface ViewerGamepadControlSocketLike {
  readonly readyState: number;
  addEventListener(type: "open", listener: (event: Event) => void): void;
  addEventListener(type: "close", listener: (event: Event) => void): void;
  addEventListener(type: "error", listener: (event: Event) => void): void;
  removeEventListener?(type: "open", listener: (event: Event) => void): void;
  removeEventListener?(type: "close", listener: (event: Event) => void): void;
  removeEventListener?(type: "error", listener: (event: Event) => void): void;
  send(message: string): void;
  close(): void;
}

export type ViewerGamepadControlSocketConstructorLike = new (url: string) => ViewerGamepadControlSocketLike;

export interface ViewerGamepadControlSenderOptions {
  providerSessionId?: string;
  url: string | null;
  WebSocketCtor?: ViewerGamepadControlSocketConstructorLike;
}

export interface ViewerGamepadControlSender {
  publish(snapshot: ViewerGamepadSnapshot, timestampS?: number): void;
  dispose(): void;
  getLatestMessage(): ViewerControlMessage | null;
}

export interface ViewerGamepadPublicationControllerOptions {
  /** 接続中の中立sampleも送り、平面切替の中立確認を有効に保つ。省略時は旧cadence。 */
  neutralHeartbeat?: boolean;
  publish(snapshot: ViewerGamepadSnapshot): void;
  heartbeatIntervalMs?: number;
  setTimeoutFn?: (callback: () => void, delayMs: number) => ReturnType<typeof setTimeout>;
  clearTimeoutFn?: (timeoutId: ReturnType<typeof setTimeout>) => void;
}

export interface ViewerGamepadPublicationController {
  update(snapshot: ViewerGamepadSnapshot): void;
  suspend(): void;
  resume(): void;
  dispose(): void;
}

export interface ViewerGamepadSamplingOptions {
  deadzone?: number;
  clampMin?: number;
  clampMax?: number;
}

export interface ViewerGamepadLikeButton {
  pressed: boolean;
  value?: number;
}

export interface ViewerGamepadLike {
  connected: boolean;
  index?: number;
  id?: string;
  axes: ArrayLike<number>;
  buttons: ArrayLike<ViewerGamepadLikeButton>;
}

const DEFAULT_DEADZONE = 0.1;
const DEFAULT_CLAMP_MIN = -1;
const DEFAULT_CLAMP_MAX = 1;
export const DEFAULT_VIEWER_GAMEPAD_HEARTBEAT_INTERVAL_MS = 100;

function currentTimestampS(): number {
  const performanceNow = globalThis.performance?.now();
  if (typeof performanceNow === "number") {
    return performanceNow / 1000;
  }

  return Date.now() / 1000;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/** 非finite値を0とし、deadzone後のaxisを[-1, 1]へclampする。 */
export function normalizeViewerGamepadAxis(
  value: number,
  options: ViewerGamepadSamplingOptions = {},
): number {
  const deadzone = options.deadzone ?? DEFAULT_DEADZONE;
  const clampMin = options.clampMin ?? DEFAULT_CLAMP_MIN;
  const clampMax = options.clampMax ?? DEFAULT_CLAMP_MAX;
  const clamped = clamp(value, clampMin, clampMax);
  const magnitude = Math.abs(clamped);

  if (magnitude <= deadzone) {
    return 0;
  }

  const scaled = (magnitude - deadzone) / Math.max(1 - deadzone, Number.EPSILON);
  return Math.sign(clamped) * clamp(scaled, 0, 1);
}

function normalizeViewerGamepadButton(value: ViewerGamepadLikeButton): ViewerGamepadButtonSnapshot {
  return {
    pressed: value.pressed,
    value: isFiniteNumber(value.value) ? clamp(value.value, 0, 1) : null,
  };
}

function isGamepadLike(value: unknown): value is ViewerGamepadLike {
  return typeof value === "object" && value !== null && "axes" in value && "buttons" in value && "connected" in value;
}

function buildZeroStateSnapshot(): ViewerGamepadSnapshot {
  return {
    connected: false,
    axes: [],
    buttons: [],
    stale: true,
    zero_state: true,
  };
}

/** browser Gamepadをpollし、disconnect/欠落時はstale zero snapshotを返す。 */
export function sampleViewerGamepadSnapshot(
  gamepads: ArrayLike<ViewerGamepadLike | null | undefined> | null | undefined,
  options: ViewerGamepadSamplingOptions = {},
): ViewerGamepadSnapshot {
  if (gamepads === null || gamepads === undefined) {
    return buildZeroStateSnapshot();
  }

  const pads = Array.from(gamepads).filter(isGamepadLike);
  const connectedPad = pads.find((pad) => pad.connected) ?? null;
  if (connectedPad === null) {
    return buildZeroStateSnapshot();
  }

  const axes = Array.from(connectedPad.axes, (value) =>
    isFiniteNumber(value) ? normalizeViewerGamepadAxis(value, options) : 0,
  );
  const rawAxes = Array.from(connectedPad.axes, (value) => (isFiniteNumber(value) ? value : 0));
  const buttons = Array.from(connectedPad.buttons, (button) => normalizeViewerGamepadButton(button));
  const zeroState =
    axes.every((value) => value === 0) &&
    buttons.every((button) => !button.pressed && (button.value === null || button.value === 0));

  return {
    connected: true,
    index: connectedPad.index,
    id: connectedPad.id,
    raw_axes: rawAxes,
    axes,
    buttons,
    stale: false,
    zero_state: zeroState,
  };
}

function isActiveGamepadSnapshot(snapshot: ViewerGamepadSnapshot): boolean {
  return snapshot.connected && !snapshot.stale && !snapshot.zero_state;
}

/** connect/disconnectとheartbeatを所有し、stop後のsample送信を停止する。 */
export function createViewerGamepadPublicationController(
  options: ViewerGamepadPublicationControllerOptions,
): ViewerGamepadPublicationController {
  const heartbeatIntervalMs = options.heartbeatIntervalMs ?? DEFAULT_VIEWER_GAMEPAD_HEARTBEAT_INTERVAL_MS;
  if (!Number.isFinite(heartbeatIntervalMs) || heartbeatIntervalMs <= 0) {
    throw new Error("heartbeatIntervalMs must be a positive finite number");
  }

  const setTimeoutFn = options.setTimeoutFn ?? globalThis.setTimeout.bind(globalThis);
  const clearTimeoutFn = options.clearTimeoutFn ?? globalThis.clearTimeout.bind(globalThis);
  let latestSnapshot: ViewerGamepadSnapshot | null = null;
  let latestSignature: string | null = null;
  let heartbeatTimeoutId: ReturnType<typeof setTimeout> | null = null;
  let suspended = false;
  let disposed = false;

  const cancelHeartbeat = (): void => {
    if (heartbeatTimeoutId === null) {
      return;
    }

    clearTimeoutFn(heartbeatTimeoutId);
    heartbeatTimeoutId = null;
  };

  const heartbeatEligible = (snapshot: ViewerGamepadSnapshot): boolean =>
    isActiveGamepadSnapshot(snapshot) || (options.neutralHeartbeat === true && snapshot.connected && !snapshot.stale);

  const scheduleHeartbeat = (): void => {
    cancelHeartbeat();
    if (disposed || suspended || latestSnapshot === null || !heartbeatEligible(latestSnapshot)) {
      return;
    }

    heartbeatTimeoutId = setTimeoutFn(() => {
      heartbeatTimeoutId = null;
      if (disposed || suspended || latestSnapshot === null || !heartbeatEligible(latestSnapshot)) {
        return;
      }

      options.publish(latestSnapshot);
      scheduleHeartbeat();
    }, heartbeatIntervalMs);
  };

  return {
    update(snapshot: ViewerGamepadSnapshot): void {
      if (disposed || suspended) {
        return;
      }

      const signature = JSON.stringify(snapshot);
      latestSnapshot = snapshot;
      if (signature === latestSignature) {
        return;
      }

      latestSignature = signature;
      options.publish(snapshot);
      scheduleHeartbeat();
    },
    suspend(): void {
      if (disposed || suspended) {
        return;
      }

      suspended = true;
      cancelHeartbeat();
      latestSnapshot = null;
      latestSignature = null;
    },
    resume(): void {
      if (disposed) {
        return;
      }

      suspended = false;
    },
    dispose(): void {
      disposed = true;
      suspended = true;
      cancelHeartbeat();
      latestSnapshot = null;
    },
  };
}

function snapshotToGamepadMessage(snapshot: ViewerGamepadSnapshot): ViewerControlGamepadMessage {
  const message: ViewerControlGamepadMessage = {
    connected: snapshot.connected,
    axes: snapshot.axes,
    buttons: snapshot.buttons.map((button) => {
      const mappedButton: ViewerControlGamepadButtonMessage = { pressed: button.pressed };
      if (button.value !== null) {
        mappedButton.value = button.value;
      }
      return mappedButton;
    }),
    stale: snapshot.stale,
    zero_state: snapshot.zero_state,
  };

  if (snapshot.raw_axes !== undefined) {
    message.raw_axes = [...snapshot.raw_axes];
  }

  if (snapshot.index !== undefined) {
    message.index = snapshot.index;
  }
  if (snapshot.id !== undefined) {
    message.id = snapshot.id;
  }

  return message;
}

export function buildViewerGamepadControlMessage(
  snapshot: ViewerGamepadSnapshot,
  timestampS: number,
  options: ViewerGamepadControlMessageOptions = {},
): ViewerControlMessage {
  const message: ViewerControlMessage = {
    type: "viewer_control_message",
    timestamp_s: timestampS,
    source_kind: "gamepad",
    provider_id: "gamepad/v1",
    provider_schema: "viewer_gamepad_sample/v1",
    gamepad: snapshotToGamepadMessage(snapshot),
    metadata: {
      intent_kind: "local_endpoint_velocity",
      input_continuity: "continuous",
      source_kind: "viewer_gamepad",
      control_frame: "world",
      local_endpoint_speed_m_s: 0.1,
      local_endpoint_max_delta_m: 0.03,
      ...options.metadata,
    },
  };

  if (options.sequence !== undefined) {
    message.sequence = options.sequence;
  }

  return message;
}

/** browser sampleをwire envelopeへ送り、command semanticsはbackend Mappingへ残す。 */
export function createViewerGamepadControlSender(
  options: ViewerGamepadControlSenderOptions,
): ViewerGamepadControlSender {
  const WebSocketCtor =
    options.WebSocketCtor ?? (globalThis.WebSocket as unknown as ViewerGamepadControlSocketConstructorLike | undefined);
  let socket: ViewerGamepadControlSocketLike | null = null;
  let latestMessage: ViewerControlMessage | null = null;
  let sequence = 0;

  const isEnabled = options.url !== null && options.url.trim() !== "" && WebSocketCtor !== undefined;

  const handleSocketOpen = (): void => {
    flush();
  };
  const handleSocketClose = (): void => {
    socket = null;
  };
  const handleSocketError = (): void => {
    // Keep latest message queued; viewer must remain usable without backend ingestion.
  };

  const flush = (): void => {
    if (socket === null || socket.readyState !== 1 || latestMessage === null) {
      return;
    }

    try {
      socket.send(JSON.stringify(latestMessage));
    } catch {
      // Ignore socket send failures; the browser control path must not crash the viewer.
    }
  };

  const attachSocket = (): void => {
    if (!isEnabled || socket !== null) {
      return;
    }

    try {
      socket = new WebSocketCtor(options.url as string);
    } catch {
      socket = null;
      return;
    }

    socket.addEventListener("open", handleSocketOpen);
    socket.addEventListener("close", handleSocketClose);
    socket.addEventListener("error", handleSocketError);
  };

  const dispose = (): void => {
    if (socket === null) {
      return;
    }

    socket.removeEventListener?.("open", handleSocketOpen);
    socket.removeEventListener?.("close", handleSocketClose);
    socket.removeEventListener?.("error", handleSocketError);
    try {
      socket.close();
    } finally {
      socket = null;
    }
  };

  return {
    publish(snapshot: ViewerGamepadSnapshot, timestampS = currentTimestampS()): void {
      latestMessage = buildViewerGamepadControlMessage(snapshot, timestampS, { sequence,
        ...(options.providerSessionId === undefined ? {} : { metadata: { viewer_provider_session_id: options.providerSessionId } }),
      });
      sequence += 1;
      attachSocket();
      flush();
    },
    dispose,
    getLatestMessage(): ViewerControlMessage | null {
      return latestMessage;
    },
  };
}
