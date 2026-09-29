import type { DynamicsPresentation } from "../wasm-scene/dynamicsPresentation.js";
/** solver由来の観測値。実機の力・安全性と混同しない。 */
export function DynamicsPanel({value}:{value:DynamicsPresentation}){
 if(value.status==="absent")return null;
 if(value.status!=="available")return <section className="inspector-section"><h2>動力学</h2><p>{value.reason}</p></section>;
 return <section className="inspector-section" data-testid="dynamics-panel" data-frame-index={value.frame}>
  <div className="inspector-heading"><h2>動力学・反力</h2><span className="section-kicker">DYNAMICS</span></div><p>{value.time.toFixed(3)} s · frame {value.frame}</p>
  {value.objects.map(o=><p key={o.id}>{o.id} ({o.motion})<br/>z = {o.z.toFixed(4)} m · 速度 {o.velocity.map(v=>v.toFixed(3)).join(", ")} m/s</p>)}
  <p>観測対象の有効なsolver接触 {value.contacts.filter(c=>c.status==="measured").length}件</p>
  {value.contacts.map((c,i)=><p key={i}>{c.pair}<br/>{c.force===null ? "力は未取得" : c.force.map(f=>f.toFixed(3)).join(", ")+" N"}</p>)}
  <details><summary>指令と実状態</summary>{value.joints.map(j=><p key={j.name}>{j.name}<br/>指令 {j.target.toFixed(3)} / 実状態 {j.position.toFixed(3)} rad<br/>actuator {j.torque.toFixed(3)} Nm</p>)}</details>
  <p className="inspector-note">力は矢印先のgeomに働くworld座標の値。native MuJoCoの数値解であり、実機測定ではありません。</p>
 </section>;
}
