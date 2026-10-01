---
status: canonical
owner: architecture
last_verified: 2026-09-21
canonical_for:
  - product viewer wasm scene renderer operation
related:
  - docs/archive/design/mujoco-wasm-scene-renderer-design.md
  - docs/archive/research/mujoco-webviewer-options.md
  - docs/archive/operations/wasm-qpos-sync-poc.md
---

# product viewer WASM scene renderer

`apps/mujoco-viewer`は`@mujoco/mujoco` WASM scene rendererの現在のproduction ownerである。
renderer、tests、fixture、operator pathはproduct viewer側に一本化する。

## boundary

- Python native MuJoCo backend / IK / FK / runtime が source of truth
- Browser WASM MuJoCo は visual renderer only
- browser 側で IK / FK / qpos recompute はしない
- browser 側で qpos correction はしない
- qpos は runtime payload を優先し、未接続時はpluginが宣言したMuJoCo named home keyframeをstartup poseとして使う

## product viewer entrypoint

- `apps/mujoco-viewer/src/main.tsx`
- default renderer mode: `wasm-scene`
- WebSocket接続時のmodel path: 最初のprofile-aware payloadのURL + digestから解決するversioned viewer declaration
- 未接続時のcompatibility path: plugin-owned
  `/mujoco/fast_arm/viewer-profile.json`をlegacy static facade経由でloadする

## WebSocket connectionとlive inputのlifecycle

- `connectionStatus`の所有者はWebSocket lifecycleである。開始前は`connecting`、`onOpen`は`open`、
  `onClose`は`closed`、`onConnectionError`は`error`、WebSocket無効時は`disabled`を設定する。
- declaration fetch、digest validation、model / VFS fetch、MuJoCo compile、scene構築はrendererの
  `status`（`loading` / `ready` / `error`など）だけを更新し、既に`open`になったconnectionを
  `connecting`へ戻さない。
- payload-first bootstrapは`open -> first profile-aware payload -> declaration / model bootstrap -> ready`
  の順で進み、bootstrap中もconnectionを`open`として保持する。
- live input availabilityは`connectionStatus === "open" && status !== "error"`から導出する。
  したがって`open/loading`、`open/ready`、`open/warning`はactive、`open/error`はfail-safeでinactive、
  closed / connection error / disabledもinactiveである。
- ProductViewerAppのinput effectはraw renderer statusではなく、この安定したavailability booleanだけを
  依存する。loading -> ready / warningではsenderとpollingを再生成せず、renderer errorになった時点で
  keyboard sender、gamepad sender、keyboard RAF、gamepad RAF / heartbeatをdisposeする。
- declaration fetch、digest validation、model / VFS fetch、MuJoCo compile、scene構築のfailureは共通して
  renderer `status=error`へ到達し、connectionStatusが`open`のままでもlive inputをfail-safe停止する。
- keyboardのblur / hidden safety、gamepadのfocus / visibility safety、cadence、deadzoneはこの
  connection lifecycleによって変更しない。

## 接触task logとpayloadのoffline表示

- `contact-task-log/v1` JSONLをlocal fileとして読み込める。readerはschema、canonical JSONL、manifest / signal digest、scene / object / presentation、trial、profile bindingを厳密に検証し、contact point / normal、cube pose、raw world force、derived signal、Task state、raw-evidence outcomeを表示する。
- viewerはcontact physics、force transform / filter、Task outcomeを再計算しない。3D overlayはbackend presentation projectionから作り、derived force arrowはframeが`mujoco_world`と明示された場合だけ描く。
- offline contact logにはrobot qposが含まれない。別途表示中のposeと時刻同期が保証されないため、UIはlog-only入力をその旨明示する。決定的synthetic fixtureはMuJoCo実測として表示しない。
- transport payload-v0 JSONをlocal fileとして読み込む入口では、既存のprofile / qpos validatorを通し、同じsnapshot由来の`qpos`とoptional `metadata.contact_task_v1`を表示する。contact metadataがmissing / malformed / stale / profile mismatchなら接触force overlayを消去し、残留させない。
- ContactScene全体の`qpos`にobject freejointが含まれる場合、producerは別optional `metadata.contact_scene_robot_qpos_v1` (`contact-scene-robot-qpos/v1`)で、同一scene / manifest / frame / timeのfull-scene qposからloaded Robot profileのcanonical joint addressを示す。viewerはこのmappingを同sampleの`contact_task_v1`とprofile / model identityへ照合し、妥当な場合だけ対応joint値を適用する。mappingまたはbindingが欠落・不正・stale・replayed・mismatchedならsceneを`ready`にせず、前回sceneを新sampleとして残さない。extensionがないpayloadは従来どおりqpos lengthとmodel dimensionのexact matchを要求する。
- live payloadでもcontact projectionはread-only display用である。invalid contact metadataはcontact sectionをunavailableにし、qpos / sceneの別validationを迂回しない。どの入口もnetwork、serial、OSC、device、robotへのforce / command出力を追加しない。

