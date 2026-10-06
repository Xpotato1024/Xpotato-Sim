---
status: supporting
owner: runtime
last_verified: 2026-10-03
canonical_for: []
related:
  - docs/contracts/workbench.md
  - docs/contracts/finite-trial-runtime.md
---

# Workbench低遅延置換候補の実測

## 先行測定の条件と実行経路（Python 3.14.3）

ユーザー展開条件のSHA-256は`445a954184604c4735e8755302a65d72912b847e35682465a1755eff185f5b10`。
dynamic-cube-push、control 1/60 s、physics 1/600 s、入力age 0.2 s、input-wait 5 s、Task 60 sを維持した。
条件内の有限予算は18,000 ticks、wall 360 sである。dropや別物理条件へ置換していない。
入力軌跡は条件に含まれないため合成系列を別に定義した。最初に新epochの中立を実取得し、
遅延測定は左右交互の第1軸±0.16、他軸0を52回ずつ変更、その後は実取得した中立を送信した。
これは実Gamepad・参加者の入力ではない。欠測の中立補完やreceiptの付け直しはない。

専用profileの所有Edge、実service/private WebSocket、native MuJoCo worker、production buildを使った。
既存browser/CDPへ接続していない。benchmark中にtest/buildを並行実行していない。
EdgeのGPUはIntel UHD Graphics 630 / ANGLE D3D11、Python 3.14.3、MuJoCo 3.9.0、websockets 16.0。
frontendは同一package-lock、Vite 7.3.5。比較baselineはmain `2fd4df8638df929f13bce731961494094f163bd3`のread-only source。
既存baseline buildを同じlockで再buildし、全13 fileのbyte SHA-256が一致した。
baseline sourceをmainのGit blobと照合した443 fileには112件のCRLF/LF差があり、改行正規化後は全件一致した。
このためbaseline checkout全体のbyte一致は主張せず、実際に使用したbuildのbyte一致と区別する。
候補は`c559e4839877113223f076325cf8ab16f0552e5e`からの本差分。raw manifestでsource/build identityを保持する。

## 先行実測結果（3.12受入へ流用しない）

| 指標 | baseline | 最終候補 | 初期目標 |
| --- | --- | --- | --- |
| runningの約10秒窓RTF範囲 | 0.99846–1.00153（98窓） | 0.99782–1.00085（99窓） | 0.98–1.02 |
| Singleの入力変更→対応描画提出p95 | 48.5 ms | 49.6 ms | 80 ms |
| Assistの入力変更→対応描画提出p95 | 47.1 ms | 49.7 ms | 80 ms |
| hot path p95 / p99 | 未計測 | 3.75 / 4.03 ms | 8 / 12 ms |
| receipt→apply p99 | 未計測 | 21.02 ms | 50 ms |
| STOP→保存確定・busy解除p95 | 未計測 | 33.6 ms（20回） | 100 ms |
| 通常試行の実commit時間 | 60.0167 s | 60.0167 s | Task 60 s |
| 同条件の明示retry | 20/20 | 20/20 | 20回 |

renderは各layoutのwarm-up 4件を除いた48件で、対応sequenceのCPU draw submissionまでをbrowser時計内で測った。
GPU完了や実device遅延ではない。RTFはstatus受信のbrowser時計内のwall deltaと実commit simulation deltaから求めた。
候補はhost `perf_counter_ns`の10秒窓RTFも公開する。host/browser時計間の引算はしていない。
hot/apply分位は終端前の最大600件の有界sampleから算出した。別threadの送信・保存CPUを含む全process CPUではない。
Task終端は`task_success`だが、Task evidenceは「観測窓完了、contact successではない」、`observed_pairs=[]`。
この系列は接触へ到達しておらず、接触操作・押し込み負荷での等速性を受け入れた証拠にはならない。

## 解釈と限界

この実測で双方が初期目標を満たし、候補の構造変更と20 retryの成立を確認した。
baselineでも元のinput-wait timeout/0.906秒stale/低RTFは再現しなかった。
候補の優位や元障害の恒久解消を、この比較から断定しない。実Gamepad、元発生操作系列、接触系列は未受入。

350 msの表示送信停滞を実worker WebSocketへ注入しても通常入力時は60 tick超をcommitし、
最新sequenceを消費してoperator_abortを記録した。本当の350 ms入力停止はtechnical_invalid/staleを維持した。
STOP slot、FIFO overflow、sender失敗/close、遅い開始記録後の入力待ち、終端記録失敗とretry禁止はfocused regressionで検査した。
senderは専用threadであり、shared-memory表示processへの完全分離とGIL/serialization競合の除去は未実装。
開始記録はrunning前の同期gate、終端記録は非同期finalizingであり、記録を省略して性能値を得ていない。

再現入口は`scripts/viewer/measure_workbench_latency.py`の位置引数
`<source root> <dist root> <label> <evidence root> <temporary root> <Edge executable>`と
`--condition <condition JSON> --profile dynamic-cube-push --normal-seconds 60 --retries 20`。
現在のharnessは`--source-revision <測定sourceの完全SHA>`も必須とする。
raw evidence、固定build、source/diff/検証manifestはユーザー指定のtask evidence rootへ保持し、
実行環境・cache・browser profileは完了時に除去する。独立review/push/PRは親taskの担当で、本taskでは未実行。

