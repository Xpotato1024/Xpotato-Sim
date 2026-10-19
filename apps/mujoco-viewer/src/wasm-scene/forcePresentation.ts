/** backendが固定した表示条件と、適用poseに一致する反力sampleだけを採用する。 */
import type { TransportPayloadV0 } from "../types/transportPayload.js";
import { parseSceneContactPayload } from "../contact/sceneContactPresentation.js";
import { parseDynamics } from "./dynamicsPresentation.js";

export type ForceCuePair = {endpoint:string;object:string;status:string;force:number[]|null;
  load:number|null;point:number[]|null;saturated:boolean;length:number;loadFraction:number};
export type ForcePresentation = {status:"absent"|"hidden"|"available"|"unavailable";
  comparison:boolean;mode:"no_force_cue"|"force_cue"|null;reason:string;
  conditionSha:string|null;sampleId:string|null;sourceTick:number|null;sourceFrame:number|null;
  receivedAtMs:number|null;adoptedAtMs:number|null;renderSubmittedAtMs:number|null;receiptKind:"websocket"|"offline"|null;
  skippedSourceTicks:number;pairs:ForceCuePair[]};

export function emptyForcePresentation():ForcePresentation {
  return {status:"absent",comparison:false,mode:null,reason:"追加接触力の比較条件は未選択",
    conditionSha:null,sampleId:null,sourceTick:null,sourceFrame:null,receivedAtMs:null,adoptedAtMs:null,
    renderSubmittedAtMs:null,receiptKind:null,skippedSourceTicks:0,pairs:[]};
}
const record=(x:unknown):x is Record<string,unknown>=>typeof x==="object"&&x!==null&&!Array.isArray(x);
const exact=(x:unknown,keys:string[]):x is Record<string,unknown>=>record(x)&&Object.keys(x).length===keys.length&&keys.every(k=>k in x);
const finite=(x:unknown):x is number=>typeof x==="number"&&Number.isFinite(x);
const integer=(x:unknown):x is number=>finite(x)&&Number.isSafeInteger(x)&&x>=0;
const vec=(x:unknown):x is number[]=>Array.isArray(x)&&x.length===3&&x.every(finite);
const digest=(x:unknown):x is string=>typeof x==="string"&&/^[a-f0-9]{64}$/.test(x);

export class ForcePresentationStream {
  current=emptyForcePresentation();
  private identity:string|null=null;
  private sourceManifest:string|null=null;
  private maxAgeMs=200;
  private pendingSubmission=false;
  readonly counts={adopted:0,submitted:0,replacedBeforeRender:0,invalidatedBeforeRender:0};

  invalidate(reason:string):ForcePresentation {
    if(!this.current.comparison)return this.current;
    if(this.pendingSubmission){this.counts.invalidatedBeforeRender++;this.pendingSubmission=false;}
    this.current={...this.current,status:"unavailable",reason,pairs:[]};
    return this.current;
  }

  /** 時計はbrowser内だけで比較し、host/source時刻との差をlatencyと呼ばない。 */
  expire(nowMs:number):ForcePresentation {
    if(this.current.receivedAtMs!==null&&nowMs-this.current.receivedAtMs>this.maxAgeMs
      &&this.current.status!=="unavailable")return this.invalidate("新しい反力sampleの受信が停止しました");
    return this.current;
  }

  submitted(nowMs:number):void {
    if((this.current.status==="available"||this.current.status==="hidden")&&this.current.renderSubmittedAtMs===null
      &&finite(nowMs)&&nowMs>=this.current.adoptedAtMs!) {
      this.current={...this.current,renderSubmittedAtMs:nowMs};
      this.counts.submitted++;this.pendingSubmission=false;
    }
  }

  /** 条件を受け取った時点で旧cueを覆う。検証・scene適用前に力sampleは採用しない。 */
  observeBinding(payload:TransportPayloadV0):void {
    if(payload.metadata.force_presentation_v1!==undefined&&!this.current.comparison)
      this.current={...this.current,comparison:true,status:"unavailable",reason:"固定した表示条件を検証中です"};
  }

