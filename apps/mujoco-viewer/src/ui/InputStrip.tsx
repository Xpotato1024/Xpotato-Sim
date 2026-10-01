import type {ProductViewerState,ProductViewerInputOverlayState} from "../wasm-scene/productViewerState.js";
import {loadcellDisplayValues} from "../app/instrumentPresentation.js";
import {inputKind,inputAvailability,standardGamepad,type BrowserGamepadDisplay,type InputKind} from "./inputStripPresentation.js";
function Stick({axes,label}:{axes:readonly number[];label:string}) {
  const valid=axes.length===2 && axes.every(v=>Number.isFinite(v)&&Math.abs(v)<=1);
  return <div className="strip-stick"><span>{label}</span><svg viewBox="0 0 64 64" role="img" aria-label={`${label} ${valid?axes.join(', '):'未取得'}`}>
    <rect x="4" y="4" width="56" height="56" className="stick-bound" /><path d="M4 32H60M32 4V60" className="stick-cross" />
    {valid && <circle cx={32+axes[0]*28} cy={32+axes[1]*28} r="3" className="stick-point" />}</svg></div>;
}
function GamepadStrip({input,raw}:{input:ProductViewerInputOverlayState;raw:BrowserGamepadDisplay|null}) {
  const standard=standardGamepad(raw);
  const axes=raw?.axes ?? input.gamepadInstrumentAxes;
  const buttons=raw?.buttons ?? input.gamepadButtons;
  const control=input.gamepadTriggerControl;
  const z=(side:"left"|"right")=>{
    const applied=control?.sides[side];
    const trigger=standard?(side==="left"?6:7):applied?.triggerButton;
    const bumper=standard?(side==="left"?4:5):applied?.signButton;
    if(trigger===undefined||bumper===undefined)return <div className="strip-z">{side} · backend trigger/sign割当未取得</div>;
    const value=buttons[trigger]?.value;
    return <div className="strip-z" title={applied?`backend trigger B${applied.triggerButton} / sign B${applied.signButton}`:undefined}>
      <strong>{applied?`Z(${side==="left"?'L':'R'})`:'raw shoulder'} · 適用 {applied?`${applied.zSign>0?'+':'-'}Z`:'未取得'}</strong>
      <span>{standard?(side==="left"?'LT':'RT'):`B${trigger}`} raw {value==null?'—':value.toFixed(2)} · {standard?(side==="left"?'LB':'RB'):`B${bumper}`} {buttons[bumper]?.pressed==null?'未取得':buttons[bumper].pressed?'押下':'解放'}</span>
      <meter min="0" max="1" value={value??0} aria-label={`B${trigger} analog`} data-unknown={value==null} />
      <span>{applied?.status??'未適用'} · {control?.endpointBindings[side] ?? (control?.outputSide===side?'単一手先':'未割当')}</span></div>;
  };
  const names=['A','B','X','Y','LB','RB','LT','RT','Back','Start','LS','RS','↑','↓','←','→','Home'];
  return <><div className="gamepad-primary">{z('left')}<Stick axes={axes.slice(0,2)} label={standard?'左stick XY':'A0/A1'} />
    <Stick axes={axes.slice(2,4)} label={standard?'右stick XY':'A2/A3'} />{z('right')}
    <div className="strip-buttons">{buttons.map((button,i)=> <span key={i} data-active={button.pressed===true}>{standard?names[i]??`B${i}`:`B${i}`} {button.pressed===null?'?':button.pressed?'●':'○'}</span>)}</div></div>
    <small>{standard?'standard mapping確認済み':`generic index: ${raw?.mapping||'mapping未取得'} / ${raw?'shape未対応の可能性':'backendだけでは標準配置を確認できません'}`} · browser raw {raw?`${raw.sampledAtMs.toFixed(0)} ms`:'未取得'} / backend別sample</small></>;
}
function SelfrionetteStrip({input}:{input:ProductViewerInputOverlayState}) {
  const channels=loadcellDisplayValues(input.rawSignal);
  if (!channels) return <p>7ch raw未取得 / contract不一致</p>;
  const scale=Math.max(...channels.map(Math.abs))||1;
  return <><div className="strip-channels">{channels.map((v,i)=><div key={i}><span>CH{i+1}</span><output>{v.toFixed(2)}</output>
    <meter min={-scale} max={scale} value={v} aria-label={`CH${i+1} 相対比`} /></div>)}</div>
    <small>raw unit・未校正 / 指・力N対応なし / バーは同一sample内の相対比 ±{scale.toPrecision(3)} / source時刻 {input.rawSignal!.sourceTimestampS}</small></>;
}
function KeyboardStrip({input}:{input:ProductViewerInputOverlayState}) {
  const entries=Object.entries(input.keyboardKeyState);
  return <><div className="strip-buttons">{entries.length?entries.map(([key,pressed])=><kbd key={key} data-active={pressed}>{key} {pressed?'●':'○'}</kbd>):<span>記録キー未取得（未押下とは判定しません）</span>}</div>
    <small>backend記録: {input.keyboardActiveKeyCodes.join(' / ')||'押下キー情報なし'} / {input.keyboardFocusState??'focus未取得'}</small></>;
}
const sourceRenderers={gamepad:GamepadStrip,keyboard:KeyboardStrip,selfrionette:SelfrionetteStrip};
export function InputStrip({state,raw=null,selected,live=true}:{state:ProductViewerState;raw?:BrowserGamepadDisplay|null;selected?:InputKind;live?:boolean}) {
  const input=state.inputOverlay;
  const unavailable=inputAvailability(input,selected,live);
  const kind=inputKind(input?.sourceKind);
  const Renderer=kind==='unknown'?null:sourceRenderers[kind];
  const matchedRaw=input && raw?.id===input.gamepadId && raw.index===input.gamepadIndex &&
    !!raw.sessionId && raw.sessionId===input.providerSessionId ? raw : null;
  return <section className="input-strip" aria-label="入力source計器" data-source={kind}>
    <header title={`backend age ${input?.commandAgeMs??"—"} ms / frame ${state.currentFrameIndex??"—"} / sequence ${input?.sequence??"—"}`}><strong>入力 · {input?.sourceKind??"未取得"}</strong><span>生入力とbackend適用状態は別sample</span></header>
    {unavailable?<p role="status">— {unavailable}</p>:input&&Renderer?<Renderer input={input} raw={matchedRaw}/>:<p>generic source / {input?.rawSignal?.sampleSchema??'取得済み信号なし'} · {input?.rawSignal?.values.join(', ')??'—'}</p>}
  </section>;
}
