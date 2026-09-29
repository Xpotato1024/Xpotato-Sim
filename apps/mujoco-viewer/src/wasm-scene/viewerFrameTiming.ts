import type { TransportPayloadV0 } from "../types/transportPayload.js";
import type { ViewerWebSocketPayloadObservation } from "../transport/websocketClient.js";

const MAX_TIMING_SAMPLES = 512;

export interface ViewerPayloadCandidate {
  payload: TransportPayloadV0;
  receivedAtMs: number;
  parseDurationMs: number;
}

export interface ViewerFrameTimingSnapshot {
  receivedFrameCount: number;
  compatibilityAcceptedFrameCount: number;
  compatibilityInvalidFrameCount: number;
  sceneAppliedFrameCount: number;
  coalescedFrameCount: number;
  parseErrorCount: number;
  latestReceivedFrameIndex: number | null;
  latestReceivedAtMs: number | null;
  latestCompatibilityAcceptedFrameIndex: number | null;
  latestSceneAppliedFrameIndex: number | null;
  latestIngressStatus: "none" | "received" | "accepted" | "compatibility_invalid" | "parse_error";
  receivedToAppliedFrameDistance: number | null;
  receiveToApplyAgeMsP50: number | null;
  receiveToApplyAgeMsP95: number | null;
  receiveToApplyAgeMsMax: number | null;
  parseDurationMsP50: number | null;
  parseDurationMsP95: number | null;
  parseDurationMsMax: number | null;
  sceneApplyDurationMsP50: number | null;
  sceneApplyDurationMsP95: number | null;
  sceneApplyDurationMsMax: number | null;
  uiStateUpdateCount: number;
  uiStateUpdateFrequencyHz: number;
}

export interface ViewerFrameTiming {
  receive(payload: TransportPayloadV0, observation: ViewerWebSocketPayloadObservation): void;
  acceptLatestCandidate(payload: TransportPayloadV0, observation: ViewerWebSocketPayloadObservation): void;
  recordCompatibilityInvalidIngress(): void;
  recordParseError(): void;
  takeLatestCandidate(): ViewerPayloadCandidate | null;
  recordSceneApplied(candidate: ViewerPayloadCandidate, sceneApplyDurationMs: number): void;
  recordUiStateUpdate(): void;
  snapshot(): ViewerFrameTimingSnapshot;
  now(): number;
  dispose(): void;
}

/** 標本が変わった時だけ一度sortし、同じpercentileの反復計算を避ける。 */
class TimingSamples {
  private values: number[] = [];
  private cached: { p50: number | null; p95: number | null; max: number | null } | null = null;
  append(value: number): void {
    this.values.push(Math.max(0, value));
    if (this.values.length > MAX_TIMING_SAMPLES) this.values.shift();
    this.cached = null;
  }
  summary(): { p50: number | null; p95: number | null; max: number | null } {
    if (this.cached === null) {
      const sorted = [...this.values].sort((a, b) => a - b);
      const at = (fraction: number): number | null => sorted.length === 0 ? null
        : sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil(sorted.length * fraction) - 1))];
      this.cached = { p50: at(0.5), p95: at(0.95), max: at(1) };
    }
    return this.cached;
  }
}

