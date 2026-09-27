/** backendが決めた平面状態の表示専用decoder。ボタンからmodeを再計算しない。 */
export type PlaneSide = "left" | "right";
export interface StickPlanePresentation {
  plane: "xy" | "xz";
  requestedPlane: "xy" | "xz";
  status: "armed" | "waiting_neutral";
  modeButton: number;
  velocity: [number, number, number];
}
export interface GamepadPlanePresentation {
  outputSide: PlaneSide;
  reason: string | null;
  sides: Record<PlaneSide, StickPlanePresentation>;
}
const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const plane = (value: unknown): value is "xy" | "xz" => value === "xy" || value === "xz";

export function parseGamepadPlanePresentation(value: unknown): GamepadPlanePresentation | null {
  if (!record(value) || value.schema !== "gamepad-plane-control/v1" || value.output_scope !== "single_endpoint" ||
      (value.output_side !== "left" && value.output_side !== "right") || !record(value.sides) ||
      (value.reason !== null && typeof value.reason !== "string")) return null;
  const sides = {} as Record<PlaneSide, StickPlanePresentation>;
  for (const side of ["left", "right"] as const) {
    const item = value.sides[side];
    if (!record(item) || !plane(item.plane) || !plane(item.requested_plane) ||
        (item.status !== "armed" && item.status !== "waiting_neutral") ||
        typeof item.mode_button !== "number" || !Number.isInteger(item.mode_button) || item.mode_button < 0 ||
        !Array.isArray(item.velocity_m_s) || item.velocity_m_s.length !== 3 ||
        !item.velocity_m_s.every(v => typeof v === "number" && Number.isFinite(v))) return null;
    if (item.status === "waiting_neutral" && item.velocity_m_s.some(v => v !== 0)) return null;
    if (item.status === "armed" && item.plane !== item.requested_plane) return null;
    sides[side] = { plane: item.plane, requestedPlane: item.requested_plane, status: item.status,
                    modeButton: item.mode_button, velocity: [...item.velocity_m_s] as [number, number, number] };
  }
  return { outputSide: value.output_side, reason: value.reason, sides };
}
