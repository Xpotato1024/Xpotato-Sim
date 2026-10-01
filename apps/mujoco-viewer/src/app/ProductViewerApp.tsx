import { SceneContactPanel } from "./SceneContactPanel.js";
import { DynamicsPanel } from "./DynamicsPanel.js";
import { GamepadPlaneStatus } from "./GamepadPlaneStatus.js";
import { GamepadTriggerStatus } from "./GamepadTriggerStatus.js";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import {
  parseContactTaskLogJsonl,
  unavailableContactTaskPresentation,
} from "../contact/contactTaskLog.js";
import { readViewerEndpointConfig } from "../config/websocketEndpoint.js";
import {
  createViewerKeyboardCapture,
  DEFAULT_VIEWER_KEYBOARD_CAPTURE_KEYS,
} from "../input/keyboardInput.js";
import type { ViewerGamepadLike } from "../input/gamepadInput.js";
import { createViewerInputLifecycle, readViewerInputSelection, readViewerInputStartup } from "./viewerInputLifecycle.js";
import {
  desiredToSiteErrorPresentation,
  endpointDifferencePresentation,
  formatEndpointEvaluationAngles,
  formatEndpointEvaluationScalar,
  formatEndpointEvaluationVector,
} from "../wasm-scene/endpointEvaluationFormat.js";
import {
  createInitialProductViewerState,
  formatInputOverlayText,
  isProductViewerLiveInputEnabled,
  type ProductViewerState,
} from "../wasm-scene/productViewerState.js";
import { createMujocoSceneRenderer } from "../wasm-scene/mujocoSceneRenderer.js";
import { parseTransportPayloadV0Message } from "../transport/parseTransportPayloadV0Message.js";
import { loadDefaultViewerRobotProfile } from "../robot-profiles/registry.js";
import type { ViewerRobotProfile } from "../robot-profiles/types.js";
import { viewerVisualLegend } from "../wasm-scene/visualStyles.js";
import { describeWorkbenchConnection, formatWorkbenchAge } from "./workbenchPresentation.js";
import { createPresentationCadence, presentationCriticalKey } from "./presentationCadence.js";
import { JointInstruments } from "../ui/JointInstruments.js";
import { InputInstruments } from "../ui/InputInstruments.js";
import {InputStrip,GamepadDiagnosticDetails} from "../ui/InputStrip.js";
import {browserGamepadDisplay,type BrowserGamepadDisplay} from "../ui/inputStripPresentation.js";
import {SceneViewport,scenePanes} from "../ui/SceneViewport.js";
import {jointRailGroups} from "../wasm-scene/jointPresentation.js";
import type {ViewLayout} from "../wasm-scene/viewportPresentation.js";
import "./productViewer.css";
import "../ui/operation.css";

function formatNumber(value: number | null): string {
  if (value === null) {
    return "n/a";
  }

  return Number.isInteger(value) ? String(value) : value.toFixed(6);
}

function formatContactVector(value: readonly number[] | null | undefined): string {
  return value === null || value === undefined ? "unavailable" : `[${value.map((item) => item.toFixed(3)).join(", ")}]`;
}

const Legend = memo(function Legend({ profile }: { profile: ViewerRobotProfile | null }) {
  const legendItems = useMemo(
    () => (profile === null ? [] : viewerVisualLegend(profile)),
    [profile],
  );

  return (
    <div className="viewer-legend">
      {legendItems.map((item) => (
        <div className="viewer-legend__item" key={item.label}>
          <span className="viewer-legend__swatch" style={{ background: item.color }} />
          <div className="viewer-legend__text">
            <strong>{item.label}</strong>
            <span>{item.detail}</span>
          </div>
        </div>
      ))}
    </div>
  );
});

