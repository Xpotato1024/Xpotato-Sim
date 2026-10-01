import assert from "node:assert/strict";
import {existsSync} from "node:fs";
import {buildProductViewerInputOverlayState} from "../src/wasm-scene/productViewerState.js";
assert.equal(existsSync("src/app/gamepadPlanePresentation.ts"),false);
assert.equal(existsSync("src/app/GamepadPlaneStatus.tsx"),false);
const overlay=buildProductViewerInputOverlayState({source_kind:"viewer_gamepad",gamepad_plane_control_v1:{schema:"gamepad-plane-control/v1"}});
assert.equal(overlay && "gamepadPlaneControl" in overlay,false);
console.log("retired gamepad plane checks passed");
