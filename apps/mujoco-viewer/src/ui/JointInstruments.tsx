import { memo } from "react";
import { angleNeedle, jointReadouts } from "../wasm-scene/jointPresentation.js";
import type { ProductViewerState } from "../wasm-scene/productViewerState.js";

/** 不変の目盛りを再構築せず、角度針と数値だけを現在stateで更新する。 */
const DialFace = memo(function DialFace() {
  return <g>
            <circle cx="42" cy="43" r="30" className="dial-track" />
            {[0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330].map((angle) => <line key={angle} x1="42" y1="13" x2="42" y2={angle % 90 === 0 ? 18 : 16}
              transform={`rotate(${angle} 42 43)`} className="dial-tick" />)}
            <text x="42" y="8" textAnchor="middle" className="dial-zero">0</text>
            <text x="79" y="47" textAnchor="middle" className="dial-zero">+</text>
  </g>;
});

/** 現在角の方向と数値を分離。range meterや実機のsafe zoneではない。 */
export function JointInstruments({ state, numbers }: { state: ProductViewerState; numbers: ProductViewerState }) {
  const live = jointReadouts(state.jointLayout, state.currentQpos);
  const labels = jointReadouts(state.jointLayout, numbers.currentQpos);
  if (!live.length) return <p className="instrument-empty">関節情報を待っています</p>;
  return <div className="joint-instruments">
    {live.map((joint, index) => {
      const number = labels[index];
      const needle = joint.kind === "hinge" ? angleNeedle(joint.value) : null;
      const text = joint.value === null ? null : joint.kind === "hinge" ? number?.degrees?.toFixed(1) : joint.kind === "slide" ? number?.value?.toFixed(3) : null;
      const unit = joint.kind === "hinge" ? "°" : joint.kind === "slide" ? " m" : "";
      return <div className="joint-instrument" key={joint.name}>
        <div className="joint-instrument__label"><span>J{index + 1}</span><strong title={joint.name}>{joint.name}</strong></div>
        <div className="joint-instrument__body">
          {joint.kind === "hinge" ? <svg className="angle-indicator" viewBox="0 0 84 80" role="img" aria-label={`${joint.name} 角度指標 ${text ?? "未取得"}${unit}`}>
            <DialFace />
            {needle !== null && <><line x1="42" y1="43" x2={needle.x} y2={needle.y} className="dial-needle" /><circle cx="42" cy="43" r="3" className="dial-hub" /></>}
            {needle === null && <text x="42" y="47" textAnchor="middle" className="dial-missing">—</text>}
          </svg> : <span className="joint-kind">{joint.kind}</span>}
          <div className="joint-readout"><span className="joint-readout__number">{text ?? "—"}<small>{text ? unit : ""}</small></span>
            <span className="joint-readout__source">{joint.kind === "hinge" ? "回転角" : joint.kind === "slide" ? "並進座標" : "複数座標・角度計非対応"}</span>
          </div>
        </div>
      </div>;
    })}
  </div>;
}
