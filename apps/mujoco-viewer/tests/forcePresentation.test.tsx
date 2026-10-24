import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server";
import { ForcePresentationStream, emptyForcePresentation } from "../src/wasm-scene/forcePresentation.js";
import { ForceOverlay } from "../src/wasm-scene/forceOverlay.js";
import { ForceCuePanel } from "../src/app/ForceCuePanel.js";
import { DynamicsPanel } from "../src/app/DynamicsPanel.js";
import { SceneContactPanel } from "../src/app/SceneContactPanel.js";
import { parseDynamics } from "../src/wasm-scene/dynamicsPresentation.js";
import { parseSceneContactPayload } from "../src/contact/sceneContactPresentation.js";
import type { TransportPayloadV0 } from "../src/types/transportPayload.js";

function fixture(mode="force_cue",tick=2,frame=tick):TransportPayloadV0 {
 const model="a".repeat(64),scene="b".repeat(64),settings="c".repeat(64),time=tick/60;
 const pair=(endpoint:string,i:number,sign:number)=>({endpoint_id:endpoint,object_id:"cube",status:"active",contact_indices:[i],unavailable_contacts:0,
  raw_force_world_n:[sign*2,0,0],normal_load_n:2,force_world_n:[sign*2,0,0],anchor_world_m:[.4,0,.05],
  filtered:false,deadbanded:false,rate_limited:false,saturated:false});
 const contact=(endpoint:string,sign:number)=>({geom1:`${endpoint}_tool`,geom2:"cube_geom",role1:{kind:"tool",id:endpoint},role2:{kind:"object",id:"cube"},
  point_world_m:[.4,0,.05],normal_geom1_to_geom2_world:[sign,0,0],distance_m:-.0001,penetration_m:.0001,status:"measured",
  force_on_geom2_world_n:[sign*2,0,0],torque_on_geom2_world_nm:[0,0,0]});
 return {version:0,frame_index:frame,time_s:time,qpos:[.4,0,.05,1,0,0,0,.1],qvel:[0,0,0,0,0,0,0],bodies:[],sites:[],target_position_m:null,metadata:{
  robot_joint_names:["joint"],model_sha256:model,coordinated_runtime_v1:{epoch:"trial-1",arm_ids:["left","right"],state:"running",tick},
  scene_state_layout_v1:{schema_version:"scene-state-layout/v1",qpos_dimension:8,qvel_dimension:7,joints:[
   {name:"floating_cube",kind:"free",role:"object",qpos_address:0,qvel_address:0},{name:"joint",kind:"hinge",role:"robot",qpos_address:7,qvel_address:6}]},
  scene_contact_binding_v1:{schema_version:"scene-contact-binding/v1",model_sha256:model,scene_digest:scene,epoch:"trial-1",endpoint_ids:["left","right"],object_ids:["cube"]},
  scene_contact_geometry_v1:{schema_version:"scene-contact-geometry/v2",model_sha256:model,scene_digest:scene,frame_index:frame,simulation_time_s:time,status:"observed",scope:"tool_object_geometry",force_status:"separate_dynamics_evidence",force_n:null,
   objects:[{instance_id:"cube",position_m:[.4,0,.05],orientation_wxyz:[1,0,0,0]}],contacts:[{endpoint_id:"left",object_id:"cube",tool_geom_name:"left_tool",object_geom_name:"cube_geom",point_world_m:[.4,0,.05],normal_world:[1,0,0],distance_m:-.0001,penetration_m:.0001,relation:"penetrating"}]},
  scene_contact_task_v1:{schema_version:"contact-observation-presentation/v1",classification:"running",phase:"observing",reason:null,epoch:"trial-1",scene_digest:scene,target_object_ids:["cube"],observed_pairs:[["left","cube"]],force_evaluated:false,frame_index:frame,simulation_time_s:time,presentation_frame_index:frame,presentation_time_s:time},
  scene_dynamics_v1:{schema_version:"scene-dynamics-observation/v1",model_sha256:model,scene_digest:scene,settings_digest:settings,frame_index:frame,simulation_time_s:time,semantics:"coordinated_actuator_servo_dynamic/v1",gravity_m_s2:[0,0,-9.81],
   objects:[{instance_id:"cube",motion_type:"dynamic",position_m:[.4,0,.05],orientation_wxyz:[1,0,0,0],linear_velocity_world_m_s:[0,0,0],angular_velocity_world_rad_s:[0,0,0]}],
   joints:[{name:"joint",target_rad:.12,position_rad:.1,velocity_rad_s:0,actuator_force_nm:1}],contacts:[contact("left",1),contact("right",-1)]},
  force_presentation_v1:{schema_version:"force-presentation-binding/v1",epoch:"trial-1",model_sha256:model,scene_digest:scene,settings_digest:settings,condition_sha256:"d".repeat(64),
   condition:{schema_version:"force-presentation/v1",mode,scale_m_per_n:.002,max_arrow_m:.12,normal_load_full_scale_n:30,max_sample_age_s:.2}},
  scene_force_signal_v1:{schema_version:"scene-force-signal/v1",frame:"mujoco_world",unit:"N",sign_convention:"object_on_tool",manifest_sha256:"e".repeat(64),
   model_sha256:model,scene_digest:scene,settings_digest:settings,epoch:"trial-1",control_tick:tick,source_frame_index:tick,simulation_time_s:time,observed_monotonic_s:800+time,status:"available",pairs:[pair("left",0,-1),pair("right",1,1)],reason:null}
 }};
}
const good=fixture(),stream=new ForcePresentationStream();
const value=stream.apply(good,100,105);
assert.equal(value.status,"available");assert.equal(value.pairs.length,2);
assert.deepEqual(value.pairs.map(p=>p.force),[[-2,0,0],[2,0,0]]);
assert.deepEqual(value.pairs.map(p=>p.load),[2,2]);assert.equal(value.pairs[0].length,.004);
assert.equal(value.adoptedAtMs!-value.receivedAtMs!,5);stream.submitted(112);
assert.equal(stream.current.renderSubmittedAtMs,112);
const repeat=fixture("force_cue",2,3);
stream.apply(repeat,190,195);
assert.equal(stream.current.receivedAtMs,100);assert.equal(stream.current.sourceFrame,2);
assert.equal(stream.current.renderSubmittedAtMs,112);
assert.equal(stream.expire(301).status,"unavailable");assert.deepEqual(stream.current.pairs,[]);
const fresh=stream.apply(fixture("force_cue",5,8),310,315);
assert.equal(fresh.status,"available");assert.equal(fresh.skippedSourceTicks,2);
assert.equal(fresh.renderSubmittedAtMs,null);
assert.equal(stream.apply(fixture(),320,321).status,"unavailable");

