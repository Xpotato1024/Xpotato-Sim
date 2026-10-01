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

triggerのbinding構造検証はplugin-local `_binding_validation.py`が所有する。
軸の順序・符号・button制約とneutral待ち・session状態機械はtrigger ownerで維持する。

`sim-gamepad-left-xyz`／`sim-gamepad-right-xyz`とFastArmの標準Gamepad profileはstick XY + analog trigger Zを使用する。
trigger量・bumper符号ラッチ・session・表示は[Gamepad trigger操作契約](../../../../../docs/contracts/gamepad-trigger-control.md)を参照する。
旧XY/XZ切替は退役し、`gamepad_plane_control`は明示拒否する。

## 複数手先へのtyped projection

`map_coordinated_input` は明示side-to-endpoint bindingに従って左右の速度を型付き要求へ返す。
single-endpoint入口とtrigger状態機械・正規化・ゲインを共有し、表示用metadataをcommandへ逆変換しない。
[共同実行契約](../../../../../docs/contracts/coordinated-arm-runtime.md) を参照する。

## 双腕Viewer表示

`latest_trigger_presentation`はMapping sessionが計算した表示情報のcopyを返す。
`reset_coordinated_presentation`は未取得・fault時に中立待ち／速度ゼロの表示へ戻すだけで、
新たな入力観測や運動指令を生成しない。共同入口のscopeは`coordinated`、single-endpoint入口は従来どおり。


## モデルと入力bindingの分離

共通LaunchProfile/v2では、選択モデルの1〜2手先を`side_to_endpoint`で明示的に結ぶ。
Mappingは肩姿勢・原型/鏡映・腕数ごとのIKを所有せず、共通のtrigger操作から名前付き要求を作る。
runtimeが表示へ`endpoint_bindings`を付け、未割当stickを稼働中の腕として表示しない。
