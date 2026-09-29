---
status: canonical
owner: runtime
last_verified: 2026-09-29
canonical_for:
  - local simulation workbench control and resource lifetime
related:
  - docs/contracts/finite-trial-runtime.md
  - docs/operations/unified-cli.md
  - docs/design/adr/0013-workbench-worker-supervision.md
---

# 実験Workbench

## 対応範囲と操作

`xpotato-sim workbench`はprofile未選択で待機するforegroundアプリである。未選択時は
モデル、入力、physics stepを作らない。登録済みnamed-model v2/v3/v4を一覧から選び、
「検証・準備」→同一初期状態のscene/shader準備→明示「開始」→新しい中立入力→有限試行→
結果保存→明示「同じ条件で再試行」を行う。次条件と適用中profileは分けて表示する。
drop/fixed-contact/push、片腕/双腕、幾何/動力学を共通`TrialRunner`へ渡す。
v1や非対応Sourceは理由付きで不可表示し、別profileやneutral入力へ置換しない。
全軸parameter editorは#593であり、本契約には含めない。

GUIと`--run-once`は同じ制御service、worker、TrialRunnerを使用する。既存`trial` CLIも
同じTrialRunnerを使う。headlessにはrenderer ACKを要求しない。MuJoCoがphysical stateの
唯一の正本、Three.jsは描画だけを所有し、新しいphysics/Task判定は作らない。
trialの終端でWeb/backendは終了しない。アプリはoperatorのCtrl+Cで終了し、所有process/jobを閉じる。

## 起動

依存がinstall済みのrepository rootからPowerShellで実行する。保存先は永続成果物、
temporary rootは事前に用意した絶対directoryを指定する。以下の値を実際の絶対pathとsource identityへ置き換える。

```powershell
$taskRoot = 'C:\absolute\task-temp'
$resultRoot = 'C:\absolute\trial-results'
$sourceIdentity = '<HEAD SHAと未commit差分のidentity>'
.\.venv\Scripts\xpotato-sim.exe workbench --temporary-root $taskRoot --result-root $resultRoot --software-revision $sourceIdentity --open-browser
```

`--web-dist`省略時はsourceのVite dev serverを使う。固定buildでの検証は明示的にbuildし、
出力rootを`--web-dist`へ渡す。devからbuildへの自動fallbackはない。

```powershell
$env:XPOTATO_VITE_CACHE = Join-Path $taskRoot 'vite-cache'
$dist = Join-Path $taskRoot 'viewer-dist'
node apps/mujoco-viewer/node_modules/vite/bin/vite.js build --config apps/mujoco-viewer/vite.config.ts --configLoader runner --outDir $dist
.\.venv\Scripts\xpotato-sim.exe workbench --temporary-root $taskRoot --result-root $resultRoot --software-revision $sourceIdentity --web-dist $dist --open-browser
```

buildは`apps/mujoco-viewer/index.html`、参照module/CSSとWASM資産を含む必要がある。
静的serverは参照の存在・非空、WASM header、root内pathを検査し、欠落・外部URL・symlinkを拒否する。
任意の代替HTMLをGUIとして生成しない。これは依存JS全体の完全性署名やbrowser動作保証ではない。

```powershell
.\.venv\Scripts\xpotato-sim.exe workbench --temporary-root $taskRoot --result-root $resultRoot --software-revision $sourceIdentity --run-once --profile dynamic-cube-drop --fixture tests/fixtures/trial_gamepad/short-movement.json --ticks 5
```

`--profile`だけでは初期選択を表示するだけでprepare/Startしない。`--run-once`はprofileとfixtureを
必須とし、明示された有限実行後にexitする。fixtureとbrowser入力は排他で、fixtureはlive input証拠ではない。
`--startup-check`はWebとworkerの起動確認後に終了する。通常GUIはfixtureを指定せずGamepadを使う。

| option | 既定・意味 |
| --- | --- |
| `--backend-port` / `--web-port` | 8766 / 5173。異なるloopback portのみ |
| `--ticks` | 省略時は選択profileのsteps。有限なcommit予算 |
| `--input-wait-s` | 5秒。Start後の入力中立待ち |
| `--wall-s` | 360秒。Start後の総wall時間 |
| `--prepare-s` | 30秒。runner準備上限、親の監督期限は追加2秒 |
| `--diagnostic-memory` | 指定時だけtracemallocを開始。通常はPython heap未測定（null） |
| `--control-stdin` | 自動検証用の一時capabilityをstdinの1行から取得。保存しない |

## 制御と停止

外部要求は64 KiB以下のJSON文字列、既知operation/field、有限値、登録profile IDに限定する。
任意path/XML/shell/import/出力許可をprotocolへ公開しない。bindは127.0.0.1、HostとOriginは
指定portへ固定する。Originなしのlocal CLI/workerは許可するが、mutatorにはcapabilityと単一connection所有権が必要。
閲覧は状態・保存結果だけ。capability付きURLは`--open-browser`でfragmentとして直接渡し、画面はfragmentを消す。
再接続は状態照会だけで、操作権取得とStartは自動化しない。

