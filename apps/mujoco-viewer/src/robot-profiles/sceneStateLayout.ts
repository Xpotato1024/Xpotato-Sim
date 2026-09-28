/** native sceneの全座標partition。Robot subsetやfreejointを推測・並べ替えない。 */
export interface SceneStateJoint { name:string; kind:"free"|"ball"|"slide"|"hinge"; role:"robot"|"object"; qpos_address:number; qvel_address:number }
export interface SceneStateLayout { schema_version:"scene-state-layout/v1"; qpos_dimension:number; qvel_dimension:number; joints:readonly SceneStateJoint[] }
const widths={free:[7,6],ball:[4,3],slide:[1,1],hinge:[1,1]} as const;
function record(v:unknown):v is Record<string,unknown>{return typeof v==="object"&&v!==null&&!Array.isArray(v);}
function exact(v:unknown,keys:string[]):v is Record<string,unknown>{return record(v)&&Object.keys(v).length===keys.length&&keys.every(k=>k in v);}
function natural(v:unknown):v is number{return typeof v==="number"&&Number.isSafeInteger(v)&&v>=0;}
export function decodeSceneStateLayout(raw:unknown,robotNames:readonly string[]):SceneStateLayout {
 if(!exact(raw,["schema_version","qpos_dimension","qvel_dimension","joints"])||raw.schema_version!=="scene-state-layout/v1"||!natural(raw.qpos_dimension)||raw.qpos_dimension<1||raw.qpos_dimension>4096||!natural(raw.qvel_dimension)||raw.qvel_dimension<1||raw.qvel_dimension>4096||!Array.isArray(raw.joints))throw new Error("invalid scene layout");
 const q:number[]=[],v:number[]=[],names:string[]=[],robot:string[]=[],joints:SceneStateJoint[]=[];
 for(const j of raw.joints){
  if(!exact(j,["name","kind","role","qpos_address","qvel_address"])||typeof j.name!=="string"||!j.name||typeof j.kind!=="string"||!Object.hasOwn(widths,j.kind)||(j.role!=="robot"&&j.role!=="object")||!natural(j.qpos_address)||!natural(j.qvel_address))throw new Error("invalid scene joint");
  const kind=j.kind as SceneStateJoint["kind"],w=widths[kind];
  names.push(j.name);if(j.role==="robot")robot.push(j.name);
  for(let n=0;n<w[0];n++)q.push(j.qpos_address+n);
  for(let n=0;n<w[1];n++)v.push(j.qvel_address+n);
  joints.push({name:j.name,kind,role:j.role,qpos_address:j.qpos_address,qvel_address:j.qvel_address});
 }
 if(new Set(names).size!==names.length||robot.length!==robotNames.length||robot.some(n=>!robotNames.includes(n))||q.length!==raw.qpos_dimension||v.length!==raw.qvel_dimension||q.sort((a,b)=>a-b).some((a,i)=>a!==i)||v.sort((a,b)=>a-b).some((a,i)=>a!==i))throw new Error("scene coordinate coverage/Robot identity mismatch");
 return Object.freeze({schema_version:"scene-state-layout/v1",qpos_dimension:raw.qpos_dimension,qvel_dimension:raw.qvel_dimension,joints:Object.freeze(joints.map(j=>Object.freeze(j)))});
}
export function validateCompiledSceneLayout(layout:SceneStateLayout,nq:number,nv:number,names:readonly string[],types:ArrayLike<number>,qa:ArrayLike<number>,va:ArrayLike<number>):void{
 const kinds=["free","ball","slide","hinge"];
 if(nq!==layout.qpos_dimension||nv!==layout.qvel_dimension||names.length!==layout.joints.length)throw new Error("compiled scene dimensions differ");
 for(let i=0;i<names.length;i++){
  const j=layout.joints[i];if(j.name!==names[i]||j.kind!==kinds[types[i]]||j.qpos_address!==qa[i]||j.qvel_address!==va[i])throw new Error("compiled scene joint layout differs");
 }
}
/** 全sceneの値を検証。freejointのquaternionを角度や3D位置として誤用しない。 */
export function validateSceneStateValues(layout:SceneStateLayout,qpos:readonly number[],qvel:readonly number[]):void {
 if(qpos.length!==layout.qpos_dimension||qvel.length!==layout.qvel_dimension||!qpos.every(Number.isFinite)||!qvel.every(Number.isFinite))throw new Error("scene state dimensions/nonfinite values");
 for(const j of layout.joints){
  if(j.kind!=="free"&&j.kind!=="ball")continue;
  const start=j.qpos_address+(j.kind==="free"?3:0);
  const q=qpos.slice(start,start+4);
  if(Math.abs(q.reduce((a,b)=>a+b*b,0)-1)>1e-8)throw new Error("scene quaternion is not unit length");
 }
}
