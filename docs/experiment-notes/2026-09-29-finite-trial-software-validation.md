---
status: supporting
owner: runtime
last_verified: 2026-09-29
canonical_for: []
related:
  - docs/contracts/finite-trial-runtime.md
---

# 有限trialのsoftware検証条件

## 主張の範囲

#591の実装受入用software validationであり、参加者実験・実機取得・物理性能評価ではない。
旧contact manifestやmetricへ変換しない。新しい研究上のTask成功条件も追加しない。

## 入力と実行

- 基点: `669886e5de72ed35839c26d2eea8306f9d2d7648`。未commit実装の内容identityは検証時のevidenceに保存する。
- 環境: Windows、CPython 3.14.3、MuJoCo 3.9.0。dependency変更なし。
- fixture: `tests/fixtures/trial_gamepad/short-movement.json`、`trial-gamepad-fixture/v1`。
  software検証用の明示記録3標本で、offsetは0、0.02、0.04秒。中立→両stick±0.3→中立。
  raw timestamp/sequenceを保存し、再生時はoffsetをmonotonicへ対応させる。
- CLI例: `uv run xpotato-sim trial --profile dynamic-cube-drop --fixture tests/fixtures/trial_gamepad/short-movement.json --result-root <保存先> --ticks 4 --software-revision <revision>`。
- stress: `tests/runtime/test_trial_resources.py`。`dynamic-cube-drop`、1/60秒control dt、2 commit×120試行。
  direct runnerの決定的時計は10秒に固定し、実時間性能を測らずreset/参照を検証する。CLIの実monotonic経路は別test。
- phase: 準備前、準備後、20/40/80/120試行後、close後。各phaseでGC、tracemalloc、process RSS/private bytes、owner数を取得する。

## 観測

`591-trial-reviewed.log`の54件成功に120試行の測定を含む。source tree identityは
`83b91af9731e2c20a58f47deb9694b5d63afa95a52fd62746472b44b84471b69`、log SHA-256は
`d60cb27e4d82a114a4cf9665945b99ba905eb02627cfd5d60ccae83073928017`。
これは当該測定snapshotの値で、後続差分の検証を代替しない。

| 項目 | warm-up 20回 | 120回 |
|---|---:|---:|
| model factory累計 | 1 | 1 |
| native model構築累計（準備中の一時model含む） | 4 | 4 |
| live model / MjData所有slot | 1 / 6 | 1 / 6 |
| asset数 / bytes | 8 / 3562414 | 8 / 3562414 |
| pending / epoch / input slot | 0 / 1 / 1 | 0 / 1 / 1 |
| tracemalloc current bytes | 3811726 | 3830682 |
| RSS bytes | 255287296 | 255287296 |
| private bytes | 591368192 | 591364096 |

全試行で初期integration stateが一致し、前Source/Mapping参照はretry時に解放された。
close後はexecution、input runtime、Source、Task stateのGC参照が0となり、provider weak referenceも解放された。
native wrapperはGC列挙で0と出るため、0個と解釈せずowner slotとmodel構築counterを併用した。
測定用snapshot列自身の小さなPython heap増加も含む。RSS単独でリークの有無を断定しない。

## 限界

短い120試行は長時間・全conditionのリーク不在を証明しない。GUI、GPU、通信再接続、実機、正式metricは未検証。
実行中native callの強制中断は保証せず、同期APIから戻った境界でwatchdogを判定する。
