/** backendが確定した無次元commandだけを投影する。校正・符号・neutral判定はしない。 */
export type DeviceInputSample = {role:"left"|"right";deviceId:string;sequence:number;deviceMs:number;
  receivedAtS:number;command:number[];profileRecord:string;settingsGeneration:number;tareEpoch:number};
export type DeviceInputPresentation = {devices:DeviceInputSample[];unit:"1"};
const object=(v:unknown):v is Record<string,any>=>v!==null&&typeof v==="object"&&!Array.isArray(v);
const count=(v:unknown):v is number=>typeof v==="number"&&Number.isSafeInteger(v)&&v>=0;
const seven=(v:unknown,test:(n:any)=>boolean)=>Array.isArray(v)&&v.length===7&&v.every(test);
export function parseDeviceInputPresentation(raw:unknown):DeviceInputPresentation|null {
  if(!object(raw)||raw.schema_version!=="selfrionette-device-snapshot/v1"||raw.unit!=="1"||raw.source_active!==true||!object(raw.devices))return null;
  const roles=Object.keys(raw.devices);
  if(roles.length<1||roles.length>2||roles.some(role=>!["left","right"].includes(role)))return null;
  const devices:DeviceInputSample[]=[];
  for(const role of roles.sort()) {
    const d=raw.devices[role];
    if(!object(d)||d.schema!=="selfrionette-bridge-event/v1"||d.event!=="sample"||d.domain!=="normalized_input"||d.ready!==true||d.missing!==false||
      !object(d.source)||typeof d.source.device_id!=="string"||!/^srn-[a-f0-9]{32}$/.test(d.source.device_id)||
      !object(d.profile)||typeof d.profile.record!=="string"||!count(d.sequence)||!count(d.device_ms)||!count(d.settings_generation)||!count(d.tare_epoch)||
      typeof d.received_at_s!=="number"||!Number.isFinite(d.received_at_s)||d.received_at_s<0||
      !seven(d.quality_flags,v=>v===1)||!seven(d.span_exceeded,v=>v===false)||!seven(d.unsupported_direction,v=>v===false)||
      !seven(d.command,v=>typeof v==="number"&&Number.isFinite(v)&&Math.abs(v)<=1))return null;
    devices.push({role:role as "left"|"right",deviceId:d.source.device_id,sequence:d.sequence,deviceMs:d.device_ms,
      receivedAtS:d.received_at_s,command:[...d.command],profileRecord:d.profile.record,
      settingsGeneration:d.settings_generation,tareEpoch:d.tare_epoch});
  }
  if(new Set(devices.map(d=>d.deviceId)).size!==devices.length)return null;
  return {devices,unit:"1"};
}
