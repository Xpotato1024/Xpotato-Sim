import type {ForcePresentation} from "../wasm-scene/forcePresentation.js";
import {useLayoutEffect,useRef} from "react";

/** 原力と表示加工を区別し、無効・非表示条件で以前の値を残さない。 */
export function ForceCuePanel({value}:{value:ForcePresentation}) {
  const panel=useRef<HTMLElement|null>(null);
  useLayoutEffect(()=>{panel.current?.setAttribute("data-panel-committed-at-ms",String(performance.now()));},[value]);
  if(!value.comparison)return null;
  return <section ref={panel} className="inspector-section force-cue-panel" aria-label="追加接触力の提示"
    data-status={value.status} data-condition-sha={value.conditionSha} data-sample-id={value.sampleId}
    data-source-tick={value.sourceTick} data-source-frame={value.sourceFrame} data-received-at-ms={value.receivedAtMs}
    data-scene-adopted-at-ms={value.adoptedAtMs} data-scene-submitted-at-ms={value.renderSubmittedAtMs}>
    <h2>接触力の表示条件</h2><p>{value.reason}</p>
    {value.status==="available"&&value.pairs.map(p=><div key={`${p.endpoint}/${p.object}`}>
      <strong>{p.endpoint} ← {p.object}</strong>
      {p.force===null?<p>荷重を取得できません（{p.status}）</p>:<>
        <p>反力 [{p.force.map(v=>v.toFixed(2)).join(", ")}] N<br/>法線荷重 {p.load?.toFixed(2)} N {p.saturated?"・表示上限":""}</p>
        <meter aria-label={`${p.endpoint}/${p.object} 法線荷重`} min={0} max={1} value={p.loadFraction}/>
      </>}
    </div>)}
    <small>固定スケール。MuJoCoの数値解であり実機の測定値ではありません。</small>
  </section>;
}
