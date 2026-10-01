---
status: canonical
owner: architecture
last_verified: 2026-07-16
canonical_for:
  - live viewer smoke path
related:
  - docs/operations/websocket-publisher-runner.md
  - docs/operations/backend-viewer-startup.md
  - docs/architecture/data-flow.md
  - apps/mujoco-viewer/README.md
---

# live viewer smoke

replay payload v0からbrowser viewer runtimeまでのdeterministic local smoke pathを確認する。

## command

```bash
uv run python scripts/viewer/run_live_viewer_smoke.py --host 127.0.0.1 --port 8766 --steps 3 --grace-period-s 5
```

## viewer URL

```text
apps/mujoco-viewer/?websocketUrl=ws://127.0.0.1:8766
```

CLIが表示するWebSocket endpointは`ws://127.0.0.1:8766`、browser viewer URLは
`apps/mujoco-viewer/?websocketUrl=ws://127.0.0.1:8766`である。endpointとbrowser pageを混同しないよう、
CLIは両方を出力する。

`websocketUrl`なしで`/apps/mujoco-viewer/`を開くと、設計どおりdisconnectedのままになる。
旧`?ws=`は退役エラーとして起動を停止する。`?websocketUrl=ws://127.0.0.1:8766`を使用する。

## 推奨手順

1. terminal 1でsmoke commandを起動する。
2. CLIが表示したViewer URLをcopyする。
3. grace period中にbrowserでViewer URLを開く。
4. viewer statusが`WebSocket: open`へ変わることを確認する。
5. marker summaryがpayload v0 frame updateを反映することを確認する。

smoke commandはlocal WebSocket server起動後、最初のpayload publish前にbrowserが接続できるようgrace periodを
設ける。viewer WebSocket clientは現在reconnectを実装していないため、server準備前にbrowserを開くとerror stateに
残る場合がある。grace window終了前にbrowserが接続しない場合、runnerはpayloadをdropする。


canonical backend / viewer startup guideは`docs/operations/backend-viewer-startup.md`、host / port / URL contractは
`docs/operations/websocket-host-port-contract.md`を正とする。
E2E smoke / troubleshootingの本体は
`docs/operations/runtime-to-viewer-e2e-smoke.md` に置く。

## smoke pathが証明する範囲

- Python replay dry-runがpayload v0を生成する
- local/dev WebSocket publisher runnerがclientへpayload v0をdeliveryできる
- browser viewerがconfigured endpointへ接続できる
- viewer runtimeが受信payloadをstateへ保持する
- marker rendering skeletonがlatest payloadからsummary text、scene placeholder text、root attributeを更新する
- viewerがmarker skeleton object向けThree.js scene object registryを維持し、marker scene modelからpayload
  marker positionを直接適用する

## success condition

- viewer statusがopen WebSocket connectionを示す
- summary textが受信`frame_index`まで進む
- viewer rootのbody/site countが受信payloadへ追従する
- rendered marker summaryに`base_link`と`tip`が残る

## client不在時のbehavior

Python publisher runnerは不在client向けpayloadをbufferしない。frame publish時にbrowser viewerが未接続なら、そのframeを
dropする。smoke pathをdeterministicに保つにはgrace periodを使うか、viewerを先に起動する。

## scope

- browser automationなし
- production serverなし
- auth / TLSなし
- reverse proxyなし
- public network exposureなし
- serial、OSC、hardware accessなし
- `@types/three`またはRapierの再導入なし
- direct marker position assignmentを超えるThree.js real scene mutationなし
- direct payload coordinateを超えるbody/site/target position mappingなし
- FK / IKなし
