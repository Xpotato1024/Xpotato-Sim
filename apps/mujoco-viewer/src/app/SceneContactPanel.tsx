import type { SceneContactPresentation } from "../contact/sceneContactPresentation.js";

/** 同じ適用frameのgeometry表示。力や接触成功を捏造せず、未評価と明記する。 */
export function SceneContactPanel({value,live}:{value:SceneContactPresentation; live:boolean}) {
  if (value.status === "absent") return null;
  if (value.status !== "available") return <section className="inspector-section" data-testid="scene-contact-panel"><h2>接触診断</h2><p>{value.reason}</p></section>;
  const phases = {observing:"観測中",completed:"観測期間終了",aborted:"停止",invalid:"観測無効"};
  return <section className="inspector-section" data-testid="scene-contact-panel" data-contact-count={value.contacts.length} data-scene-phase={value.phase} data-frame-index={value.frameIndex}>
    <div className="inspector-heading"><h2>接触診断</h2><span className="section-kicker">GEOMETRY</span></div>
    <p className="inspector-primary">{live ? phases[value.phase] : "通信待機 / 最終適用frame"}</p>
    <p className="inspector-note">固定物体 {value.objects.length}個 · frame {value.frameIndex} · {value.timeS.toFixed(2)} s</p>
    <p className="inspector-note">力：評価対象外。接触表示のみで、押し返し・貫通防止は行いません。</p>
    <div className="scene-contact-objects">{value.objects.map(o => <div key={o.id}><strong>{o.id}</strong> {value.targets.includes(o.id) ? "対象" : "対象外"}<br/>{o.position.map(v=>v.toFixed(3)).join(", ")} m</div>)}</div>
    {value.contacts.length === 0 ? <p>手先–物体の接触 / 近接なし</p> : <div className="scene-contact-records">{value.contacts.map((c,i) => <div key={i} data-contact-relation={c.relation}>
      <strong>{c.endpointId} → {c.objectId}</strong><br/>
      {c.relation === "penetrating" ? "食い込み" : c.relation === "touching" ? "接触" : "近接"} · 距離 {(c.distanceM*1000).toFixed(2)} mm · 食い込み {(c.penetrationM*1000).toFixed(2)} mm
    </div>)}</div>}
    <p className="inspector-note">点＝接触位置、矢印＝手先から物体への法線（固定表示長）。青：近接、黄：接触、橙：食い込み。</p>
    {value.taskReason === null ? null : <p className="inspector-note">{value.taskReason}</p>}
  </section>;
}
