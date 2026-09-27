/**
 * backend/MuJoCo payloadをUI表示へ投影するviewer state。
 * physical stateのSoT、FK/IK、qpos補完を所有せず、欠落値へ別姿勢をfallbackしない。
 */
import type { TransportEndpointEvaluationPayload, TransportPayloadV0 } from "../types/transportPayload.js";
import type { ViewerRobotProfile } from "../robot-profiles/types.js";
import {
  unavailableContactTaskPresentation,
  type ContactTaskInputSource,
  type ContactTaskPresentationV1,
} from "../contact/contactTaskLog.js";
import { parseRawInputSignal, normalizedAxes, pressedGamepadButtons, type RawInputSignal } from "../app/instrumentPresentation.js";
import { parseGamepadPlanePresentation, type GamepadPlanePresentation } from "../app/gamepadPlanePresentation.js";
import type { JointDisplayLayout } from "./jointPresentation.js";
import { formatQpos } from "./mujocoQposSync.js";
import type { ViewerFrameTimingSnapshot } from "./viewerFrameTiming.js";
import {
  buildEndpointPresentationState,
  formatEndpointPresentationText,
  type EndpointPresentationState,
} from "./endpointPresentation.js";

export type ProductViewerConnectionStatus = "disabled" | "connecting" | "open" | "closed" | "error";
export type ProductViewerRendererMode = "wasm-scene";
export type ProductViewerStatus = "booting" | "loading" | "ready" | "warning" | "error";
export type ProductViewerQposStatus = "loading" | "ready" | "unavailable" | "invalid";

export interface ProductViewerInputOverlayButtonState {
  pressed: boolean | null;
  value: number | null;
}

export interface ProductViewerInputOverlayState {
  endpointPresentation: EndpointPresentationState;
  sourceKind: string;
  rawSignal: RawInputSignal | null;
  intentKind: string | null;
  inputContinuity: string | null;
  sourceActive: boolean;
  commandAgeMs: number | null;
  staleReason: string | null;
  viewerSourceKind: string | null;
  sequence: number | null;
  axisValues: number[];
  localEndpointSpeedMS: number | null;
  localEndpointMaxDeltaM: number | null;
  endpointVelocityMS: number[];
  endpointDeltaM: number[];
  runtimeInputSafetyApplied: boolean | null;
  targetStatus: string | null;
  targetRejected: boolean | null;
  targetRejectionReason: string | null;
  targetRejectionMessage: string | null;
  localMotionPolicy: string | null;
  motionStatus: string | null;
  motionRejectionReason: string | null;
  qposDeltaNormRad: number | null;
  rejectedDesiredEndpointM: number[] | null;
  lastValidTargetPositionM: number[] | null;
  endpointEvaluationState: "available" | "missing" | "malformed";
  endpointEvaluationUnavailableReason: string | null;
  keyboardActiveKeyCodes: string[];
  keyboardFocusState: string | null;
  keyboardZeroState: boolean | null;
  keyboardKeyState: Record<string, boolean>;
  gamepadPlaneControl?: GamepadPlanePresentation | null;
  gamepadConnected: boolean | null;
  gamepadIndex: number | null;
  gamepadId: string | null;
  gamepadAxes: number[] | null;
  gamepadInstrumentAxes: number[];
  gamepadInstrumentPressedButtons: number[] | null;
  gamepadButtons: ProductViewerInputOverlayButtonState[];
  gamepadStale: boolean | null;
  gamepadZeroState: boolean | null;
}

/** renderer/transport/inputの表示状態。qpos値はbackendまたは明示fixture由来に限る。 */
export interface ProductViewerState {
  rendererMode: ProductViewerRendererMode;
  connectionStatus: ProductViewerConnectionStatus;
  status: ProductViewerStatus;
  robotProfileId: string | null;
  modelContractVersion: string | null;
  modelPath: string;
  fixturePath: string;
  sourceLabel: string;
  qposStatus: ProductViewerQposStatus;
  qposError: string | null;
  currentFrameIndex: number | null;
  currentTimestampS: number | null;
  currentQpos: number[] | null;
  jointLayout: JointDisplayLayout | null;
  currentQposText: string;
  endpointEvaluation: TransportEndpointEvaluationPayload | null;
  contactTaskPresentation: ContactTaskPresentationV1;
  contactTaskInputSource: ContactTaskInputSource;
  inputOverlay: ProductViewerInputOverlayState | null;
  viewerTiming: ViewerFrameTimingSnapshot | null;
  modelNq: number | null;
  modelNv: number | null;
  modelNgeom: number | null;
  modelNmesh: number | null;
  sceneSummaryText: string;
  statusText: string;
}

