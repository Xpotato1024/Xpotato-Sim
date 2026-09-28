---
status: canonical
owner: mapping
last_verified: 2026-09-28
canonical_for:
  - gamepad stick XY and analog-trigger Z control
related:
  - docs/contracts/continuous-endpoint-velocity-input.md
  - docs/contracts/gamepad-plane-control.md
  - docs/contracts/launch-profile.md
---

# Gamepad stick XY + trigger Z操作

## 目的

Gamepadを補助的な連続入力として扱い、平面切替なしで各手先のXYZ速度を同時に要求する。
左右stickは常時X/Y、同側analog triggerはZの大きさ、同側bumperはZ符号を選ぶ。

標準配置を確認したGamepadでは次を使う。機種の自動認識・自動校正ではない。

| 操作セット | X/Y | Z量 | Z符号 |
|---|---|---|---|
| 左 | Left stick | LT / button 6 value | LB / button 4 |
| 右 | Right stick | RT / button 7 value | RB / button 5 |

bumper非押下を+Z、押下を-Zとする。Z符号はtriggerがneutralのときだけ更新し、
triggerを押し込んだ最中のbumper操作で速度を瞬時反転させない。

## 設定契約

`viewer_keyboard_gamepad_mapping/v1`のoptional `gamepad_trigger_control`で明示する。

```json
{
  "gamepad_trigger_control": {
    "schema": "gamepad-trigger-control/v1",
    "output_side": "left",
    "neutral_threshold": 0.1,
    "left": {
      "axes": [0, 1],
      "signs": [1, -1],
      "trigger_button": 6,
      "sign_button": 4
    },
    "right": {
      "axes": [2, 3],
      "signs": [1, -1],
      "trigger_button": 7,
      "sign_button": 5
    }
  }
}
```

`output_side`は単一手先のv1実行で適用する入力セットを示す。
named-endpoint v2実行では`coordination.side_to_endpoint`が適用先を所有する。
左右stickの4軸、2 trigger、2 sign buttonは重複させない。

`gamepad_trigger_control`、`gamepad_plane_control`、`gamepad_axis_map`は同時指定しない。

## 入力値とneutral

browser Gamepad providerはbuttonごとに`pressed`と`value`を送る。
trigger controlはbutton 6/7の`value`を0..1のanalog値として必須にし、
値が欠落した機種をdigital triggerとして推測しない。

stickは既存Gamepad互換projectionを通し、triggerも`neutral_threshold`以下を0として
残りを0..1へ線形投影する。その後、共通continuous velocity builderのdeadzone、
per-side norm clamp、`gamepad_speed_m_s`を適用する。

coordinated runtimeの開始・restartでは、対象sideのstick 2軸とtriggerがneutralである
fresh sampleを受け取るまで運動を開始しない。bumper単独は速度を生成しないためneutralを妨げない。

triggerがneutralになった時点でbumper状態をZ符号としてラッチする。
disconnect、stale、device change、provider session changeではtrigger符号のarmingをresetする。

ViewerのGamepad取得はPage Visibilityをactivation境界とし、documentが`visible`なら
`document.hasFocus()`の値に依存せずpoll / publishする。DevToolsや別windowへfocusが移っても
visibleなViewerはGamepad入力を継続する。一方、別tabへの切替などで`hidden`になった場合は
即時zero snapshotを送り、heartbeatとactive publicationを停止する。

## 表示と診断

backendは`metadata.gamepad_trigger_control_v1`へ次を出す。

- `output_scope` / `output_side`
- `endpoint_bindings`
- sideごとの`status`
- `z_sign`
- `trigger_value`
- `trigger_button` / `sign_button`
- `velocity_m_s`

Viewerはこのmetadataを再計算せず、入力欄へコンパクトに表示する。
現行primary profileではXY/XZ plane表示を使用しない。

`gamepad-plane-control/v1`は既存profile・fixture・過去検証の互換経路として残す。
新規の標準Gamepad XYZ操作にはtrigger controlを使用する。

## 現行profile

次のprofileはtrigger controlを使用する。

- `sim-gamepad-left-xyz`
- `sim-gamepad-right-xyz`
- `fast-arm-single-gamepad`
- `fast-arm-left-gamepad`
- `fast-arm-right-gamepad`
- `fast-arm-bimanual-gamepad`

## 検証境界

software testではXYZ 6方向、左右独立符号、trigger中の符号反転抑止、
session reset、v1/v2 profile、双腕WebSocket payload、Viewer parser/markupを検証する。

実Gamepad機種ごとのbutton番号・analog rangeの受入確認は別途必要である。
この変更は実機ロボット、serial、OSC、hardware outputを作動させない。
