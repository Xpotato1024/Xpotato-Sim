import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server";
import { GamepadPlaneStatus } from "../src/app/GamepadPlaneStatus.js";
import { parseGamepadPlanePresentation } from "../src/app/gamepadPlanePresentation.js";
import { buildProductViewerInputOverlayState } from "../src/wasm-scene/productViewerState.js";

const value = {
  schema: "gamepad-plane-control/v1", output_scope: "single_endpoint", output_side: "left", reason: null,
  sides: {
    left: { plane: "xy", requested_plane: "xz", status: "waiting_neutral", mode_button: 4, velocity_m_s: [0,0,0] },
    right: { plane: "xz", requested_plane: "xz", status: "armed", mode_button: 5, velocity_m_s: [0,0,.05] },
  },
};
const parsed = parseGamepadPlanePresentation(value);
assert.ok(parsed);
assert.equal(parsed.sides.left.requestedPlane, "xz");
assert.equal(parsed.sides.right.status, "armed");
assert.equal(parsed.outputSide, "left");
const html = renderToStaticMarkup(<GamepadPlaneStatus value={parsed} live={true} />);
assert.match(html, /左スティック/);
assert.match(html, /中立へ/);
assert.match(html, /入力診断のみ/);
assert.match(html, /右 · XZ/);
assert.match(renderToStaticMarkup(<GamepadPlaneStatus value={parsed} live={false} />), /前回値/);
assert.equal(renderToStaticMarkup(<GamepadPlaneStatus value={null} live={true} />), "");
for (const bad of [null, {}, {...value,schema:"v2"}, {...value,output_side:"both"},
    {...value,sides:{left:value.sides.left}}, {...value,reason:42},
    {...value,sides:{...value.sides,left:{...value.sides.left,velocity_m_s:[0,1,0]}}},
    {...value,sides:{...value.sides,right:{...value.sides.right,velocity_m_s:[0,0,NaN]}}}]) {
  assert.equal(parseGamepadPlanePresentation(bad), null);
}
value.sides.right.velocity_m_s[2] = .09;
assert.equal(parsed.sides.right.velocity[2], .05);
const overlay = buildProductViewerInputOverlayState({source_kind:"viewer_gamepad", gamepad_plane_control_v1:value});
assert.ok(overlay?.gamepadPlaneControl);
assert.equal(overlay.gamepadPlaneControl.sides.right.velocity[2], .09);
assert.equal(buildProductViewerInputOverlayState({source_kind:"viewer_gamepad"})?.gamepadPlaneControl, undefined);
console.log("gamepad plane presentation and markup tests passed");
