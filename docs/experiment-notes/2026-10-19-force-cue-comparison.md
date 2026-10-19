---
status: supporting
owner: runtime
last_verified: 2026-10-19
canonical_for: []
related:
  - docs/contracts/finite-trial-runtime.md
  - docs/contracts/workbench.md
  - docs/operations/product-viewer-wasm-scene-renderer.md
  - tests/runtime/test_force_presentation.py
  - apps/mujoco-viewer/tests/forcePresentation.test.tsx
---

# 追加接触力の提示条件のsoftware比較

## 目的と固定条件

#629の`0d2f4360b98ceae15389039cd936439296944170`へ#630の差分を加え、同じnative worldと
同じ固定Viewer buildで`no_force_cue`と`force_cue`を順に実行した。これは接触提示の漏れ、
sample identity、STOPと条件切替のsoftware観測である。実機、実Gamepad、参加者、光学遅延、
表示性能の研究受入を測定したものではない。

`preset_condition("dynamic-fixed-contact", TrialLimits(1200, wall_s=60, prepare_s=60))`を展開し、
`display_comparison.mode`だけを変えた。modelは`fast_arm/bimanual/v1`、100 mm固定cube、
tool sphere 10 mm、Taskは`contact_observation_task/v1`の60秒観測である。
controlは1/60 s、physicsは1/600 s、`implicitfast` / `Newton` / 50 iterations / tolerance 1e-10、
max input ageは0.2 sを維持した。今回の試行は20 simulation秒の予算より前に明示STOPで終了した。

共通fixtureは`trial-gamepad-fixture/v1`、0.0〜30.0 sへ0.1 s刻みの301件。
最初は中立、以後は左右のraw axesとaxesを`[0, -0.64, 0, 0]`へ固定した。
8 buttonsはすべて0/false、connected=true、stale=false、sequence=i、timestamp_s=offset_s=i/10。
session IDは`force-cue-software-comparison-v1`、providerは`gamepad/v1`。
既存deadzone 0.1、speed 0.1 m/s、符号・軸対応を通した左world +X入力で、右は中立である。
fixtureは初期状態から進め、実行中のqpos書換えや接触checkpoint投入は行っていない。

表示条件は共通で0.002 m/N、arrow上限0.12 m、法線荷重meter上限30 N、
browser受信の失効上限0.2 s。通常の物体・robot映像、camera、Task/停止の共通表示は同じまま、
force modeだけを変えた。finite trialに記録するpresentation、Viewer source/build、
scene、model、numerical conditionとfixtureのdigestが比較対象のidentityとなる。

## 実行方法

同じ`uv.lock`で同期したPython環境とViewerの`npm ci --ignore-scripts`、`npm run build`を使用した。
生成fixtureと展開済み条件を保存し、次のCLIを起動した。`${QA_ROOT}`は測定用の出力directoryを表す。
capabilityは測定process内で生成し、`--control-stdin`へ渡した。値は観測ログへ記録していない。

```bash
OPENBLAS_NUM_THREADS=1 python -c 'from xpotato_sim.cli import main; raise SystemExit(main())' \
  workbench --condition "${QA_ROOT}/no_force_cue.json" \
  --fixture "${QA_ROOT}/fixture.json" --result-root "${QA_ROOT}/results" \
  --temporary-root "${QA_ROOT}" --software-revision force-cue-local-review \
  --web-port 5197 --backend-port 8797 --control-stdin
```

1500×1000のChromium 134.0.6998.35 headlessで、`--use-angle=swiftshader`と
`--enable-unsafe-swiftshader`を明示した。GPU実端末の代用とはしない。
browser automationのPlaywrightとJapanese fontは測定環境だけへ用意し、製品依存には追加していない。
操作権取得→検証・準備→shader準備完了→Assistの3画面→操作画面→開始と進めた。
source tickが180以上になった時点のcounterとDOM、続く画面画像を取得し、STOPと保存完了を確認した。
次にforce条件をimportして準備し、同じfixtureと画面で実行した。最後に同条件の再試行を準備した。

## 同じbuildでの計測値

以下は両modeでSTOPと保存完了まで成功した1組の観測である。各modeの最初の受信frameから
counter取得までの窓は3.391 s / 3.5305 s。RTFはその時点で最新のbackend statusに載った
183 / 182 tickまでの窓であり、browser窓と同じ標本数ではない。

| 観測 | no_force_cue | force_cue |
|---|---:|---:|
| backendの最新RTF | 0.99965 | 0.99858 |
| 採用source tick / frame | 204 / 204 | 212 / 212 |
| force採用sample数 | 203 | 211 |
| 全paneのrender提出sample数 | 8 | 5 |
| 描画前の最新値置換数 | 195 | 206 |
| source tick欠落数 / 描画前無効化数 | 0 / 0 | 0 / 0 |
| force処理平均 ms / 呼出数 | 0.1146 / 205 | 0.1160 / 213 |
| 最新提出sampleの受信→採用処理 ms | 0.6 | 0.7 |
| 同sampleの採用処理→render呼出完了 ms | 18.3 | 15.9 |
| 矢印数 / 幾何cue数 / 旧force cue数 | 0 / 0 / 0 | 1 / 0 / 0 |
| STOP後の矢印数 / 終端記録 | 0 / complete | 0 / complete |

