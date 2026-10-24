# normalized_endpoint_velocity Mapping

## 意味とresponsibility

品質検証済みの7 channel 無次元 `command` を named endpoint の並進速度へ写像する。
canonical declaration は [`CONTROL_MAPPING_PLUGIN`](plugin.py)。実装は [`implementation.py`](implementation.py)。

## input / output

`selfrionette_device_normalized_sample/v1` を受け取り、`CoordinatedInput` 内の endpoint velocity を出力する。
source sequence と receipt を保存し、同じ sample の再読を新規入力にしない。

## parameters

各 role の `channel_axis_weights` は明示的な7×3行列。`speed_m_s`、`max_speed_m_s`、`control_frame` (`world` / `tool`) を必須とする。
channel と軸の暗黙対応、推定された左右差、既定の実機 gain は設けない。
生成した速度のノルムを `max_speed_m_s` で制限する。

## lifecycleとside effect

import / construction で入力取得や物理出力を開始しない。
校正値や積分器を持たず、source の正規化後値に追加 filter / dead zone / neutral 補正を掛けない。

## compatibilityとcomposition

Device、Mapping、`side_to_endpoint` の role 集合は完全一致を要求する。
単手・両手とも共通 coordinated runtime の中で実行する。

## command route

`local_endpoint_velocity_to_joint_position/v1` route を宣言する。
目標積分、world / tool 座標変換、IK、制約評価、native commit は既存 runtime が所有する。

## constraintsとnon-goals

- 全 channel の厳密な零 command を試行開始の中立 gate に用いる。
- raw count の解釈、N への換算、Device の profile 適用や物理的 channel 同定は行わない。
- 別 physics world、Viewer 側 IK、実機出力経路を作らない。

## tests / validation

- `tests/plugins/mappings/normalized_endpoint_velocity/`
- `tests/runtime/test_device_trial.py`

## canonical architecture / contract

- [Device input contract](../../../../../docs/contracts/selfrionette-device-input.md)
- [Input Source registry](../../../../../docs/contracts/runtime-input-source-registry.md)
