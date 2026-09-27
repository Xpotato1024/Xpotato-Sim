# transport

## 責務

`MuJoCoState` の JSON 変換、送信、frame logging、replay recording を扱う。

## 入力

`MuJoCoState`

## 出力

JSON message、WebSocket frame、log frame

## 依存してよい層

`schemas`

## 依存してはいけない層

`plugins/input_sources`, `plugins/mappings`, `motion`, `kinematics`,
`mujoco_backend`, `runtime`

## 禁止事項

IK禁止、target更新禁止、MuJoCo step禁止、入力デバイス読み取り禁止、Three.js表示禁止。

現在のpublic surfaceは`transport/__init__.py`、wire contractは
`docs/contracts/transport-payload.md`を正とする。将来file名をREADMEで予約しない。

## canonical routing

- [transport payload](../../../docs/contracts/transport-payload.md)
- [runtime data flow](../../../docs/architecture/data-flow.md)
- [WebSocket publisher operation](../../../docs/operations/websocket-publisher-runner.md)
