/** 固定物体の幾何観測を同じ適用frameへ束縛する。physics/Task判定は再実装しない。 */
import type { TransportPayloadV0 } from "../types/transportPayload.js";

type V3 = [number, number, number];
export interface SceneContactPoint {
  endpointId: string; objectId: string; toolGeom: string; objectGeom: string;
  point: V3; normal: V3; distanceM: number; penetrationM: number;
  relation: "near" | "touching" | "penetrating";
}
export interface SceneContactObject { id: string; position: V3; orientation: number[] }
export type SceneContactPresentation =
  | { status: "absent" | "unavailable"; reason: string }
  | { status: "available"; reason: null; modelSha256: string; sceneDigest: string; epoch: string;
      frameIndex: number; timeS: number; objects: SceneContactObject[]; contacts: SceneContactPoint[];
      dynamic?: boolean; targets: string[]; phase: "observing" | "completed" | "aborted" | "invalid"; taskReason: string | null };

export function unavailableSceneContact(reason: string): SceneContactPresentation {
  return { status: "unavailable", reason };
}
function record(x: unknown): x is Record<string, unknown> { return typeof x === "object" && x !== null && !Array.isArray(x); }
function exact(x: unknown, keys: string[]): x is Record<string, unknown> {
  return record(x) && Object.keys(x).length === keys.length && keys.every(k => k in x);
}
function finite(x: unknown): x is number { return typeof x === "number" && Number.isFinite(x); }
function index(x: unknown): x is number { return finite(x) && Number.isSafeInteger(x) && x >= 0; }
function array(x: unknown, n: number): x is number[] { return Array.isArray(x) && x.length === n && x.every(finite); }
function id(x: unknown): x is string { return typeof x === "string" && /^[a-z][a-z0-9_]{0,47}$/.test(x); }
function hash(x: unknown): x is string { return typeof x === "string" && /^[a-f0-9]{64}$/.test(x); }
function ids(x: unknown): x is string[] {
  return Array.isArray(x) && x.length <= 32 && x.every(id) && new Set(x).size === x.length;
}
function unit(x: number[]): boolean { return Math.abs(x.reduce((s,v) => s+v*v,0)-1) <= 1e-8; }
function same(a: readonly string[], b: readonly string[]): boolean { return a.length === b.length && a.every(v => b.includes(v)); }