export type ProductViewerRendererStatePatch = Omit<Partial<ProductViewerState>, "connectionStatus">;

/** renderer-owned fieldだけを更新し、transport connection stateを上書きしない。 */
export function applyProductViewerRendererStatePatch(
  previous: ProductViewerState,
  patch: ProductViewerRendererStatePatch,
): ProductViewerState {
  const next = {
    ...previous,
    ...patch,
  };
  next.statusText = patch.statusText ?? formatViewerStatusText(next);
  next.currentQposText =
    patch.currentQposText ?? (next.currentQpos === null ? "[]" : formatQpos(next.currentQpos));
  return next;
}

export function isProductViewerLiveInputEnabled(
  state: Pick<ProductViewerState, "connectionStatus" | "status">,
): boolean {
  return state.connectionStatus === "open" && state.status !== "error";
}

/** profile declarationから未接続のprojection stateを作り、physical stateは生成しない。 */
export function createInitialProductViewerState(profile?: ViewerRobotProfile): ProductViewerState {
  return {
    rendererMode: "wasm-scene",
    connectionStatus: "disabled",
    status: "booting",
    robotProfileId: profile?.profileId ?? null,
    modelContractVersion: profile?.modelContractVersion ?? null,
    modelPath: profile?.modelUrl ?? "unavailable",
    fixturePath: profile?.fixtureUrl ?? "unavailable",
    sourceLabel: "loading",
    qposStatus: "loading",
    qposError: null,
    currentFrameIndex: null,
    currentTimestampS: null,
    currentQpos: null,
    jointLayout: null,
    currentQposText: "[]",
    endpointEvaluation: null,
    contactTaskPresentation: unavailableContactTaskPresentation("No contact task log or metadata loaded."),
    contactTaskInputSource: "none",
    inputOverlay: null,
    viewerTiming: null,
    modelNq: null,
    modelNv: null,
    modelNgeom: null,
    modelNmesh: null,
    sceneSummaryText: "booting",
    statusText: "booting",
  };
}