## startup pose source

- `home` keyframe: canonical fast_arm startup qpos。pre-payload表示はMJCFからこのqposを読む
- compiled MuJoCo model default qpos: historical fallbackではなく、startup sourceには使わない
- fixture qpos: default startup path では使わない
- runtime qpos: WebSocket payload が来たら `data.qpos` に適用する

## viewer declaration startup

- generic viewer sourceはproduction robot ID registryを持たない。
- WebSocket接続時は最初のframeにあるdeclaration resource path、deterministic public URL、SHA-256 digestを
  検証してfull declarationを一度だけfetch / strict decodeする。model、fixture、VFSのURL / resource対応、
  joint order、qpos dimension、keyframe、model contractを描画前に検証する。
- steady-state frameは同じcompact referenceだけを比較し、full declarationを再送・再decodeしない。
  reconnectでは再fetchし、session中のdigest / URL / resource path変更を拒否する。
- payload compatibility metadataとdeclarationが一致した場合だけmodelをloadし、qposを適用する。
- `fastArm.ts`は未接続時の既存表示を維持するcompatibility facadeであり、宣言内容を再定義せず
  plugin-owned JSONをloadする。新robot onboardingでこのfacadeまたはTypeScript registryを編集しない。
- viewerはdeclarationからrendering resourceを構成するだけで、IK、FK、planning、qpos生成、安全判定を
  行わない。

## canonical qpos fixture

- owner: fast_arm Robot Plugin resource
- path: `assets/mujoco/fast_arm/fixtures/fast_arm_sweep_x_qpos.json`
- schema owner: `apps/mujoco-viewer/src/wasm-scene/qposFrameTypes.ts`
- 再生成: `uv run python scripts/viewer/export_wasm_qpos_fixture.py --preset sweep_x --steps 30`
- fixture playbackはdebug/validation専用であり、startupはnamed `home` keyframeを使う

## fixture生成のintegrity

fixture再生成はstale velocity、BADQACC、time rollback、non-finite qpos、dimension不一致をrejectする。
exporterはin-memory sequence全体をvalidateし、serialization成功後だけtargetをatomicに置換する。
canonical fixtureはstrictly increasing simulation time、finiteな4-value qpos、move / return progression、
intentional terminal holdを持つ30 framesである。current SHA-256は
`4925D77535A67ED0E4EB68BDCC0B66C262D2D11AE5E1F7DCA99C3AE5E38D312A`である。

## 旧rendererの扱い

- decision: deleted
- default production routeは旧Three.js hand-built renderer stackをimportしない
- code bloatを避けるため旧viewer-specific renderer / runtime / view model / testsを削除した

## 実行

通常はrepository rootから`uv run xpotato-sim app --profile sim-keyboard`を使う。
Webとbackendを起動し、接続先と入力providerを指定したURLを一度だけ開く。
起動・終了・障害時の正本は`backend-viewer-startup.md`である。以下はWeb単体の開発手順。

```powershell
cd apps\mujoco-viewer
npm ci
npm run dev -- --host 127.0.0.1 --port 5173
```