  apply(payload:TransportPayloadV0,receivedAtMs:number,nowMs:number,receiptKind:"websocket"|"offline"="websocket"):ForcePresentation {
    const binding=payload.metadata.force_presentation_v1;
    if(binding===undefined){
      if(!this.current.comparison)return this.current=emptyForcePresentation();
      return this.invalidate("固定した表示条件が欠落しました");
    }
    // malformed条件でも旧診断へのfallbackを禁止する。非表示側の漏れをfail closedにする。
    this.observeBinding(payload);
    const bad=(reason="接触力の条件・sampleが無効です")=>this.invalidate(reason);
    if(!finite(receivedAtMs)||!finite(nowMs)||receivedAtMs>nowMs)return bad();
    const geometry=parseSceneContactPayload(payload),dynamics=parseDynamics(payload);
    if(geometry.status!=="available"||!geometry.dynamic||dynamics.status!=="available"
      ||!exact(binding,["schema_version","condition","condition_sha256","epoch","model_sha256","scene_digest","settings_digest"])
      ||binding.schema_version!=="force-presentation-binding/v1"
      ||binding.epoch!==geometry.epoch||binding.model_sha256!==geometry.modelSha256
      ||binding.scene_digest!==geometry.sceneDigest||binding.settings_digest!==dynamics.settings
      ||!digest(binding.condition_sha256))return bad();
    const condition=binding.condition;
    if(!exact(condition,["schema_version","mode","scale_m_per_n","max_arrow_m","normal_load_full_scale_n","max_sample_age_s"])
      ||condition.schema_version!=="force-presentation/v1"
      ||!(condition.mode==="force_cue"||condition.mode==="no_force_cue")
      ||![condition.scale_m_per_n,condition.max_arrow_m,condition.normal_load_full_scale_n,condition.max_sample_age_s].every(v=>finite(v)&&v>0)
      ||Number(condition.max_sample_age_s)>.2||Number(condition.max_arrow_m)>1)return bad();
    const endpointIds=(payload.metadata.scene_contact_binding_v1 as {endpoint_ids:string[]}).endpoint_ids;
    const identity=JSON.stringify([geometry.epoch,geometry.modelSha256,geometry.sceneDigest,dynamics.settings,
      binding.condition_sha256,condition.mode,condition.scale_m_per_n,condition.max_arrow_m,
      condition.normal_load_full_scale_n,condition.max_sample_age_s]);
    if(this.identity!==null&&this.identity!==identity)return bad("実行中に表示条件が変わりました");
    this.identity=identity;this.maxAgeMs=Number(condition.max_sample_age_s)*1000;
    this.current={...this.current,mode:condition.mode,conditionSha:binding.condition_sha256};
    const s=payload.metadata.scene_force_signal_v1;
    if(!exact(s,["schema_version","frame","unit","sign_convention","manifest_sha256","model_sha256","scene_digest","settings_digest","epoch","control_tick","source_frame_index","simulation_time_s","observed_monotonic_s","status","pairs","reason"])
      ||s.schema_version!=="scene-force-signal/v1"||s.frame!=="mujoco_world"||s.unit!=="N"||s.sign_convention!=="object_on_tool"
      ||s.model_sha256!==geometry.modelSha256||s.scene_digest!==geometry.sceneDigest||s.settings_digest!==dynamics.settings
      ||s.epoch!==geometry.epoch||!digest(s.manifest_sha256)||!integer(s.control_tick)||!integer(s.source_frame_index)
      ||s.source_frame_index>payload.frame_index||s.simulation_time_s!==payload.time_s
      ||!finite(s.observed_monotonic_s)||s.observed_monotonic_s<0||s.status!=="available"
      ||!Array.isArray(s.pairs)||s.pairs.length!==endpointIds.length*geometry.targets.length
      ||!record(payload.metadata.coordinated_runtime_v1)||payload.metadata.coordinated_runtime_v1.tick!==s.control_tick
      ||payload.metadata.coordinated_runtime_v1.state!=="running")return bad();
    if(this.sourceManifest!==null&&this.sourceManifest!==s.manifest_sha256)return bad("実行中に反力の導出条件が変わりました");
    this.sourceManifest=s.manifest_sha256;
    if(this.current.sourceTick!==null&&s.control_tick<this.current.sourceTick)return bad("古い反力sampleです");
    if(this.current.sourceTick===s.control_tick)return s.source_frame_index===this.current.sourceFrame
      ? this.expire(nowMs) : bad("同じtickのsource identityが変わりました");
    if(nowMs-receivedAtMs>this.maxAgeMs)return bad("反力sampleの表示待ち期限を超過しました");
    const pairs:ForceCuePair[]=[];const keys=new Set<string>();
    for(const p of s.pairs){
      if(!exact(p,["endpoint_id","object_id","status","contact_indices","unavailable_contacts","raw_force_world_n","normal_load_n","force_world_n","anchor_world_m","filtered","deadbanded","rate_limited","saturated"])
        ||typeof p.endpoint_id!=="string"||!endpointIds.includes(p.endpoint_id)
        ||typeof p.object_id!=="string"||!geometry.targets.includes(p.object_id)
        ||!["active","no_contact","partial_missing","measurement_unavailable"].includes(String(p.status))
        ||!Array.isArray(p.contact_indices)||p.contact_indices.length>4096||!p.contact_indices.every(v=>integer(v)&&v<dynamics.contacts.length)
        ||new Set(p.contact_indices).size!==p.contact_indices.length
        ||!integer(p.unavailable_contacts)||p.unavailable_contacts>p.contact_indices.length
        ||![p.filtered,p.deadbanded,p.rate_limited,p.saturated].every(v=>typeof v==="boolean"))return bad();
      const key=`${p.endpoint_id}/${p.object_id}`;if(keys.has(key))return bad();keys.add(key);
      const usable=p.status==="active"||p.status==="no_contact";
      if(usable?(!vec(p.force_world_n)||!vec(p.raw_force_world_n)||!finite(p.normal_load_n)||p.normal_load_n<0
          ||p.status==="active"&&(!vec(p.anchor_world_m)||p.contact_indices.length===0)
          ||p.unavailable_contacts!==0
          ||p.status==="no_contact"&&(p.contact_indices.length!==0||p.anchor_world_m!==null||p.normal_load_n!==0
            ||p.force_world_n.some(v=>v!==0)||p.raw_force_world_n.some(v=>v!==0)))
        :(p.force_world_n!==null||p.raw_force_world_n!==null||p.normal_load_n!==null||p.anchor_world_m!==null))return bad();
      const force=usable?p.force_world_n as number[]:null;
      const magnitude=force===null?0:Math.hypot(...force);
      if(!Number.isFinite(magnitude))return bad();
      const length=Math.min(magnitude*Number(condition.scale_m_per_n),Number(condition.max_arrow_m));
      const load=usable?Number(p.normal_load_n):null;
      pairs.push({endpoint:p.endpoint_id,object:p.object_id,status:String(p.status),force,load,
        point:p.anchor_world_m as number[]|null,length,
        saturated:Boolean(p.saturated)||magnitude*Number(condition.scale_m_per_n)>Number(condition.max_arrow_m)
          ||load!==null&&load>Number(condition.normal_load_full_scale_n),
        loadFraction:load===null?0:Math.min(1,load/Number(condition.normal_load_full_scale_n))});
    }
    if(this.pendingSubmission)this.counts.replacedBeforeRender++;
    this.counts.adopted++;this.pendingSubmission=true;
    this.current={status:condition.mode==="no_force_cue"?"hidden":"available",comparison:true,mode:condition.mode,
      reason:condition.mode==="no_force_cue"?"追加の接触力提示なし（通常映像は表示）":"対象物が手先へ加えるworld反力 / N",
      conditionSha:binding.condition_sha256,sampleId:`${geometry.epoch}/${s.control_tick}/${s.source_frame_index}/${s.manifest_sha256}`,
      sourceTick:s.control_tick,sourceFrame:s.source_frame_index,receivedAtMs,adoptedAtMs:nowMs,renderSubmittedAtMs:null,receiptKind,
      skippedSourceTicks:this.current.skippedSourceTicks+(this.current.sourceTick===null?0:Math.max(0,s.control_tick-this.current.sourceTick-1)),
      pairs:condition.mode==="no_force_cue"?[]:pairs};
    return this.current;
  }
}