export function formatViewerStatusText(state: ProductViewerState): string {
  const currentFrame = state.currentFrameIndex === null ? "n/a" : String(state.currentFrameIndex);
  const currentTimestamp = state.currentTimestampS === null ? "n/a" : state.currentTimestampS.toFixed(6);
  const modelNq = state.modelNq === null ? "n/a" : String(state.modelNq);
  const qposError = state.qposError === null ? "none" : state.qposError;
  const endpointEvaluation = state.endpointEvaluation === null ? "unavailable" : "available";
  const contact = state.contactTaskPresentation;
  const contactForce =
    contact.status === "available" && contact.derivedForce?.forceN !== null && contact.derivedForce !== null
      ? contact.derivedForce.forceN.map((value) => value.toFixed(3)).join(", ") + " N"
      : "unavailable";
  const inputOverlay = state.inputOverlay === null ? "unavailable" : "available";
  const viewerTiming = state.viewerTiming;

  return [
    `renderer mode: ${state.rendererMode}`,
    `robot profile: ${state.robotProfileId ?? "unavailable"}`,
    `model contract: ${state.modelContractVersion ?? "unavailable"}`,
    `model path: ${state.modelPath}`,
    `debug fixture path (reference only): ${state.fixturePath}`,
    `pose source: ${state.sourceLabel}`,
    `connection: ${state.connectionStatus}`,
    `qpos status: ${state.qposStatus}`,
    `current frame index: ${currentFrame}`,
    `current timestamp_s: ${currentTimestamp}`,
    `model.nq: ${modelNq}`,
    `current qpos: ${state.currentQposText}`,
    `endpoint evaluation: ${endpointEvaluation}`,
    `contact task status: ${contact.status}`,
    `contact task input: ${state.contactTaskInputSource}`,
    `contact declared source: ${contact.sourceKind ?? "unavailable"}`,
    `contact derived force: ${contactForce}`,
    `contact unavailable reason: ${contact.reason ?? "none"}`,
    `input overlay: ${inputOverlay}`,
    `latest received frame: ${viewerTiming?.latestReceivedFrameIndex ?? "n/a"}`,
    `latest compatibility-accepted frame: ${viewerTiming?.latestCompatibilityAcceptedFrameIndex ?? "n/a"}`,
    `latest scene-applied frame: ${viewerTiming?.latestSceneAppliedFrameIndex ?? "n/a"}`,
    `received frames: ${viewerTiming?.receivedFrameCount ?? 0}`,
    `compatibility-accepted frames: ${viewerTiming?.compatibilityAcceptedFrameCount ?? 0}`,
    `compatibility-invalid frames: ${viewerTiming?.compatibilityInvalidFrameCount ?? 0}`,
    `scene-applied frames: ${viewerTiming?.sceneAppliedFrameCount ?? 0}`,
    `latest ingress status: ${viewerTiming?.latestIngressStatus ?? "none"}`,
    `received-to-applied frame distance: ${viewerTiming?.receivedToAppliedFrameDistance ?? "n/a"}`,
    `receive-to-apply age p50_ms: ${viewerTiming?.receiveToApplyAgeMsP50?.toFixed(3) ?? "n/a"}`,
    `receive-to-apply age p95_ms: ${viewerTiming?.receiveToApplyAgeMsP95?.toFixed(3) ?? "n/a"}`,
    `receive-to-apply age max_ms: ${viewerTiming?.receiveToApplyAgeMsMax?.toFixed(3) ?? "n/a"}`,
    `payload parse p50/p95/max_ms: ${viewerTiming === null ? "n/a" : [viewerTiming.parseDurationMsP50, viewerTiming.parseDurationMsP95, viewerTiming.parseDurationMsMax].map((value) => value?.toFixed(3) ?? "n/a").join("/")}`,
    `scene apply p50/p95/max_ms: ${viewerTiming === null ? "n/a" : [viewerTiming.sceneApplyDurationMsP50, viewerTiming.sceneApplyDurationMsP95, viewerTiming.sceneApplyDurationMsMax].map((value) => value?.toFixed(3) ?? "n/a").join("/")}`,
    `coalesced frames: ${viewerTiming?.coalescedFrameCount ?? 0}`,
    `UI state updates: ${viewerTiming?.uiStateUpdateCount ?? 0}`,
    `UI state update frequency_hz: ${viewerTiming?.uiStateUpdateFrequencyHz.toFixed(3) ?? "n/a"}`,
    `qpos error: ${qposError}`,
    "browser-side IK/FK/qpos recompute: disabled",
  ].join("\n");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function parseStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.filter((item): item is string => typeof item === "string");
}

function parseBooleanRecord(value: unknown): Record<string, boolean> {
  if (!isRecord(value)) {
    return {};
  }

  const result: Record<string, boolean> = {};
  for (const [key, item] of Object.entries(value)) {
    if (typeof item === "boolean") {
      result[key] = item;
    }
  }

  return result;
}

function parseOptionalString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function parseOptionalBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function parseOptionalInteger(value: unknown): number | null {
  return isFiniteNumber(value) && Number.isInteger(value) ? value : null;
}

function parseOptionalNonNegativeInteger(value: unknown): number | null {
  return isFiniteNumber(value) && Number.isInteger(value) && value >= 0 ? value : null;
}

function parseOptionalFiniteNumber(value: unknown): number | null {
  return isFiniteNumber(value) ? value : null;
}

function parseOptionalVector3(value: unknown): number[] | null {
  if (!Array.isArray(value) || value.length !== 3) {
    return null;
  }

  const parsed = value.filter((component): component is number => isFiniteNumber(component));
  return parsed.length === 3 ? parsed : null;
}

function parseInputOverlayButtons(value: unknown): ProductViewerInputOverlayButtonState[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.map((button) => {
    if (typeof button === "boolean") return { pressed: button, value: null };
    if (!isRecord(button)) {
      return { pressed: null, value: null };
    }

    return {
      pressed: parseOptionalBoolean(button.pressed),
      value: parseOptionalFiniteNumber(button.value),
    };
  });
}

