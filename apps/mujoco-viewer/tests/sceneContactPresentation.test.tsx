import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server";
import { SceneContactPanel } from "../src/app/SceneContactPanel.js";
import { parseSceneContactPayload, SceneContactStream, unavailableSceneContact } from "../src/contact/sceneContactPresentation.js";
import { SceneContactOverlay } from "../src/wasm-scene/sceneContactOverlay.js";
import type { TransportPayloadV0 } from "../src/types/transportPayload.js";

function fixture(frame=2,time=.2) {
  return {
    frame_index:frame,time_s:time,qpos:[0],metadata:{
      model_sha256:"a".repeat(64),
      coordinated_runtime_v1:{epoch:"trial-1",arm_ids:["left"]},
      scene_contact_binding_v1:{schema_version:"scene-contact-binding/v1",model_sha256:"a".repeat(64),scene_digest:"b".repeat(64),epoch:"trial-1",endpoint_ids:["left"],object_ids:["cube"]},
      scene_contact_geometry_v1:{schema_version:"scene-contact-geometry/v1",model_sha256:"a".repeat(64),scene_digest:"b".repeat(64),frame_index:frame,simulation_time_s:time,status:"observed",scope:"tool_object_geometry",force_status:"not_evaluated_kinematic",force_n:null,
        objects:[{instance_id:"cube",position_m:[.3,.4,.5],orientation_wxyz:[1,0,0,0]}],
        contacts:[{endpoint_id:"left",object_id:"cube",tool_geom_name:"left_tool",object_geom_name:"object_cube",point_world_m:[.25,.4,.5],normal_world:[1,0,0],distance_m:-.002,penetration_m:.002,relation:"penetrating"}]},
      scene_contact_task_v1:{schema_version:"contact-observation-presentation/v1",classification:"running",phase:"observing",reason:null as string|null,epoch:"trial-1",scene_digest:"b".repeat(64),target_object_ids:["cube"],observed_pairs:[["left","cube"]],force_evaluated:false,frame_index:frame,simulation_time_s:time,presentation_frame_index:frame,presentation_time_s:time}
    }
  };
}
const payload=(x:ReturnType<typeof fixture>)=>x as unknown as TransportPayloadV0;
const good=parseSceneContactPayload(payload(fixture()));
assert.equal(good.status,"available");
if(good.status!=="available")throw Error("expected available");
assert.equal(good.contacts[0].penetrationM,.002);
const html=renderToStaticMarkup(<SceneContactPanel value={good} live={true}/>);
assert.match(html,/接触診断/);assert.match(html,/食い込み/);assert.match(html,/2.00 mm/);assert.match(html,/力：評価対象外/);
assert.match(html,/data-frame-index="2"/);
assert.equal(renderToStaticMarkup(<SceneContactPanel value={{status:"absent",reason:"none"}} live={true}/>),"");
assert.match(renderToStaticMarkup(<SceneContactPanel value={unavailableSceneContact("invalid")} live={true}/>),/invalid/);
for(const mutate of [
  (p:ReturnType<typeof fixture>)=>{p.metadata.scene_contact_geometry_v1.frame_index--},
  (p:ReturnType<typeof fixture>)=>{p.metadata.scene_contact_geometry_v1.simulation_time_s+=.1},
  (p:ReturnType<typeof fixture>)=>{p.metadata.scene_contact_geometry_v1.model_sha256="f".repeat(64)},
  (p:ReturnType<typeof fixture>)=>{p.metadata.scene_contact_geometry_v1.scene_digest="f".repeat(64)},
  (p:ReturnType<typeof fixture>)=>{p.metadata.scene_contact_geometry_v1.contacts[0].normal_world=[0,0,0]},
  (p:ReturnType<typeof fixture>)=>{p.metadata.scene_contact_geometry_v1.contacts[0].penetration_m=0},
  (p:ReturnType<typeof fixture>)=>{p.metadata.scene_contact_geometry_v1.contacts[0].relation="near"},
  (p:ReturnType<typeof fixture>)=>{p.metadata.scene_contact_geometry_v1.contacts[0].point_world_m=[NaN,0,0]},
  (p:ReturnType<typeof fixture>)=>{p.metadata.scene_contact_geometry_v1.contacts[0].object_id="other"},
  (p:ReturnType<typeof fixture>)=>{p.metadata.scene_contact_geometry_v1.contacts[0].endpoint_id="right"},
  (p:ReturnType<typeof fixture>)=>{p.metadata.scene_contact_geometry_v1.objects.push(p.metadata.scene_contact_geometry_v1.objects[0])},
  (p:ReturnType<typeof fixture>)=>{p.metadata.scene_contact_geometry_v1.objects[0].orientation_wxyz=[2,0,0,0]},
  (p:ReturnType<typeof fixture>)=>{p.metadata.scene_contact_task_v1.force_evaluated=true},
  (p:ReturnType<typeof fixture>)=>{p.metadata.scene_contact_task_v1.epoch="other"},
  (p:ReturnType<typeof fixture>)=>{p.metadata.scene_contact_task_v1.target_object_ids=["missing"]},
  (p:ReturnType<typeof fixture>)=>{p.metadata.scene_contact_task_v1.presentation_frame_index--},
  (p:ReturnType<typeof fixture>)=>{p.metadata.scene_contact_task_v1.classification="success"},
  (p:ReturnType<typeof fixture>)=>{p.metadata.scene_contact_binding_v1.object_ids.push("cube")},
  (p:ReturnType<typeof fixture>)=>{p.metadata.coordinated_runtime_v1.arm_ids=["right"]},
]){
  const p=fixture();mutate(p);assert.equal(parseSceneContactPayload(payload(p)).status,"unavailable");
}
const extra=fixture();Object.assign(extra.metadata.scene_contact_geometry_v1,{unknown:true});assert.equal(parseSceneContactPayload(payload(extra)).status,"unavailable");
const force=fixture();Object.assign(force.metadata.scene_contact_geometry_v1,{force_n:0});assert.equal(parseSceneContactPayload(payload(force)).status,"unavailable");
const near=fixture();Object.assign(near.metadata.scene_contact_geometry_v1.contacts[0],{distance_m:.001,penetration_m:0,relation:"near"});assert.equal(parseSceneContactPayload(payload(near)).status,"available");
const terminal=fixture(3,.2);Object.assign(terminal.metadata.scene_contact_task_v1,{phase:"completed",classification:"success",frame_index:2,reason:"window complete"});assert.equal(parseSceneContactPayload(payload(terminal)).status,"available");
const stream=new SceneContactStream();
assert.equal(stream.apply(payload(fixture())).status,"available");
assert.equal(stream.apply(payload(fixture())).status,"unavailable");
assert.equal(stream.apply(payload(fixture(1,.1))).status,"unavailable");
assert.equal(stream.apply(payload(terminal)).status,"available");
const changed=fixture(4,.2);changed.metadata.scene_contact_binding_v1.scene_digest="c".repeat(64);changed.metadata.scene_contact_geometry_v1.scene_digest="c".repeat(64);changed.metadata.scene_contact_task_v1.scene_digest="c".repeat(64);
assert.equal(stream.apply(payload(changed)).status,"unavailable");
assert.equal(stream.apply({metadata:{}} as TransportPayloadV0).status,"unavailable");

