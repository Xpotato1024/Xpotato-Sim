/** backendが確定した左右独立の操作平面を表示する。 */
import type { GamepadPlanePresentation } from "./gamepadPlanePresentation.js";

export function GamepadPlaneStatus({ value, live, motionStatus }: {
  value: GamepadPlanePresentation | null; live: boolean; motionStatus?: string | null;
}) {
  if (value === null) return null;
  const coordinated = value.outputScope === "coordinated";
  const stopped = coordinated && (motionStatus === "faulted" || motionStatus === "stopped");
  const scopeLabel = coordinated
    ? `モデル手先への適用: ${Object.values(value.endpointBindings).join(" / ")}`
    : `単腕への適用: ${value.outputSide === "left" ? "左スティック" : "右スティック"}`;
  return <section className="gamepad-plane-status" aria-label="スティックの操作平面" data-testid="gamepad-plane-status">
    <p className="inspector-note">{scopeLabel}{!live && !stopped && " · 前回値（更新待ち）"}</p>
    {stopped && <p className="inspector-note tone-warning">モデル全体停止：設定を確認して新しいsessionで再起動してください。</p>}
    {(["left", "right"] as const).map(side => {
      const item = value.sides[side];
      const destination = coordinated
        ? (value.endpointBindings[side] ? `${value.endpointBindings[side]}へ適用` : "入力診断のみ")
        : side === value.outputSide ? "単腕へ適用" : "入力診断のみ";
      return <div key={side} className="gamepad-plane-row" data-testid={`plane-${side}`}>
        <strong>{side === "left" ? "左" : "右"} · {item.plane.toUpperCase()}</strong>
        <span>{stopped ? "停止中" : item.status === "waiting_neutral" ? `${item.requestedPlane.toUpperCase()}へ切替待ち：スティックを中立へ` : "操作可能"}</span>
        <small>切替ボタン {item.modeButton} · {destination}</small>
      </div>;
    })}
    {value.reason !== null && <p className="inspector-note">入力未取得・中立確認または全体faultの確認が必要です。</p>}
  </section>;
}