function parseInputOverlayState(
  payloadOrMetadata: Record<string, unknown>,
): ProductViewerInputOverlayState {
  const isPayload = isRecord(payloadOrMetadata.metadata);
  const metadata: Record<string, unknown> = isPayload
    ? (payloadOrMetadata.metadata as Record<string, unknown>)
    : payloadOrMetadata;
  const controlMessage = isRecord(metadata.viewer_control_message) ? metadata.viewer_control_message : null;
  const keyboard = controlMessage !== null && isRecord(controlMessage.keyboard) ? controlMessage.keyboard : null;
  const gamepad = controlMessage !== null && isRecord(controlMessage.gamepad) ? controlMessage.gamepad : null;
  const runtimeInputSafetyApplied = metadata.runtime_input_safety_applied === true;
  const targetRejected = metadata.target_rejected === true;
  const rejectedDesiredEndpointM = parseOptionalVector3(metadata.rejected_desired_endpoint_m);
  const lastValidTargetPositionM = isPayload ? parseOptionalVector3(payloadOrMetadata.target_position_m) : null;
  const targetStatusRaw = parseOptionalString(metadata.target_status);
  const axisValues = parseOptionalVector3(metadata.axis_values) ?? [];
  const endpointVelocityMS = parseOptionalVector3(metadata.endpoint_velocity_m_s) ?? [];
  const endpointDeltaM = parseOptionalVector3(metadata.endpoint_delta_m) ?? [];
  const endpointEvaluationPresent = isPayload && "endpoint_evaluation" in payloadOrMetadata;
  const endpointEvaluation = endpointEvaluationPresent ? payloadOrMetadata.endpoint_evaluation : undefined;
  const endpointEvaluationState = !endpointEvaluationPresent
    ? "missing"
      : endpointEvaluation === null
      ? "malformed"
      : "available";
  const endpointEvaluationUnavailableReason =
    endpointEvaluationState === "available"
      ? null
      : endpointEvaluationState === "malformed"
        ? "endpoint_evaluation present but failed validation"
        : runtimeInputSafetyApplied
          ? targetRejected
            ? "endpoint_evaluation withheld on rejected target"
            : "endpoint_evaluation withheld on runtime input safety hold"
          : "endpoint_evaluation missing from payload";

  return {
    endpointPresentation: buildEndpointPresentationState(metadata),
    sourceKind: typeof metadata.source_kind === "string" ? metadata.source_kind : "n/a",
    rawSignal: isPayload ? parseRawInputSignal(metadata.input_signal_v1, payloadOrMetadata.frame_index, payloadOrMetadata.time_s) : null,
    intentKind: parseOptionalString(metadata.intent_kind),
    inputContinuity: parseOptionalString(metadata.input_continuity),
    sourceActive: metadata.source_active === true,
    commandAgeMs: parseOptionalNonNegativeInteger(metadata.command_age_ms),
    staleReason: parseOptionalString(metadata.stale_reason),
    viewerSourceKind: controlMessage === null ? null : parseOptionalString(controlMessage.viewer_source_kind),
    sequence: controlMessage === null ? null : parseOptionalInteger(controlMessage.sequence),
    axisValues,
    localEndpointSpeedMS: parseOptionalFiniteNumber(metadata.local_endpoint_speed_m_s),
    localEndpointMaxDeltaM: parseOptionalFiniteNumber(metadata.local_endpoint_max_delta_m),
    endpointVelocityMS,
    endpointDeltaM,
    runtimeInputSafetyApplied: runtimeInputSafetyApplied ? true : metadata.runtime_input_safety_applied === false ? false : null,
    targetStatus:
      targetStatusRaw ??
      (targetRejected
        ? "rejected"
        : runtimeInputSafetyApplied
          ? "held"
          : lastValidTargetPositionM === null
            ? null
            : "accepted"),
    targetRejected: targetRejected ? true : metadata.target_rejected === false ? false : null,
    targetRejectionReason: parseOptionalString(metadata.target_rejection_reason),
    targetRejectionMessage: parseOptionalString(metadata.target_rejection_message),
    localMotionPolicy: parseOptionalString(metadata.local_motion_policy),
    motionStatus: parseOptionalString(metadata.motion_status),
    motionRejectionReason: parseOptionalString(metadata.motion_rejection_reason),
    qposDeltaNormRad: parseOptionalFiniteNumber(metadata.qpos_delta_norm_rad),
    rejectedDesiredEndpointM,
    lastValidTargetPositionM,
    endpointEvaluationState,
    endpointEvaluationUnavailableReason,
    keyboardActiveKeyCodes: keyboard === null ? [] : parseStringArray(keyboard.active_key_codes),
    keyboardFocusState: keyboard === null ? null : parseOptionalString(keyboard.focus_state),
    keyboardZeroState: keyboard === null ? null : parseOptionalBoolean(keyboard.zero_state),
    keyboardKeyState: keyboard === null ? {} : parseBooleanRecord(keyboard.key_state),
    ...("gamepad_plane_control_v1" in metadata ? { gamepadPlaneControl: parseGamepadPlanePresentation(metadata.gamepad_plane_control_v1) } : {}),
    gamepadConnected: gamepad === null ? null : parseOptionalBoolean(gamepad.connected),
    gamepadIndex: gamepad === null ? null : parseOptionalInteger(gamepad.index),
    gamepadId: gamepad === null ? null : parseOptionalString(gamepad.id),
    // 診断でも軸を詰め直さず、既知の値列と不正・未取得を区別する。
    gamepadAxes: gamepad === null ? [] : Array.isArray(gamepad.axes) && gamepad.axes.every(isFiniteNumber)
      ? [...gamepad.axes] : null,
    gamepadInstrumentAxes: normalizedAxes(gamepad?.axes),
    gamepadInstrumentPressedButtons: pressedGamepadButtons(gamepad?.buttons),
    gamepadButtons: gamepad === null ? [] : parseInputOverlayButtons(gamepad.buttons),
    gamepadStale: gamepad === null ? null : parseOptionalBoolean(gamepad.stale),
    gamepadZeroState: gamepad === null ? null : parseOptionalBoolean(gamepad.zero_state),
  };
}