// No WebGL is required: pooled read-only overlay has exactly point+normal, not another cube.
const overlay=new SceneContactOverlay();overlay.update(good);assert.equal(overlay.group.children.length,2);
const first=overlay.group.children[0];for(let i=0;i<100;i++)overlay.update(good);
assert.equal(overlay.group.children[0],first);assert.equal(overlay.group.children.length,2);
assert.deepEqual([first.position.x,first.position.y,first.position.z],good.contacts[0].point);
overlay.update(unavailableSceneContact("stale"));assert.ok(overlay.group.children.every(o=>!o.visible));
overlay.update(good);assert.ok(overlay.group.children.every(o=>o.visible));
overlay.dispose();assert.equal(overlay.group.children.length,0);overlay.dispose();
console.log("scene contact presentation / stream / pooled overlay checks passed");


import { resolveGeomDisplayColor } from "../src/wasm-scene/visualStyles.js";
const style={color:"#ffffff",label:"test"} as Parameters<typeof resolveGeomDisplayColor>[0];
assert.deepEqual(resolveGeomDisplayColor(style,[.75,.25,.1,.85],false),[.75,.25,.1]);
assert.equal(resolveGeomDisplayColor(style,[1,1,1,1],true),"#ffffff");
assert.throws(()=>resolveGeomDisplayColor(null,[NaN,0,0,1],false));
