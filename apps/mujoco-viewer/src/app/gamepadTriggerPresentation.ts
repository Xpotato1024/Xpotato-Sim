export type TriggerSide = "left" | "right";
export type TriggerOutputScope = "single_endpoint" | "coordinated";

export interface TriggerSidePresentation {
  status: "armed" | "waiting_trigger_neutral";
  zSign: 1 | -1;
  triggerValue: number;
  triggerButton: number;
  signButton: number;
  velocity: [number, number, number];
}

export interface GamepadTriggerPresentation {
  outputScope: TriggerOutputScope;
  outputSide: TriggerSide | null;
  endpointBindings: Partial<Record<TriggerSide, string>>;
  reason: string | null;
  sides: Record<TriggerSide, TriggerSidePresentation>;
}

const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
export function parseGamepadTriggerPresentation(value: unknown): GamepadTriggerPresentation | null {
  if (!record(value) || value.schema !== "gamepad-trigger-control/v1" ||
      (value.output_scope !== "single_endpoint" && value.output_scope !== "coordinated") ||
      !record(value.sides) || (value.reason !== null && typeof value.reason !== "string")) return null;
  const outputScope = value.output_scope;
  const outputSide = value.output_side;
  if (outputScope === "single_endpoint") {
    if (outputSide !== "left" && outputSide !== "right") return null;
  } else if (outputSide !== null) {
    return null;
  }

  const endpointBindings: Partial<Record<TriggerSide, string>> = {};
  if (outputScope === "coordinated") {
    if (!record(value.endpoint_bindings)) return null;
    const entries = Object.entries(value.endpoint_bindings);
    if (entries.length < 1 || entries.length > 2 ||
        new Set(entries.map(([, name]) => name)).size !== entries.length) return null;
    for (const [side, name] of entries) {
      if ((side !== "left" && side !== "right") || typeof name !== "string" ||
          !/^[a-z][a-z0-9_]{0,31}$/.test(name)) return null;
      endpointBindings[side] = name;
    }
  }
  const sides = {} as Record<TriggerSide, TriggerSidePresentation>;
  for (const side of ["left", "right"] as const) {
    const item = value.sides[side];
    if (!record(item) ||
        (item.status !== "armed" && item.status !== "waiting_trigger_neutral") ||
        (item.z_sign !== 1 && item.z_sign !== -1) ||
        typeof item.trigger_value !== "number" || !Number.isFinite(item.trigger_value) ||
        item.trigger_value < 0 || item.trigger_value > 1 ||
        typeof item.trigger_button !== "number" || !Number.isInteger(item.trigger_button) || item.trigger_button < 0 ||
        typeof item.sign_button !== "number" || !Number.isInteger(item.sign_button) || item.sign_button < 0 ||
        !Array.isArray(item.velocity_m_s) || item.velocity_m_s.length !== 3 ||
        !item.velocity_m_s.every(v => typeof v === "number" && Number.isFinite(v))) return null;
    if (item.status === "waiting_trigger_neutral" && item.velocity_m_s[2] !== 0) return null;
    sides[side] = {
      status: item.status,
      zSign: item.z_sign,
      triggerValue: item.trigger_value,
      triggerButton: item.trigger_button,
      signButton: item.sign_button,
      velocity: [...item.velocity_m_s] as [number, number, number],
    };
  }
  return {
    outputScope,
    endpointBindings,
    outputSide: outputScope === "single_endpoint" ? outputSide as TriggerSide : null,
    reason: value.reason,
    sides,
  };
}