/** 旧R7-Hのforce evidenceとは別schema。欠落・誤時刻・誤モデルをzero接触へ置換しない。 */
export function parseSceneContactPayload(payload: TransportPayloadV0): SceneContactPresentation {
  const m = payload.metadata;
  const g = m.scene_contact_geometry_v1, b = m.scene_contact_binding_v1, t = m.scene_contact_task_v1;
  if (g === undefined && b === undefined && t === undefined) return { status: "absent", reason: "幾何接触診断は未選択" };
  const bad = (why: string) => unavailableSceneContact(why);
  if (!exact(b,["schema_version","scene_digest","model_sha256","epoch","endpoint_ids","object_ids"]) ||
      b.schema_version !== "scene-contact-binding/v1" || !hash(b.model_sha256) || b.model_sha256 !== m.model_sha256 ||
      !hash(b.scene_digest) || typeof b.epoch !== "string" || !b.epoch || b.epoch.length > 256 ||
      !ids(b.endpoint_ids) || b.endpoint_ids.length < 1 || !ids(b.object_ids)) return bad("scene binding不正");
  const objectIds = b.object_ids;
  const runtime = m.coordinated_runtime_v1;
  if (!record(runtime) || runtime.epoch !== b.epoch || !ids(runtime.arm_ids) || !same(runtime.arm_ids,b.endpoint_ids)) return bad("runtime/scene binding不一致");
  if (!exact(g,["schema_version","scene_digest","model_sha256","frame_index","simulation_time_s","status","scope","force_status","force_n","objects","contacts"]) ||
      !["scene-contact-geometry/v1","scene-contact-geometry/v2"].includes(String(g.schema_version)) || g.model_sha256 !== b.model_sha256 || g.scene_digest !== b.scene_digest ||
      !index(g.frame_index) || g.frame_index !== payload.frame_index || !finite(g.simulation_time_s) || g.simulation_time_s < 0 ||
      g.simulation_time_s !== payload.time_s || g.status !== "observed" || g.scope !== "tool_object_geometry" ||
      g.force_status !== (g.schema_version==="scene-contact-geometry/v2" ? "separate_dynamics_evidence" : "not_evaluated_kinematic") || g.force_n !== null ||
      !Array.isArray(g.objects) || g.objects.length > 32 || !Array.isArray(g.contacts) || g.contacts.length > 256) return bad("幾何観測のschema/model/frame不一致");
  const objects: SceneContactObject[] = [];
  for (const o of g.objects) {
    if (!exact(o,["instance_id","position_m","orientation_wxyz"]) || !id(o.instance_id) ||
        !array(o.position_m,3) || !array(o.orientation_wxyz,4) || !unit(o.orientation_wxyz)) return bad("物体pose不正");
    objects.push({id:o.instance_id,position:o.position_m as V3,orientation:o.orientation_wxyz});
  }
  if (new Set(objects.map(o => o.id)).size !== objects.length || !same(objects.map(o => o.id),b.object_ids)) return bad("物体instance不一致");
  const contacts: SceneContactPoint[] = [];
  for (const c of g.contacts) {
    if (!exact(c,["endpoint_id","object_id","tool_geom_name","object_geom_name","point_world_m","normal_world","distance_m","penetration_m","relation"]) ||
        !id(c.endpoint_id) || !b.endpoint_ids.includes(c.endpoint_id) || !id(c.object_id) || !b.object_ids.includes(c.object_id) ||
        typeof c.tool_geom_name !== "string" || !c.tool_geom_name || typeof c.object_geom_name !== "string" || !c.object_geom_name ||
        !array(c.point_world_m,3) || !array(c.normal_world,3) || !unit(c.normal_world) || !finite(c.distance_m) ||
        !finite(c.penetration_m) || c.penetration_m < 0 || Math.abs(c.penetration_m-Math.max(0,-c.distance_m)) > 1e-12 ||
        c.relation !== (c.distance_m < 0 ? "penetrating" : c.distance_m === 0 ? "touching" : "near")) return bad("接触record不正");
    contacts.push({endpointId:c.endpoint_id,objectId:c.object_id,toolGeom:c.tool_geom_name,objectGeom:c.object_geom_name,
      point:c.point_world_m as V3,normal:c.normal_world as V3,distanceM:c.distance_m,penetrationM:c.penetration_m,
      relation:c.relation as SceneContactPoint["relation"]});
  }
  if (!exact(t,["schema_version","classification","phase","reason","epoch","scene_digest","target_object_ids","observed_pairs","force_evaluated","frame_index","simulation_time_s","presentation_frame_index","presentation_time_s"]) ||
      t.schema_version !== "contact-observation-presentation/v1" || t.epoch !== b.epoch || t.scene_digest !== b.scene_digest ||
      !ids(t.target_object_ids) || t.target_object_ids.length < 1 || !t.target_object_ids.every(x => objectIds.includes(x)) ||
      t.force_evaluated !== false || !index(t.frame_index) || t.frame_index > g.frame_index ||
      !finite(t.simulation_time_s) || t.simulation_time_s !== g.simulation_time_s ||
      t.presentation_frame_index !== g.frame_index || t.presentation_time_s !== g.simulation_time_s ||
      !(t.reason === null || typeof t.reason === "string") || !Array.isArray(t.observed_pairs) || t.observed_pairs.length > 64) return bad("Task観測のidentity/frame不一致");
  const phases: Record<string,string> = {observing:"running",completed:"success",aborted:"failure",invalid:"technical_invalid"};
  if (typeof t.phase !== "string" || !(t.phase in phases) || t.classification !== phases[t.phase] ||
      (t.phase === "observing" && t.frame_index !== g.frame_index)) return bad("Task phase/classification不一致");
  for (const p of t.observed_pairs) {
    if (!Array.isArray(p) || p.length !== 2 || !b.endpoint_ids.includes(p[0]) || !t.target_object_ids.includes(p[1])) return bad("Task pair不一致");
  }
  return {status:"available",reason:null,modelSha256:b.model_sha256,sceneDigest:b.scene_digest,epoch:b.epoch,
    frameIndex:g.frame_index,timeS:g.simulation_time_s,objects,contacts,dynamic:g.schema_version==="scene-contact-geometry/v2",targets:t.target_object_ids,
    phase:t.phase as "observing" | "completed" | "aborted" | "invalid",taskReason:t.reason as string | null};
}

/** 同一renderer lifetimeではscene/epoch変更と古いframeを拒否する。再開は新sessionが必要。 */
export class SceneContactStream {
  private identity: string | null = null;
  private frame = -1;
  private time = -1;
  apply(payload: TransportPayloadV0): SceneContactPresentation {
    const value = parseSceneContactPayload(payload);
    if (value.status !== "available") return value.status === "absent" && this.identity !== null
      ? unavailableSceneContact("選択済み診断の観測が欠落しています") : value;
    const identity = `${value.modelSha256}/${value.sceneDigest}/${value.epoch}`;
    if ((this.identity !== null && identity !== this.identity) || value.frameIndex <= this.frame || value.timeS < this.time) {
      return unavailableSceneContact("旧frame・変更されたscene/epochは表示しません");
    }
    this.identity = identity; this.frame = value.frameIndex; this.time = value.timeS;
    return value;
  }
}