command ID・期待revision・trial ticket・generationを検査する。dedup履歴128件はacceptedとcompleted/errorを
保持する。最新結果32件、peer最大8、pending要求32、peer制御FIFO32、描画は最新1frameだけとする。
private worker/frame輸送は1 MiB、外部commandは64 KiBとして分離する。大きすぎるprivate frameも無制限にはしない。
phase変化と実行中最大0.5秒間隔のstatusでtick・simulation時間を更新し、描画sampleの時間と区別する。

STOPは専用1slot・固有ID・2秒期限を持ち、pendingが満杯でも先に処理する。未dispatch要求は取り消し、
旧generationのstatus/frame/assetを無効にする。通常statusやprepare完了ではSTOP監督を解除しない。
同じID・generationのSTOP完了で、inactiveな状態とerrorなしを確認して解除する。
期限超過やworker死亡は一度だけfaultを通知し、所有process/job終了を待つ。2秒は強制停止手続き開始の上限であり、
OSのprocess回収時間までのhard realtime保証ではない。結果確定は保存記録で確認する。
owner切断もactive/処理中なら同じ監督を開始する。閲覧peerの不正要求でowner試行を停止しない。

開始前STOPは準備を破棄して未選択へ戻る。active STOPはoperator_abortを保存し、次のprepare/retryも明示操作とする。
prepare/reset失敗・記録失敗後は自動再開しない。生存workerで明示「検証・準備」を行うと、失敗したrunnerをcloseし、
新しいrunnerで検証・準備する。過去結果と不完全fileは保持する。記録失敗をsuccess/retry可能へ変換しない。
worker死亡・強制終了後はアプリを終了して明示再起動する。GUIだけの再接続ではworkerを復活させない。

## 入力と資源所有

browserは既存`sampleViewerGamepadSnapshot`と`buildViewerGamepadControlMessage`で40 msごとに新しくpollする。
raw axes/buttonsを保持し、試行epochごとにsessionとsequenceを新規にする。初回device未取得はsampleを送らず入力待ち期限に従う。
取得後のblur/欠落/切断はstale/disconnectedとして送り、
中立入力として補完しない。cached sampleのheartbeatで鮮度を延ばさない。backendの0.2秒freshnessは不変。
async scene準備の成功・失敗・finallyは開始時のsocket/generation/epochに束縛する。旧loadは新epochを失敗扱いにしない。
CONNECTING/OPENの重複socketを作らず、callbackは現socketを確認する。閲覧preview後のclaimでも現ready epochを再ACKする。

| 資源 | 所有者・上限 | 破棄点 |
| --- | --- | --- |
| Web/process/job、worker、async service task | launcher / `OwnedApplicationWorkers` | アプリ終了、監督fault |
| Python MjModel/MjData、Source/Mapping/Task/input/log | workerの単一thread上のTrialRunner | 同条件retryはmodel再利用、状態ownerをreset。条件変更/復旧/closeで旧参照を解放 |
| asset file/allowlist | workerは現modelだけ生成、serviceが公開 | 次prepare/STOP/faultでallowlist無効化、アプリ終了でtask内directory削除 |
| 結果と履歴 | service 32 / 128、永続結果はtrial別file | bounded listから退役。永続結果は消さない |
| WASM module | browser loaderのPromise 1件 | ページ寿命。失敗したloadだけ再試行可能 |
| WASM model/data、mesh/material/texture、shader | rendererの現scene | 同model retry再利用、model切替でdelete/dispose、cacheは現sceneに限定 |
| async load/compile | rendererの直列chainとabort/generation | invalidateで旧結果を拒否し、disposeは進行中compileのsettle後に一度だけ解放 |
| WebSocket、40 ms timer、reconnect listener | WorkbenchApp各1 | disconnectで入力停止、unmountでclose/clear/remove |
| rAF、resize listener/observer、OrbitControls | renderer各1 | renderer disposeで停止・解除 |

`window.__workbenchCounters()`は所有slot、生成/delete数、renderer GPU counts、Python RSS/private bytesを公開する。
WASM heapは実exportされたdata propertyがある場合だけ読み、未export getterには触らずnullを返す。
timer/listenerの値はこのownerの数でありbrowser全体の実測ではない。RSSが直ちに低下しないだけでリークと断定しない。
反復・モデル切替・新規起動の固定build実測と合成deviceによるfrontend入力確認は、
[software検証note](../experiment-notes/2026-09-29-workbench-software-validation.md)へ分離する。
Mapping単独のparameter編集は#593であり、単体検証や短期測定を無期限のリーク不在・実Gamepad受入へ読み替えない。
