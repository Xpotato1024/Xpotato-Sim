---
status: canonical
owner: runtime
last_verified: 2026-10-02
canonical_for:
  - local simulation workbench control and resource lifetime
related:
  - docs/contracts/finite-trial-runtime.md
  - docs/operations/unified-cli.md
  - docs/design/adr/0013-workbench-worker-supervision.md
---

# 実験Workbench

## 設定・準備と操作画面（#564/#587、レイアウトv1.0）

初期画面はSetup（設定・準備）。profile、condition editor、import/export/diff、適用条件と次条件、
hash/epoch/tick、結果とraw診断はSetupに置く。ready後の「操作画面へ」はローカル表示遷移のみで、
Startを送らない。「開始」はOperateでのみ有効とし、従来の操作権、busy、generation、renderer ACK/epochと新しい中立入力のgateを使う。
waiting_input/running/finalizing中はSetupへの遷移・条件編集を無効にする。画面切替ではrenderer、model、socket、pollerを再生成しない。

Operateは状態帯、固定操作帯、関節railと大きな自由視点、下のInput stripで構成する。
初回Singleは補助列なし。明示Assistは上面XYと正面YZを上下の別矩形に追加し、選択をapp session中保持する。
各viewの名前・軸凡例・カメラ操作はsceneの外headerへ置く。自由視点はPerspective/Orbit、補助は固定正投影。
Assistはoperator基準へ揃え、上面はscreen-right=-Y / screen-up=+X、正面はscreen-right=-Y / screen-up=+Zとする。視点変更で入力mappingは変更しない。
主cameraのposition/targetは切替で変更せず、補助のcenterと対象境界は初期または明示fitで決める。
v1.1は主視点:補助列を2:1に拡げ、1pxの境界と入力帯まで続く左右railへ整理する。
Gamepad操作帯は中央の左Z/左XY/右XY/右Zのみ。Zは確定符号付き入力量の縦バー、ボタン詳細はSetupに置く。
初期画角とフォーカスはnative変換済みgeometryへ合わせる。表示のためにmodelやphysicsを複製しない。
「操作視点」はoperator presetへ戻し、「全体」は現在の観察方向を保持して対象を収める。Workbenchの初期cameraはoperatorとする。
停止要求は開始とは別の操作帯右端に置き、確認modalを使わない。停止要求中、停止確認・保存済み、保存中、記録失敗を区別する。
停止はsimulationの操作であり実機非常停止ではない。障害・記録失敗の明示復旧契約は以下の従来手順を維持する。

Input stripは共通のsource/availability/age/frameとsource-specific投影を分離する。
Workbenchの入力consumerはGamepad-onlyのままで、選択Gamepadと受信sourceが一致しなければ計器を消す。
Selfrionette/Keyboard/unknownの共通表示は従来Viewerまたはsoftware fixture consumerの表示能力であり、
WorkbenchへのSource追加・physical接続・校正ではない。鮮度、切断、別epochでは旧active値を表示しない。
正常終端後は入力を現在値として表示せず、関節は「終了時」として保持する。Setupから終端の操作画面への再表示は開始を伴わない。
関節表示・source表示・補助cameraの詳細は [viewer操作契約](../operations/product-viewer-wasm-scene-renderer.md) に従う。

Quad、研究条件のfreeze、全表示へのfeedback非公開policyは未実装であり、研究条件受入の完了を意味しない。

## 対応範囲と操作

`xpotato-sim workbench`はprofile未選択で待機するforegroundアプリである。未選択時は
モデル、入力、physics stepを作らない。登録済みnamed-model v2/v3/v4を一覧から選び、
「検証・準備」→同一初期状態のscene/shader準備→明示「開始」→新しい中立入力→有限試行→
結果保存→明示「同じ条件で再試行」を行う。次条件と適用中profileは分けて表示する。
drop/fixed-contact/push、片腕/双腕、幾何/動力学を共通`TrialRunner`へ渡す。
v1や非対応Sourceは理由付きで不可表示し、別profileやneutral入力へ置換しない。
Basicは登録preset、Advancedはbackend descriptorによるparameter/物体editorである。
Robotモデル、物体定義・配置、Mapping、Task期間、有限予算を編集できる。
Inputはnamed-model経路で`viewer`/`gamepad/v1`だけに対応する。別Source/Mapping、正式Evaluation、
Robot固有寸法・gain、任意物体集合・形状型追加は理由付きで無効にし、別機能へ代用しない。

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
| `--condition` | export済み展開条件JSON。profile/ticks/期限optionと排他。予算は条件内limitsを使用 |
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

