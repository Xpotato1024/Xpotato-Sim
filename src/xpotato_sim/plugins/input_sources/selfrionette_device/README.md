# selfrionette_device Input Source

## 意味とresponsibility

Selfrionette-Device の共有 Rust `LiveService` が生成した品質付き正規化出力を受け取る。
declaration は [`INPUT_SOURCE_PLUGIN`](plugin.py)、provider は `selfrionette-device/v1`、sample schema は `selfrionette_device_normalized_sample/v1`。

## input / output

`selfrionette-device-input/v1` envelope 内の bridge sample を一度だけ検証し、最大2 role の最新値を `RawInputFrame` と `selfrionette-device-snapshot/v1` metadata に保持する。
7 channel の `normalized` / `command` は無次元であり、raw count や N として表示しない。
元の sequence、device time、producer receipt、tare epoch と source/profile/operator identity を保存する。

## parameters

`bridge_revision` は40桁の固定 commit SHA。`bindings` は left / right と device ID、firmware source SHA、Protocol 3、profile revision、operator / posture / grip、settings generation を明示する。
単手・両手とも個体を自動選択しない。完全一致しない sample は拒否する。

## lifecycleとside effect

import、profile validation、source construction は serial を開かない。
取得は application 所有の有界 bridge process が担当し、source は parser / 最新値保持だけを担当する。
close で値を無効化する。再試行では旧 session を再利用しない。

## compatibilityとcomposition

`normalized_endpoint_velocity/v1` へ明示的に組み合わせ、既存 `ModelExecution` / `TrialRunner` / Workbench を通す。
旧 `selfrionette/v1` の raw / normalization schema と互換扱いしない。校正、filter、tare、neutral、profile 適用は Device 側に一元化する。

## constraintsとnon-goals

- 1 message は64 KiB、最新値は最大2 role。全 role が新規で usable になるまで command を生成しない。
- held、stale、missing、rejected、unsupported、span exceeded、clock regression、個体不一致を零入力に置き換えない。
- channel と身体部位の対応、実機受入、serial 自動接続、物理出力は担当しない。

## tests / validation

- `tests/plugins/input_sources/selfrionette_device/`
- `tests/runtime/test_device_trial.py`
- `tests/runtime/test_device_bridge.py`
- `tests/runtime/test_workbench_device_worker.py`

## canonical architecture / contract

- [Device input contract](../../../../../docs/contracts/selfrionette-device-input.md)
- [Input Source registry](../../../../../docs/contracts/runtime-input-source-registry.md)
