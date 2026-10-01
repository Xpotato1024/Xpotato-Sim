---
status: canonical
owner: operations
last_verified: 2026-07-16
canonical_for:
  - local/dev WebSocket publisher runner
related:
  - docs/architecture/runtime-composition.md
  - docs/architecture/data-flow.md
  - docs/contracts/transport-payload.md
  - docs/operations/runtime-dry-run.md
  - docs/operations/backend-viewer-startup.md
---

# WebSocket publisher runner

replayed payload v0 JSON向けPython-side local / dev WebSocket publisher runnerのcurrent手順を示す。

## 実行内容

- deterministic replay MuJoCo pipelineを再利用する
- 各`MuJoCoState`をtransport payload v0 JSONへ変換する
- connected WebSocket clientへJSONをpublishする
- `127.0.0.1`のloopbackを既定値にする
- payload schemaを変更しない
- browser viewerを開かない
- production WebSocket serverを実装しない

## manual Web View smoke command

manual browser smokeには短い`sweep_x` programmed input pathを使う。MuJoCo QACC instability warningを出す可能性が
ある長いdynamics pathを使わず、HTTP-served viewerがpayloadを受信することを確認する推奨commandである。

```bash
uv run xpotato-sim viewer --robot fast_arm --host 127.0.0.1 --port 8766 --steps 6 --interval-s 0.033 --grace-period-s 60 --preset sweep_x
```

default pathはunit testでcoverするpayload compatibility pathのままである。以前のdefault `--steps 120` commandを
manual browser smokeの推奨にしない。長時間MuJoCo dynamics stabilityはこのsmokeの判定外とする。

ここでのacceptance targetはpublisher / transport smokeとbrowser payload parse smokeである。proper 3D GUI
renderingをこのPRの成果として主張しない。

## option

- `--host`: bind host。default `127.0.0.1`
- `--port`: bind port。default `8766`
- `--steps`: replay step数。default `1`
- `--dt-s`: replay step duration second。default `1.0 / 60.0`
- `--interval-s`: published frame間delay second。default `0.0`
- `--grace-period-s`: publish前にviewer WebSocket connectionを待つsecond。default `0.05`
- `--preset`: optional programmed input preset。`sweep_x`をsupportする

## behavior

- startup時に`serving on ws://...` endpointを表示し、`--grace-period-s`の間viewerを待つ
- grace period終了前にclientが接続しなければ、silent returnせず明示reason付きでexitする
- client接続後にpayload publish開始をlogする
- publish完了時にcompletion reasonをlogする
- connected clientは各payloadをJSON stringとして受信する
- `frame_index`はpublished stepごとに1増える
- `interval_s`はstep間へpauseを入れる
- `grace_period_s`は最初のpayload送信前にlocal clientが接続する時間を与える
- manual Web view smokeには上記の短い`--preset sweep_x --steps 6` commandを使う。長時間dynamics runのQACC
  warningはbrowser smoke acceptance pathに含めない
- browser runtimeはdiagnostic payload textを表示してpayload v0をparseできるが、proper 3D GUI visual smokeではない

## scope制限

- authenticationなし
- TLSなし
- deployment abstractionなし
- multi-room / multi-topic routingなし
- hardware、serial、OSC accessなし
- viewer変更なし

## viewer connection

browser viewerはautomatic defaultではなく明示query parameterで接続する。

```text
?websocketUrl=ws://127.0.0.1:8766
```

旧`?ws=`は退役エラーとなり、接続も静的Viewerの起動も行わない。`websocketUrl`を指定する。endpoint query自体がない場合、viewerはdisconnectedのまま
`WebSocket: disabled`を表示する。viewer側のendpoint configurationとconnection status displayはpublisher runnerから分離する。

viewerはHTTP server経由で開く。`file:///.../index.html`を直接開かない。browser module loadingは`file:` URLを
unique originとして扱い、CORSにより`dist/browser/main.js`をblockする場合がある。

```powershell
Set-Location apps/mujoco-viewer
python -m http.server 5173
```

```text
http://127.0.0.1:5173/index.html?websocketUrl=ws://127.0.0.1:8766
```
host / port / public host contractは`docs/operations/websocket-host-port-contract.md`で固定する。

runnerとbrowser viewerを組み合わせるsmokeは次を参照する。

- `docs/operations/live-viewer-smoke.md`
- `scripts/viewer/run_live_viewer_smoke.py`

dry-run、publisher、viewer、browser connectionを接続するtop-level startup guideは
`docs/operations/backend-viewer-startup.md`である。

smoke pathはbrowser側でrendering-onlyを維持し、marker summary updateまでで停止する。Three.js real scene mutation、
production hosting、auth、TLS、serial、OSC、hardware accessは追加しない。
