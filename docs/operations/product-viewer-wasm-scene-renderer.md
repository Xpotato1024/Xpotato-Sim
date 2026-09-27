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

画面は3D中心のworkbenchである。上部に接続/受信age/描画/入力取得、右に入力・手先・タスクの概要、
下部にqposを置く。モデルpath、内部統計、生の診断、contact log/payloadの読込みは詳細診断を開いて確認する。
狭い画面では状態→3D→qpos→概要の順とし、内部モデル情報を3Dより先に並べない。

`ready`は描画準備であり、接続や実機安全性の証拠ではない。接続は未指定/接続中/受信待ち/受信中/
更新停止/配信終了/エラーを区別する。受信ageはbrowserのmonotonic receipt時刻から表示し、
1000 ms超を表示上の更新停止とする。この閾値はbackendの入力staleやphysical safety gateではない。
閉じた接続では最終受信値を表示中と明記する。invalid payloadをfresh/安全へ変換しない。

カメラの斜め/正面/側面/上面/全体は、既にMuJoCoが算出したbody位置を使ってviewだけを変える。
受信qposを編集・再計算せず、body範囲から算出する表示marginを可動域や安全clearanceと扱わない。
canvas実寸とResizeObserverでaspectを追従し、小画面を固定最小幅へ引き伸ばさない。

「入力取得を停止」はbrowser providerをdisposeする。これはlocalな取得停止であり、実機非常停止、
backend session停止、即時physical stopの通知ではない。backend側は既存のneutral/stale契約に従う。
再開は既存のconnection/error gateを満たす場合だけ可能で、終了済みの接続では入力を再開しない。
input/textarea/select/contenteditableやUIボタンでのキーはrobot入力へ渡さず、編集中に保持キーを解放する。
blur/非表示/disposeの既存契約を維持し、dispose後に追加publishしない。

概要に現れないcontact evidenceや各種provenanceは削除せず、詳細診断で従来どおり検証・表示する。
状態表示とカメラ、編集field隔離は`workbenchPresentation.test.ts`で検証する。

## 関節・入力計器と診断固定

関節欄はloaded MuJoCo modelのjoint name/type/`jnt_qposadr`を検証し、hingeを角度指標へ投影する。
針は0を上、正方向を時計回りとして円周方向を示す。短いdegree数値は累積角度を保持し、
360度で数値をwrapしない。slideはmeter単位の並進座標、ball/freeは複数座標として角度計非対応を示す。
欠測・次元不一致・非finite値は針を消し、`—`を表示する。raw qposは詳細診断で確認できる。
この指標は可動域meterではなく、モデルlimitや実機safe zoneを捏造しない。表示値の出所も併記する。

Gamepadは取得されたnormalized axesの順序を保つXY表示と符号付きバー、押下button番号を示す。
不正な軸をfilterして番号を詰め直さず、配列全体を計器表示から除外する。不正buttonを未押下へ変換しない。
Selfrionetteは`input_signal_v1`を検証して生の7chを示す。単位・指との対応・力への換算は未校正と明記する。
バーは同一sample内の相対比で、表示scaleを併記する。異なる時刻の絶対振幅比較は生値で行う。
Keyboardはbackendが記録した押下キーとfocus状態を示す。staleの表示は既存backend診断から導出する。
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
