---
status: supporting
owner: runtime
last_verified: 2026-10-02
canonical_for: []
related:
  - docs/contracts/workbench.md
  - docs/contracts/finite-trial-runtime.md
  - docs/contracts/coordinated-arm-runtime.md
---

# simulation操作性能の測定（#610）

## 条件と再現入口

基点は未mergeのPR #609、SHA `223de22f8b310e5b92d5aa3946d8093796031ff6`。
候補はその基点の`codex/610-performance`未commit差分であり、mainとの比較ではない。
Windows、既存venvのMuJoCo 3.9.0、既存lock/依存、bimanual FastArmを使用した。
physics dtは1/600秒、control dtは1/60秒、implicitfast/Newton、50 iterations、tolerance 1e-10。
gain、接触条件、関節制限、速度/tracking error gate、入力上限0.2秒は変更していない。

`dynamic-fixed-contact`の診断用boxを半寸法 `(0.05, 0.60, 0.05)` m、中心 `(0.36, 0, 0.46)` mへ
展開し、同一モデルでfree-space、左接触、左右接触を作った。homeから100tick、接触対象の腕に
world +X 0.06 m/sを要求して接触へ近づけ、測定中は中立または両腕+X 0.002 m/sを要求する。
一つのboxへ両腕を接触させるための大型fixtureであり、100 mm cubeの把持・持上げ・成功率を表さない。
fixed objectへの接触でもnative solver、servo、重力、力計測は実MuJoCoで実行している。

依頼指定の外部evidence rootには次の再開scriptを残した。絶対pathはhandoffを参照する。

- `run-measurements.py`: `git archive`で基点blobをtask tempへ展開し、候補と交互に別processで比較する。
- `measure.py`: native physics/copy/FK、IK、prepare/commit、snapshot、観測、serializationを分離測定する。
- `measure-input-age.py`: 実monotonic receipt、25 Hz入力、60 Hz有限TrialRunnerのageを測る。
- `trial-parity.py` / `compare-trial-parity.py`: 同じ通常Mapping入力からTask終端までを基点と比較する。
- `browser-check.py`: 固定production buildと合成Gamepadから通常protocolを通して実MuJoCoを操作する。
- `run-validation.py`: focused/related/architecture、Viewer tests・compile/typecheck/buildと文書検査。

stage測定は外部`perf_counter_ns`とcProfileを使った。MuJoCoのtimer callbackは設定せず、
未設定のnative timer 0を処理時間0とは扱わない。solver反復は実`solver_niter`を保存した。
最終性能比較はcProfile・stage wrapperなし、各scenario 240tick×3反復、実行順を交互にし、
各反復の平均/p50/p95/maxをJSONに残す。表は3反復の平均tickの中央値である。

## 支配要因と変更

基点の双腕接触profilingでは`mj_step` 1200回のnative累積時間は約25 ms、
全120tickは約604 msだった。各substepを含むPython `_check_data`累積は約224 ms、
観測sample累積は約191 msで、native接触solver単独が支配的という仮説は支持されなかった。
このprofiling値はwrapper/cProfile込みであり、通常実行の性能表とは分ける。

warning countの配列判定、NumPy reduction wrapperの削減、4関節のscalar速度/追従検査、
不変scene/settings digestの構築時解決、同generationのfrozen共同snapshot共有、
完全中立時のゼロ増分IK省略を実装した。検査項目・実行substep数・閾値は維持する。
candidateの完全`mj_copyData`、warmstart/equality/plugin等のintegration state、
各substepのnative計算、現在dataからのgeometry/force観測、Task更新は維持する。
partial state copyや計算済みでない物理値のcacheへの置換は行っていない。

## 観測結果

以下は最終候補のplain tick測定である。通常browserと反対面pinchの追加比較は後段と外部evidenceの`end-to-end-summary.json`に記録する。

