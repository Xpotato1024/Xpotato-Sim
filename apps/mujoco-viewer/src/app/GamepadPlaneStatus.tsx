/** 現行単腕へ出力する側と、左右独立のmodeを区別して表示する。 */
import type { GamepadPlanePresentation } from "./gamepadPlanePresentation.js";

export function GamepadPlaneStatus({ value, live }: { value: GamepadPlanePresentation | null; live: boolean }) {
  if (value === null) return null;
  return <section className="gamepad-plane-status" aria-label="スティックの操作平面" data-testid="gamepad-plane-status">
    <p className="inspector-note">単腕への適用: {value.outputSide === "left" ? "左スティック" : "右スティック"}
      {!live && " · 前回値（更新待ち）"}</p>
    {(["left", "right"] as const).map(side => {
      const item = value.sides[side];
      return <div key={side} className="gamepad-plane-row" data-testid={`plane-${side}`}>
        <strong>{side === "left" ? "左" : "右"} · {item.plane.toUpperCase()}</strong>
        <span>{item.status === "waiting_neutral" ? `${item.requestedPlane.toUpperCase()}へ切替待ち：スティックを中立へ` : "操作可能"}</span>
        <small>切替ボタン {item.modeButton} · {side === value.outputSide ? "単腕へ適用" : "入力診断のみ"}</small>
      </div>;
    })}
    {value.reason !== null && <p className="inspector-note">入力未取得・中立確認が必要です。</p>}
  </section>;
}
