/** 現在描画したscene frameに対応するnative動力学値だけを読む。 */
import type { TransportPayloadV0 } from "../types/transportPayload.js";
import { parseSceneContactPayload } from "../contact/sceneContactPresentation.js";
import { decodeSceneStateLayout, validateSceneStateValues } from "../robot-profiles/sceneStateLayout.js";
export type DynamicsPresentation = {status:"absent"|"unavailable";reason:string} | {status:"available";frame:number;time:number;settings:string;objects:{id:string;motion:string;z:number;velocity:number[]}[];joints:{name:string;target:number;position:number;torque:number}[];contacts:{pair:string;force:number[]|null;status:string}[]};
const record=(x:unknown):x is Record<string,unknown>=>typeof x==="object"&&x!==null&&!Array.isArray(x);
const exact=(x:unknown,keys:string[]):x is Record<string,unknown>=>record(x)&&Object.keys(x).length===keys.length&&keys.every(k=>k in x);
const finite=(x:unknown):x is number=>typeof x==="number"&&Number.isFinite(x);
const vec=(x:unknown,n:number):x is number[]=>Array.isArray(x)&&x.length===n&&x.every(finite);
export function parseDynamics(payload:TransportPayloadV0):DynamicsPresentation{
 const d=payload.metadata.scene_dynamics_v1;
 const bad=():DynamicsPresentation=>({status:"unavailable",reason:"動力学観測のidentity/frame/値が不正"});
 const g=parseSceneContactPayload(payload);
 if(d===undefined)return g.status==="available"&&g.dynamic ? bad() : {status:"absent",reason:"動力学は未選択"};
 if(g.status!=="available"||!g.dynamic||!exact(d,["schema_version","model_sha256","scene_digest","settings_digest","frame_index","simulation_time_s","semantics","gravity_m_s2","objects","joints","contacts"])||d.schema_version!=="scene-dynamics-observation/v1"||d.model_sha256!==g.modelSha256||d.scene_digest!==g.sceneDigest||d.frame_index!==payload.frame_index||d.simulation_time_s!==payload.time_s||d.semantics!=="coordinated_actuator_servo_dynamic/v1"||typeof d.settings_digest!=="string"||!/^[a-f0-9]{64}$/.test(d.settings_digest)||!vec(d.gravity_m_s2,3)||!Array.isArray(d.objects)||d.objects.length!==g.objects.length||!Array.isArray(d.joints)||!Array.isArray(d.contacts)||d.contacts.length>4096)return bad();
 const objects:{id:string;motion:string;z:number;velocity:number[]}[]=[];
 for(const o of d.objects){
  if(!exact(o,["instance_id","motion_type","position_m","orientation_wxyz","linear_velocity_world_m_s","angular_velocity_world_rad_s"])||typeof o.instance_id!=="string"||!["fixed","dynamic"].includes(String(o.motion_type))||!vec(o.position_m,3)||!vec(o.orientation_wxyz,4)||!vec(o.linear_velocity_world_m_s,3)||!vec(o.angular_velocity_world_rad_s,3))return bad();
  const match=g.objects.find(v=>v.id===o.instance_id);if(!match||o.position_m.some((v,i)=>v!==match.position[i])||o.orientation_wxyz.some((v,i)=>v!==match.orientation[i]))return bad();
  objects.push({id:o.instance_id,motion:String(o.motion_type),z:o.position_m[2],velocity:o.linear_velocity_world_m_s});
 }
 if(new Set(objects.map(o=>o.id)).size!==objects.length)return bad();
 const names=payload.metadata.robot_joint_names;if(!Array.isArray(names)||names.length!==d.joints.length)return bad();
 let layout;
 try {layout=decodeSceneStateLayout(payload.metadata.scene_state_layout_v1,names);validateSceneStateValues(layout,payload.qpos,payload.qvel);} catch{return bad();}
 const joints:{name:string;target:number;position:number;torque:number}[]=[];
 for(let i=0;i<d.joints.length;i++){
  const j=d.joints[i];if(!exact(j,["name","target_rad","position_rad","velocity_rad_s","actuator_force_nm"])||j.name!==names[i]||!finite(j.target_rad)||!finite(j.position_rad)||!finite(j.velocity_rad_s)||!finite(j.actuator_force_nm))return bad();
  const address=layout.joints.find(v=>v.role==="robot"&&v.name===j.name);
  if(!address||j.position_rad!==payload.qpos[address.qpos_address]||j.velocity_rad_s!==payload.qvel[address.qvel_address])return bad();
  joints.push({name:String(j.name),target:j.target_rad,position:j.position_rad,torque:j.actuator_force_nm});
 }
 const contacts:{pair:string;force:number[]|null;status:string}[]=[];
 for(const c of d.contacts){
  if(!exact(c,["geom1","geom2","role1","role2","point_world_m","normal_geom1_to_geom2_world","distance_m","penetration_m","status","force_on_geom2_world_n","torque_on_geom2_world_nm"])||typeof c.geom1!=="string"||typeof c.geom2!=="string"||!vec(c.point_world_m,3)||!vec(c.normal_geom1_to_geom2_world,3)||!finite(c.distance_m)||!finite(c.penetration_m)||c.penetration_m!==Math.max(0,-c.distance_m))return bad();
  if(Math.abs(c.normal_geom1_to_geom2_world.reduce((a,v)=>a+v*v,0)-1)>1e-8)return bad();
  for(const role of [c.role1,c.role2]) {
   if(!exact(role,["kind","id"])||typeof role.id!=="string"||!role.id||!["object","tool","support"].includes(String(role.kind)))return bad();
   if(role.kind==="object"&&!objects.some(o=>o.id===role.id))return bad();
  }
  if(c.status!=="measured"&&c.status!=="measurement_unavailable")return bad();
  if(c.status==="measured"?(!vec(c.force_on_geom2_world_n,3)||!vec(c.torque_on_geom2_world_nm,3)):(c.force_on_geom2_world_n!==null||c.torque_on_geom2_world_nm!==null))return bad();
  contacts.push({pair:`${c.geom1} → ${c.geom2}`,force:c.force_on_geom2_world_n as number[]|null,status:String(c.status)});
 }
 return {status:"available",frame:payload.frame_index,time:payload.time_s,settings:d.settings_digest,objects,joints,contacts};
}

/** 既定のscene readerと同じ描画lifetimeで数値実行条件の差替えも拒否する。 */
export class DynamicsStream {
 private identity:string|null=null;
 apply(payload:TransportPayloadV0):DynamicsPresentation {
  const result=parseDynamics(payload);
  if(result.status!=="available")return result.status==="absent"&&this.identity!==null
    ? {status:"unavailable",reason:"動力学観測が欠落しました"}:result;
  const g=parseSceneContactPayload(payload);
  if(g.status!=="available")return {status:"unavailable",reason:"scene観測が無効です"};
  const id=`${g.modelSha256}/${g.sceneDigest}/${g.epoch}/${result.settings}`;
  if(this.identity!==null&&id!==this.identity)return {status:"unavailable",reason:"動力学条件が実行中に変わりました"};
  this.identity=id;return result;
 }
}