| 入力 | scene | 基点平均 ms | 候補平均 ms | 短縮率 |
| --- | --- | ---: | ---: | ---: |
| 中立 | free-space | 2.764 | 1.445 | 47.7% |
| 中立 | 左接触 | 3.018 | 1.715 | 43.2% |
| 中立 | 左右接触 | 3.215 | 1.920 | 40.3% |
| 両腕+X 0.002 m/s | free-space | 2.708 | 1.799 | 33.6% |
| 両腕+X 0.002 m/s | 左接触 | 2.997 | 2.065 | 31.1% |
| 両腕+X 0.002 m/s | 左右接触 | 3.222 | 2.294 | 28.8% |

全比較tickのqpos/qvel、geometry、contact force、全integration state、
model/数値条件identityを照合し、差は0だった。接触endpoint集合も各scenarioで一致した。
この一致は測定した有限fixtureのparityであり、任意姿勢の完全証明ではない。
共通TrialRunnerの120tick/2秒Task比較でも、試行ごとに発行したepochだけを明示正規化し、
全frameの物理・接触・Task、condition digest、最終integration stateと終端を完全照合した。
両方で左右/cubeの観測を保持してtask_successとなった。これは観測window完了というTaskの意味で、
把持・接触成功・持上げの達成を表さない。

実時計の通常TrialRunnerでは480tickを実行し、入力age最大約47 ms、
Task＋表示観測込みのtick p95約3.98 ms、最大約18.69 ms、終端はsimulation_budgetだった。
この計測はWebSocket/browserを含まない。Windows monotonicの粒度以下のingress差は0として観測された。
CPU-only時点のChromium固定build検証では通常stickで100 mm fixed cubeへの実接触を発生させ、
poll p95約50.8 ms、最大約51.1 msを観測した。
trigger中のbumper変更、trigger解放後の符号更新、STOP/operator_abort保存、
明示retry後の旧ticket拒否、disconnect/technical_invalidを確認した。
今回の環境では元の200 ms超過を再現しておらず、実device操作や停止完全解消を主張しない。

## bounded backpressureの設計と今回の適用判断

**以下は未実装の設計候補であり、現transport仕様ではない。**
現行はservice pending 32、worker WebSocket受信high-water 8、worker単一threadである。
serviceのqueueが空でもsocket/worker bufferに旧receiptの入力が残り得る。
単なるsend完了ACKや1件in-flightだけではMappingがそのsampleを消費した証拠にならない。
`ViewerInputSource`は最新frameを保持し、trigger符号ラッチはtick時にMappingが更新するため、
worker ingestだけをACKすると次の入力がtick前に上書きする可能性がある。

具体案は、制御FIFO32と専用STOP slotを維持し、入力専用の有界遷移FIFOと
連続値slotを分離する。初期実装ではcoalesceせず全sampleをFIFOへ置く方が検証しやすい。
許容する最大pendingは4、worker側in-flightは1、ACKは元session/sequence/ticket/generationと
実receiptを含め、Mappingがsampleを一度消費したtick境界でのみ返す。
元receiptからのageは待機中も保持し、期限超過・overflowは明示technical_invalidで停止する。
旧commandの高速再生、receipt再付与、欠測中立補完、ACK timeout後の自動再送・再開を禁止する。

連続値coalesceを追加する場合は、全button pressed/value、triggerの中立境界と解放、
全体中立境界、source/provider/session/device identity、sequence単調性、
hidden/disconnect/stale/errorを遷移barrierとして扱う。同一barrier区間内のaxes/trigger量だけを
最新値へ置換し、最初と最後のsample、barrierを越えるsampleを消さない。
入力中のSTOPはACK待ちを越えて専用slotから監督し、generation変更時に未消費入力を失効する。
FIFO上限は遅延保証ではなく、4sampleが0.2秒内に消費できなければ安全停止するという上限である。