Vite dev server は起動後にブラウザを自動で開き、`/apps/mujoco-viewer/` を表示する。
実際の port は Vite の表示に従う。`5175` は手元環境での一例。
ポートが使用中なら Vite が次の空きポートを選ぶ。

## validation

```powershell
cd apps\mujoco-viewer
npm run typecheck
npm test
npm run build
```

```powershell
cd <repository root>
git diff --check
```

## browser smoke

- viewerをloadできる
- WASMをloadできる
- fast_arm sceneをloadできる
- initial pose sourceが明示される
- qpos sync pathが動作するか、qpos unavailableを明示する
- floor / axes / legend / colorを表示する
- 旧rendererがdefault production pathにない

## 既知の制限

- fixture qpos は debug 用の参照としてのみ扱い、startup では自動適用しない
- live WebSocket qpos availability depends on publisher payloads
- browser-side payload correction is intentionally absent

## 操作画面と状態表示

画面は設定・診断（Setup）と操作（Operate）を分離する。同じapp/rendererを保ち、ローカル表示だけ切り替える。
Operateは状態、少数操作、関節rail、scene群、横長Input stripを表示する。モデルpath、内部統計、
全結果、raw診断、contact log/payloadの読込みはSetupに保持する。狭幅では区域scrollを使い、
Workbenchの停止要求を固定操作帯の右端に残す。多関節railだけ独立scrollできる。

`ready`は描画準備であり、接続や実機安全性の証拠ではない。接続は未指定/接続中/受信待ち/受信中/
更新停止/配信終了/エラーを区別する。受信ageはbrowserのmonotonic receipt時刻から表示し、
1000 ms超を表示上の更新停止とする。この閾値はbackendの入力staleやphysical safety gateではない。
閉じた接続では最終受信値を表示中と明記する。invalid payloadをfresh/安全へ変換しない。

カメラのpresetは既存の方向を維持する。初期表示と「フォーカス」は、native変換済みmeshの境界を
画角とpane実寸に収める。受信qposを編集・再計算せず、表示marginを可動域や安全clearanceと扱わない。
canvas実寸とResizeObserverでaspectを追従する。狭幅では計器workspaceの可読最低幅を保って区域scrollとし、canvasだけを歪めない。
「フォーカス」はbodyに属するRobot/物体へ寄せ、「全体」はworld直下の有限な支持台等も含める。
無限平面・座標軸・接触矢印はframing境界へ入れない。両操作は現在の観察方向を保持し、
「操作視点」はoperator方向へ戻してフォーカスする。カメラの初期化・明示操作以外で移動物体へ自動追従しない。
対象が移動して外へ出た場合は明示的にフォーカスし直す。補助二面はfit時の境界を保持し、resize時に
XY/YZの縦横比を満たす共通のY縮尺で表示する。床や支持台を描画から消す操作ではない。

「入力取得を停止」はbrowser providerをdisposeする。これはlocalな取得停止であり、実機非常停止、
backend session停止、即時physical stopの通知ではない。backend側は既存のneutral/stale契約に従う。
再開は既存のconnection/error gateを満たす場合だけ可能で、終了済みの接続では入力を再開しない。
input/textarea/select/contenteditableやUIボタンでのキーはrobot入力へ渡さず、編集中に保持キーを解放する。
blur/非表示/disposeの既存契約を維持し、dispose後に追加publishしない。

概要に現れないcontact evidenceや各種provenanceは削除せず、詳細診断で従来どおり検証・表示する。
状態表示とカメラ、編集field隔離は`workbenchPresentation.test.ts`で検証する。

## 関節・入力計器と診断固定

