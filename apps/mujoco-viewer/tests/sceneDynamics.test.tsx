import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server";
import { decodeSceneStateLayout, validateCompiledSceneLayout, validateSceneStateValues } from "../src/robot-profiles/sceneStateLayout.js";
import { decodeViewerRobotDeclaration, viewerRobotProfileCanonicalJson } from "../src/robot-profiles/declaration.js";
import { FAST_ARM_VIEWER_DECLARATION_DOCUMENT } from "./testViewerProfile.js";
import { resolveTransportQpos } from "../src/wasm-scene/mujocoQposSync.js";
import { parseDynamics, DynamicsStream } from "../src/wasm-scene/dynamicsPresentation.js";
import { DynamicsPanel } from "../src/app/DynamicsPanel.js";
import type { TransportPayloadV0 } from "../src/types/transportPayload.js";

const rawLayout={schema_version:"scene-state-layout/v1",qpos_dimension:8,qvel_dimension:7,joints:[
 {name:"floating_cube",kind:"free",role:"object",qpos_address:0,qvel_address:0},
 {name:"joint",kind:"hinge",role:"robot",qpos_address:7,qvel_address:6}]};
const layout=decodeSceneStateLayout(rawLayout,["joint"]);
validateCompiledSceneLayout(layout,8,7,["floating_cube","joint"],[0,3],[0,7],[0,6]);
for(const mutated of [
 {...rawLayout,qvel_dimension:8},
 {...rawLayout,joints:[rawLayout.joints[0],{...rawLayout.joints[1],qpos_address:0}]},
 {...rawLayout,joints:[rawLayout.joints[0],{...rawLayout.joints[1],role:"object"}]},
 {...rawLayout,joints:[...rawLayout.joints,rawLayout.joints[0]]},
])assert.throws(()=>decodeSceneStateLayout(mutated,["joint"]));
assert.throws(()=>validateCompiledSceneLayout(layout,8,7,["joint","floating_cube"],[3,0],[0,1],[0,1]));
const qpos=[.4,0,.05,1,0,0,0,.1];
validateSceneStateValues(layout,qpos,[0,0,0,0,0,0,0]);
assert.throws(()=>validateSceneStateValues(layout,[.4,0,.05,2,0,0,0,.1],[0,0,0,0,0,0,0]));
assert.throws(()=>validateSceneStateValues(layout,qpos,[0]));
const profile=decodeViewerRobotDeclaration({...FAST_ARM_VIEWER_DECLARATION_DOCUMENT,schemaVersion:"viewer-robot-declaration/v2",jointNames:["joint"],qposDimension:1,sceneStateLayout:rawLayout});
assert.equal(profile.qposDimension,1);assert.equal(profile.sceneStateLayout?.qpos_dimension,8);
assert.equal(JSON.parse(viewerRobotProfileCanonicalJson(profile)).sceneStateLayout.qpos_dimension,8);
assert.throws(()=>decodeViewerRobotDeclaration({...FAST_ARM_VIEWER_DECLARATION_DOCUMENT,sceneStateLayout:rawLayout}));
function fixture():TransportPayloadV0 {
 const model="a".repeat(64),scene="b".repeat(64);
 return {version:0,frame_index:2,time_s:.2,qpos:[...qpos],qvel:[0,0,0,0,0,0,0],bodies:[],sites:[],target_position_m:null,metadata:{
  robot_profile_id:profile.profileId,model_contract_version:profile.modelContractVersion,robot_joint_names:["joint"],robot_qpos_dimension:1,scene_state_layout_v1:rawLayout,
  model_sha256:model,coordinated_runtime_v1:{epoch:"trial-1",arm_ids:["left"]},
  scene_contact_binding_v1:{schema_version:"scene-contact-binding/v1",model_sha256:model,scene_digest:scene,epoch:"trial-1",endpoint_ids:["left"],object_ids:["cube"]},
  scene_contact_geometry_v1:{schema_version:"scene-contact-geometry/v2",model_sha256:model,scene_digest:scene,frame_index:2,simulation_time_s:.2,status:"observed",scope:"tool_object_geometry",force_status:"separate_dynamics_evidence",force_n:null,objects:[{instance_id:"cube",position_m:[.4,0,.05],orientation_wxyz:[1,0,0,0]}],contacts:[]},
  scene_contact_task_v1:{schema_version:"contact-observation-presentation/v1",classification:"running",phase:"observing",reason:null,epoch:"trial-1",scene_digest:scene,target_object_ids:["cube"],observed_pairs:[],force_evaluated:false,frame_index:2,simulation_time_s:.2,presentation_frame_index:2,presentation_time_s:.2},
  scene_dynamics_v1:{schema_version:"scene-dynamics-observation/v1",model_sha256:model,scene_digest:scene,settings_digest:"c".repeat(64),frame_index:2,simulation_time_s:.2,semantics:"coordinated_actuator_servo_dynamic/v1",gravity_m_s2:[0,0,-9.81],
   objects:[{instance_id:"cube",motion_type:"dynamic",position_m:[.4,0,.05],orientation_wxyz:[1,0,0,0],linear_velocity_world_m_s:[0,0,0],angular_velocity_world_rad_s:[0,0,0]}],
   joints:[{name:"joint",target_rad:.12,position_rad:.1,velocity_rad_s:0,actuator_force_nm:1}],
   contacts:[{geom1:"floor",geom2:"cube_geom",role1:{kind:"support",id:"floor"},role2:{kind:"object",id:"cube"},point_world_m:[.4,0,0],normal_geom1_to_geom2_world:[0,0,1],distance_m:-.0001,penetration_m:.0001,status:"measured",force_on_geom2_world_n:[0,0,.981],torque_on_geom2_world_nm:[0,0,0]}]}
 }};
}
const good=fixture();
const pose=resolveTransportQpos(good,8,profile);assert.equal(pose.status,"ready");assert.deepEqual(pose.qpos,qpos);
const missing=fixture();delete missing.metadata.scene_state_layout_v1;assert.equal(resolveTransportQpos(missing,8,profile).status,"invalid");
const badQuat=fixture();badQuat.qpos[3]=2;assert.equal(resolveTransportQpos(badQuat,8,profile).status,"invalid");
const parsed=parseDynamics(good);assert.equal(parsed.status,"available");
const html=renderToStaticMarkup(<DynamicsPanel value={parsed}/>);assert.match(html,/0.981/);assert.match(html,/dynamic/);assert.match(html,/実機測定ではありません/);
for(const mutate of [
 (d:any)=>{d.frame_index=1}, (d:any)=>{d.model_sha256="d".repeat(64)}, (d:any)=>{d.simulation_time_s=.3},
 (d:any)=>{d.objects[0].position_m[2]=.6},(d:any)=>{d.objects.push(d.objects[0])},
 (d:any)=>{d.joints[0].actuator_force_nm=NaN},(d:any)=>{d.contacts[0].force_on_geom2_world_n=null},
 (d:any)=>{d.contacts[0].status="pretend"}, (d:any)=>{d.unknown=1},
]){const p=fixture();mutate(p.metadata.scene_dynamics_v1);assert.equal(parseDynamics(p).status,"unavailable");}
const noForce=fixture();delete noForce.metadata.scene_dynamics_v1;assert.equal(parseDynamics(noForce).status,"unavailable");
const stream=new DynamicsStream();assert.equal(stream.apply(good).status,"available");
const changed=fixture();(changed.metadata.scene_dynamics_v1 as any).settings_digest="e".repeat(64);assert.equal(stream.apply(changed).status,"unavailable");
console.log("full scene layout, dynamic state and force presentation tests passed");
