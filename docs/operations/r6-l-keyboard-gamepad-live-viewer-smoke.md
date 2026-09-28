---
status: canonical
owner: operations
last_verified: 2026-07-16
canonical_for:
  - R6-L keyboard / gamepad live viewer smoke procedure
related:
  - docs/README.md
  - docs/reports/implementation/r6-l-keyboard-viewer-input.md
  - docs/reports/implementation/r6-l-gamepad-viewer-input.md
  - docs/reports/implementation/r6-l-viewer-input-overlay.md
  - docs/contracts/viewer-control-message-schema.md
  - docs/contracts/transport-payload.md
  - docs/contracts/r7-b-runtime-input-pipeline-contract.md
---

# R6-L keyboard / gamepad live viewer smoke

## 目的

manual keyboardとbrowser gamepadによるlive viewer control smoke pathを検証する。viewerがinputをcaptureし、backend
`ViewerInputSource`がviewer control messageを受信し、既存runtime pipelineがsimulationを進め、viewerがread-only
payload stateとoverlay stateを表示する。

## 前提条件

- backendが`--input-source viewer`とviewer inbound control messageをsupportするcurrent checkoutを使う
- viewer control schema、runtime ingress、overlayのfocused validationが成功している
- `apps/mujoco-viewer` dependencyをinstall済みである
- keyboard smokeではfocusを受けられるbrowserを使う。gamepad smokeではgamepadを接続し、Viewer documentをvisibleに保つ（GamepadはDOM focusを要求しない）
- このsmokeではserial deviceをopenせず、OSCを送信せず、robot hardwareへaccessしない

## backend起動

viewer input sourceを有効にしてbackend runtimeを実行する。

```powershell
uv run xpotato-sim viewer --robot fast_arm `
  --host 127.0.0.1 `
  --port 8766 `
  --steps 18000 `
  --dt-s 0.0166666667 `
  --interval-s 0.0166666667 `
  --grace-period-s 30 `
  --input-source viewer
```

注記:

- backendがsimulation stateのsource of truthである
- viewerはMuJoCo stateを直接mutateしない
- inbound WebSocket messageはsimulatorではなく`ViewerInputSource`を更新する
- runtime step loopはviewer messageをingestした後にsimulationを進める
- current checkoutがviewer ingressをsupportすることをpreflightで確認する
- documented step intervalではfinite runが約5分続く。operatorが早く完了した場合は`Ctrl+C`でbackendを停止する。
  keyboard/gamepad check完了前にbackendが終了した場合は再実行し、failure noteへ記録する
- `--input-source viewer`では正の`interval_s`がabsolute monotonic deadlineを使う。compute、simulation、annotation、
  serialization、enqueue時間はcadenceへ加算せずremaining sleepから差し引く。`interval_s=0`はfast-as-possibleのまま
- 完了時にbounded `live runtime timing summary` JSON objectを1件出力する。wall/simulation time、realtime factor、
  stage timing、sleep、deadline lag/miss、frame count、live delivery coalescing、bounded shutdown timeout/drop countを
  含み、全frameは保持しない。deadline missには1 microsecondを超えるpost-sleep scheduler overshootを含む
- final live delivery flushはbest-effortで1 secondにboundする。timeout時はsender taskをcancelしてawaitし、pendingまたは
  unconfirmed in-flight stateをsent frameではなくshutdown dropとして数える

## viewer起動

```powershell
cd apps/mujoco-viewer
npm ci
npm run dev -- --host 127.0.0.1 --port 5173
```

想定URL:

```text
http://127.0.0.1:5173/apps/mujoco-viewer/?websocketUrl=ws://127.0.0.1:8766
```

`/apps/mujoco-viewer/`だけではdisconnected viewerになる。live smoke URLには
`websocketUrl=ws://127.0.0.1:8766`を含める。

## keyboard smoke手順

1. browserでviewer URLを開く。
1. viewer connectionがopen stateになることを確認する。
1. browser windowまたはcanvasへfocusする。
1. `KeyW`、`KeyA`、`KeyS`、`KeyD`、`Space`、`ShiftLeft`、`ShiftRight`を押す。
1. input overlayがactive key codeとkeyboard controlのsource kindを表示することを確認する。
1. crashせずcommand ageとstale stateを更新することを確認する。
1. target、tip、error displayがlive command pathへ追従することを確認する。
1. keyをreleaseし、windowをblurする。
1. key stateがclearされ、overlayがblurred / stale stateをreportすることを確認する。
1. focus regain後にstuck keyが残らないことを確認する。

## gamepad smoke手順