必要な受入は、遅いworker/描画停止、buffer内age超過、短いbutton edge、trigger解放直後の押下、
中立→移動、source/session/sequence変更、hidden/復帰、disconnect/error、STOP/旧ticketの
実protocol検証である。tick側のsample消費契約まで変更せずに安全なtransport置換を保証できないため、
今回はcoalesce/ACK導入を見送り、実測hotspotと入力取得・frame公開の待ち時間改善、具体設計を成果とする。
既存buffer滞留によるstaleの可能性は残る。実deviceの双腕cube操作で新しい原因別診断を取得し、
残る遅延がbuffer支配と確認された場合に、この設計と受入を同じscopeで実装する。

## correctnessと表示cadenceの修正

prepare後のlive qpos変更が共有snapshotに隠れるP1を修正した。commit guardはlive dataを
freshに読み、親の`parent-cache-probe.py`もcandidateでREJECTEDとなる。
candidate改変の回帰に加え、live qposを実際に変更する回帰を追加した。

CPU-onlyの通常Workbench計測ではSingle中央値68.9→68.2 ms、Assist74.6→70.2 msで、
core改善だけでは全体の待ちを十分減らせなかった。入力40 msとframe50 msのtimer待ちを
解消するため、live入力を約60Hz、worker公開をticket/phase/tick変化駆動にした。
ready/terminalの重複snapshot生成、無条件timerによるframe再送を止め、peer最新1frame slotは維持する。
表示sampleはforward済みstateを読むだけにし、generic snapshot APIは従来動作を維持する。
異なる表示回数で全integration state、warmstart、force、Task観測と終端が一致する回帰を追加した。

## 公開scriptによる再測定

