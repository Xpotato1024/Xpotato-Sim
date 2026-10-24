import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {resolve} from "node:path";
import {createElement} from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {parseDeviceInputPresentation} from "../src/input/deviceInputPresentation.js";
import {DeviceInputPanel} from "../src/app/DeviceInputPanel.js";
import {inputKind} from "../src/ui/inputStripPresentation.js";
import {buildProductViewerInputOverlayState} from "../src/wasm-scene/productViewerState.js";

const event=JSON.parse(readFileSync(resolve("../../tests/fixtures/selfrionette_device/bridge-sample-v1.json"),"utf8"));
event.received_at_s=1;
const raw={schema_version:"selfrionette-device-snapshot/v1",unit:"1",source_active:true,devices:{left:event}};
const parsed=parseDeviceInputPresentation(raw)!;
assert.equal(inputKind("selfrionette-device/v1"),"selfrionette_device");
assert.deepEqual(parsed.devices[0].command,[.25,-.1,0,0,0,0,0]);
assert.equal(parsed.devices[0].sequence,81);
assert.equal(parsed.devices[0].deviceMs,2000);
const payload = {
  version: 0, frame_index: 1, time_s: 0, qpos: [], qvel: [], bodies: [], sites: [],
  target_position_m: null,
  metadata: {source_kind: "selfrionette-device/v1", source_active: true, selfrionette_device_input_v1: raw},
};
const overlay = buildProductViewerInputOverlayState(payload)!;
assert.equal(overlay.sourceKind, "selfrionette-device/v1");
assert.deepEqual(overlay.deviceNormalized, parsed);
assert.equal(overlay.rawSignal, null, "無次元commandを旧raw入力として投影しない");
assert.equal(buildProductViewerInputOverlayState({
  ...payload, metadata: {source_kind: "selfrionette-device/v1", source_active: false},
})?.deviceNormalized, null, "未取得を中立の7chへ補完しない");

const rightEvent = {...event, sequence: 42, device_ms: 1100, received_at_s: .99,
  source: {...event.source, device_id: "srn-fedcba9876543210fedcba9876543210"},
  command: [0, 0, 0, 0, 0, 0, 0]};
const dualOverlay = buildProductViewerInputOverlayState({...payload, metadata: {
  ...payload.metadata, selfrionette_device_input_v1: {...raw, devices: {right: rightEvent, left: event}},
}})!;
assert.deepEqual(dualOverlay.deviceNormalized?.devices.map(device => ({
  role: device.role, deviceId: device.deviceId, sequence: device.sequence,
  deviceMs: device.deviceMs, receivedAtS: device.receivedAtS, command: device.command,
})), [
  {role: "left", deviceId: event.source.device_id, sequence: 81, deviceMs: 2000, receivedAtS: 1,
    command: [.25, -.1, 0, 0, 0, 0, 0]},
  {role: "right", deviceId: rightEvent.source.device_id, sequence: 42, deviceMs: 1100, receivedAtS: .99,
    command: [0, 0, 0, 0, 0, 0, 0]},
], "左右の元sampleを保持し、取得済み中立を欠測と区別する");
for(const invalid of [{...raw,unit:"N"},{...raw,source_active:false},
  {...raw,devices:{left:{...event,quality_flags:[1,2,1,1,1,1,1]}}},
  {...raw,devices:{left:{...event,command:[null,0,0,0,0,0,0]}}},
  {...raw,devices:{left:{...event,span_exceeded:[true,false,false,false,false,false,false]}}},
  {...raw,devices:{left:event,right:event}}]) {
  assert.equal(parseDeviceInputPresentation(invalid),null);
  assert.equal(buildProductViewerInputOverlayState({...payload, metadata: {
    ...payload.metadata, selfrionette_device_input_v1: invalid,
  }})?.deviceNormalized, null, "破損・失効したDevice入力をViewerへ投影しない");
}

const panel=renderToStaticMarkup(createElement(DeviceInputPanel,{enabled:true,onAction(){throw new Error("render may not operate a device");},
  value:{ready:false,error:null,roles:{left:{phase:"configured",ready:false,pending:null,clock_synchronized:true,session_neutral_valid:false,error:null,
    expected:{source:event.source,profile:event.profile,operator:event.operator}}}}}));
assert.match(panel,/個体を確認して接続/);
assert.match(panel,/使用姿勢で力を抜いてneutral取得/);
assert.equal((panel.match(/disabled=""/g)??[]).length,3,"次の個別操作以外は無効");
assert.doesNotMatch(panel,/COM9|profile_path/);
console.log("Device normalized units, single/dual overlays, quality rejection and explicit preparation projection passed");