export function createViewerFrameTiming(
  monotonicNow: () => number = () => performance.now(),
): ViewerFrameTiming {
  const startedAtMs = monotonicNow();
  const receiveToApplyAgeMs = new TimingSamples();
  const parseDurationMs = new TimingSamples();
  const sceneApplyDurationMs = new TimingSamples();
  let pending: ViewerPayloadCandidate | null = null;
  let disposed = false;
  let receivedFrameCount = 0;
  let compatibilityAcceptedFrameCount = 0;
  let compatibilityInvalidFrameCount = 0;
  let sceneAppliedFrameCount = 0;
  let coalescedFrameCount = 0;
  let parseErrorCount = 0;
  let latestReceivedFrameIndex: number | null = null;
  let latestReceivedAtMs: number | null = null;
  let latestCompatibilityAcceptedFrameIndex: number | null = null;
  let latestSceneAppliedFrameIndex: number | null = null;
  let latestIngressStatus: ViewerFrameTimingSnapshot["latestIngressStatus"] = "none";
  let uiStateUpdateCount = 0;

  return {
    receive(payload, observation) {
      if (disposed) {
        return;
      }
      receivedFrameCount += 1;
      latestReceivedFrameIndex = payload.frame_index;
      latestReceivedAtMs = observation.receivedAtMs;
      latestIngressStatus = "received";
      parseDurationMs.append(observation.parseDurationMs);
    },
    acceptLatestCandidate(payload, observation) {
      if (disposed) {
        return;
      }
      compatibilityAcceptedFrameCount += 1;
      latestCompatibilityAcceptedFrameIndex = payload.frame_index;
      latestIngressStatus = "accepted";
      if (pending !== null) {
        coalescedFrameCount += 1;
      }
      pending = {
        payload,
        receivedAtMs: observation.receivedAtMs,
        parseDurationMs: observation.parseDurationMs,
      };
    },
    recordCompatibilityInvalidIngress() {
      if (disposed) {
        return;
      }
      pending = null;
      compatibilityInvalidFrameCount += 1;
      latestIngressStatus = "compatibility_invalid";
    },
    recordParseError() {
      if (disposed) {
        return;
      }
      pending = null;
      parseErrorCount += 1;
      latestIngressStatus = "parse_error";
    },
    takeLatestCandidate() {
      if (disposed) {
        return null;
      }
      const candidate = pending;
      pending = null;
      return candidate;
    },
    recordSceneApplied(candidate, durationMs) {
      if (disposed) {
        return;
      }
      sceneAppliedFrameCount += 1;
      latestSceneAppliedFrameIndex = candidate.payload.frame_index;
      receiveToApplyAgeMs.append(monotonicNow() - candidate.receivedAtMs);
      sceneApplyDurationMs.append(durationMs);
    },
    recordUiStateUpdate() {
      if (!disposed) {
        uiStateUpdateCount += 1;
      }
    },
    snapshot() {
      const elapsedS = Math.max(0, monotonicNow() - startedAtMs) / 1000;
      const receivedToAppliedFrameDistance =
        latestReceivedFrameIndex === null || latestSceneAppliedFrameIndex === null
          ? null
          : Math.max(0, latestReceivedFrameIndex - latestSceneAppliedFrameIndex);
      return {
        receivedFrameCount,
        compatibilityAcceptedFrameCount,
        compatibilityInvalidFrameCount,
        sceneAppliedFrameCount,
        coalescedFrameCount,
        parseErrorCount,
        latestReceivedFrameIndex,
        latestReceivedAtMs,
        latestCompatibilityAcceptedFrameIndex,
        latestSceneAppliedFrameIndex,
        latestIngressStatus,
        receivedToAppliedFrameDistance,
        receiveToApplyAgeMsP50: receiveToApplyAgeMs.summary().p50,
        receiveToApplyAgeMsP95: receiveToApplyAgeMs.summary().p95,
        receiveToApplyAgeMsMax: receiveToApplyAgeMs.summary().max,
        parseDurationMsP50: parseDurationMs.summary().p50,
        parseDurationMsP95: parseDurationMs.summary().p95,
        parseDurationMsMax: parseDurationMs.summary().max,
        sceneApplyDurationMsP50: sceneApplyDurationMs.summary().p50,
        sceneApplyDurationMsP95: sceneApplyDurationMs.summary().p95,
        sceneApplyDurationMsMax: sceneApplyDurationMs.summary().max,
        uiStateUpdateCount,
        uiStateUpdateFrequencyHz: elapsedS > 0 ? uiStateUpdateCount / elapsedS : 0,
      };
    },
    now() {
      return monotonicNow();
    },
    dispose() {
      disposed = true;
      pending = null;
    },
  };
}
