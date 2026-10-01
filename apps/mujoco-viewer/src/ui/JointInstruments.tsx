import { memo } from "react";
import { angleNeedle, angleSector, angleLabel, jointReadouts } from "../wasm-scene/jointPresentation.js";
import type { ProductViewerState } from "../wasm-scene/productViewerState.js";
const DialFace = memo(function DialFace() {
  return <g><circle cx="42" cy="43" r="30" className="dial-track" />
    {Array.from({length:12}, (_, i) => i * 30).map(angle => <line key={angle} x1="42" y1="13" x2="42" y2={angle % 90 === 0 ? 19 : 16}
      transform={`rotate(${angle} 42 43)`} className="dial-tick" />)}
    <text x="42" y="9" textAnchor="middle" className="dial-zero">0</text>
    <text x="78" y="46" textAnchor="middle" className="dial-zero">+90</text>
    <text x="6" y="46" textAnchor="middle" className="dial-zero">-90</text>
    <text x="42" y="80" textAnchor="middle" className="dial-zero">±180</text></g>;
});
/** 同一sampleのqposとaddressを使い、別sampleの数値を混ぜない。 */
export function JointInstruments({state, names, unavailable, terminal}: {
  state: ProductViewerState; numbers?: ProductViewerState; names?: readonly string[]; unavailable?: string; terminal?: boolean;
}) {
  const readouts = jointReadouts(state.jointLayout, !unavailable && state.qposStatus === "ready" ? state.currentQpos : null);
  const live = names ? names.map(name => readouts.find(j => j.name === name)).filter(j => j !== undefined) : readouts;
  if (!live.length) return <p className="instrument-empty">関節情報未取得</p>;
  return <div className="joint-instruments">{live.map((joint, index) => {
    const needle = joint.kind === "hinge" ? angleNeedle(joint.value) : null;
    const sector = joint.kind === "hinge" ? angleSector(joint.value) : null;
    const out = joint.degrees !== null && Math.abs(joint.degrees) > 180;
    const label = joint.kind === "hinge" ? angleLabel(joint.degrees) : joint.kind === "slide" && joint.value !== null ? `${joint.value.toFixed(3)} m` : "—";
    return <div className="joint-instrument" key={joint.name} title={joint.name}>
      <div className="joint-instrument__label">q{index + 1}</div>
      {joint.kind === "hinge" ? <svg className="angle-indicator" viewBox="0 0 84 84" role="img" aria-label={`${joint.name} ${label}${out ? " 表示範囲外" : ""}`}>
        {sector && <path d={sector} className={joint.value! > 0 ? "dial-positive" : "dial-negative"} />}
        <DialFace />{needle && <><line x1="42" y1="43" x2={needle.x} y2={needle.y} className="dial-needle" /><circle cx="42" cy="43" r="2" className="dial-hub" /></>}
        {!needle && <text x="42" y="47" textAnchor="middle" className="dial-missing">—</text>}
      </svg> : <span className="joint-kind">{joint.kind}</span>}
      <output className="joint-degree">{label}</output>
      <small>{unavailable || (out ? "表示範囲外" : joint.value === null ? joint.kind === "ball" || joint.kind === "free" ? "複数座標・診断" : "未取得/invalid" : terminal ? "終了時" : "")}</small>
    </div>;
  })}</div>;
}