## 独立checkpoint後の置換（Python 3.12.13）

先行sender/readerが共有したsync WSでは、sendall中のprotocol mutexが受信を止めることが確認された。
本番は単一接続の公開async APIと専用IO threadへ置換した。実socketをbackpressure状態にしても、
入力受信とnative進行が続き、表示送信完了前にSTOPの`operator_abort`記録が成立する回帰を追加した。
ready/terminal frameは必須FIFO、通常表示は最新slotとし、status→対応frameの順を保つ。
保存threadは回収可能なspawn processへ置換し、Start gateでreadyを確認する。
記録期限後はprocessを回収し、期限内にownerが採用したpendingだけを完了markerへ公開する。
通常Task終端のstorage hang、close、startup hang、親finalizing期限を検査した。

環境はPython 3.12.13、MuJoCo 3.9.0、websockets 16.0、同じlock/build方式である。
`monotonic`はGetTickCount64（分解能0.015625 s）、`perf_counter`はQPC（分解能0.0000001 s）。
receipt/freshnessは前者、周期deadline/tick所要時間/backend RTFは後者を用い、clock domain間の減算は行わない。
専用環境の実行file、base executable、import source、lock SHAはraw `environment312.json`に記録した。
添付条件のraw SHAと物理条件は先行測定から不変で、既存3.12環境とbaselineは変更していない。

通常系列の製品sourceは`d027cf3edb3e1fe7a4ad0ca60fb49396363ebdca`。
baselineは同じmain、同じ固定production build、新品profile、合成±0.16→中立系列で比較した。
backend RTFを持たないbaselineとの共通比較は、browser内status受信時刻の差と実commit simulation差を使う。
candidateのhost実行RTFは別列の観測で、backend RTFをbrowser補間から作っていない。

| Python 3.12 通常系列の指標 | baseline | candidate |
| --- | --- | --- |
| 共通status観測の約10秒RTF（重複する101窓） | 0.99503–1.00075 | 0.99105–0.99932 |
| host実行RTF（約10秒、101窓） | 未計測 | 0.99078–0.99880 |
| Single / Assist 描画提出p95（各48件） | 52.6 / 54.5 ms | 47.9 / 49.1 ms |
| hot path p95 / p99（末尾600件） | 未計測 | 4.30 / 4.78 ms |
| receipt→apply p99（末尾600件） | 未計測 | 16 ms（粗いhost時計の標本） |
| deadline lag p95 / p99（末尾600件） | 未計測 | 14.12 / 14.97 ms |
| STOP→保存確定・busy解除p95（20件） | 22.5 ms | 49.2 ms |
| 実commit simulation時間 / retry | 60.0167 s / 20成功 | 60.0167 s / 20成功 |

末尾600件を60秒全体のp95/p99とは呼ばない。描画48件/layout、STOP20件、RTF101窓は別標本である。
全retryのnative build数は1、毎回新epoch/中立sequence 0から実commitし、記録完了と停止応答を確認した。
通常系列は`observed_pairs=[]`で、元のtimeout/stale/低RTFはbaselineでも再現しなかった。
0.5→1への改善や元障害の根本原因確定は主張しない。

初回3.12候補`9ee33f7`のSTOP p95は802.1 msで目標未達だった。
終端時のspawnをStart gateへ移した`271f73a`では57.8 msとなり、終了/再接続修正後の上表では49.2 msだった。
これらを履歴rawとして残し、数値を上書きしていない。Startの同期記録とprocess ready待ちは残る。

### 通常初期状態からの片腕push系列

同じ添付条件のまま、描画測定後にraw axes `[0,-0.7,0,0]`を3秒、以後は中立へ戻す明示合成系列を追加した。
他軸とtrigger/buttonは0、conditionの`output_side=left`を維持した。contact checkpoint、qpos直接設定、物体移動による接触初期化は行っていない。
この追加系列は通常±0.16→中立のbaseline比較と別試行であり、beforeの接触性能比較ではない。

source `75aa1258a326b3714ffa548e6f21ed41faa95397`は通常測定の製品code/buildと同一で、差は接触raw収集と正本文書だけである。
60.0167 sの実commitと20 retryが成立し、Taskの`observed_pairs=[["left","cube"]]`を確認した。
最初のtool接触表示はsimulation 11.3667 s、tool接触のraw frameを先頭128件に有界収集した。
cube最終位置は`[0.467619,0.566352,0.459892]` m（初期`[0.36,0.56,0.461]` m）で、x方向に約0.1076 m移動した。
この128 frame内のright joint targetの変化幅は全jointで0 rad、raw tool接触力の最大normは約1.3204 Nだった。
これはnative simulation診断で、全試行の最大力、force課題達成、実機force受入ではない。
Taskは引き続き観測窓完了をsuccessとし、`force_evaluated=false`で、押し込み成功を判定していない。

