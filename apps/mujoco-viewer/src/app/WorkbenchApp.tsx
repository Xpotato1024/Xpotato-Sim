import {useEffect, useRef, useState} from "react";
import {createMujocoSceneRenderer, type MujocoSceneRenderer} from "../wasm-scene/mujocoSceneRenderer.js";
import {createInitialProductViewerState} from "../wasm-scene/productViewerState.js";
import {SceneContactPanel} from "./SceneContactPanel.js";
import {DynamicsPanel} from "./DynamicsPanel.js";
import {JointInstruments} from "../ui/JointInstruments.js";
import {InputStrip,GamepadDiagnosticDetails} from "../ui/InputStrip.js";
import {browserGamepadDisplay,type BrowserGamepadDisplay} from "../ui/inputStripPresentation.js";
import {SceneViewport,scenePanes} from "../ui/SceneViewport.js";
import {jointRailGroups} from "../wasm-scene/jointPresentation.js";
import type {ViewLayout} from "../wasm-scene/viewportPresentation.js";
import {formatInputOverlayText} from "../wasm-scene/productViewerState.js";
import type {TransportPayloadV0} from "../types/transportPayload.js";
import "./productViewer.css";
import "./workbench.css";
import "../ui/operation.css";
import {canConnect, conditionReadIsCurrent, editorReplyIsCurrent, createWorkbenchGamepadMessages, preparationIsCurrent, type Preparation} from "./workbenchLifecycle.js";
import {ConditionEditor,type Condition,type Descriptor} from "./ConditionEditor.js";

type Ticket = {trial_id: string; epoch: string; condition_sha256: string};
type Status = {phase: string; revision: number; generation: number; busy: string|null; busy_operation?:string|null;
  ticket: Ticket|null; profile_id: string|null; ticks: number; simulation_time_s:number; error: string|null;
  profiles: {id:string; available:boolean; reason:string|null}[];
  results: {trial_id:string; runner_stop_reason:string; recording:string; ticks:number}[];
  fixture_mode: boolean; renderer_ready:boolean; preselected_profile?:string; native_builds?:number; python_heap?:number;
  rss_bytes?:number; private_bytes?:number};
const phases: Record<string,string> = {unselected:"未選択・待機",ready:"開始待ち",waiting_input:"新しい中立入力を待機",
  running:"実行中",finalizing:"保存中",terminal:"停止確認・結果保存済み",faulted:"停止・障害",recording_failed:"記録失敗",closed:"終了"};