終端と画面へのstatus到着の間に残った同ticket入力は、control/worker境界で有効なGamepad messageであることを
確認して棄却し、現状態を返す。STOP監督中も同じ扱いで指令をforwardしない。ready、旧ticket、権限なし、
fixture、不正messageは引き続き拒否し、`TrialRunner.ingest`のactive-trial条件を緩めない。
要求の拒否はrunnerの保存結果・停止理由へ上書きせず、画面は元の停止理由と別の要求・接続診断を区別する。
正常なTask終端・simulation予算上限・operator停止・入力失効を後続のactive-trial受付エラーへ置き換えない。

STOPは専用1slot・固有ID・2秒期限を持ち、pendingが満杯でも先に処理する。未dispatch要求は取り消し、
旧generationのstatus/frame/assetを無効にする。通常statusやprepare完了ではSTOP監督を解除しない。
同じID・generationのSTOP完了で、inactiveな状態とerrorなしを確認して解除する。
期限超過やworker死亡は一度だけfaultを通知し、所有process/job終了を待つ。2秒は強制停止手続き開始の上限であり、
OSのprocess回収時間までのhard realtime保証ではない。結果確定は保存記録で確認する。
owner切断もactive/処理中なら同じ監督を開始する。閲覧peerの不正要求でowner試行を停止しない。

開始前STOPは準備を破棄して未選択へ戻る。active STOPはoperator_abortを保存し、次のprepare/retryも明示操作とする。
prepare/reset失敗後は自動再開しない。生存workerで明示「検証・準備」を行うと、失敗したrunnerをcloseし、
新しいrunnerで検証・準備する。記録失敗後は原因確認後の明示prepareを許可し、失敗runnerをcloseして新trial ID/epochで準備する。失敗file・失敗分類は保持し、retry・自動開始は拒否する。active/finalizing中は適用しない。
過去結果と不完全fileは保持し、記録失敗をsuccessへ変換しない。
worker死亡・強制終了後はアプリを終了して明示再起動する。GUIだけの再接続ではworkerを復活させない。

## 入力と資源所有

browserの取得寿命は従来Viewerと同じ`gamepadLifecycle`が所有する。Workbenchは描画rAFから独立した
約60 Hz（周期`1000 / 60` ms）のtimerで毎回実sampleを取得する。描画fpsによる間引きやcached heartbeatの再送を行わない。
従来ViewerのrAF/publication cadenceは維持する。完全なJavaScript停止で取得が遅れた場合のfreshness判定は緩めない。
raw axes/buttonsを保持し、試行epochごとにsessionとsequenceを新規にする。初回device未取得はsampleを送らず入力待ち期限に従う。
visibleならfocus=falseでも取得する。取得後のhidden/欠落/切断はstale/disconnectedとして送り、
中立入力として補完しない。cached sampleのheartbeatで鮮度を延ばさない。backendの0.2秒freshnessは不変。
hiddenでは即時失効して取得schedulerとheartbeatを停止し、visible復帰時は新しく取得する。

入力滞留の診断は[有限試行の時刻分類](finite-trial-runtime.md)を使う。
performance改善は物理条件・Task予算・freshnessを緩めず、同条件の実MuJoCo比較で確認する。
接触solver、Python検査、IK、copy、観測、serialization、入力ageを分けた測定と、
未実装のbackpressure設計・適用限界は[性能測定note](../experiment-notes/2026-10-02-simulation-performance.md)へ記録する。
capability、claim、ticket/epoch、busy、phase、fixture gateは送信時点で確認し、dispose後は送らない。
Keyboardのfocus契約は変更しない。
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
| WebSocket、表示鮮度timer、reconnect listener | WorkbenchApp各1 | disconnectで入力停止、unmountでclose/clear/remove |
| Gamepad実sample取得timer | 共通gamepadLifecycleの1 owner、約60Hz | hiddenで停止、visibleで新規取得、unmountでdispose |
| rAF、resize listener/observer、OrbitControls | renderer各1 | renderer disposeで停止・解除 |

