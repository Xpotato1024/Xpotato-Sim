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

## 条件と実行経路

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

## 実測結果

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
raw evidence、固定build、source/diff/検証manifestはユーザー指定のtask evidence rootへ保持し、
実行環境・cache・browser profileは完了時に除去する。独立review/push/PRは親taskの担当で、本taskでは未実行。
