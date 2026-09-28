import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server";
import { GamepadTriggerStatus } from "../src/app/GamepadTriggerStatus.js";
import { parseGamepadTriggerPresentation } from "../src/app/gamepadTriggerPresentation.js";
import { buildProductViewerInputOverlayState } from "../src/wasm-scene/productViewerState.js";

const value = {
  schema: "gamepad-trigger-control/v1",
  output_scope: "single_endpoint",
  output_side: "left",
  reason: null,
  sides: {
    left: {
      status: "armed", z_sign: -1, trigger_value: .55,
      trigger_button: 6, sign_button: 4, velocity_m_s: [.02, .01, -.05],
    },
    right: {
      status: "waiting_trigger_neutral", z_sign: 1, trigger_value: .7,
      trigger_button: 7, sign_button: 5, velocity_m_s: [0, 0, 0],
    },
  },
};

const parsed = parseGamepadTriggerPresentation(value);
assert.ok(parsed);
assert.equal(parsed.sides.left.zSign, -1);
assert.equal(parsed.sides.left.triggerValue, .55);
assert.equal(parsed.outputSide, "left");
const html = renderToStaticMarkup(
  <GamepadTriggerStatus value={parsed} live={true} />,
);
assert.match(html, /左 · stick XY/);
assert.match(html, /LT -Z/);
assert.match(html, /LBで符号/);
assert.doesNotMatch(html, /右 · stick XY/);
assert.match(
  renderToStaticMarkup(<GamepadTriggerStatus value={parsed} live={false} />),
  /前回値/,
);
assert.equal(
  renderToStaticMarkup(<GamepadTriggerStatus value={null} live={true} />),
  "",
);

const overlay = buildProductViewerInputOverlayState({
  source_kind: "viewer_gamepad",
  gamepad_trigger_control_v1: value,
});
assert.ok(overlay?.gamepadTriggerControl);
assert.equal(overlay.gamepadTriggerControl.sides.left.zSign, -1);
assert.equal(
  buildProductViewerInputOverlayState({ source_kind: "viewer_gamepad" })?.gamepadTriggerControl,
  undefined,
);
const coordinated = parseGamepadTriggerPresentation({
  ...value,
  output_scope: "coordinated",
  output_side: null,
  endpoint_bindings: { left: "left", right: "right" },
});
assert.ok(coordinated);
const coordinatedHtml = renderToStaticMarkup(
  <GamepadTriggerStatus value={coordinated} live={true} />,
);
assert.match(coordinatedHtml, /leftへ適用/);
assert.match(coordinatedHtml, /rightへ適用/);
assert.match(coordinatedHtml, /RT \+Z/);

for (const bad of [
  null,
  {},
  { ...value, schema: "v2" },
  { ...value, output_side: "both" },
  { ...value, sides: { left: value.sides.left } },
  { ...value, sides: { ...value.sides, left: { ...value.sides.left, z_sign: 0 } } },
  { ...value, sides: { ...value.sides, left: { ...value.sides.left, trigger_value: 2 } } },
  { ...value, output_scope: "coordinated", output_side: "left" },
]) {
  assert.equal(parseGamepadTriggerPresentation(bad), null);
}

const stopped = renderToStaticMarkup(
  <GamepadTriggerStatus value={coordinated} live={false} motionStatus="faulted" />,
);
assert.match(stopped, /モデル全体停止/);
assert.doesNotMatch(stopped, /前回値/);
console.log("gamepad trigger presentation and compact markup tests passed");