`window.__workbenchCounters()`は所有slot、生成/delete数、renderer GPU counts、Python RSS/private bytesを公開する。
WASM heapは実exportされたdata propertyがある場合だけ読み、未export getterには触らずnullを返す。
timer/listenerの値はこのownerの数でありbrowser全体の実測ではない。RSSが直ちに低下しないだけでリークと断定しない。
反復・モデル切替・新規起動の固定build実測と合成deviceによるfrontend入力確認は、
[software検証note](../experiment-notes/2026-09-29-workbench-software-validation.md)へ分離する。
parameter/model編集後も同じ資源所有とepoch隔離を使う。単体検証や短期測定を無期限のリーク不在・実Gamepad受入へ読み替えない。

## 展開条件と停止中の編集

`experiment/edited_condition.py`が`workbench-condition/v1`、descriptorとstrict入口を所有する。
条件は`schema_version`、登録`preset_id`、`configuration`（Robot/model/Input/Mapping/coordination/execution）、
登録モデル構成の`model_configuration_sha256`、展開済みEnvironment、Task、未対応を表すEvaluation null、
有限`limits`を持つ。登録モデル構成が変わった古いexportは拒否し、local path、port、trial IDは含めない。
旧launch-profile v1〜v4の保存byte/digest・CLIは変更せず、内部で既存decoderと`resolve_trial_profile`へ渡す。
GUIと`--condition --run-once --fixture`は同じresolver/service/worker/TrialRunnerを使う。

型・単位・選択肢・minimum/maximum・排他的minimum・availability/reasonはbackend descriptorとして返し、
同じdescriptorを入口検査に使う。model選択と登録endpoint bindingの変更は明示した一操作で、他の条件を置換しない。
物体はpresetの有界集合とidentityに束縛する。定義（box半寸法m、質量kg、摩擦）と配置（world位置m、
単位wxyz quaternion、fixed/dynamic、初期速度）を分離し、同一MuJoCo worldへ既存Environmentが構築する。
kinematicのdynamic物体は拒否する。物体数・形状型の追加や新Taskは本editorでは公開しない。

`clone`は登録presetから新しい展開コピー、`edit`はparameter検証、`export`は検証済みコピー、
`import`はJSON内容の再検証、`diff`は適用条件（未適用時は次条件）との差分を返す。
60,000 bytes上限、exact field/配列構造、登録identity、有限数、既存parameter/physics契約を検査する。
重複JSON key、未知field/ID、path/XML/code/import参照、非対応組合せを捨てず拒否する。
exportはschema・完全展開値を保存し、import後のcanonical条件が再現する。server上のfile selectorは存在しない。

編集応答は期待revisionとticketを検査し、active、処理中、記録失敗、旧epochで拒否する。
GUIのeditor操作はrequest ID付きの単一in-flightとし、file読取中もdraft変更を禁止する。
遅延・失敗・別要求の応答は他のdraftやexportを変更しない。socket/revision/generation/epoch変更で失効し、30 sの待機上限を持つ。
CLIで明示したticks・input/wall/prepare上限は初期条件と各preset cloneに保持する。outer watchdogも受付済みconditionのprepare_sを使う。
executionのsteps/interval_s/grace_period_sは旧app専用で有限trialには作用しないため編集を無効にし、理由を表示する。
fixed/dynamic遷移はObjectInstance契約から公開されたmotion templateを使い、frontendへ物理defaultを重複定義しない。
次条件は適用conditionと別のコピーであり、editだけでnative worldを変更しない。
「検証・準備」は共通resolverの後にnative buildと既存初期貫通検査を行い、成功したassetだけをallowlistへ公開する。
旧trialが実行された場合はterminal・記録確定後だけ次条件を適用する。記録失敗で保存処理が終了した場合は、前述の明示prepare復旧だけを許可する。readyの未開始previewは破棄して再準備できる。
STOP中のprepare遅延完了・旧frame/入力/loadは既存generation/epoch gateで無効にする。


### 入力取得と観測公開のcadence

live Gamepad取得は描画fpsから独立した約60Hz（`1000 / 60` ms）のtimerで行う。
hidden/disconnect/error時の停止通知、button/trigger、中立、source/session/sequence、
元受信時刻と0.2秒gateは従来どおり扱う。意味保存を伴わないlatest-only化は行わない。
workerはticket・phase・成功commitによるtick数の変化後にframeを公開する。
ready/terminalの同じ状態をtimerで繰り返し生成しない。peer最新1frame slotと制御FIFOは維持する。
表示sampleはreset/commitでforward済みのnative stateを読み、追加の`mj_forward`を呼ばない。
表示頻度でwarmstartやTask進行が変わらないことを回帰testで照合する。

