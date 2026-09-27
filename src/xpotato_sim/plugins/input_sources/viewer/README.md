# viewer Input Source

## 意味とresponsibility

browser frontendからbackendへ届いたcontrol sampleをruntime Input Sourceとして保持・供給するbridgeである。
canonical declaration: [`INPUT_SOURCE_PLUGIN`](plugin.py)

## input / output

frontend acquisition結果をviewer control sampleとして受け、backend readerがcanonical frameとhealthを出力する。
browser event取得そのものはviewer applicationの責務である。

## parameters

metadataとinitial endpointを扱う。current contractは[`plugin.py`](plugin.py)を正とする。

## lifecycleとside effect

backend-side queue / stateを読むsoftware boundaryであり、deviceやserial portをopenしない。
接続・sample状態はhealthとして扱う。

## compatibilityとcomposition

Mapping selection、keyboard / gamepad binding、speed等のMapping parameterを所有しない。
frontend acquisition、backend source、Mapping変換を別責務として維持する。

## constraintsとnon-goals

- constraint: malformed control sampleを暗黙zeroへ変換しない
- non-goal: browser-side FK / IK、Robot selection、command semantics routeを所有しない

## tests / validation

- [keyboard / gamepad smoke](../../../../../docs/operations/r6-l-keyboard-gamepad-live-viewer-smoke.md)

## canonical architecture / contract

- [viewer control schema](../../../../../docs/contracts/viewer-control-message-schema.md)
- [Input Source registry](../../../../../docs/contracts/runtime-input-source-registry.md)