export function buildProductViewerInputOverlayState(
  payloadOrMetadata: TransportPayloadV0 | Record<string, unknown> | null | undefined,
): ProductViewerInputOverlayState | null {
  if (payloadOrMetadata === null || payloadOrMetadata === undefined) {
    return null;
  }

  if (!isRecord(payloadOrMetadata)) {
    return null;
  }

  return parseInputOverlayState(payloadOrMetadata);
}

function formatNumberList(values: readonly number[]): string {
  if (values.length === 0) {
    return "none";
  }

  return `[${values.map((value) => value.toFixed(4)).join(", ")}]`;
}

function formatKeyList(keys: readonly string[]): string {
  if (keys.length === 0) {
    return "none";
  }

  return keys.join(", ");
}

function formatButtonList(buttons: readonly ProductViewerInputOverlayButtonState[]): string {
  if (buttons.length === 0) {
    return "none";
  }

  return buttons
    .map((button, index) => {
      const value = button.value === null ? "" : ` ${button.value.toFixed(2)}`;
      return `${index}:${button.pressed === null ? "invalid" : button.pressed ? "pressed" : "released"}${value}`;
    })
    .join(", ");
}

function formatVector3List(values: readonly number[] | null): string {
  if (values === null) {
    return "n/a";
  }

  if (values.length === 0) {
    return "none";
  }

  return `[${values.map((value) => value.toFixed(4)).join(", ")}]`;
}