for(const mutate of [
 (p:any)=>{p.metadata.force_presentation_v1.condition.mode="adaptive"},
 (p:any)=>{p.metadata.force_presentation_v1.condition.scale_m_per_n=0},
 (p:any)=>{p.metadata.force_presentation_v1.condition.max_sample_age_s=.21},
 (p:any)=>{p.metadata.force_presentation_v1.epoch="old"},
 (p:any)=>{p.metadata.scene_force_signal_v1.epoch="old"},
 (p:any)=>{p.metadata.scene_force_signal_v1.source_frame_index=3},
 (p:any)=>{p.metadata.scene_force_signal_v1.simulation_time_s=0},
 (p:any)=>{p.metadata.scene_force_signal_v1.settings_digest="f".repeat(64)},
 (p:any)=>{p.metadata.scene_force_signal_v1.status="stale"},
 (p:any)=>{p.metadata.coordinated_runtime_v1.state="stopped"},
 (p:any)=>{p.metadata.scene_force_signal_v1.pairs[0].force_world_n[0]=NaN},
 (p:any)=>{p.metadata.scene_force_signal_v1.pairs[0].normal_load_n=-1},
 (p:any)=>{p.metadata.scene_force_signal_v1.pairs[0].contact_indices=[22]},
 (p:any)=>{p.metadata.scene_force_signal_v1.pairs[0].endpoint_id="foreign"},
 (p:any)=>{p.metadata.scene_force_signal_v1.pairs[0].object_id="floor"},
 (p:any)=>{p.metadata.scene_force_signal_v1.pairs.pop()},
 (p:any)=>{delete p.metadata.scene_force_signal_v1},
]) {
 const p=fixture();mutate(p);const s=new ForcePresentationStream(),v=s.apply(p,100,101);
 assert.equal(v.status,"unavailable");assert.equal(v.comparison,true);assert.deepEqual(v.pairs,[]);
 const absent=fixture();delete absent.metadata.force_presentation_v1;
 assert.equal(s.apply(absent,110,111).status,"unavailable");
}
for(const key of ["mode","scale_m_per_n"]){
 const s=new ForcePresentationStream();s.apply(fixture(),0,1);const p:any=fixture("force_cue",3);
 p.metadata.force_presentation_v1.condition[key]=key==="mode"?"no_force_cue":.003;
 assert.equal(s.apply(p,2,3).status,"unavailable");
}
const saturation:any=fixture();saturation.metadata.scene_force_signal_v1.pairs[0].force_world_n=[100,0,0];
saturation.metadata.scene_force_signal_v1.pairs[0].normal_load_n=100;
const saturated=new ForcePresentationStream().apply(saturation,0,1).pairs[0];
assert.equal(saturated.saturated,true);assert.equal(saturated.length,.12);assert.equal(saturated.loadFraction,1);
assert.equal(saturated.load,100);assert.deepEqual(saturated.force,[100,0,0]);
const overflow:any=fixture();overflow.metadata.scene_force_signal_v1.pairs[0].force_world_n=Array(3).fill(Number.MAX_VALUE);
assert.equal(new ForcePresentationStream().apply(overflow,0,1).status,"unavailable");
const large:any=fixture();large.metadata.scene_force_signal_v1.pairs[0].force_world_n=[1e308,1e308,1e308];
const largeOverlay=new ForceOverlay();largeOverlay.update(new ForcePresentationStream().apply(large,0,1));
assert.ok(largeOverlay.group.children.every((a:any)=>[a.quaternion.x,a.quaternion.y,a.quaternion.z,a.quaternion.w].every(Number.isFinite)));
largeOverlay.dispose();
const missing:any=fixture();Object.assign(missing.metadata.scene_force_signal_v1.pairs[0],{status:"measurement_unavailable",unavailable_contacts:1,raw_force_world_n:null,normal_load_n:null,force_world_n:null,anchor_world_m:null});
const unavailable=new ForcePresentationStream().apply(missing,0,1);
assert.equal(unavailable.status,"available");assert.equal(unavailable.pairs[0].force,null);
assert.match(renderToStaticMarkup(<ForceCuePanel value={unavailable}/>),/荷重を取得できません/);
const stale=new ForcePresentationStream();assert.equal(stale.apply(fixture(),0,201).status,"unavailable");
assert.equal(stale.apply(fixture(),5,4).status,"unavailable");
const coalesced=new ForcePresentationStream();coalesced.apply(fixture(),0,1);coalesced.apply(fixture("force_cue",3),2,3);
assert.deepEqual(coalesced.counts,{adopted:2,submitted:0,replacedBeforeRender:1,invalidatedBeforeRender:0});
coalesced.submitted(4);coalesced.submitted(5);assert.equal(coalesced.counts.submitted,1);
coalesced.apply(fixture("force_cue",4),6,7);coalesced.invalidate("stop");assert.equal(coalesced.counts.invalidatedBeforeRender,1);