主要な小さい測定scriptと100mm cube checkpointをrepositoryに収めた。
`measure_simulation_hot_path.py`はfree-space/片側/両腕の同条件stage計測とplain測定を担う。
依存更新・実機アクセスを行わず、CPU時間をCI合否の閾値にしない。
`ROOT`は比較対象の展開済みroot、`DIST`はその固定Viewer build、`EVIDENCE`は保持する成果物、
`TEMP_ROOT`は唯一のtask temp、`CHROMIUM`は固定版の実行ファイルを指定する。
Python実行前に`PYTHONDONTWRITEBYTECODE=1`、`PYTHONUTF8=1`を設定し、
`PYTHONPATH`へ`ROOT/src`と`ROOT/src/xpotato_sim/plugins/robots/fast_arm/core/src`を明示する。

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = "$ROOT/src;$ROOT/src/xpotato_sim/plugins/robots/fast_arm/core/src"
$env:TMP = $TEMP_ROOT
$env:TEMP = $TEMP_ROOT
# EVIDENCE/TEMP_ROOTは明示した絶対pathに事前作成する。
& $PY "$CANDIDATE/scripts/diagnostics/fast_arm/measure_simulation_hot_path.py" --output "$EVIDENCE/stages.json" --ticks 120
& $PY "$CANDIDATE/scripts/diagnostics/fast_arm/measure_simulation_hot_path.py" --output "$EVIDENCE/plain.json" --ticks 240 --velocity 0.002 --no-profile --plain
& $PY "$CANDIDATE/scripts/diagnostics/fast_arm/measure_pinch_checkpoint.py" $ROOT "$EVIDENCE/pinch-zero.json" 0
& $PY "$CANDIDATE/scripts/diagnostics/fast_arm/measure_pinch_checkpoint.py" $ROOT "$EVIDENCE/pinch-inward.json" 0.1
& $PY "$CANDIDATE/scripts/viewer/measure_workbench_latency.py" $ROOT $DIST $LABEL $EVIDENCE $TEMP_ROOT $CHROMIUM
```

pinchは20warmup+200回、毎回同じnative checkpointへ戻してcacheを測定外で失効する。
左右toolが実100mm cubeの反対面に接する初期状態で、0/0.1m/sの内向き速度を比較する。
ユーザーの連続軌跡・把持成功を示す試行ではない。reportは全qpos/qvel、integration state、
geometry/contact/force、native配列を含む。Web/backendは5396/8986を使用するため直列実行する。
Chromiumは今回専用の新規profileとOS選択CDP portを使い、`DevToolsActivePort`とbrowser endpointの一致を確認する。
既存のCDP endpointへ接続せず、所有を確認できたbrowserだけを終了する。以前の測定はCDP9386で実施した。
各layoutは4warmup+48sample、Single/Assistの描画量を維持し、通常Workbenchと実MuJoCoを通す。
CPU描画提出までの指標であり、GPU完了・入力to光子・実Gamepad遅延の保証ではない。

## 最終候補の追加測定結果

反対面pinchは親の同じscriptをそのまま実行した。追加の公開scriptでは観測fieldを拡げ、
qpos/qvel、全integration state、geometry/contact/force、qacc/warmstart/actuator/constraint forceが
baselineとcandidateで完全一致した。両速度とも2接触を保持した。

| 内向き速度 | baseline中央値 / p95 ms | candidate中央値 / p95 ms |
| --- | ---: | ---: |
| 0 m/s | 3.175 / 3.317 | 1.945 / 2.091 |
| 0.1 m/s | 3.133 / 3.323 | 2.265 / 2.437 |

固定Chromium 1228、Intel UHD Graphics 630 / ANGLE D3D11、1440×900の通常Workbenchで、
baseline→candidate→baseline→candidateを直列に測定した。各行は48sampleの中央値 / p95である。
baseline backendは223de blob展開、Viewerは同基点で有効な#608固定build、candidateは今回の固定build。
固定distの全file hashはevidenceの`end-to-end-summary.json`へ記録した。
同じ入力・model・dt・安全gateを使用し、測定中にtest/build/別benchmarkを並行実行しなかった。

| 回 | Single baseline ms | Single candidate ms | Assist baseline ms | Assist candidate ms |
| --- | ---: | ---: | ---: | ---: |
| 1 | 69.1 / 101.7 | 37.2 / 53.2 | 68.8 / 102.3 | 37.9 / 54.0 |
| 2 | 67.1 / 84.2 | 37.2 / 52.6 | 66.6 / 99.6 | 39.5 / 52.0 |

2回の中央値を平均するとSingle68.1→37.2 ms（45.4%短縮）、Assist67.7→38.7 ms（42.8%短縮）。
この値はCPU描画提出までであり、物理Gamepadの入力to光子や200ms問題の解消を表さない。

resourceはCDP `Performance.TaskDuration`差分とwall時間からrenderer主threadの1core相当を測った。
Singleはbaseline12.7–13.2%→candidate22.1–22.9%、Assist17.1%→23.7–24.4%へ増えた。
renderer JS heap使用量はbaseline約11.6–14.2MB、candidate約10.5–15.4MB。
表示量は維持したが公開回数が増えるtradeoffがある。backend、GPU、他Chromium process、host全体の
CPU/RSSはこの値に含まれず未計測で、全体CPU負荷低下は主張しない。
元のbuffer滞留リスクと実deviceで200msを越える原因は未確定のままである。

## 検証と主張範囲

今回の変更層でfocused/related/architecture、Viewer全30 test entry、compile/typecheck/buildを再検証した。
focused146件、related/architecture328件、後から追加したworker/Task回帰を含む70件がpass。
親live-cache probeはREJECTED、120tick Task比較は全物理/接触/Taskと終端が一致。
最終browserで実接触・trigger latch/解放・STOP保存・旧ticket拒否・disconnect停止を確認した。
既存STOP/old-ticket/hidden/disconnect/triggerの安全契約と元receipt・0.2秒gateは維持する。
独立レビューとcurrent-head CI、commit/push/PR作成は親担当で、この実装taskでは未実行である。
実Gamepad、participant、実機、serial、OSC、OS変更、依存更新は行っていない。
ユーザーの200ms超過原因は未確定であり、停止の完全解消を主張しない。

## #610 ordinary-input stale stopの追試と修復

起点は`46ed388dc1c4f1f0a94307dfa330aaf46e1475ad`、未commitのsource差分と
`tests/runtime/test_workbench_input_batch.py`を対象とする。実Gamepad、participant、実機I/Oは使わない。
元の利用者のprofile・実行環境・terminal記録は未取得であり、発生環境の再現ではない。

元sourceを別interpreter内で再構成した合成clock試験では、receipt 10.031秒のsampleを10.221秒に
ingestし、10.234秒にtickすると、queueに後続11sampleがある条件でも`age_s=0.203000`で
`technical_invalid`、0tickとなった。修正経路は全12sampleを順に消費し、最新receipt 10.218秒で
1tickだけ進めた。過去motionの追い付き再生、receipt再付与、gate延長は行っていない。

実MuJoCoと実worker WebSocketの回帰では、約60Hzで120sampleを送信し、advance前後へ
190/250/700msの停滞をそれぞれ注入した。受付loopとadvance入口で受信済みbatchを消費し、
6条件とも試行を継続してSTOPで`operator_abort`を保存した。途中のtrigger解除・中立、
disconnect/stale通知、旧schema、session変更、sequence再利用を捨てないことも検査した。
tick時の最新actual receiptが201ms古い無入力とwall budgetはstrictに停止する。receipt間隔単独の終了判定はreview修正で除去した。これらは模擬負荷とsoftware回帰である。

固定Viewer buildと新規所有Chromiumを通した追試では、合成Gamepadの通常wireから実MuJoCo接触を
確認した後、stickを500msごとに±0.15へ反転して30秒間継続した。trigger符号ラッチ・解除、
中立、STOP保存、旧ticket拒否、disconnectの無効終了を確認した。別試行でbrowser主threadを
300ms停止すると、service/processed receiptが同じ値で止まり、tick側の0.2秒gateで
`technical_invalid`となった。自動resumeは起きなかった。これはCPU busy loopによる無sample再現であり、
GCそのものの観測や実deviceの入力欠落を証明するものではない。

Pythonの関連201件、Viewer compile/lifecycle test/typecheck/buildがpassした。最終のwall監督補足を含む
focused107件とMarkdown検査（errors=0）もpassした。command、終了code、fixture/helper、source hashと
完全結果は親への`handoff.md`から参照する。既存の平均遅延値は本修復の達成指標として再利用しない。

### 残るpolicy判断

新鮮な入力が受信済みのlocal backlogにあるケースは改善したが、本当の0.2秒超の無sampleは
通常操作を終了させる。長いGC/render停止もmain-thread timerを止めるため、timerの独立化だけでは避けられない。
通常運用でこの欠落を許容するには、stale motionを適用せずsimulationを一時停止し、operatorの
明示resumeと新鮮な中立を要求する別policyが必要となる。formal evaluationはstrictのまま保ち、
pause時間、wall budget、結果の無効性と探索操作の継続を明示的に区別する設計を親へ引き継ぐ。
本taskではphase/schema、Task成功、正式な実験有効性、自動resumeを変更していない。

### input batching独立reviewの修正・追試

中立自身のreceiptを保持し、neutral age .31秒/latest age .01秒の親probeは`waiting_neutral`となった。新しい中立を取得した後だけ起動する。64件batchが連続してもwall/input-wait監督を通し、終端後のbatchも全件ticket/valid late messageを検査する。STOP/close取消と通常late入力を区別する。receipt gapは診断に留め、元のnow−latest receipt gateを維持する。

同じworker wire/clockでbaseは15件backlogの最初のreceipt 10.060秒をtick 10.263秒に使って203ms stale停止、修正版はsequence 15まで消費して1tick進行した。実WebSocket・実MuJoCoで60Hzの合成入力3,661件を61秒供給し、worker ownerだけ700ms停止した試験は3,545tick、processed sequence 3,660で明示STOPまで継続した。190/250/700msのadvance前後6ケース、連続64件batchの両監督、65件境界STOP/close、不正なlate後続入力も検証した。元のユーザー実行環境・実Gamepadは未再現である。前回のViewer/buildと実接触・trigger/中立/STOP/disconnect証拠は変更影響がない範囲で再利用する。詳細結果とsource identityは今回のhandoffに残す。