export function formatInputOverlayText(inputOverlay: ProductViewerInputOverlayState | null): string {
  if (inputOverlay === null) {
    return [
      "input source: unavailable",
      "intent kind: n/a",
      "input continuity: n/a",
      "active: n/a",
      "command age_ms: n/a",
      "stale reason: n/a",
      "runtime input safety: n/a",
      "target status: n/a",
      "target rejected: n/a",
      "target rejection reason: n/a",
      "target rejection message: n/a",
      "local motion policy: n/a",
      "motion status: n/a",
      "motion rejection reason: n/a",
      "qpos delta norm_rad: n/a",
      "axis values: none",
      "endpoint velocity_m_s: none",
      "endpoint delta_m: none",
      "rejected desired endpoint_m: n/a",
      "last valid target_m: n/a",
      "endpoint evaluation: unavailable",
      "endpoint evaluation unavailable reason: n/a",
      "keyboard active keys: none",
      "keyboard focus: n/a",
      "gamepad axes: none",
      "gamepad buttons: none",
    ].join("\n");
  }

  return [
    formatEndpointPresentationText(inputOverlay.endpointPresentation),
    `input source: ${inputOverlay.sourceKind}`,
    `viewer source kind: ${inputOverlay.viewerSourceKind ?? "n/a"}`,
    `intent kind: ${inputOverlay.intentKind ?? "n/a"}`,
    `input continuity: ${inputOverlay.inputContinuity ?? "n/a"}`,
    `active: ${inputOverlay.sourceActive ? "yes" : "no"}`,
    `sequence: ${inputOverlay.sequence === null ? "n/a" : String(inputOverlay.sequence)}`,
    `command age_ms: ${inputOverlay.commandAgeMs === null ? "n/a" : String(inputOverlay.commandAgeMs)}`,
    `stale reason: ${inputOverlay.staleReason ?? "none"}`,
    `runtime input safety: ${
      inputOverlay.runtimeInputSafetyApplied === null ? "n/a" : String(inputOverlay.runtimeInputSafetyApplied)
    }`,
    `target status: ${inputOverlay.targetStatus ?? "n/a"}`,
    `target rejected: ${inputOverlay.targetRejected === null ? "none" : String(inputOverlay.targetRejected)}`,
    `target rejection reason: ${inputOverlay.targetRejectionReason ?? "none"}`,
    `target rejection message: ${inputOverlay.targetRejectionMessage ?? "none"}`,
    `local motion policy: ${inputOverlay.localMotionPolicy ?? "n/a"}`,
    `motion status: ${inputOverlay.motionStatus ?? "n/a"}`,
    `motion rejection reason: ${inputOverlay.motionRejectionReason ?? "none"}`,
    `qpos delta norm_rad: ${inputOverlay.qposDeltaNormRad === null ? "n/a" : inputOverlay.qposDeltaNormRad.toFixed(6)}`,
    `axis values: ${formatNumberList(inputOverlay.axisValues)}`,
    `endpoint velocity_m_s: ${formatNumberList(inputOverlay.endpointVelocityMS)}`,
    `endpoint delta_m: ${formatNumberList(inputOverlay.endpointDeltaM)}`,
    `rejected desired endpoint_m: ${formatVector3List(inputOverlay.rejectedDesiredEndpointM)}`,
    `last valid target_m: ${formatVector3List(inputOverlay.lastValidTargetPositionM)}`,
    `endpoint evaluation: ${inputOverlay.endpointEvaluationState}`,
    `endpoint evaluation unavailable reason: ${inputOverlay.endpointEvaluationUnavailableReason ?? "none"}`,
    `keyboard active keys: ${formatKeyList(inputOverlay.keyboardActiveKeyCodes)}`,
    `keyboard focus: ${inputOverlay.keyboardFocusState ?? "n/a"}`,
    `keyboard zero state: ${inputOverlay.keyboardZeroState === null ? "n/a" : String(inputOverlay.keyboardZeroState)}`,
    `keyboard key state: ${Object.keys(inputOverlay.keyboardKeyState).length === 0 ? "none" : JSON.stringify(inputOverlay.keyboardKeyState)}`,
    `gamepad connected: ${inputOverlay.gamepadConnected === null ? "n/a" : String(inputOverlay.gamepadConnected)}`,
    `gamepad index: ${inputOverlay.gamepadIndex === null ? "n/a" : String(inputOverlay.gamepadIndex)}`,
    `gamepad id: ${inputOverlay.gamepadId ?? "n/a"}`,
    `gamepad axes: ${inputOverlay.gamepadAxes === null ? "unavailable / invalid" : formatNumberList(inputOverlay.gamepadAxes)}`,
    `gamepad buttons: ${formatButtonList(inputOverlay.gamepadButtons)}`,
    `gamepad stale: ${inputOverlay.gamepadStale === null ? "n/a" : String(inputOverlay.gamepadStale)}`,
    `gamepad zero state: ${inputOverlay.gamepadZeroState === null ? "n/a" : String(inputOverlay.gamepadZeroState)}`,
  ].join("\n");
}