function EndpointEvaluationPanel({ state }: { state: ProductViewerState }) {
  const endpointEvaluation = state.endpointEvaluation;
  if (endpointEvaluation === null) {
    return <div className="viewer-endpoint-evaluation__empty">Endpoint evaluation: unavailable</div>;
  }

  const unit = endpointEvaluation.unit ?? "n/a";
  const differenceText = (vector: unknown, norm: unknown, fromFrame: unknown, toFrame: unknown): string => {
    const value = endpointDifferencePresentation(vector, norm, fromFrame, toFrame, endpointEvaluation.frame_mismatch_note);
    return value.comparable && value.valueM !== null && value.vectorM !== null
      ? formatEndpointEvaluationVector(value.vectorM) + " |norm| " + formatEndpointEvaluationScalar(value.valueM) + " " + unit
      : value.reason ?? "unavailable";
  };

  return (
    <dl className="viewer-endpoint-evaluation__kv">
      <div>
        <dt>Desired</dt>
        <dd>{`${formatEndpointEvaluationVector(endpointEvaluation.desired_endpoint_m ?? null)} ${endpointEvaluation.unit ?? "n/a"}`}</dd>
      </div>
      <div>
        <dt>qpos-like joint angles</dt>
        <dd>{`${formatEndpointEvaluationAngles(endpointEvaluation.qpos_like_joint_angles_rad ?? null)} rad`}</dd>
      </div>
      <div>
        <dt>FK</dt>
        <dd>{`${formatEndpointEvaluationVector(endpointEvaluation.fk_endpoint_m ?? null)} ${endpointEvaluation.unit ?? "n/a"}`}</dd>
      </div>
      <div>
        <dt>Site</dt>
        <dd>{`${formatEndpointEvaluationVector(endpointEvaluation.site_endpoint_m ?? null)} ${endpointEvaluation.unit ?? "n/a"}`}</dd>
      </div>
      <div>
        <dt>Desired -&gt; FK error</dt>
        <dd>
          {differenceText(endpointEvaluation.desired_to_fk_error_vector_m, endpointEvaluation.desired_to_fk_error_norm_m,
            endpointEvaluation.desired_endpoint_coordinate_frame, endpointEvaluation.fk_endpoint_coordinate_frame)}
        </dd>
      </div>
      <div>
        <dt>Desired -&gt; site error</dt>
        <dd>
          {differenceText(endpointEvaluation.desired_to_site_error_vector_m, endpointEvaluation.desired_to_site_error_norm_m,
            endpointEvaluation.desired_endpoint_coordinate_frame, endpointEvaluation.site_endpoint_coordinate_frame)}
        </dd>
      </div>
      <div>
        <dt>FK -&gt; site error</dt>
        <dd>
          {differenceText(endpointEvaluation.fk_to_site_error_vector_m, endpointEvaluation.fk_to_site_error_norm_m,
            endpointEvaluation.fk_endpoint_coordinate_frame, endpointEvaluation.site_endpoint_coordinate_frame)}
        </dd>
      </div>
      <div>
        <dt>Frames</dt>
        <dd>
          <div>desired: {endpointEvaluation.desired_endpoint_coordinate_frame ?? "n/a"}</div>
          <div>FK: {endpointEvaluation.fk_endpoint_coordinate_frame ?? "n/a"}</div>
          <div>site: {endpointEvaluation.site_endpoint_coordinate_frame ?? "n/a"}</div>
        </dd>
      </div>
      <div>
        <dt>Note</dt>
        <dd>{endpointEvaluation.frame_mismatch_note ?? "n/a"}</dd>
      </div>
    </dl>
  );
}

function InputOverlayPanel({ state }: { state: ProductViewerState }) {
  return <><pre className="viewer-input-overlay__text">{formatInputOverlayText(state.inputOverlay)}</pre>
    <pre className="viewer-input-overlay__text">{state.inputOverlay?.rawSignal
      ? JSON.stringify(state.inputOverlay.rawSignal, null, 2)
      : "raw input signal: unavailable / invalid"}</pre></>;
}

