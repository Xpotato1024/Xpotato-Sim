import type { ProductViewerInputOverlayState } from "../wasm-scene/productViewerState.js";
export type InputKind = "gamepad"|"keyboard"|"selfrionette"|"unknown";
export interface BrowserGamepadDisplay {mapping:string; id:string; index:number; sampledAtMs:number; sessionId?:string|null; axes:number[]; buttons:{pressed:boolean;value:number}[]}
export function inputKind(source:string|null|undefined):InputKind {
  if (["viewer_gamepad","gamepad","gamepad/v1"].includes(source ?? "")) return "gamepad";
  if (["viewer_keyboard","keyboard","keyboard/v1"].includes(source ?? "")) return "keyboard";
  if (["selfrionette","selfrionette/v1"].includes(source ?? "")) return "selfrionette";
  return "unknown";
}
/** 既存pollerの同じ取得結果だけを投影。deviceの選択順序も既存providerと同じ。 */
export function browserGamepadDisplay(pads:ArrayLike<Gamepad|null>|null,now:number):BrowserGamepadDisplay|null {
  if (!pads || !Number.isFinite(now)) return null;
  // 表示用の投影が不正なsampleを受けても、既存の入力取得経路へ例外を漏らさない。
  try {
    const pad=Array.from(pads).find(p=>p?.connected===true);
    if (!pad || typeof pad.id!=="string" || typeof pad.mapping!=="string" ||
        !Number.isSafeInteger(pad.index) || pad.index<0 || !pad.axes || !pad.buttons ||
        !Array.from(pad.axes).every(v=>Number.isFinite(v)&&Math.abs(v)<=1) ||
        !Array.from(pad.buttons).every(b=>b!=null&&typeof b.pressed==="boolean"&&Number.isFinite(b.value)&&b.value>=0&&b.value<=1)) return null;
    return {mapping:pad.mapping,id:pad.id,index:pad.index,sampledAtMs:now,axes:Array.from(pad.axes),
      buttons:Array.from(pad.buttons,b=>({pressed:b.pressed,value:b.value}))};
  } catch { return null; }
}
export function standardGamepad(raw:BrowserGamepadDisplay|null):boolean {
  return raw?.mapping==="standard" && Array.isArray(raw.axes) && raw.axes.length>=4 &&
    Array.isArray(raw.buttons) && raw.buttons.length>=17 &&
    raw.axes.every(v=>Number.isFinite(v)&&Math.abs(v)<=1) &&
    raw.buttons.every(b=>b!=null&&typeof b.pressed==="boolean"&&Number.isFinite(b.value)&&b.value>=0&&b.value<=1);
}
/** source切替・喪失を共通枠で判定し、古い計器の再利用を拒否する。 */
export function inputAvailability(input:ProductViewerInputOverlayState|null,selected?:InputKind,live=true):string|null {
  if (!live) return "表示停止 / 最終値は現在入力ではありません";
  if (!input) return "入力未取得";
  if (input.sourceKind==="n/a") return "入力取得待ち";
  const kind=inputKind(input.sourceKind);
  if (selected && kind!==selected) return "選択sourceと受信sourceが不一致";
  // backendは中立sampleにも*_inactiveを付ける。明示した接続/焦点/zero情報が
  // 揃った場合だけ生入力を表示し、timeout・切断・不正を中立へ読み替えない。
  const neutral = !input.sourceActive && (
    kind === "gamepad" && input.staleReason === "gamepad_inactive" &&
      input.gamepadConnected === true && input.gamepadStale === false && input.gamepadZeroState === true ||
    kind === "keyboard" && input.staleReason === "keyboard_inactive" &&
      input.keyboardFocusState === "focused" && input.keyboardZeroState === true);
  if (input.staleReason && !neutral) return input.staleReason;
  if (kind==="gamepad" && (input.gamepadStale===true || input.gamepadConnected===false)) return "stale / disconnected";
  return null;
}

/** backendが同一sampleで確定した符号とtrigger量。速度・力ではなく符号付き入力量。 */
export function signedTriggerInput(input: ProductViewerInputOverlayState, side: "left" | "right"): number | null {
  const control = input.gamepadTriggerControl;
  if (!control || control.reason !== null || inputAvailability(input) !== null ||
      input.motionStatus === "faulted" || input.motionStatus === "stopped") return null;
  const assigned = control.outputScope === "coordinated" ? control.endpointBindings[side] : control.outputSide === side;
  const item = control.sides[side];
  if (!assigned || item.status !== "armed" || !Number.isFinite(item.triggerValue) ||
      item.triggerValue < 0 || item.triggerValue > 1 || (item.zSign !== 1 && item.zSign !== -1)) return null;
  return item.triggerValue === 0 ? 0 : item.triggerValue * item.zSign;
}

export function matchedGamepad(input: ProductViewerInputOverlayState | null, raw: BrowserGamepadDisplay | null): BrowserGamepadDisplay | null {
  return input && raw && raw.id === input.gamepadId && raw.index === input.gamepadIndex &&
    !!raw.sessionId && raw.sessionId === input.providerSessionId ? raw : null;
}
