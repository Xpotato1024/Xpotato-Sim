---
status: supporting
owner: runtime
last_verified: 2026-10-19
canonical_for: []
related:
  - docs/contracts/virtual-reaction-force.md
  - tests/runtime/test_scene_force_signal.py
---

# 現行scene反力signalのsoftware測定

## 条件と範囲

基点はmain `72005578e0624f55bf3f62bf111d82e29254a884`、#629の作業差分を加えたLinux環境。
Python依存は同じ`uv.lock`で固定した。`dynamic-fixed-contact`を`single_left`と`bimanual`へ
解決し、native providerに左手先world速度(0.06, 0, 0) m/s、右手先0を与えた。
制御周期1/60 s、physics周期1/600 s、120 commit、ウォームアップ各1回、計測各3回。
同じproviderのprepare/commitとModelExecution.sampleを計測し、signal processorを
無効にした基準と有効な場合を比較した。Task、input/network、Web、disk記録を含むapp全体のRTFではない。
clockはcommit×dtへ明示対応するsoftware時計であり、実deviceの入力ageは測定していない。

## 観測

表は3回の中央値。tick中央値は各回のtick中央値の中央値である。

| model | signal | tick中央値 ms | 計算区間RTF | 欠測signal |
|---|---|---:|---:|---:|
| single_left | 無効 | 0.860 | 18.276 | 対象外 |
| single_left | 有効 | 0.900 | 19.109 | 0 / 360 |
| bimanual | 無効 | 1.059 | 14.270 | 対象外 |
| bimanual | 有効 | 1.088 | 15.042 | 0 / 360 |

tick中央値の追加分はこの測定で約0.040 ms / 0.029 msだった。
スケジューリング変動により総時間のRTFは逆方向の差があり、性能改善や優劣を示す結果ではない。
各有効runの最後のsubstepで観測した最大法線荷重は30.576979 Nで一致した。
これはこのsoftware fixtureのcontrol観測列に限った最大値であり、substep間peak・正式研究metric・実機荷重ではない。

## 再検証入口と残件

`tests/runtime/test_scene_force_signal.py`のnative oracleは同じ片腕/双腕の180 commit後に
`mj_contactForce`とnative frameから独立に作用方向を照合する。
geom順反転、左右の相殺、対象外の台/支持面、部分欠測、filter、STOP/epochと表示再送の鮮度を別fixtureで検証する。
既存`tests/runtime/test_trial_resources.py`は120試行のnative owner・queue・参照回収を検査する。

固定Web buildでの表示rate/drop/遅延、実入力age、app全体RTFは後続#630/#631統合の検証対象として残る。
rawの連続永続記録と正式metricは#584の責務を維持する。