関節欄はloaded MuJoCo modelのjoint name/type/`jnt_qposadr`を検証し、hingeを角度指標へ投影する。
針は0を12時、+90を右、-90を左、±180を下とする。0から針まで中心を含む扇形を面塗りし、
正は時計回り赤、負は反時計回り青。色は正負であり安全帯ではない。目盛りは30度刻み、0/±90/±180を強調する。
q1..qNと符号付き整数degreeを表示し、非ゼロの絶対値1未満は+<1°/-<1°、0/-0は0°。
針・扇形・数値は同一sampleの丸め前qposを使う。±180超は元数値と「表示範囲外」を残し、通常針・塗りを消す。
modulo/clamp、home引算、右腕符号反転、予測・平滑化・角度アニメーションを行わない。
slideはmeter単位の並進座標、ball/freeは複数座標として角度計非対応を示す。
欠測・次元不一致・非finite値は針を消し、`—`を表示する。raw qposは詳細診断で確認できる。
この指標は可動域meterではなく、モデルlimitや実機safe zoneを捏造しない。表示値の出所も併記する。

共通Input stripはsource/availability/frame/ageを判定した後、静的なsource rendererへ投影する。
source不一致、stale/disconnectedでは以前の計器を消し、neutralへ置換しない。unknown/replay/fixtureはgeneric表示。
既存sourceは中立にも`gamepad_inactive`/`keyboard_inactive`を付ける。この理由だけで値を隠さず、
Gamepadはconnected=true・stale=false・zero_state=true、Keyboardはfocused・zero_state=true、
かつsource_active=falseが揃う場合だけ、取得済みの中立sampleを表示する。timeout、不正、切断、
blurredや根拠欠測はこの例外に含めない。backendのhealth分類や入力の有効化を変更するものではない。
Gamepadの操作帯v1.1は **左Z / 左XY / 右XY / 右Z** の4計器だけを中央揃えする。
XYは取得座標の2D表示、Zは中央0・正が上・負が下の縦バー（表示範囲-1..1）である。
Zは同一backend sampleの`trigger_value × z_sign`を表示する。これは確定符号付き入力量であり、
deadzone後の速度や接触力ではない。browserの最新triggerと古いbackend符号を掛け合わせず、
bumper押下から符号を再計算しない。reasonなし・armed・対象sideへの割当が確認できる場合だけ表示し、
未割当・確定待ち・不正・失効では0を補完せず`—`とする。中立の取得値0は中央として示す。
ボタン押下、LT/RT・LB/RBのraw値、index、session/sequence、endpoint割当の詳細はSetupの診断へ退避する。
既存pollerのbrowser sampleからmapping="standard"・shape・値・provider sessionを確認した場合のみ
左右stickの物理名称を使う。不明・非standardは元のA0/A1等のindexを示し、Zを推測しない。
生入力とbackend確定値の出所は区別し、表示部品に新しい取得・heartbeat・送信・Mappingは追加しない。
Gamepadは取得されたaxesの順序を保ち、
不正な軸をfilterして番号を詰め直さず、配列全体を計器表示から除外する。不正buttonを未押下へ変換しない。
Selfrionetteは`input_signal_v1`を検証して生の7chを示す。単位・指との対応・力への換算は未校正と明記する。
バーは同一sample内の相対比で、表示scaleを併記する。異なる時刻の絶対振幅比較は生値で行う。
Keyboardはbackendが記録した押下キーとfocus状態を示す。staleの表示は既存backend診断から導出する。
key state未取得を未押下へ補完しない。SelfrionetteはCH1..CH7のraw unit・未校正、同一sample内の相対比を明記し、
指名・力N・左右bindingを推測しない。Input stripはv1.1でボタン列と常設の詳細凡例を退避し、全source共通100pxを使う。
raw signalのsource、sample schema、source時刻、値は詳細診断へ残す。wire仕様の正本は
[transport payload契約](../contracts/transport-payload.md)である。

React計器表示は50 ms（最大20 Hz）、通常の数値ラベルは250 ms（4 Hz）を目安にcoalesceする。
接続、error、stale、hold/reject、欠測、sourceやbutton/keyboard状態の変化は待たずに更新する。
3D適用、入力取得、安全判定、backend周期、実験記録はこの表示間引きへ接続しない。
これはbrowser schedulerの目安でありhard real-time保証ではない。fatal error/dispose時はpending更新を破棄する。

