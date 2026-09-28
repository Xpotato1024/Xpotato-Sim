import type { GamepadTriggerPresentation } from "./gamepadTriggerPresentation.js";

const SIDE_LABEL = { left: "左", right: "右" } as const;
const TRIGGER_LABEL = { left: "LT", right: "RT" } as const;
const SIGN_LABEL = { left: "LB", right: "RB" } as const;

export function GamepadTriggerStatus({ value, live, motionStatus }: {
  value: GamepadTriggerPresentation | null; live: boolean; motionStatus?: string | null;
}) {
  if (value === null) return null;
  const coordinated = value.outputScope === "coordinated";
  const stopped = coordinated && (motionStatus === "faulted" || motionStatus === "stopped");
  return <section className="gamepad-trigger-status" aria-label="Gamepad XYZ操作" data-testid="gamepad-trigger-status">
    {(["left", "right"] as const).map(side => {
      const item = value.sides[side];
      const assigned = coordinated ? value.endpointBindings[side] : side === value.outputSide ? "単腕" : null;
      if (assigned === null || assigned === undefined) return null;
      const direction = item.zSign > 0 ? "+Z" : "-Z";
      return <div key={side} className="gamepad-trigger-row" data-testid={`trigger-${side}`}>
        <strong>{SIDE_LABEL[side]} · stick XY</strong>
        <span>{TRIGGER_LABEL[side]} {direction} · {SIGN_LABEL[side]}で符号</span>
        <small>{assigned}へ適用 · trigger {item.triggerValue.toFixed(2)}
          {item.status === "waiting_trigger_neutral" ? " · triggerを離して符号確定" : ""}</small>
      </div>;
    })}
    {!live && !stopped && <p className="inspector-note">前回値（更新待ち）</p>}
    {stopped && <p className="inspector-note tone-warning">モデル全体停止</p>}
    {value.reason !== null && <p className="inspector-note">入力状態を確認してください。</p>}
  </section>;
}