host実行RTFは0.99116–0.99724（101窓）、描画提出p95は48.2/48.6 ms（各48件）、STOP p95は40.0 ms（20件）。
hot path末尾600件のp95/p99は4.47/4.98 ms、receipt→apply末尾600件のp99は粗いhost時計で16 msだった。
接触収集の初回は支持面接触で先頭128件が埋まったため、tool接触を収集するfilterへ修正して再測定した。
初回のTask接触・cube移動結果と支持面rawも保存し、失敗した収集範囲を隠していない。

再現は前述の位置引数に`--source-revision <完全SHA>`を加え、通常系列は同じoption、追加接触系列だけ`--synthetic-push`を指定する。
raw rootの`latency-before312-60`、`latency-candidate312-reviewed`、`latency-candidate312-push-raw`と各statistics JSONを対応させる。
最終HEADへのsource/build同一性、condition raw SHA、環境、残存成果物はcontinuation report/manifestで束縛する。
shared-memory snapshot、GIL/JSON CPU競合の完全除去、Start記録と終端marker公開を含む完全async storageは未実装である。
全件CI、独立最終review、push、新Draft PRは親taskへ引き継ぐ。実Gamepad・参加者・実機・元発生操作系列は未受入である。


## 最終境界補修後の統合確認（3ba2320）

製品sourceは`3ba23203aeb0a0d0cf7dc591dbe74cc635384a75`、Python 3.12.13、前節と同じ添付条件・固定build方式・所有Edgeである。
保存の結果取得/回収後の期限再確認と、中継Peerのrequired FIFO・STOP時frame失効を補修した。
対象4回帰は補修前red、補修後runtime/architectureの276件が成功した。独立のread-only補完レビューで前回P1/P2の閉鎖を確認し、補完範囲に新規findingはなかった。
操作状態帯のRTFはbackend値を既存status周期で表示し、終了時・未計測を区別する。

同じ通常遅延測定の後、左軸raw `[0,-0.7,0,0]` を3秒、その後中立とする合成pushを実行した。
60.0167秒の実commitと20回の明示retryが成立し、hostの約10秒RTFは0.99417–0.99839（101窓）だった。
left–cube接触を128 frameに有界収集し、最終cube位置は `(0.46721, 0.56650, 0.45989)` m。
初期x=0.360 mから約0.1072 m移動した。Taskのsuccessは観測窓完了であり、力評価・押し成功・実機受入ではない。

| 同Python 3.12・固定buildの指標 | main通常系列 | 3ba2320（push前の同じ遅延系列） |
| --- | --- | --- |
| Single 描画提出中央値 / p95、48件 | 38.0 / 52.6 ms | 48.8 / 49.9 ms |
| Assist 描画提出中央値 / p95、48件 | 35.7 / 54.5 ms | 41.1 / 49.8 ms |
| renderer主thread CPU時間 / wall、Single | 25.18% | 12.81% |
| 同、Assist | 24.89% | 15.73% |
| STOP→保存確定p95、20回 | 22.5 ms | 48.3 ms |

CPUはrenderer主threadだけで、worker・GPU・全processの削減率ではない。中央値の遅延はこの一回比較では改善しておらず、
通常時の保存確定はmainより遅い。全指標の高速化を主張しない。候補の目的は表示待ちによる入力停止の分離、保存hangの有限回収、CPU負荷の削減を同時に成立させることにある。
元の半速化・0.906秒staleはbaselineでも未再現で、恒久解消の証明には使わない。
rawは`latency-final312-3ba2320/browser-validation.json`と同試行のterminal/final-state、親集計`parent-final-summary.json`に保持した。
主な検証fixtureと再現入口はrepository内、機械依存のraw/build/環境は既存task evidence rootに保持し、製品sourceへ混ぜない。


## 2026-10-05: 利用者の等速性受入未達と受信待ちの検証

利用者報告はRTF 0.607（最後の10秒窓）、直前advance 22.16ms、3601 ticks、Sim約60秒に手元約90秒である。
無操作でも約16秒遅れる。これらは利用者が貼付した診断/観察値であり、こちらの機械での新たな再現値ではない。
最終Task観測はleft–cube接触を含むが、Taskのsuccessは観測窓完了であり、等速性受入を意味しない。

baselineはPR615の`6f4913a132dc7737a8715a34fba908733972385a`。LLM-01/Python3.12.13、同じ添付条件、
実private WebSocket/native workerへ接続した合成中立入力を60/30/20Hzで各約8秒供給した。
各最後のhost RTFは0.99927/1.00114/0.99947、operator_abort、最大receipt gapは31/47/63msだった。
source cadenceの切り分け用であり、通常browser60Hzの代替受入・元発生環境の再現・接触操作の再現ではない。
物理刻みと鮮度上限を維持した。rawはtask evidenceのdeadline-before/report.jsonに保持する。

別の決定的回帰では実workerに22ms/advanceの仮想経過時間を与え、周期超過後にも4回すべてtimeout=0.001が
指定されることを変更前に検出した（5commit自体は成立）。候補は期限到来後timeout=0、inactive20msへ変更する。
この回帰は待ち要求の欠陥を証明するもので、実PCの改善率やOSの待ち精度は証明しない。
新しい区間別分布はwall経過時間・区間ごとの末尾標本であり、CPU時間や60秒全体の分位点ではない。
実操作受入は引き続き未達として扱い、UI表示時刻、物理dt、鮮度上限を変えて帳尻を合わせない。