8/3.391 ≈ 2.36 Hz、5/3.5305 ≈ 1.42 Hzという提出密度は、この短いsoftware描画窓の値である。
未描画の受信sampleは有界な最新値で置換された。workerはその間もほぼ等速で進行していた。
両条件の処理平均の差は約0.0014 msだが、反復比較も分散評価もなく、優劣の根拠とはしない。

| identity | 値 |
|---|---|
| 計測Viewer source SHA-256 | `ef18d5d21bbcefec70c8419074aaf31acee987aaa3a70878a5b6dd9417f5a361` |
| 計測Viewer build SHA-256 | `818a339fd78bc130118c172a46fb5891bad266b7880a6c8c4708226d150c72cc` |
| 計測fixture SHA-256 | `f21ec825c9dcd3c66dd8f97e5a470e50dcd08f47779dc4a38b54a1b75b054f57` |
| no-force trial ID | `083f116a49e64dac80943a5b3c6c39da` |
| force trial ID | `4e32cedfdf1247979b03cf45ccacc69d` |

計測fixtureは201,382 bytesで、provenance説明に誤って「20 Hz」と残っていたが、実sampleの
offsetは0.1 s刻み・301件・30.0 sであり10 Hzだった。保存済み開始記録は書換えず、誤記をここで訂正する。
説明を10 Hzへ修正した追試fixtureは201,383 bytes、SHA-256は
`18f68a57d3efea7a4bd52d54fd19591943c2fed287fa9ef5dafe8e37877a7875`となる。

計測後に極端な有限成分のnorm overflow拒否と、arrowの二乗normを避けた正規化を追加した。
提出候補buildのsource SHA-256は`e9cf0fd986a3905fbde951252c598203c83c35bc25d8375aa64e74507d82bc3c`、
build SHA-256は`7cb98f146263b3f6e5abd025505b783b400b38a52beb72d804a5df4765369606`。
focused regression、typecheck、production buildはこの候補で成功した。表の計測値を候補buildで再取得したとは扱わない。

## 観測の解釈

no-force側では数値・meter・矢印を描かず、通常映像を保持した。force側では
`left ← cube`のworld反力と法線荷重、`right ← cube`の有効ゼロを区別して表示した。
主画面、上面、正面が同じsceneのarrowを参照し、geometry marker、距離・食込み・法線・接触数、
旧force overlay、raw dynamics/actuator診断を両比較条件で抑制した。
STOP後は矢印が0、数値panelが消去された。条件変更前後のnativeとWASM model buildはそれぞれ1のままだった。

`receivedAtMs`はWebSocket message event開始、`adoptedAtMs`は検証済みposeに続くforce採用処理、
`renderSubmittedAtMs`は同じsampleの全pane render呼出し完了を指す。DOM数値panelは別の10 Hz consumerで、
独立のsample IDとDOM commit時刻を持つ。これらはbrowserの同一performance clock内だけで差を取る。
workerのmonotonic、source時計、simulation時刻とは引き算しない。WebGL呼出し完了は光学的提示完了ではない。

`forceProcessingMs / forceProcessingSamples`は検証・矢印更新・最新値公開の平均wall時間で、
WebGL描画、React DOM、backend処理を含まない。採用数、描画前置換数、無効化数、描画提出数を分け、
source tick欠落と、受信後に描画まで届かなかったsampleを混同しない。

## 再現テストと限界

`tests/runtime/test_force_presentation.py`は同じ入力列を両modeで再生し、epochだけを除いたqpos/qvel、
native dynamics、Task出力が完全一致することを検査する。表示の変更だけではnative modelを再buildしない。
条件・buildの固定、active試行での変更拒否、legacy export、STOP/resetを別caseで検査する。
Viewer testは両panelの漏れ、矢印pool、飽和時の元値保持、欠測・不正値・非有限norm、
tick/epoch/model/scene/settings/condition不一致、同一tick再送による鮮度延長拒否、0.2秒失効を扱う。

候補buildの追試では両modeの表示とSTOP消去、再試行後の初期姿勢・無効なforce表示を観測したが、
Setup限定の準備ラベルを操作画面で待つharnessがtimeoutした。その後の追試1件では、STOP時に
serviceが所有worker回収を表示した。trial `33628b00b0064f289dc99755f5619bed`のterminalは
`operator_abort` / `recording=complete` / `error=null`だった。保存成立とUIの正常停止確認は区別する。
既存2秒STOP監視による回収との対応が考えられるが、原因の寄与率は未確定であり、正常STOPの全面受入を主張しない。
その失敗画面の取得もsoftware描画でtimeoutした。これらの失敗を成功した上記1組で置き換えない。

今回の低描画rateはheadlessのsoftware描画を含む環境の観測である。実際の利用端末の等速性・
操作可能なcue更新率・光学遅延の受入を認定しない。比較modeごと1回の短時間取得であり、
性能優位や統計的有意差を主張しない。input→sceneの連続evidenceと正式metricは#583/#584へ残る。
実行前のfixtureを整形JSONにした際、既存内部256 KiB制限でworker起動が拒否されたため、
同じ内容を201,383 bytesのcompact JSONへ戻して再開した。public fixtureの1 MiB上限との不一致は#631で扱う。