1. browser-compatible gamepadを接続する。
1. browserでviewer URLを開く。
1. viewer connectionがopen stateになることを確認する。
1. stickを動かしbuttonを押す。
1. input overlayがnormalized axis、button state、gamepad source kindを表示することを確認する。
1. connected / stale stateを正しくreportすることを確認する。
1. gamepadを切断する。
1. crashせずsafe zero / stale stateへfallbackすることを確認する。
1. target、tip、error displayがlive command pathと整合することを確認する。

## 想定overlay behavior

- `source_kind`はbackend runtime input sourceを反映する
- `source_active`はbackendがinput sourceをliveと判断しているかを反映する
- key hold中はkeyboard active key codeを表示する
- pad接続中はgamepad axisとbuttonを表示する
- `command_age_ms`と`stale_reason`をread-only diagnosticとして表示する
- optional field欠落でviewerがcrashしない
- target rejection / hold frameでは`runtime_input_safety_applied`、`target_status`、`target_rejected`、
  `target_rejection_reason`、`target_rejection_message`、`rejected_desired_endpoint_m`、held
  `target_position_m`をoverlayで読める
- rejected / held frameで`endpoint_evaluation`がない場合、再計算せずunavailableと表示する
- viewer-origin WebSocket messageをbackend runnerがingestしていることを前提とする
- status sectionはreceived、compatibility-accepted、scene-applied frameを区別し、frame distance、
  receive-to-apply age、parse/apply timing、coalesced frame、UI update frequencyをreportする。これらは
  browser-monotonic observationであり、backend monotonic clockから直接subtractしない
- compatibility-invalid payloadまたはparse errorはingress barrierであり、古いunapplied compatible candidateをdiscardする。
  applied済みscene poseは変えず、後続valid candidate適用までUIがwarning/invalidをreportする

## 120 s acceptance

no-inputとcontinuously-held-inputを別々に評価する。同じmachine、browser、command、loopback endpoint、
`dt_s=1/60`、`interval_s=1/60`を使う。browserをforeground/visibleに保ち、5 second warm-upを120 second
evaluation windowから除外する。

acceptance threshold:

- absolute simulation/wall driftは最大1.0 s
- realtime factorは0.99から1.01
- viewer receive-to-apply age p95は最大100 ms
- latest received-to-applied frame distanceはboundedで、elapsed timeとともに増加しない
- slow senderがsimulation enqueueをblockせず、unbounded queueを作らない

unavailable measurementは推定せず`not run`と記録する。canonical pacing implementationとmeasured comparisonは
`docs/reports/implementation/r7-e-p25-live-viewer-pacing-backlog.md`に記録する。

## 想定target / tip / error behavior

- target marker、tip marker、error vectorはbackend payload由来のまま
- viewerはqpos、FK、IK、MuJoCo stateを再計算しない
- backendがcommand-side targetとsimulation stepを担当する
- active input変更時はtarget / tip / error readoutがbackend runtime pathと同期して動く

## failure checklist

### backend disconnected

- backend切断時もviewerが動作を続ける
- viewerがnot connectedまたはstaleをreportする
- overlayがsafe unavailable / stale valueへfallbackする
- payload未受信でもbrowserがcrashしない

### 誤ったWebSocket URL

- URLが誤っている場合viewerは接続しない
- browserは使用可能なままで、simulation stateをlocal mutateしない

### focus / blur / stuck key

- `blur`がkeyboard stateをclearする
- browserがvisibility lossをreportした場合にkeyboard stateをclearする
- focus regainでstale held keyを再導入しない

### gamepad不在 / unsupported browser

- `navigator.getGamepads()` unavailableでもviewerを使用できる
- overlayがsafe zero / stale fallbackをreportする
- unsupported browser behaviorでviewerがcrashしない

### stale state

- backend timeout window後にoverlayがstale stateを表示する
- stale reasonはread-onlyでsimulation stateをmutateしない
- backend idle / disconnected中はcommand ageが増える

## 責務境界

- viewer: keyboard / gamepad stateをcaptureしてcontrol messageを送る
- backend: viewer control messageをvalidateして`ViewerInputSource`を更新する
- runtime: 既存input pipeline経由でsimulationを更新する
- viewer overlay: payload stateをread-only表示する

## operator note template

```text
date:
time:
host/port:
branch:
PR stack:
backend command:
viewer url:
keyboard result:
gamepad result:
overlay fields:
target/tip/error observation:
overlay result:
backend notes:
warm-up s:
evaluation duration s:
simulation time s:
wall elapsed s:
realtime factor:
deadline miss count:
deadline lag max s:
publish/enqueue time s:
shutdown timeout/drop count:
latest received/accepted/applied frame:
received-to-applied frame distance:
receive-to-apply age p50/p95/max ms:
coalesced frame count:
compatibility-invalid / parse-error count:
browser visibility:
screenshots/logs:
failure notes:
hardware validation: not run
```