## 2026-10-05: advanceと外側入力のCPU仕事量の局所削減

新しい利用者報告は各600標本で、receive waitの中央値/p95/最大が0.02/0.07/0.50ms、
outer inputが3.46/7.86/12.55ms、advanceが20.79/26.32/34.25ms、projectionが0.16/0.36/1.39ms、
statusが3.07/4.79/7.12msだった。control周期は16.67ms。利用者環境はPython 3.12.12、
NumPy 2.4.6、MuJoCo 3.9.0、Windows 11、logical CPU 8、tracemalloc無効と報告されている。
最終raw axesは`[.5207,0,1,.1808]`であり、このrunを無操作とは断定しない。
最後のtool接触がなくてもtable接触4件があり、接触観測のCPU仕事は残る。
これらの区間値はwall時間で、CPU時間やsource clockとは別である。

baselineはPR615の`907780aee3626ccfd84fe723f0483c6e444f5324`を`git archive`で固定した。
mainや旧PRへ切り替えず、専用archiveを別sourceとして読んだ。候補は同HEADから本節の製品5file差分。
archive SHA-256は`7e0cba6e93788d0a055ba7da5448c05555bab4368b6ea09e74c57187160d3688`、
共通benchmark SHA-256は`9021dc0c19bd0b556d1b0580eced4773c04566d8e3a2dcef429c3f32aa2d4aff`。
測定前後の全5fileのbyte hash、import source、lock hashは`comparison-summary.json`と各runの`measurements.json`に保存した。

製品変更は3箇所の処理に限定した。
runnerのstrict wire検証後は同じ`ViewerControlMessage`をprivate typed経路へ渡し、Executionの再JSON parseを除去した。
既存publisherのwire入口とSource/Mapping境界の検証は維持した。
毎substepの関節限界検査は、全native配列のfinite検査後にliveの限界を直接照合し、
違反DTO用の二重tuple/float変換とgeneratorを減らした。native配列と設定は呼出内のlocal参照に限り、
substep間ではcacheしない。接触frameは`rtol=0, atol=1e-8`の全要素比較を直接行い、
NaN/Inf・右手系拒否を維持して汎用`allclose`の仕事を除いた。world変換、力符号、単位は不変である。
declaration/digest/metadataのcache、別IK、`mj_step(nstep)`、物理条件・gain・鮮度変更は採用していない。

指定venv executableの実体はLLM-01のPython **3.12.13**、NumPy 2.4.6、MuJoCo 3.9.0、
Windows 11 build 26200、logical CPU 8だった。既存環境を変更せず、OpenBLAS/OMP thread設定も追加していない。
同じ添付条件のraw SHA、control 1/60 s、physics 1/600 s、solver/iterations/gain、age 0.2 sを維持した。
benchmarkにはprofiler/tracemallocを使わず、全取得入力を順次消費して毎tickのTask観測・記録を維持した。
receiptとsource timestampは決定的な合成clockの別fieldで、計測には`perf_counter_ns`と`process_time_ns`を使った。
実device、WebSocket、browser、status準備を含む全外側loopや実時間pacingは測っていない。

中立と両腕非ゼロの有界系列を各341入力/340commitとし、最初の41入力を除いた300標本を採った。
各系列のsimulation時間は約5.667秒で、60秒全試行の受入測定ではない。支持面接触は初期0件から4件となり、
これらの系列でtool接触には到達していない。二側tool接触は後述の別checkpointで比較した。
非ゼロはraw axes`[±.25,0,±.25,0]`を25入力ごとに反転し、先頭のfresh neutralから開始した。
同じbenchmarkをbefore→after→before→afterで順次実行し、他のtest/buildは同時実行していない。
下表は最終source hash付き`before-3 / after-3 / before-4 / after-4`の2反復、各600標本のwall中央値/p95。
先行の反復1/2は別rawとして保持し、最終表へ混ぜていない。

| 合成系列・区間 | before 中央値 / p95 (ms) | after 中央値 / p95 (ms) |
| --- | --- | --- |
| 中立 ingest | 0.388 / 0.428 | 0.343 / 0.387 |
| 中立 advance（Task・同一sampleを含む） | 2.416 / 2.613 | 2.071 / 2.310 |
| 中立 committed projection取得・payload/JSON | 0.240 / 0.255 | 0.238 / 0.257 |
| 両腕非ゼロ ingest | 0.392 / 0.443 | 0.348 / 0.405 |
| 両腕非ゼロ advance（Task・同一sampleを含む） | 2.768 / 3.057 | 2.447 / 2.792 |
| 両腕非ゼロ committed projection取得・payload/JSON | 0.245 / 0.262 | 0.245 / 0.268 |