### 受信batchとfreshness判定（#610）

sampleのread/Mapping後にhost clockを評価し、そのsampleとnowを同じtickの鮮度判定へ渡す。
鮮度判定の線形化点は、このownerが保持するsampleに対するhost nowの取得時点とする。
staleの場合、fault latch・provider invalidate・結果保存より前にownerのqueueを一度だけ再確認する。
新入力は最大64件を順序検査・消費してtickを延期し、次iterationのwall/input-wait監督へ戻る。
STOP/closeを検出した場合、または既にdeferred操作を保持している場合も、積分せずownerへ戻す。空queueなら元sample/nowでstrictに終了する。
workerの受付batchは鮮度の最終判断をこのtickへ委ねるが、不正sample・future・pretrialは即時拒否する。
再確認後の任意OS停止まで競合が起きない保証はない。receiptを現在時刻で置換せず、faultから復帰しない。

workerは1件ごとにtickを挟まず、既に受信済みの同ticket入力を最大64件まで順に消費する。
受信loopとadvance入口の両方で確認し、両者の間にworkerが停滞して届いた入力もtick前に消費する。
各sampleのvalidation、Mappingのbutton/trigger符号ラッチ・解除、中立を処理し、physicsは最新sampleで
1tickだけ進める。元receiptを保持し、古いmotionの追い付き再生やlatest-onlyの履歴破棄は行わない。
64件に達したiterationではtick/frame生成を後回しにし、次の受信へ戻る。worker受信queueも64件、
service制御queueは32件のままとする。受信batch内のSTOP/closeは入力を積分せず優先し、
別ticket・要求ID付き操作はbatchの境界として扱う。既存service STOP監督も維持する。

batchの過去sampleは元receiptで順序・source・利用可能性を検査する。receipt間隔は診断に残すが、
過去のgapだけを停止理由にしない。最新sampleが実時刻で0.2秒を超えて古い場合は停止する。切断、hiddenのstale通知、
不正sample、source/session変更は後続のfresh sampleで消さない。初回batch内の中立は自身のreceiptがtick時に0.2秒以内で、同source epochかつ試行開始以後の場合だけarm条件に使い、
最初のtickでpreflightだけを行う。Task/physics積分や正式結果を過去sample数だけ進めない。

通常の終端後入力はbatch全件の同ticket・valid late messageを検査し、不正な後続入力を黙って捨てない。明示STOP/close取消とは区別する。
statusの`service_last_input_receipt_s`と`input_diagnostics`は、serviceの最新receipt、workerのbatch件数・
上限到達、ingest開始時刻、消費成功sequence/source timestamp/receipt、直前tick所要時間を分離する。
session ID、device値、入力値や全sample logは追加しない。browser時計とhost receiptは別clock domainである。
service receiptだけ先行し、processed receipt/sequenceが止まる場合はqueue/worker遅延の候補となる。
両receiptが止まる場合はsource/送信/service側の欠落を調べる。診断だけでGCやdevice故障を断定しない。

browser主threadの長時間停止など、tick時の最新actual receiptが0.2秒超のstaleなら試行は引き続き`technical_invalid`となる。
simulation一時停止による操作継続、明示resumeと再中立、wall budgetや実験有効性の扱いは別のpolicy判断を要し、
本修正は自動resumeやformal evaluation変更を導入しない。


## 実装owner

Workbench固有の制御要求は`runtime/application/workbench_control.py`、通信と停止監督は
`workbench_service.py`、単一processのTrialRunner接続は`workbench_worker.py`、headless制御は
`workbench_client.py`、process memory診断は`workbench_metrics.py`が所有する。
`runtime/runners/workbench.py`はCLIとworker/web process起動を所有する。
worker/webのmodule起動名は従来entryへ明示固定し、stdin start gateとjob参加順序を維持する。
外部CLI、wire/log/condition、入力・停止・physicsの契約はこの配置変更で変更しない。


controlの認可後dispatchはeditor、input、lifecycleのprivate methodへ分ける。
同じWorkbenchControl objectだけがrevision、履歴、次条件、STOP監督を保持し、mutable状態を複製しない。
通信・停止監督とworkerの受信batch・command・projectionは、clock/recv/advanceとSTOPの順序を
保つため同じloopに維持する。別のstate machineやcontext転送層は導入しない。