/** 条件/試行の状態はbackendのみが所有する。表示準備と入力接続だけを所有する。 */
export function WorkbenchApp() {
  const canvas = useRef<HTMLCanvasElement>(null);
  const interaction = useRef<HTMLDivElement>(null);
  const [screen,setScreen]=useState<"setup"|"operate">("setup");
  const [layout,setLayout]=useState<ViewLayout>("single");
  const [raw,setRaw]=useState<BrowserGamepadDisplay|null>(null);
  const receivedAt=useRef<number|null>(null);
  const [frameStale,setFrameStale]=useState(false);
  const renderer = useRef<MujocoSceneRenderer|null>(null);
  const socket = useRef<WebSocket|null>(null);
  const current = useRef<Status|null>(null);
  const claimed = useRef(false);
  const capability = useRef(new URLSearchParams(location.hash.slice(1)).get("capability") || "");
  const [status, setStatus] = useState<Status|null>(null);
  const [selected,setSelected] = useState("");
  const [owned,setOwned] = useState(false);
  const [connected,setConnected] = useState(false);
  const [ready,setReady] = useState<string|null>(null);
  const [error,setError] = useState("");
  const [edited,setEdited]=useState<Condition|null>(null);
  const [descriptors,setDescriptors]=useState<Descriptor[]>([]);
  const [changes,setChanges]=useState<any[]>([]);
  const editorPending=useRef<{id:string;export:boolean;socket:WebSocket|null;status:Status}|null>(null);
  const [editorRequestId,setEditorRequestId]=useState<string|null>(null);
  const editingBusy=editorRequestId!==null;
  const initialized=useRef(false);
  const clearEditor=()=>{editorPending.current=null;setEditorRequestId(null);};
  const beginEditor=(exportFile=false)=>{
    const s=current.current;if(!s||editorPending.current) return null;
    const pending={id:crypto.randomUUID(),export:exportFile,socket:socket.current,status:s};
    editorPending.current=pending;setEditorRequestId(pending.id);return pending;
  };
  const sendEditor=(pending:NonNullable<typeof editorPending.current>,op:string,extra:object={})=>
    send({op,request_id:pending.id,capability:capability.current,revision:pending.status.revision,ticket:pending.status.ticket,...extra});
  const editRequest=(op:string,extra:object={},exportFile=false)=>{const pending=beginEditor(exportFile);if(pending) sendEditor(pending,op,extra);};
  const [state,setState] = useState(createInitialProductViewerState);
  const readyEpoch = useRef<string|null>(null);
  const preparing = useRef<Preparation|null>(null);
  const failedEpoch = useRef<string|null>(null);
  const send = (value: object) => {if(socket.current?.readyState===WebSocket.OPEN) socket.current.send(JSON.stringify(value));};
  const command = (op:string, extra:object={}) => {
    const s=current.current;
    if(!s) return;
    send({op, id:crypto.randomUUID(), revision:s.revision, ticket:s.ticket, capability:capability.current,...extra});
  };
  useEffect(()=>{
    if(!editingBusy)return;
    const pending=editorPending.current;
    const timeout=window.setTimeout(()=>{if(editorPending.current===pending){clearEditor();setError("条件要求が時間切れです。状態を再取得してから操作してください");send({op:"status"});}},30000);
    return ()=>window.clearTimeout(timeout);
  },[editorRequestId]);
  useEffect(()=>{
    history.replaceState(null,"",location.pathname+location.search);
    let disposed=false;
    const sampleInput=createWorkbenchGamepadMessages();
    const r=createMujocoSceneRenderer({canvas:canvas.current!,interactionElement:interaction.current!,getScenePanes:()=>scenePanes(canvas.current!),initialCameraView:"operator",profile:null,onStateChange:setState,onError:e=>setError(e.message)});
    renderer.current=r;
    // 明示再接続は状態照会だけ。claimもStartも自動送信しない。
    const connect=()=>{
      if(disposed || !canConnect(socket.current)) return;
      const port=new URLSearchParams(location.search).get("workbench");
      if(!port || !/^\d{1,5}$/.test(port)) {setError("制御portが不正です");return;}
      const ws=new WebSocket(`ws://127.0.0.1:${port}/control`);
      socket.current=ws;
      const isCurrent=()=>!disposed && socket.current===ws;
      ws.onopen=()=>{if(!isCurrent()) return;setConnected(true);send({op:"status"});};
      ws.onclose=()=>{if(!isCurrent()) return;claimed.current=false;setOwned(false);setConnected(false);
        clearEditor();
        r.invalidateWorkbench();preparing.current=null;failedEpoch.current=null;setReady(null);readyEpoch.current=null;};
      ws.onerror=()=>{if(isCurrent()) setError("制御接続を確認してください");};
      ws.onmessage=async event=>{
        if(disposed || socket.current!==ws) return;
        try {
          const message=JSON.parse(event.data);
          if(message.type==="claimed") {
            claimed.current=true;setOwned(true);send({op:"status"});
            if(current.current?.phase==="ready" && current.current.ticket?.epoch===readyEpoch.current) command("renderer_ready");
          }
          if(message.type==="rejected" || message.error) setError(message.error);
          const pending=editorPending.current;
          const editorReply=editorReplyIsCurrent(pending,message,socket.current,current.current);
          if(message.type==="rejected" && pending && message.request_id===pending.id) clearEditor();
          if(message.type==="edited_condition" && editorReply && message.generation===current.current?.generation
              && message.ticket?.epoch===current.current?.ticket?.epoch && message.revision===pending!.status.revision+1) {
            setEdited(message.condition);setSelected(message.condition.preset_id);setDescriptors(message.descriptors);
            current.current={...current.current!,revision:message.revision};setStatus(current.current);setError("");
            clearEditor();
            if(pending!.export) {
              const url=URL.createObjectURL(new Blob([JSON.stringify(message.condition,null,2)],{type:"application/json"}));
              const a=document.createElement("a");a.href=url;a.download="workbench-condition.json";a.click();URL.revokeObjectURL(url);
            }
          }
          if(message.type==="condition_diff" && editorReply) {setChanges(message.changes);clearEditor();}
          if(message.type==="status") {
            if(message.ticket?.epoch!==current.current?.ticket?.epoch || message.generation!==current.current?.generation) setRaw(null);
            if(message.generation!==current.current?.generation || message.ticket?.epoch!==current.current?.ticket?.epoch
              || !["ready","waiting_input","running","terminal"].includes(message.phase)) {
              r.invalidateWorkbench(); readyEpoch.current=null; preparing.current=null; failedEpoch.current=null; setReady(null);
            }
            if(editorPending.current && !conditionReadIsCurrent(editorPending.current.socket,editorPending.current.status,ws,message)) clearEditor();
            current.current=message;setStatus(message);
            if(message.initial_condition && !initialized.current) {
              initialized.current=true;setEdited(message.initial_condition);setDescriptors(message.initial_descriptors);
            }
            if(message.preselected_profile) setSelected(old=>old||message.preselected_profile);
            if(message.ticket?.epoch!==readyEpoch.current) setReady(null);
          }
          if(message.type==="accepted" && current.current) {
            current.current={...current.current,revision:message.revision};
            setStatus(current.current);
          }
          if(message.type==="frame") {
            const s=current.current;
            if(!s || !s.ticket || s.busy || message.generation!==s.generation || message.ticket.epoch!==s.ticket.epoch) return;
            receivedAt.current=performance.now();setFrameStale(false);
            const epoch=s.ticket.epoch;
            if(failedEpoch.current===epoch) return;
            if(readyEpoch.current!==epoch) {
              if(preparing.current?.epoch===epoch) return;
              const captured:Preparation={socket:ws,epoch,generation:s.generation};
              preparing.current=captured;
              const valid=()=>!disposed && preparing.current===captured
                && preparationIsCurrent(captured,socket.current,current.current);
              try {
                await r.prepareWorkbench(message.payload as TransportPayloadV0,epoch);
                if(!valid()) return;
                readyEpoch.current=epoch;failedEpoch.current=null;setReady(epoch);
                if(claimed.current && current.current?.phase==="ready") command("renderer_ready");
              } catch(e) {
                if(valid()) {failedEpoch.current=epoch;setError(String(e));}
              } finally {
                if(preparing.current===captured) preparing.current=null;
              }
            } else r.applyWorkbench(message.payload,epoch);
          }
        } catch(e) {if(isCurrent()) setError(String(e));}
      };
    };
    connect();
    const timer=window.setInterval(()=>{
      const s=current.current;
      setFrameStale(!!s && ["waiting_input","running"].includes(s.phase) && receivedAt.current!==null && performance.now()-receivedAt.current>1000);
      if(!s || s.busy || !claimed.current || s.fixture_mode || !["waiting_input","running"].includes(s.phase) || !s.ticket) return;
      const pads=document.hasFocus()?navigator.getGamepads():null;
      const message=sampleInput(s.ticket.epoch,pads,performance.now()/1000);
      const observed=browserGamepadDisplay(pads,performance.now());
      const session=message?.metadata?.viewer_provider_session_id;
      setRaw(observed&&typeof session==="string"?{...observed,sessionId:session}:null);
      if(message===null) return;
      send({op:"input",capability:capability.current,ticket:s.ticket,message:JSON.stringify(message)});
    },40);
    const reconnect=()=>connect();
    window.addEventListener("workbench-reconnect",reconnect);
    // 測定用projectionはsecretを含めず、renderer内部counterだけを公開する。
    (window as any).__workbenchCounters=()=>({...r.counters(),sockets:socket.current?.readyState===1?1:0,timers:1,
      pythonHeap:current.current?.python_heap,pythonRss:current.current?.rss_bytes,pythonPrivate:current.current?.private_bytes,
      nativeBuilds:current.current?.native_builds});
    return ()=>{disposed=true;window.clearInterval(timer);window.removeEventListener("workbench-reconnect",reconnect);
      socket.current?.close();socket.current=null;r.dispose();delete (window as any).__workbenchCounters;};
  },[]);
  const active=!!status && ["waiting_input","running","finalizing"].includes(status.phase);
  const busy=!!status?.busy;
  const groups=jointRailGroups(state.jointLayout,state.modelContractVersion);
  const left=groups.find(g=>g.id!=="right"),right=groups.find(g=>g.id==="right");
  const invalidJoint=!connected?"未接続":!ready?"別epoch / 準備待ち":frameStale?"更新停止":state.qposStatus!=="ready"?"invalid / 未取得":undefined;
  return <main className="workbench operation-shell" data-screen={screen}>
    <header><h1>Xpotato-Sim 実験Workbench</h1><p>有限試行 · simulation-only · ロボット出力なし</p>
      <span>{connected?"接続中":"未接続"} / {owned?"操作権あり":"閲覧のみ"}</span>
      {!connected && <button onClick={()=>window.dispatchEvent(new Event("workbench-reconnect"))}>状態へ再接続</button>}
      {!owned && connected && capability.current && <button onClick={()=>send({op:"claim",capability:capability.current})}>操作権を取得</button>}
    </header>
    <nav className="operation-controls" aria-label="試行操作">
      <div className="operation-controls__actions">
      <button disabled={active||busy} onClick={()=>setScreen("setup")} aria-pressed={screen==="setup"}>設定・準備</button>
      <button disabled={screen==="setup"&&(!ready||!["ready","terminal"].includes(status?.phase??""))} onClick={()=>setScreen("operate")} aria-pressed={screen==="operate"}>操作画面へ</button>
      <button className="primary-control" disabled={screen!=="operate"||!owned||busy||editingBusy||status?.phase!=="ready"||!status.renderer_ready||ready!==status.ticket?.epoch} onClick={()=>command("start")}>開始</button>
      <button disabled={!owned||busy||editingBusy||status?.phase!=="terminal"} onClick={()=>{setReady(null);command("retry");}}>同じ条件で再試行</button>
      <button aria-pressed={layout==="single"} onClick={()=>setLayout("single")}>Single</button>
      <button aria-pressed={layout==="assist"} onClick={()=>setLayout("assist")}>Assist</button>
      <span>{status?.busy_operation==="stop"?"停止要求中":status?.phase==="finalizing"?"保存中":phases[status?.phase??"unselected"]??status?.phase} · simulation {status?.simulation_time_s?.toFixed(2)??"—"} s</span>
      </div>
      <button className="stop-control" disabled={!owned||(!active&&!busy&&status?.phase!=="ready")} onClick={()=>command("stop")}>停止を要求</button>
    </nav>
    {(error||status?.error) && <p className="operation-warning" role="alert">{error||status?.error}</p>}
    <section className="workbench-controls" hidden={screen!=="setup"}>
      <label>次の条件<select aria-label="次の条件" value={selected} disabled={active||busy||editingBusy||!owned} onChange={e=>{if(editorPending.current)return;setSelected(e.target.value);setEdited(null);setChanges([]);if(e.target.value) editRequest("clone",{profile_id:e.target.value});}}>
        <option value="">profileを選択してください</option>
        {status?.profiles.map(p=><option key={p.id} value={p.id} disabled={!p.available}>{p.id}{p.available?"":` — 利用不可: ${p.reason}`}</option>)}
      </select></label>
      <button disabled={!owned||!selected||active||busy||editingBusy} onClick={()=>{setError("");setReady(null);command("prepare",{profile_id:selected,...(edited?{condition:edited}:{})});}}>検証・準備</button>
      <p>適用中: {status?.profile_id??"なし"} / {busy?"処理受付済み・完了待ち":phases[status?.phase??"unselected"]??status?.phase}</p>
      <p>適用condition: {status?.ticket?.condition_sha256??"なし"} / 次の編集条件: {edited?.preset_id??"未選択"}</p>
      <p>描画: {ready?"初期scene・shader準備済み":"準備待ち"} / 入力: {status?.fixture_mode?"明示software検証fixture":"開始後にGamepadの新しい中立入力を確認"}</p>
      <p>simulation時間 {status?.simulation_time_s??0} s · tick {status?.ticks??0} · epoch {status?.ticket?.epoch??"なし"}</p>
      {(error||status?.error) && <p role="alert">{error||status?.error}</p>}
    </section>
    <div className="workbench-body"><div className="operation-workspace" data-left={!!left} data-right={!!right}>
      {left&&<aside className="joint-rail joint-rail--left" aria-label={left.label}><h2>{left.label}</h2><JointInstruments state={state} names={left.names} unavailable={invalidJoint} terminal={status?.phase==="terminal"}/></aside>}
      <SceneViewport canvas={canvas} interaction={interaction} renderer={renderer} layout={layout} visible={!!ready}/>
      {right&&<aside className="joint-rail joint-rail--right" aria-label={right.label}><h2>{right.label}</h2><JointInstruments state={state} names={right.names} unavailable={invalidJoint} terminal={status?.phase==="terminal"}/></aside>}
      <InputStrip state={state} raw={raw} selected="gamepad" live={connected&&!!ready&&!frameStale&&status?.phase!=="terminal"}/>
    </div>
      <aside className="setup-inspector" hidden={screen!=="setup"}><ConditionEditor condition={edited} descriptors={descriptors} onChange={c=>{if(!editorPending.current)setEdited(c);}} disabled={!owned||active||busy||editingBusy||status?.phase==="recording_failed"}
        onError={setError} onValidate={()=>editRequest("edit",{condition:edited})} onImport={async file=>{
          const pending=beginEditor();if(!pending) return;
          const captured=pending.status;const ws=pending.socket;
          try {
            const condition=await file.text();const now=current.current;
            if(editorPending.current!==pending || !conditionReadIsCurrent(ws,captured,socket.current,now)) {
              if(editorPending.current===pending)clearEditor();
              setError("条件file読取中に世代が変わりました。改めてimportしてください");return;
            }
            sendEditor(pending,"import",{condition});
          } catch(e) {setError(String(e));if(editorPending.current===pending)clearEditor();}
        }}
        onExport={()=>editRequest("edit",{condition:edited},true)}
        onDiff={()=>editRequest("diff",{condition:edited})}/>
        {changes.length>0 && <ul aria-label="条件差分">{changes.map((d,i)=><li key={i}>{d.path.join(".")}: {JSON.stringify(d.before)} → {JSON.stringify(d.after)}</li>)}</ul>}
        <SceneContactPanel value={state.sceneContactPresentation} live={connected}/><DynamicsPanel value={state.dynamicsPresentation}/>
        <details><summary>入力・qpos診断</summary><GamepadDiagnosticDetails state={state} raw={raw} live={connected&&!!ready&&!frameStale&&status?.phase!=="terminal"}/><pre>{formatInputOverlayText(state.inputOverlay)}</pre><pre>{state.currentQposText}</pre></details>
        <h2>保存結果（最新32件）</h2>{status?.results.map(result=><article key={result.trial_id}>
          <strong>{result.runner_stop_reason}</strong><p>{result.trial_id}</p><p>{result.ticks} ticks / 記録 {result.recording}</p>
        </article>)}<p>結果はresult rootのtrial別記録に保持されます。課題未評価を成功と補完しません。</p></aside>
    </div>
  </main>;
}