Windowsのprocess CPU計測は約15.625ms刻みで、単一tickの中央値0をCPU仕事量0とは扱わない。
上記3区間のCPU積算値は中立1828.125→1546.875ms、非ゼロ2093.750→1890.625ms（各600標本）だった。
中立の反復別は875.000→750.000ms / 953.125→796.875ms、非ゼロは1031.250→968.750ms /
1062.500→921.875ms。これは計測対象のowner process区間だけであり、全process・全試行のCPU削減率ではない。
粗いCPU時計の区間間配分や小さい差を厳密な区間別比率に読み替えない。projectionのp95は改善していない。

既存`pinch-checkpoint.json`も別比較に使用した。100mm cubeの二側接触、`dynamic-cube-drop`、
毎回同じqpos/ctrlからの独立中立tickであり、正式条件の初期状態や元操作の再現ではない。
20 warmup後200標本×2反復、観測接触数は各2件。prepare+commitの中央値/p95は
1.175/1.215→0.978/1.040ms、native sampleは0.675/0.744→0.614/0.720ms、
両区間のCPU積算は734.375→656.250ms（400標本）だった。

最終4runの全採取値を比較し、epoch UUIDだけを固定文字列へ正規化したpayload/metadata、
qpos/qvel/ctrl/time、Task state/evidence、入力・trigger離散状態、qacc/warmstart/actuator/constraint force、
接触幾何とdynamicsが全て一致した。正式条件の各系列は341frame、独立pinchは220frameで照合した。
同じwire・receiptによるmalformed、unknown field、NaN軸、stale/disconnect、provider/session、重複・逆順、
pre-trial/future/stale receiptの12拒否ケースもerror literal・停止状態・入力stateが一致した。
対応回帰はbaselineの二重parseをredで検出し、候補のtyped同一object受渡しでgreenとなった。
各substepのfinite/warning/関節限界/速度/tracking error、inclusive境界とlive設定、
live/candidate改変検知、STOP、FIFO順次消費・鮮度、contact consumerを含む関連632件が成功した。
NaN/Inf拒否試験で既存world変換が出すRuntimeWarning 2件は記録し、testをskip・弱体化していない。

rawと再現scriptはユーザー指定evidenceの`benchmark_hotpath.py`、`rejection_probe.py`、
`comparison-summary.json`、`before-3 / after-3 / before-4 / after-4`に保持する。
再現入口は指定Pythonで`benchmark_hotpath.py <pinned source root> <new output directory>`を呼び、
`PYTHONPATH`へ各sourceの`src`と内包coreの`src`を明示する。bytecode/cacheはtask temporary配下へ隔離する。
本節はLLM-01でPython検証・parse・接触観測の実仕事を削減できた証拠である。
利用者端末のadvance 20.79msやRTF 0.607の解消、全外側input/statusの削減、実device/participant受入は未確認。
対象製品5fileの独立read-onlyレビューで確認範囲にP0/P1/P2の指摘はなかった。レビュー前後と測定対象のbyte hashは一致した。
全件CIと利用者端末での等速性受入は別に確認し、限定比較の成功で代替しない。

## 2026-10-05: 両腕操作の追加報告と最適化候補の不採用

利用者は製品版`c825976`でかなり改善し、片腕操作はRTF 0.9以上、両腕同時操作では0.5台へ低下すると報告した。
貼付された最後の10秒窓はRTF 0.763、advanceの末尾600標本は中央値18.11ms、p95 21.82ms、最大30.22ms。
外側入力は2.43/5.76/9.43ms、受信待ちは0.02/0.08/0.57msだった。
この表は片腕・両腕の混合区間であり、最後のaxesが0でも右sign/triggerが押されているため中立とは扱わない。
端末はPython3.12.12、NumPy2.4.6、MuJoCo3.9.0、Win11 build26200、logical8と報告されている。
接続できた検証端末は別のLLM-01（i7-9700K、Python3.12.13）。利用者端末のCPU型番と関数別profileは未確認。

基準は`c8259761f6b04bfceb7802c0cf06f5f29e8b67ec`、添付条件raw SHAは
`445a954184604c4735e8755302a65d72912b847e35682465a1755eff185f5b10`を維持した。
物理刻み、制御周期、solver/gain、Task、入力順序・元receipt・鮮度上限0.2秒を変更していない。

### 有限差分FK共有候補

中立・左のみ・右のみ・両腕をXY/Z別に分け、Mapping後の非ゼロ腕数で入力系列を確認した。
基準の単体advance中央値は片腕約2.24ms、両腕約2.46msで、利用者の急落は再現しなかった。
同じbase FKの再利用、solver寿命の整理、独立kinematic chainの有限差分FK共有を実装・比較した。
出力を維持できた候補でも通常時の短縮は小さく、同processのJSON loopback負荷下では安定した改善を確認できなかった。
このloopbackは最大throughputの探索負荷で、利用者の通常負荷や厳密な同一仕事量の旧新比較ではない。
複雑性を増やす根拠として不十分と判断し、この候補は採用しなかった。`fk-candidate-3.patch`へ残し製品差分は戻した。
analytic Jacobianへの切替、有限差分epsilon/damping変更、physicsの省略は行わなかった。

### 表示JSONの別process化候補