「診断値を固定」はその時点の詳細診断だけをコピーし、取得日時とframeを表示する。
固定中も3D、接続表示、入力取得は継続する。解除は「live診断へ戻る」を使う。
固定操作はsession停止や物理停止ではない。表示revisionは参加者実験の比較条件として固定する。

詳細診断も軸配列の不正要素を除去してindexを詰め直さない。不正・未取得のaxesは
`unavailable / invalid`と表示し、正常な数値列は元の順序を保持する。
buttonの押下状態が不明なら`invalid`とし、`released`へ補完しない。
この変更は表示内部の状態型だけに適用し、入力wire形式やbackendへの操作値を変えない。


## 初回接触時の描画準備と集計コスト

scene準備では、Robot/物体meshと接触overlayのshaderをcompileAsyncで準備してからqpos readyを通知する。
overlayはrenderer開始前のcompile呼出し中だけ一時的にvisibleとし、完了後はhiddenへ戻すため描画frameや偽の接触recordを生成しない。scene-startupの入力開始を描画準備完了後へ保ち、
初回接触のshader compileが進行中のheartbeatを止めることを避ける。入力expiry/安全判定は緩めない。

計時統計は標本が変わったときだけ一度sortし、同じpercentileの反復計算を省く。512件の保持上限、
p50/p95の定義、状態/不正frameの即時反映は変更しない。関節計の目盛りと不変legendはmemo化し、
針・数値・fault表示は従来の現在state/cadenceを使用する。

MjvGeomのprimitive名を取得できない場合でも、材質cacheはnative RGBAを含める。
異なる色・透明度を持つcubeと台に、最初のprimitiveの材質を誤って共有しない。

## Single/Assistと関節rail

初回Single、明示Assistを同じapp sessionで保持する。Assistは自由視点と上下2段の補助正投影を表示する。
v1.1では自由視点:Assist列を2:1（補助幅の下限280px、従来の320px上限なし）とし、補助二面を等高にする。
領域の区切りは1pxへ統一し、左右railは入力帯の底まで連続させる。計器をsceneへ重ねない。
上面はXY、+Zから-Z、screen-right=-Y、screen-up=+X（up=+X）。正面はYZ、-Xから+X、
screen-right=-Y、screen-up=+Z。Main/operatorのscreen-right=-Yと左右を統一し、視点変更で入力mappingは変えない。
旧front=XZ/side=YZ/topのpresetは互換のまま、assist cameraは別identityとする。
一つのrenderer/canvas/sceneでviewport/scissorを使い、headerをscene矩形から除外する。
一回の描画iterationで同じscene stateを全paneへ使い、pane数でstate sync/physics/input送信を増やさない。
OrbitControlsはmain interaction DOMだけに結び、mainで開始したdragのpointer captureを維持する。
補助、rail、header、formは主cameraを操作しない。Single/Assist往復で主cameraをresetしない。

railは検証済みRobot joint subsetとcompiled addressを使う。FastArm assembly v3の明示namespace規約
`arm_id__local_name`とcanonical core順を照合してleft/rightを固定し、cameraで交換しない。
左右が確認できないRobotは「関節」の汎用rail。object freejointはRobot subsetへ入れない。
片腕の不要railを閉じ、空きを別情報で埋めない。未取得/invalid/stale/別epochは針・塗りを消す。
terminalに保持するsnapshotは「終了時」と区別する。

Quad、研究layout/camera/policy freeze、feedback非公開の全pane/rail/stripへの適用は今回未実装。
研究条件受入や実Gamepad/Selfrionette/Keyboard device受入はsoftware表示検証からは保証しない。

## 描画提出の計測

既存`counters()`の`renderedFrameIndex`・`renderedInputSequence`・`renderedAtMs`・`renderedPaneCount`は、
全paneの描画提出を終えた時点を観測する。browserのmonotonic時刻であり、GPU完了・画面走査・実device時刻ではない。
software入力変更から、対応するbackend sequence/frameを描画提出するまでの経路は相関を確認して測る。
既存receive-to-applyがnullのWorkbench経路を0msと補完しない。計測用にphysics、poller、送信周期を増やさない。