export function ProductViewerApp() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const interactionRef=useRef<HTMLDivElement|null>(null);
  const [screen,setScreen]=useState<"setup"|"operate">("setup");
  const [layout,setLayout]=useState<ViewLayout>("single");
  const [raw,setRaw]=useState<BrowserGamepadDisplay|null>(null);
  const rawDisplayAt=useRef(0);
  const providerSession=useRef<string|null>(null);
  const diagnosticsRef = useRef<HTMLDetailsElement | null>(null);
  const [inputPaused, setInputPaused] = useState(false);
  const [nowMs, setNowMs] = useState(() => performance.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(performance.now()), 100);
    return () => window.clearInterval(timer);
  }, []);
  const rendererRef = useRef<ReturnType<typeof createMujocoSceneRenderer> | null>(null);
  const keyboardCaptureRef = useRef(
    createViewerKeyboardCapture(
      DEFAULT_VIEWER_KEYBOARD_CAPTURE_KEYS,
      typeof document !== "undefined" && document.hasFocus() ? "focused" : "blurred",
    ),
  );
  const [profile, setProfile] = useState<ViewerRobotProfile | null>(null);
  const [rendererReady, setRendererReady] = useState(false);
  const [state, setState] = useState<ProductViewerState>(() => createInitialProductViewerState());
  const [numbers, setNumbers] = useState<ProductViewerState>(() => createInitialProductViewerState());
  const latestDisplayState = useRef(state);
  const numberCriticalKey = useRef(presentationCriticalKey(state));
  const [diagnosticSnapshot, setDiagnosticSnapshot] = useState<{ state: ProductViewerState; capturedAt: string } | null>(null);
  useEffect(() => {
    latestDisplayState.current = state;
    const key = presentationCriticalKey(state);
    if (key !== numberCriticalKey.current) {
      numberCriticalKey.current = key;
      setNumbers(state);
    }
  }, [state]);
  useEffect(() => {
    const timer = window.setInterval(() => setNumbers(latestDisplayState.current), 250);
    return () => window.clearInterval(timer);
  }, []);
  const endpointConfig = useMemo(() => {
    if (typeof window === "undefined") {
      return { websocketUrl: null as string | null };
    }

    return readViewerEndpointConfig(window.location);
  }, []);
  const launchProfileLabel = useMemo(() => typeof window === "undefined" ? null
    : new URLSearchParams(window.location.search).get("launchProfile"), []);
  const requestedProfileId = useMemo(() => {
    if (typeof window === "undefined") {
      return null;
    }
    return new URLSearchParams(window.location.search).get("robotProfileId");
  }, []);
  const inputSelection = useMemo(() => readViewerInputSelection(
    typeof window === "undefined" ? "" : window.location.search,
  ), []);
  const inputStartup = useMemo(() => readViewerInputStartup(
    typeof window === "undefined" ? "" : window.location.search,
  ), []);
  const initialCameraView =
    inputSelection.providerIds.length === 1 && inputSelection.providerIds[0] === "gamepad/v1"
      ? "operator" as const
      : "iso" as const;
  const gamepadControlPresentation =
    state.inputOverlay?.gamepadTriggerControl ?? state.inputOverlay?.gamepadPlaneControl ?? null;
  const gamepadNeutralHeartbeat = inputStartup === "scene" || gamepadControlPresentation !== null;
  const coordinatedStopped = gamepadControlPresentation?.outputScope === "coordinated"
    && (state.inputOverlay?.motionStatus === "faulted" || state.inputOverlay?.motionStatus === "stopped");
  const liveInputEnabled = !inputPaused && !coordinatedStopped && inputSelection.error === null
    && isProductViewerLiveInputEnabled(state, inputStartup);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null) {
      return;
    }
    let disposed = false;
    const presentation = createPresentationCadence<ProductViewerState>({
      intervalMs: 50, criticalKey: presentationCriticalKey, deliver: setState,
    });
    let renderer: ReturnType<typeof createMujocoSceneRenderer> | null = null;
    const start = async (): Promise<void> => {
      try {
        const initialProfile =
          endpointConfig.websocketUrl === null ? await loadDefaultViewerRobotProfile() : null;
        if (
          initialProfile !== null &&
          requestedProfileId !== null &&
          requestedProfileId !== initialProfile.profileId
        ) {
          throw new Error(`unknown compatibility viewer robot profile ID ${requestedProfileId}`);
        }
        if (disposed) {
          return;
        }
        if (initialProfile !== null) {
          setProfile(initialProfile);
        }
        renderer = createMujocoSceneRenderer({
          canvas,
          interactionElement:interactionRef.current!,
          getScenePanes:()=>scenePanes(canvas),
          profile: initialProfile,
          expectedProfileId: requestedProfileId,
          websocketUrl: endpointConfig.websocketUrl,
          initialCameraView,
          onProfileResolved: setProfile,
          onStateChange: presentation.push,
          onError(error) {
            presentation.discardPending(); // pendingな正常表示でfatal errorを上書きしない。
            if (disposed) return;
            setState((current) => ({
              ...current,
              status: "error",
              qposStatus: "unavailable",
              qposError: error.message,
              statusText: error.message,
            }));
            setRendererReady(false);
          },
        });
        rendererRef.current = renderer;
        await renderer.start();
        if (!disposed) {
          setRendererReady(true);
        }
      } catch (error) {
        presentation.discardPending();
        if (disposed) return;
        const message = error instanceof Error ? error.message : String(error);
        setRendererReady(false);
        setState((current) => ({
          ...current,
          status: "error",
          qposStatus: "unavailable",
          qposError: message,
          statusText: message,
        }));
      }
    };

    void start();
    return () => {
      disposed = true;
      presentation.dispose();
      setRendererReady(false);
      if (rendererRef.current === renderer) {
        rendererRef.current = null;
      }
      renderer?.dispose();
    };
  }, [endpointConfig.websocketUrl, requestedProfileId, initialCameraView]);

  useEffect(() => {
    if (typeof window === "undefined" || typeof document === "undefined") {
      return;
    }

    const inputLifecycle = createViewerInputLifecycle({
      providerIds: inputSelection.providerIds,
      gamepadNeutralHeartbeat,
      window,
      document,
      url: endpointConfig.websocketUrl,
      keyboardCapture: keyboardCaptureRef.current,
      onGamepadSession:session=>{providerSession.current=session;if(session===null)setRaw(null);},
      getGamepads: () => {
        if (typeof navigator === "undefined" || typeof navigator.getGamepads !== "function") {
          return null;
        }

        const pads=navigator.getGamepads();
        const now=performance.now();
        if(now-rawDisplayAt.current>=50){rawDisplayAt.current=now;const observed=browserGamepadDisplay(pads,now);setRaw(observed?{...observed,sessionId:providerSession.current}:null);}
        return pads as unknown as ArrayLike<ViewerGamepadLike | null | undefined>;
      },
    });
    inputLifecycle.setLiveInputEnabled(liveInputEnabled);
    return () => {inputLifecycle.dispose();setRaw(null);};
  }, [endpointConfig.websocketUrl, liveInputEnabled, inputSelection, gamepadNeutralHeartbeat]);

  const onContactTaskLogChange = async (event: ChangeEvent<HTMLInputElement>): Promise<void> => {
    setDiagnosticSnapshot(null);
    const input = event.currentTarget;
    const file = input.files?.[0];
    input.value = "";
    if (file === undefined) {
      return;
    }
    if (profile === null) {
      rendererRef.current?.setContactTaskPresentation(
        unavailableContactTaskPresentation("robot profile の読み込み前はcontact-task-log/v1を検証できません"),
        "offline_log",
      );
      return;
    }
    try {
      const text = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
      const presentation = await parseContactTaskLogJsonl(text, {
        profileId: profile.profileId,
        profileContractVersion: profile.profileContractVersion,
      });
      rendererRef.current?.setContactTaskPresentation(presentation, "offline_log");
    } catch (error) {
      rendererRef.current?.setContactTaskPresentation(
        unavailableContactTaskPresentation(
          error instanceof Error ? error.message : "contact-task-log/v1 JSONLを読み込めませんでした",
        ),
        "offline_log",
      );
    }
  };

  const onTransportPayloadFileChange = async (
    event: ChangeEvent<HTMLInputElement>,
  ): Promise<void> => {
    setDiagnosticSnapshot(null);
    const input = event.currentTarget;
    const file = input.files?.[0];
    input.value = "";
    if (file === undefined) {
      return;
    }
    const renderer = rendererRef.current;
    if (profile === null || renderer === null || !rendererReady) {
      setState((current) => ({
        ...current,
        contactTaskInputSource: "offline_payload",
        contactTaskPresentation: unavailableContactTaskPresentation(
          "MuJoCo viewer の初期化後にtransport payload-v0 JSONを読み込んでください",
        ),
      }));
      return;
    }
    try {
      const text = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
      renderer.applyOfflinePayload(parseTransportPayloadV0Message(text));
    } catch (error) {
      renderer.setContactTaskPresentation(
        unavailableContactTaskPresentation(
          error instanceof Error ? error.message : "transport payload-v0 JSONを読み込めませんでした",
        ),
        "offline_payload",
      );
    }
  };
  const clearContactTaskPresentation = (): void => {
    rendererRef.current?.setContactTaskPresentation(
      unavailableContactTaskPresentation("offline contact task log の表示を消去しました"),
      "none",
    );
  };

  const connection = describeWorkbenchConnection(state, Math.max(nowMs, performance.now()));
  const overlay = state.inputOverlay;
  const inputLabel = inputSelection.providerIds.length === 0 ? "入力なし" : inputSelection.providerIds.join(" / ");
  const endpointError = desiredToSiteErrorPresentation(numbers.endpointEvaluation);
  const diagnosticState = diagnosticSnapshot?.state ?? state;
  const groups=jointRailGroups(state.jointLayout,state.modelContractVersion);
  const left=groups.find(g=>g.id!=="right"),right=groups.find(g=>g.id==="right");
  const openDiagnostics = (): void => {
    setScreen("setup");
    const element = diagnosticsRef.current;
    if (element !== null) {
      element.open = !element.open;
      if (element.open) element.scrollIntoView({ block: "start" });
    }
  };

  return (
    <main className="viewer-shell operation-shell product-operation" data-screen={screen}>
      <header className="workbench-header">
        <div className="workbench-brand"><span className="brand-mark" aria-hidden="true">X</span><h1>Xpotato-Sim</h1><span className="brand-section">WORKBENCH</span></div>
        <div className="workbench-identity">{state.robotProfileId ?? "model loading"}<span title="起動URLの表示名。backendのidentityは受信データで検証します。">{launchProfileLabel ?? "MuJoCo viewer"}</span></div>
        <nav className="workbench-actions" aria-label="表示と入力">
          <button type="button" onClick={() => setInputPaused((paused) => !paused)}
            disabled={inputSelection.providerIds.length === 0 || state.connectionStatus !== "open"}
            aria-pressed={inputPaused} data-testid="input-pause">
            {inputPaused ? "入力取得を再開" : "入力取得を停止"}
          </button>
          <button type="button" onClick={openDiagnostics}>詳細診断</button>
          <button onClick={()=>setScreen("setup")} aria-pressed={screen==="setup"}>設定・診断</button>
          <button onClick={()=>setScreen("operate")} disabled={!rendererReady} aria-pressed={screen==="operate"}>操作画面へ</button>
          <button onClick={()=>setLayout("single")} aria-pressed={layout==="single"}>Single</button>
          <button onClick={()=>setLayout("assist")} aria-pressed={layout==="assist"}>Assist</button>
        </nav>
      </header>
      <div className="workbench-statusbar">
        <span className={`connection-indicator tone-${connection.tone}`} role="status" data-testid="connection-label">
          <i aria-hidden="true" />{connection.label}
        </span>
        <span className="status-age">{formatWorkbenchAge(connection.ageMs)}</span>
        <span className="status-divider" />
        <span>{state.qposStatus === "ready" ? "描画準備済み" : "描画: " + state.qposStatus}</span>
        <span className="status-divider" />
        <span className="input-summary">{inputLabel} · {liveInputEnabled ? "取得有効" : "取得停止"}</span>
        <span className="status-disclaimer">実機安全性は未判定</span>
      </div>
      {inputSelection.error === null ? null : <p className="workbench-alert" role="alert">{inputSelection.error}</p>}
      {state.qposError === null ? null : <p className="workbench-alert" role="alert">{state.qposError}</p>}
      <div className="workbench-main product-operation-body">
        <div className="operation-workspace" data-left={!!left} data-right={!!right}>
          {left&&<aside className="joint-rail joint-rail--left"><h2>{left.label}</h2><JointInstruments state={state} names={left.names} unavailable={connection.tone==="warning"||connection.tone==="danger"?connection.label:undefined}/></aside>}
        <section className="workbench-scene" aria-label="3Dロボット表示">
          <div className="scene-toolbar">
            <span className="section-kicker">SCENE</span>
            <span className="scene-source">{state.sourceLabel}</span>
            <div className="camera-actions" aria-label="カメラ方向">
              {([['operator', '操作'], ['iso', '斜め'], ['front', 'XZ面'], ['side', 'YZ面'], ['top', 'XY面'], ['fit', '全体']] as const).map(([view, label]) => (
                <button key={view} type="button" disabled={state.qposStatus !== "ready"}
                  onClick={() => rendererRef.current?.setCameraView(view)}>{label}</button>
              ))}
            </div>
          </div>
          <SceneViewport canvas={canvasRef} interaction={interactionRef} renderer={rendererRef} layout={layout}/>
          <div className="scene-timeline"><span>SIMULATION TIME</span><strong>{state.currentTimestampS === null ? "—" : state.currentTimestampS.toFixed(2) + " s"}</strong><span>FRAME</span><strong>{state.currentFrameIndex ?? "—"}</strong><span className="timeline-note">表示値は実機計測ではありません</span></div>
        </section>
          {right&&<aside className="joint-rail joint-rail--right"><h2>{right.label}</h2><JointInstruments state={state} names={right.names} unavailable={connection.tone==="warning"||connection.tone==="danger"?connection.label:undefined}/></aside>}
          <InputStrip state={state} raw={raw} live={state.connectionStatus==="disabled"||connection.tone==="positive"}/>
        </div>
        <aside className="workbench-inspector" aria-label="状態の概要" hidden={screen!=="setup"}>
          <section className="inspector-section">
            <div className="inspector-heading"><h2>入力</h2><span className="section-kicker">INPUT</span></div>
            <p className="inspector-primary">{overlay?.sourceKind ?? "入力情報なし"}</p>
            <InputInstruments state={state} numbers={numbers} />
            <GamepadTriggerStatus value={overlay?.gamepadTriggerControl ?? null} live={liveInputEnabled && connection.tone === "positive"} motionStatus={overlay?.motionStatus} />
            <GamepadPlaneStatus value={overlay?.gamepadPlaneControl ?? null} live={liveInputEnabled && connection.tone === "positive"} motionStatus={overlay?.motionStatus} />
            <div className="inspector-row"><span>取得</span><strong>{inputPaused ? "一時停止" : liveInputEnabled ? "有効" : "停止"}</strong></div>
            <div className="inspector-row"><span>backend状態</span><strong>{overlay === null ? "未取得" : overlay.sourceActive ? "入力あり" : "待機 / 保持"}</strong></div>
            <div className="inspector-row"><span>入力age</span><strong>{overlay?.commandAgeMs === null || overlay?.commandAgeMs === undefined ? "—" : overlay.commandAgeMs + " ms"}</strong></div>
            <p className="inspector-note">{overlay?.staleReason ?? "入力の解釈と安全判定はbackendが管理します。"}</p>
            <p className="inspector-note">入力取得停止は実機の非常停止ではありません。</p>
          </section>
          <section className="inspector-section">
            <div className="inspector-heading"><h2>手先</h2><span className="section-kicker">ENDPOINT</span></div>
            <div className="endpoint-readout"><strong>{endpointError.valueM === null ? "—" : (endpointError.valueM * 1000).toFixed(1)}</strong><span>mm</span></div>
            <p className="inspector-note">{endpointError.reason ?? "目標 → MuJoCo site の位置誤差"}</p>
            <div className="inspector-row"><span>motion</span><strong>{overlay?.motionStatus ?? "未取得"}</strong></div>
            {overlay?.motionRejectionReason && <p className="inspector-note tone-warning">{overlay.motionRejectionReason}</p>}
          </section>
          {state.sceneContactPresentation.status === "absent" ? <section className="inspector-section">
            <div className="inspector-heading"><h2>タスク</h2><span className="section-kicker">CONTACT</span></div>
            <p className="inspector-primary">{state.contactTaskPresentation.taskState?.phase ?? "接触情報なし"}</p>
            {state.contactTaskPresentation.taskState !== null ? <p className="inspector-note">{state.contactTaskPresentation.taskState.classification}</p> : <p className="inspector-note">接触log / payloadの読込みと証拠の詳細は診断にあります。</p>}
          </section> : null}
          <SceneContactPanel value={state.sceneContactPresentation} live={connection.tone === "positive"} />
          <DynamicsPanel value={state.dynamicsPresentation} />
          <div className="inspector-footer">{connection.detail}</div>
        </aside>
      </div>
      <details className="workbench-diagnostics" ref={diagnosticsRef} hidden={screen!=="setup"}>
        <summary>詳細診断<span>model / payload / contact / input</span></summary>
        <div className="diagnostics-body"><GamepadDiagnosticDetails state={state} raw={raw} live={state.connectionStatus==="disabled"||connection.tone==="positive"}/>
          <div className="diagnostic-snapshot-bar">
            <button type="button" data-testid="diagnostic-snapshot" aria-pressed={diagnosticSnapshot !== null}
              onClick={() => setDiagnosticSnapshot(diagnosticSnapshot === null
                ? { state: structuredClone(state), capturedAt: new Date().toISOString() } : null)}>
              {diagnosticSnapshot === null ? "診断値を固定" : "live診断へ戻る"}
            </button>
            <span>{diagnosticSnapshot === null ? "詳細診断は更新中" : `固定: ${diagnosticSnapshot.capturedAt} / frame ${diagnosticSnapshot.state.currentFrameIndex ?? '—'}`}</span>
            <span>固定はこの診断欄だけです。3D・入力・接続状態は停止しません。</span>
          </div>
          <dl className="model-diagnostics">
            <div><dt>モデル</dt><dd>{diagnosticState.modelPath}</dd></div>
            <div><dt>fixture参照</dt><dd>{diagnosticState.fixturePath}</dd></div>
            <div><dt>nq / nv / ngeom / nmesh</dt><dd>{[diagnosticState.modelNq, diagnosticState.modelNv, diagnosticState.modelNgeom, diagnosticState.modelNmesh].map(formatNumber).join(" / ")}</dd></div>
          </dl>
        <section className="viewer-panel viewer-panel--status">
          <div className="viewer-status__header">
            <h2>Status</h2>
            <div className="viewer-subtle">qpos path is render-only; diagnostics are read-only</div>
          </div>
        <pre className="viewer-status">{diagnosticState.statusText}</pre>
        {diagnosticState.qposError === null ? null : <div className="viewer-error">{diagnosticState.qposError}</div>}
        <div className="viewer-endpoint-evaluation">
          <div className="viewer-endpoint-evaluation__header">
            <h3>Endpoint evaluation</h3>
            <div className="viewer-subtle">read-only diagnostic overlay</div>
          </div>
          <EndpointEvaluationPanel state={diagnosticState} />
        </div>
        <div className="viewer-contact-task">
          <div className="viewer-contact-task__header">
            <h3>接触証拠と仮想反力</h3>
            <div className={`viewer-subtle viewer-contact-task__status viewer-contact-task__status--${diagnosticState.contactTaskPresentation.status}`}>
              {diagnosticState.contactTaskPresentation.status}
            </div>
          </div>
          <div className="viewer-contact-task__controls">
            <label>
              contact-task-log/v1 JSONL を読み込む
              <input
                data-testid="contact-task-log-input"
                type="file"
                accept=".jsonl,application/x-ndjson,application/json"
                disabled={profile === null || !rendererReady}
                onChange={(event) => void onContactTaskLogChange(event)}
              />
            </label>
            <label>
              transport payload-v0 JSON を読み込む
              <input
                data-testid="transport-payload-v0-input"
                type="file"
                accept=".json,application/json"
                disabled={profile === null || !rendererReady}
                onChange={(event) => void onTransportPayloadFileChange(event)}
              />
            </label>
            <button type="button" onClick={clearContactTaskPresentation}>
              接触表示を消去
            </button>
          </div>
          {diagnosticState.contactTaskPresentation.reason === null ? null : (
            <p className="viewer-contact-task__reason">{diagnosticState.contactTaskPresentation.reason}</p>
          )}
          {diagnosticState.contactTaskPresentation.evidenceNotice === null ? null : (
            <p className="viewer-contact-task__notice">{diagnosticState.contactTaskPresentation.evidenceNotice}</p>
          )}
          {diagnosticState.contactTaskInputSource === "offline_log" ? (
            <p className="viewer-contact-task__notice">
              offline_log の contact sample と表示中の robot qpos は別入力です。contact-task-log/v1 に robot qpos は含まれず、同一時刻の姿勢と接触の同期を保証しません。同期した表示には、同じ payload-v0 の qpos と metadata.contact_task_v1 を使用してください。
            </p>
          ) : null}
          <dl className="viewer-contact-task__kv">
            <div>
              <dt>入力元</dt>
              <dd>{diagnosticState.contactTaskInputSource}</dd>
            </div>
            <div>
              <dt>証拠の種別</dt>
              <dd>{diagnosticState.contactTaskPresentation.sourceKind ?? "unavailable"}</dd>
            </div>
            <div>
              <dt>シーン / object identity</dt>
              <dd>
                {diagnosticState.contactTaskPresentation.binding === null
                  ? "unavailable"
                  : `${diagnosticState.contactTaskPresentation.binding.scene_identity.name}/v${diagnosticState.contactTaskPresentation.binding.scene_identity.version} / ${diagnosticState.contactTaskPresentation.binding.object_identity.name}/v${diagnosticState.contactTaskPresentation.binding.object_identity.version}`}
              </dd>
            </div>
            <div>
              <dt>試行 (trial)</dt>
              <dd>{diagnosticState.contactTaskPresentation.binding?.trial.trial_id ?? "unavailable"}</dd>
            </div>
            <div>
              <dt>サンプル時刻 / frame</dt>
              <dd>
                {diagnosticState.contactTaskPresentation.sample === null
                  ? "unavailable"
                  : `${diagnosticState.contactTaskPresentation.sample.simulationTimeS.toFixed(3)} s / ${diagnosticState.contactTaskPresentation.sample.frameIndex ?? "n/a"}`}
              </dd>
            </div>
            <div>
              <dt>payloadとの経過時間 / max age</dt>
              <dd>
                {diagnosticState.contactTaskInputSource === "offline_log"
                  ? "offline_log では算出対象外"
                  : diagnosticState.contactTaskPresentation.payloadAgeS === null ||
                      diagnosticState.contactTaskPresentation.maxAgeS === null
                    ? "unavailable"
                    : diagnosticState.contactTaskPresentation.payloadAgeS.toFixed(3) +
                      " / " +
                      diagnosticState.contactTaskPresentation.maxAgeS.toFixed(3) +
                      " s"}
              </dd>
            </div>
            <div>
              <dt>立方体の位置 / half-size</dt>
              <dd>
                {diagnosticState.contactTaskPresentation.cube === null
                  ? "unavailable"
                  : `${formatContactVector(diagnosticState.contactTaskPresentation.cube.positionWorldM)} / ${formatContactVector(diagnosticState.contactTaskPresentation.cube.halfSizeM)} m`}
              </dd>
            </div>
            <div>
              <dt>生の接触証拠 (raw contact evidence)</dt>
              <dd>
                {diagnosticState.contactTaskPresentation.rawEvidence === null
                  ? "unavailable"
                  : `${diagnosticState.contactTaskPresentation.rawEvidence.status}; contacts ${diagnosticState.contactTaskPresentation.rawEvidence.contactCount ?? "n/a"}; force ${formatContactVector(diagnosticState.contactTaskPresentation.rawEvidence.forceWorldN)} N`}
              </dd>
            </div>
            <div>
              <dt>導出反力 (derived reaction force)</dt>
              <dd>
                {diagnosticState.contactTaskPresentation.derivedForce === null
                  ? "unavailable"
                  : `${diagnosticState.contactTaskPresentation.derivedForce.status}; ${diagnosticState.contactTaskPresentation.derivedForce.frame}; ${formatContactVector(diagnosticState.contactTaskPresentation.derivedForce.forceN)} N`}
              </dd>
            </div>
            <div>
              <dt>対象 task の状態</dt>
              <dd>
                {diagnosticState.contactTaskPresentation.taskState === null
                  ? "unavailable"
                  : `${diagnosticState.contactTaskPresentation.taskState.phase} / ${diagnosticState.contactTaskPresentation.taskState.classification}`}
              </dd>
            </div>
            <div>
              <dt>生の接触証拠に基づく判定 (raw-evidence outcome)</dt>
              <dd>
                {diagnosticState.contactTaskPresentation.outcome === null
                  ? "unavailable"
                  : `${diagnosticState.contactTaskPresentation.outcome.phase} / ${diagnosticState.contactTaskPresentation.outcome.classification}`}
              </dd>
            </div>
            <div>
              <dt>接触点 / 法線 (world frame)</dt>
              <dd>
                {diagnosticState.contactTaskPresentation.contacts.length === 0
                  ? "none"
                  : diagnosticState.contactTaskPresentation.contacts.map((contact) =>
                      `${contact.contactIdentity}: point ${formatContactVector(contact.pointWorldM)} m, normal ${formatContactVector(contact.normalWorld)}`,
                    ).join("; ")}
              </dd>
            </div>
          </dl>
          <div className="viewer-subtle">力の矢印は表示長を対数スケールかつ上限付きで描画し、表示する N 値は変換しません。導出反力が tool frame の場合は数値だけを示し、world frame の矢印として描画しません。</div>
        </div>
        <div className="viewer-input-overlay">
          <div className="viewer-input-overlay__header">
            <h3>Input overlay</h3>
            <div className="viewer-subtle">source, keys, axes, buttons, age, stale state</div>
          </div>
          <InputOverlayPanel state={diagnosticState} />
        </div>
      </section>
          <Legend profile={profile} />
        </div>
      </details>
    </main>
  );
}