既存の通常frame最新slot、required/control FIFOと送信順序を維持し、JSON生成だけを親専用socketにつながる
単一spawn childへ移す実装を作成した。入力readerとphysics ownerはencoderの応答を待たない構成とした。
公開peerからpickleを受け取る経路は設けず、physics/Taskの別worldも作らなかった。
JSON文字列の一致、不正値、startup/response/partial-I/O/hang、STOP・receipt、親終了を対象に検証した。
独立レビューでProcess.closeとcheckの競合により元障害を隠すP2が1件見つかり、同じlock下の回収開始通知へ補修した。
補完read-onlyレビューでは当該P2の静的閉鎖を確認した。これは性能の改善や実Gamepad受入の認定ではない。

公平な比較は所有loopbackの実worker/native MuJoCoへ、同じ60Hz・同じ241個のraw入力を送って行った。
XYとZ各約4秒を同じworkerで明示Start/STOP/retryし、before/afterを交互に2反復した。
Zはtrigger releaseを挟んだ符号変更を実装し、Mapping後の0/2腕とZ符号-1/0/+1を全runで確認した。
`worker-before-4 / worker-after-4 / worker-before-5 / worker-after-5`だけを以下の集計へ使った。
入力hash、bytes、頻度、benchmark hashが一致し、各runは正常終了・cleanupを確認した。

| 両腕系列 | before advance 中央値 / p95 (ms) | candidate 中央値 / p95 (ms) | before owner CPU積算 (ms) | candidate owner + encoder CPU概算 (ms) |
| --- | --- | --- | --- | --- |
| XY | 2.808 / 3.000 | 2.862 / 3.038 | 1984.375 | 2203.125 |
| Z | 2.837 / 3.107 | 2.789 / 2.977 | 2031.250 | 2281.250 |

CPU値は各系列2反復の合計。encoder CPUはactive区間近傍の標本差による概算で、別peer・GPU・機械全体は含まない。
Windowsのprocess CPU時計は粗いため、単一advanceのCPU中央値0を仕事量0とは扱わない。
XY/Zの親子CPU概算はそれぞれ約11%/12%増加した。startupはsender threadのみ約0.17msに対し、child版約140/145msだった。
受信側で観測したrunning frameは両方式とも約59.6frame/s、receiptからpeer受信までの中央値は約16ms、p95は約31ms。
これはCPU描画提出や画面の発光までの遅延ではない。RTFは各約4秒のactive窓でbefore/candidateとも約0.98～1.00だった。
短期の合成試験であり、利用者の持続両腕操作・60秒実時間比・実Gamepad・実機・参加者受入ではない。

### 採否と残る確認

通常負荷で一貫した短縮がなくCPU総量と起動costが増えたため、serializerの別process化も今回の既定経路へは採用しない。
補修済み候補と追加testはevidenceの`serializer-candidate`とpatchへ保存し、製品sourceとtestsは基準へ戻した。
製品を軽量化した新commitではない。実操作で改善が確認された`c825976`の製品codeを維持し、
最新報告を既存のLLM-01のgreenで否定しない。main、実験環境、依存lock、Viewerは変更していない。

探索途中のworker同期/終了の不成立、固定projection負荷harnessの未完了、親からの中断は独立に保存し、成功比較へ混ぜていない。
固定600Hz重負荷の効果を今回の採否根拠には用いない。研究用の等速性は引き続き未受入である。
次に必要なのは、利用者端末で片腕/両腕の同じ入力系列を使い、制御計算のCPU実行と実行待ちを区別する計測である。
別機械の短期平均からGIL、MuJoCo、OpenBLAS、CPU機種のいずれかを原因と断定しない。
raw、再現script、候補patch、reviewはtask evidence `xpll-bimanual-20261005`に保持する。


## 2026-10-06: 実験端末での観測処理再構成と受入未達

実験端末MIYU-LAPTOP-1（i7-1160G7、Python3.12.12、NumPy2.4.6、MuJoCo3.9.0）で追加測定した。
基準はeceeac1（製品c825976と同一）、改修製品は4fe6d0a。元checkout・結果・固定build・OS電源設定・依存を
変更せず、専用source copyと新規profileのEdge、所有loopback service/workerを使った。
条件raw SHA256は445a954184604c4735e8755302a65d72912b847e35682465a1755eff185f5b10で従来と同一。
control 1/60秒、physics 1/600秒、solver/gain、Mapping、Task、入力age0.2秒、各substep検査は不変。

### 実装の境界

固定Viewer宣言のdigest/resourceとnative body/site/geom/joint bindingをmodel寿命のimmutable planとして準備する。
位置・速度・ctrl・contact・force・gravity・timeは同じlocked native dataから読み、前のtickの値をcacheしない。
ModelExecutionの重複metadata生成、geometryの再帰asdict、検証済み表示のJSON encode/decodeを減らし、
外へ返すnested containerは入力・Task・内部表示から分離した。MuJoCoStateに存在しないpost-init検査を
仮定したschemaの置換は行っていない。詳細の正本はruntime-compositionの観測節。

