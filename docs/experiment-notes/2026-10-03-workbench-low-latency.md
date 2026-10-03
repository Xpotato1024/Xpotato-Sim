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
