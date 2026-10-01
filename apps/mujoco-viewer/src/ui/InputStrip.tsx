import type {ProductViewerState,ProductViewerInputOverlayState} from "../wasm-scene/productViewerState.js";
import {loadcellDisplayValues} from "../app/instrumentPresentation.js";
import {inputKind,inputAvailability,standardGamepad,signedTriggerInput,matchedGamepad,type BrowserGamepadDisplay,type InputKind} from "./inputStripPresentation.js";
/** XYは取得座標のまま。表示上の上下反転をcommand mappingへ戻さない。 */
function Stick({axes,label,title}:{axes:readonly number[];label:string;title:string}) {
  const valid=axes.length===2 && axes.every(v=>Number.isFinite(v)&&Math.abs(v)<=1);
  return <div className="strip-stick" title={title}><span>{label}</span>
    <svg viewBox="0 0 64 64" role="img" aria-label={`${title} ${valid?axes.join(', '):'未取得'}`}>
      <rect x="4" y="4" width="56" height="56" className="stick-bound" />
      <path d="M4 32H60M32 4V60" className="stick-cross" />
      {valid ? <circle cx={32+axes[0]*28} cy={32+axes[1]*28} r="3" className="stick-point" /> :
        <text x="32" y="36" textAnchor="middle" className="input-missing">—</text>}
    </svg></div>;
}

/** 中央0から+を上、-を下へ表示。未知の量を0へ補完しない。 */
function VerticalZ({value,label,target}:{value:number|null;label:string;target:string}) {
  const y=value===null?null:32-value*26;
  const text=value===null?'未取得':`${value>0?'+':''}${value.toFixed(2)}`;
  return <div className="strip-z" title={`${label} → ${target} / 確定符号付きtrigger入力 ${text}（速度・力ではありません）`}>
    <span>{label}</span><svg viewBox="0 0 36 64" role="img" aria-label={`${label} 確定符号付き入力 ${text}`}
      data-signed-value={value??''} data-testid={label==='左 Z'?'z-left':'z-right'}>
      <rect x="8" y="6" width="12" height="52" className="z-track" />
      {y!==null && <><rect x="8" y={Math.min(32,y)} width="12" height={Math.abs(y-32)} className="z-fill" />
        <path d={`M6 ${y}H22`} className="z-needle" /></>}
      <path d="M5 32H23" className="z-zero" />
      <text x="28" y="10" textAnchor="middle">+</text><text x="28" y="60" textAnchor="middle">−</text>
      {y===null && <text x="14" y="36" textAnchor="middle" className="input-missing">—</text>}
    </svg></div>;
}

function GamepadStrip({input,raw}:{input:ProductViewerInputOverlayState;raw:BrowserGamepadDisplay|null}) {
  const standard=standardGamepad(raw);
  const axes=raw?.axes ?? input.gamepadInstrumentAxes;
  const control=input.gamepadTriggerControl;
  const target=(side:"left"|"right")=>control?.endpointBindings[side] ?? (control?.outputSide===side?'単一手先':'未割当');
  return <div className="gamepad-primary" aria-label="左右のXYとZ入力" data-mapping={standard?'standard':'generic'}>
    <VerticalZ value={signedTriggerInput(input,'left')} label="左 Z" target={target('left')}/>
    <Stick axes={axes.slice(0,2)} label={standard?'左 XY':'A0 / A1'} title={standard?`左stick XY → ${target('left')}`:'generic axes 0/1'}/>
    <Stick axes={axes.slice(2,4)} label={standard?'右 XY':'A2 / A3'} title={standard?`右stick XY → ${target('right')}`:'generic axes 2/3'}/>
    <VerticalZ value={signedTriggerInput(input,'right')} label="右 Z" target={target('right')}/>
  </div>;
}

/** ボタン・raw index・時刻・割当は設定側の診断だけに表示する。取得・送信は所有しない。 */
export function GamepadDiagnosticDetails({state,raw=null,live=true}:{state:ProductViewerState;raw?:BrowserGamepadDisplay|null;live?:boolean}) {
  const input=state.inputOverlay;
  if (!input || inputKind(input.sourceKind)!=='gamepad') return null;
  const unavailable=inputAvailability(input,undefined,live);
  if (unavailable) return <p>入力診断: {unavailable}</p>;
  const sample=matchedGamepad(input,raw);
  const standard=standardGamepad(sample);
  const buttons=sample?.buttons ?? input.gamepadButtons;
  const names=['A','B','X','Y','LB','RB','LT','RT','Back','Start','LS','RS','↑','↓','←','→','Home'];
  return <section aria-label="Gamepad詳細診断">
    <p>{standard?'standard mapping確認済み':'generic index / 標準配置未確認'} · browser raw {sample?.sampledAtMs??'未取得'} ms / backend sequence {input.sequence??'未取得'}</p>
    <div className="strip-buttons">{buttons.map((button,i)=><span key={i} data-active={button.pressed===true}>
      {standard?names[i]??`B${i}`:`B${i}`} {button.pressed===null?'未取得':button.pressed?'押下':'解放'} · B{i} raw {button.value??'—'}
    </span>)}</div>
    {(['left','right'] as const).map(side=>{const item=input.gamepadTriggerControl?.sides[side];return item&&<p key={side}>
      {side} → {input.gamepadTriggerControl?.endpointBindings[side]??'未割当'} · 適用 {item.zSign>0?'+':'-'}Z · {item.status} · trigger {item.triggerValue} · backend B{item.triggerButton}/B{item.signButton}
    </p>;})}
  </section>;
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
  const matchedRaw=matchedGamepad(input,raw);
  return <section className="input-strip" aria-label="入力source計器" data-source={kind}>
    <header title={`backend age ${input?.commandAgeMs??"—"} ms / frame ${state.currentFrameIndex??"—"} / sequence ${input?.sequence??"—"}`}><strong>入力 · {input?.sourceKind??"未取得"}</strong><span>{kind==="gamepad"?"XY: 入力 / Z: 確定符号付き入力":"取得信号"}</span></header>
    {unavailable?<p role="status">— {unavailable}</p>:input&&Renderer?<Renderer input={input} raw={matchedRaw}/>:<p>generic source / {input?.rawSignal?.sampleSchema??'取得済み信号なし'} · {input?.rawSignal?.values.join(', ')??'—'}</p>}
  </section>;
}