独立レビューで負geom IDの末尾index誤結合P1と、返却intentによる内部trigger表示の改変P2が見つかった。
4fe6d0aで両IDの範囲を名前解決と同じ意味で検査し、単腕intentと内部presentationの所有を分離した。
追加8caseは補修前8failed、補修後の関連183caseは成功。補完独立reviewで両findingの静的閉鎖を確認した。

### 固定buildを含む両腕の交互比較

同じ既存browser harnessから測定対象だけをAssist/両腕1試行へ固定し、35回×300msの同じXY波形を使った。
source取得・入力・描画の仕組みと期限は変更していない。4fe6d0aの候補と基準をbefore/after交互に2反復。
各runは開始、running継続、明示STOP、保存後terminalを確認して終了した。表はbackend末尾約10秒窓である。

| 反復 | before RTF | after RTF | before advance p50/p95 ms | after advance p50/p95 ms |
| --- | --- | --- | --- | --- |
| 1 | 0.68164 | 0.77745 | 17.287 / 31.275 | 17.886 / 22.348 |
| 2 | 0.71987 | 0.77088 | 19.525 / 23.987 | 18.054 / 22.169 |

両反復のRTFとp95は改善したが、中央値は反復1では短縮していない。少数の短期合成比較であり、
全状態の一様な高速化、PC全体のCPU削減、60秒試行の等速性、元Gamepad操作・実機・参加者受入は認定しない。
目標RTF0.98を満たさず、実験採用の未達は維持する。とくにCPU待ちとbrowserを含む競合は残る。

先行8f9b66e候補の4条件連続runはSingle左/両RTF0.867/0.786、Assist左/両0.785/0.751で完走した。
同手順の基準runでは保存child起動期限超過を初期Startと別runの4回目Startで観測した。
後者の完了した3区間だけは部分結果として保持し、4条件完走とは呼ばない。この開始失敗は今回未修正。
実行threadの切替間隔を新規診断workerだけ1msにした別試験も行ったが、効果が一貫せずAssist両腕は
RTF0.591だったため採用しない。製品やユーザー環境のthread/OS設定にこの試行を残していない。

### 正しさの検証と証拠

LLM-01で最終製品の902観測（中立341、両腕341、独立pinch220）のqpos/qvel/ctrl/time、Task、
全payload/metadata、接触・反力・integration配列が基準と一致した。epoch UUIDだけを正規化した。
不正入力12条件の拒否内容・停止状態も一致。laptopの別のnative比較でも中立・左右単腕・両腕の
XY/Z全7系列、各181状態のqpos/qvel/ctrl/time/入力endpointがbyte一致した。
単体の処理時間は端末内でも開始直後と持続実行で大きく変わるため、一つの高速区間だけを採らない。

primaryの最終対象検証は461件および193件、独立review補修後の対象183件はいずれも成功した。
集合の重複を合算した件数を全件成功として示さない。最終全件CIはPRでcommitに対応付ける。
rawとscriptは両端末のtask evidence xpll-observation-20261006へ保存し、laptopのfinal-laptop-comparison.jsonに
source/script/conditionと成功・部分失敗の対応を残した。動作中の既存実験appへ入力していない。
検証scratchの削除は実行審査に拒否されたため残存manifestを保持し、迂回削除はしない。


## 2026-10-06: 持続接触の修正計画とViewer描画専用更新

### 修正計画と保持する境界

利用者のAC接続下の反復観察は、無操作約0.98、腕だけの60秒運動約0.975、単発の持上げ・落下後の非接触運動約0.972に対し、双腕の接触保持では低下するというものだった。接触履歴だけや腕数だけでなく、持続接触中の反復処理を対象にする。操作時の終盤RTF約0.742は未解決として維持する。

1. Viewerが受信qposに対して毎回行っていた前向き動力学を、描画値だけの更新に置き換える。body/site/geom、camera/light、tendon/flexの同等性と、backend由来の接触・反力表示を保つ。
2. AC接続を確認したMIYU-LAPTOP-1で、同じ保持状態・表示・条件による60 Sim秒の比較を行う。瞬間RTF、末尾10秒窓、全体の経過時間を分ける。原試行の初期状態からの実操作と、保存状態からの診断的継続を混同しない。
3. 修正後も持続接触で低下する場合、native solverの実反復数・接触観測・実行待ちを比較して次の変更を選ぶ。短いWASM計算の倍率だけでアプリ全体の原因を認定しない。

基準は`80c4784ce0388b41857f758df970b4b70f178759`、製品修正は`09716fd9eb9400b7768e30bd85f9adb6f2276ccf`。backend、物理刻み1/600秒、制御1/60秒、Newtonの反復上限50・許容値1e-10、Mapping、鮮度0.2秒、Task60秒は変更していない。Viewerは同じMuJoCo 3.9.0の`mj_fwdKinematics`で運動学・COM・camera/light・flex・tendonを更新する。衝突・制約・反力・sensorの再計算をしない。Three.jsの独自FKとfull-forwardへの暗黙fallbackは作らない。

### 正しさの検証

