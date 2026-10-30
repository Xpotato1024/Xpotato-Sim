/** 描画、接続、受信鮮度を別々に表示する。安全判定や実機許可は生成しない。 */
import type { ProductViewerState } from "../wasm-scene/productViewerState.js";

export type WorkbenchTone = "neutral" | "positive" | "warning" | "danger";
export interface WorkbenchConnection {
  label: string;
  detail: string;
  tone: WorkbenchTone;
  ageMs: number | null;
}
// UIの受信更新表示用。backendの入力timeoutやphysical safety条件ではない。
export const VIEWER_UPDATE_STALE_MS = 1000;

export function describeWorkbenchConnection(state: ProductViewerState, nowMs: number): WorkbenchConnection {
  const stamp = state.viewerTiming?.latestReceivedAtMs ?? null;
  const age = stamp !== null && Number.isFinite(stamp) && Number.isFinite(nowMs) && nowMs >= stamp
    ? nowMs - stamp : null;
  if (state.connectionStatus === "disabled") {
    return { label: "オフライン表示", detail: "接続先未指定・初期姿勢または読込みデータ", tone: "neutral", ageMs: null };
  }
  if (state.connectionStatus === "closed") {
    return { label: "配信終了", detail: "最終受信値を表示中・liveではありません", tone: "warning", ageMs: age };
  }
  if (state.connectionStatus === "error") {
    return { label: "接続エラー", detail: "起動terminalと接続先を確認してください", tone: "danger", ageMs: age };
  }
  if (state.connectionStatus !== "open") {
    return { label: "接続中", detail: "backendへの接続を待っています", tone: "neutral", ageMs: null };
  }
  if (state.qposStatus === "invalid" || state.qposStatus === "unavailable" ||
      ["parse_error", "compatibility_invalid"].includes(state.viewerTiming?.latestIngressStatus ?? "")) {
    return { label: "受信データ不正", detail: "有効な姿勢として扱いません・診断を確認", tone: "danger", ageMs: age };
  }
  if (age === null) {
    return { label: "接続済み・受信待ち", detail: "有効な更新時刻がまだありません", tone: "neutral", ageMs: null };
  }
  if (age > VIEWER_UPDATE_STALE_MS) {
    return { label: "更新停止", detail: "接続はopenですが新しいpayloadが届いていません", tone: "warning", ageMs: age };
  }
  return { label: "受信中", detail: "MuJoCo payloadを表示・実機安全性の判定ではありません", tone: "positive", ageMs: age };
}

export function formatWorkbenchAge(age: number | null): string {
  if (age === null) return "受信時刻なし";
  return age < 1000 ? `${Math.floor(age)} ms` : `${(age / 1000).toFixed(1)} s`;
}

/** RTFの最後の標本を停止後のlive値と取り違えない。 */
export function formatWorkbenchRtf(phase: string | null | undefined, value: number | null | undefined,
  windowSeconds?: number | null): string {
  const measured = value !== null && value !== undefined && Number.isFinite(value) && value >= 0;
  const suffix = measured && windowSeconds !== null && windowSeconds !== undefined && Number.isFinite(windowSeconds) && windowSeconds > 0
    ? `（観測窓 ${windowSeconds.toFixed(1)} s）` : "";
  if (phase === "running") return `実行中 RTF: ${measured ? value.toFixed(2) : "未計測"}${suffix}`;
  if (["terminal", "finalizing", "faulted", "recording_failed"].includes(phase ?? ""))
    return `停止時の最終 RTF: ${measured ? value.toFixed(2) : "未計測"}${suffix}`;
  return "RTF: 未計測";
}
