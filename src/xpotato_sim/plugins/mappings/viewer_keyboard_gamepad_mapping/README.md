# viewer_keyboard_gamepad_mapping

## 意味とresponsibility

viewer keyboard / gamepad sampleをcontinuous local endpoint velocity intentへ変換する。
canonical declaration: [`CONTROL_MAPPING_PLUGIN`](plugin.py)

## input / output

viewer control sampleとmapping parameterを受け、source diagnosticsを保持した`InputIntent`を出力する。

## parameters

speed、deadzone、per-tick limit、control frame等をMapping側で扱う。current field、型、resourceは
[`implementation.py`](implementation.py)と[`resources/keyboard_default.json`](resources/keyboard_default.json)を正とする。

## lifecycleとside effect

backend-side software変換で、browser event listener、gamepad device acquisition、network connectionを
所有しない。

## compatibilityとcomposition

Viewer Input Sourceはsample取得だけを担当し、このMappingのparameterを所有しない。

## command semantics route

local endpoint velocityをjoint position commandへ解決するtyped routeを宣言する。

## constraintsとnon-goals

- constraint: unknown / malformed inputとstale stateを明示的に扱う
- non-goal: browser rendering、FK / IK、Robot command executionを所有しない

## tests / validation

- [keyboard / gamepad smoke](../../../../../docs/operations/r6-l-keyboard-gamepad-live-viewer-smoke.md)

## canonical architecture / contract

- [continuous endpoint velocity](../../../../../docs/contracts/continuous-endpoint-velocity-input.md)
- [viewer control schema](../../../../../docs/contracts/viewer-control-message-schema.md)

## 明示的なGamepad軸対応

optionalな`gamepad_axis_map`は`axis_indices`と`axis_signs`を持つ。
各3要素が、要求制御座標系のX/Y/Zへ対応する。indexは相異なる非負integer、
signはintegerの+1/-1のみ。設定はstartup前に検証・copy/freezeする。

```json
{"gamepad_axis_map": {"axis_indices": [0, 1, 3], "axis_signs": [1, -1, -1]}}
```

上記は標準配置の左stick右→+X、左stick上→+Y、右stick上→+Zという
world-XY操作の確認用割当。カメラのscreen右を保証するものではない。
`sim-gamepad-world-xy`はこの設定を明示するsimulation専用起動profileである。
実機種の軸順は別途確認し、非標準配置へ自動適用しない。

省略時は従来の先頭3軸・同符号を維持する。既存`sim-gamepad`、二段deadzone、
速度scale、norm clamp、button 0/1のZ補助、world/toolの意味は変更しない。
raw sampleは保持し、取得層やrendererでは符号を補正しない。
`source_diagnostics`へ適用したindex/signを追加する。
activeなsampleで明示選択した軸が欠落すればrejectする。inactive/staleは
既存の停止処理を維持し、欠落軸を新しい有効な中立測定として扱わない。

この変更は単腕の明示設定と回帰検証まで。利用者の実Gamepad症状の再現、
カメラ投影を含む操作受入、双腕bindingは未完で、Issue #563を継続する。

## 左右独立の1スティックXYZ操作

新しい`sim-gamepad-left-xyz`／`sim-gamepad-right-xyz`と、XY/XZ切替・中立復帰・取得session・表示の規約は
[Gamepad平面操作契約](../../../../../docs/contracts/gamepad-plane-control.md)を参照する。現行の片腕へ選択した片側を適用する段階であり、双腕モデル完成ではない。

## 複数手先へのtyped projection

`map_coordinated_input` は明示side-to-endpoint bindingに従って左右の速度を型付き要求へ返す。
旧single-endpoint入口と平面状態機械・正規化・ゲインを共有し、表示用metadataをcommandへ逆変換しない。
[共同実行契約](../../../../../docs/contracts/coordinated-arm-runtime.md) を参照する。