実installed WASMの7回帰で、非接触・片側接触・両側接触の保持と解放、自由物体の並進/回転、camera/light、tendon/flex/mesh、FastArmのhomeと公開30姿勢、fixture model切替を確認した。描画に使うnative配列は57姿勢でbyte一致、mjvSceneのgeomとThree.js行列も一致した。qvel/ctrl/timeを進めず、candidateのcontact/constraint/solver統計は未実行のままである。これはbrowser全描画のpixel一致や元Gamepad系列の再現を意味しない。

Viewerの未計算値から作る接触・制約・sensor等の装飾は無効化し、接触・反力はbackend overlayのまま維持する。新旧比較は同じ描画用optionを使う。旧defaultとの差分のrangefinder表示は、既存FastArm/公開fixtureにsensorがないことを独立reviewでも確認した。任意の追加sensorの描画互換性は今回の保証対象ではない。

Viewer31実行入口、typecheck、固定build、architecture48件、文書検査が成功。独立read-only reviewで追加P0/P1/P2指摘はない。APIのsingle WASMを実行し、MTは宣言のみ確認した。最終CIとsource対応はPRで記録する。

### 実操作で保存された姿勢による局所比較

実験端末の既存結果を読取り、同じmodel digestの38終端状態を別MjDataで復元して分類した。元結果は変更していない。model SHA256は`37de10998d14bfdaa422b54838003f596bfd9ea28d27ca9890cd740e101665e4`、条件raw SHA256は`445a954184604c4735e8755302a65d72912b847e35682465a1755eff185f5b10`。

非接触、単腕接触、3個の双腕接触状態について同じmodel/qposをNodeの実WASMへ与え、old/newを交互4pass、各50warmup後600回、計2400標本ずつ測った。描画に必要な14配列が完全一致した。双腕の3状態で旧pose更新中央値は0.0199/0.0176/0.0309ms、新経路は0.0022/0.0022/0.0050msだった。状態間の比較はJIT・端末状態の差を含むため行わない。browserの描画や入力、native workerのRTFを含まない局所値であり、数十µsの短縮を0.742の低RTFの原因確定に用いない。

通常providerの手先速度だけで初期状態から保持姿勢を作る最初の探索は、片側接触を作った後にcubeの横ずれで接触が外れ、25秒までに双腕保持へ到達しなかった。成功した保持検証には含めない。

### AC接続での保存済み双腕保持状態の60秒継続比較

既存実操作の終端状態（SHA256 `576387b808488710fd7803dd543d14001c1198c4cc6be04324f3bfc5eec78635`）を選んだ。名前解決で左右toolとcubeの有効な接触を確認した状態である。元checkoutと元結果は読取りだけとし、別source copyの診断hookにより、同じmodelのintegration stateを開始時に復元し時刻原点だけ0へ戻した。初期配置からのGamepad実操作の再演ではなく、保存状態からの診断的継続である。正式な実験結果や原試行の再現に昇格しない。

旧版・候補のbackendは同一で、復元hookのbytesも同一。両方とも新規の専用Edge、Assist、1440×900 CSS px、DPR1、合成中立Gamepad、固定buildで実行した。AC接続は両試行の前後で確認した。候補の依存は同一lockから隔離checkout内に準備し、ユーザーのnode_modules・固定build・電源設定を変更していない。

| 指標 | 旧80c4784 | 候補09716fd |
| --- | --- | --- |
| 実commit | 3601 tick / 60.0167 Sim秒 | 3601 tick / 60.0167 Sim秒 |
| 終了時の約10秒窓RTF | 0.96961 | 0.96793 |
| 約10秒窓の中央値 / 最小 | 0.96162 / 0.92807 | 0.97281 / 0.92567 |
| 終端で保持したadvance p50 / p95 | 9.421 / 14.106 ms | 9.413 / 13.627 ms |
| renderer主thread CPU積算 | 31.337 s | 28.711 s |
| running確認後からterminal確認まで | 62.591 s | 62.533 s |
| 双腕接触が有効だった採取標本 | 125 / 125 | 125 / 125 |

両試行のnative final-state.jsonはbyte一致し、SHA256は`925f12a5d2abb148a917e8cbcbcca1c0c392ad23f5304a9646a359a267ea2b0b`だった。接触標本は約0.5秒間隔であり、全tickでの外部採取を意味しない。各104個の約10秒窓は重複窓であり、独立104反復ではない。経過時間はボタン押下からの総所要時間ではない。renderer CPUはGPU・worker・PC全体を含まない。

一対の比較ではrendererの仕事量削減と描画・保存状態の維持を確認できたが、RTFの有意な改善は認定しない。旧版でも0.742への低下はこの保持状態では再現していない。ユーザーの実測を否定するものではなく、持続接触の幾何状態・押付け指令・到達履歴・通常画面条件などの差が残る。目標0.98への全区間適合と、元症状の解消は未達。

実験のscript・raw・元状態hashは各端末の`xpll-contact-render-20261006`に保持した。候補配布の一括scriptと自然保持系列の追加scriptは実行審査に拒否されたため適用しなかった。候補の準備は共有ディレクトリへのjunctionを作らず、独立checkoutと固定lockによる通常buildを使用した。実装担当の一時cache等の削除も拒否され、122個の残存pathのmanifestを保持している。main・実験checkoutの変更、merge、実機操作は行っていない。