for(const mode of ["no_force_cue","force_cue"]){
 const p=fixture(mode),s=new ForcePresentationStream(),v=s.apply(p,0,1);
 const geometry=parseSceneContactPayload(p),dynamics=parseDynamics(p);
 assert.equal(renderToStaticMarkup(<DynamicsPanel value={dynamics} comparison={true}/>),"");
 const common=renderToStaticMarkup(<SceneContactPanel value={geometry} live={true} comparison={true}/>);
 assert.match(common,/観測中/);assert.doesNotMatch(common,/食い込み|距離|actuator|接触位置|接触数/);
 const html=renderToStaticMarkup(<ForceCuePanel value={v}/>);
 if(mode==="no_force_cue") {assert.equal(v.status,"hidden");assert.deepEqual(v.pairs,[]);assert.doesNotMatch(html,/meter|2\.00|left|right/);}
 else assert.match(html,/left.*right/);
 s.submitted(2);assert.equal(s.current.renderSubmittedAtMs,2);
 const overlay=new ForceOverlay();await overlay.prepare(async()=>{});overlay.update(v);
 assert.equal(overlay.group.children.filter(c=>c.visible).length,mode==="force_cue"?2:0);
 const pool=overlay.group.children.length;
 for(let i=0;i<120;i++){overlay.update(v);overlay.update(s.invalidate("stop"));}
 assert.equal(overlay.group.children.length,pool);assert.ok(overlay.group.children.every(c=>!c.visible));
 overlay.dispose();assert.equal(overlay.group.children.length,0);overlay.update(v);assert.equal(overlay.group.children.length,0);
}
assert.equal(renderToStaticMarkup(<ForceCuePanel value={emptyForcePresentation()}/>),"");
console.log("frozen force comparison, cue isolation, bounded overlay and browser-clock adoption tests passed");
