# mujoco_backend

## 責務

typed Robot commandまたはbackend-local diagnostic commandをMuJoCo qpos / ctrlへ反映し、
`MuJoCoState`を生成する。MuJoCoをphysical SoTとして扱う。production runtimeのRobot command入口は
selected routeにbindされたproviderである。診断も既存のtyped projectionを再利用する。
`MotionCommand`は`record_motion_command_envelope`で診断情報として保持するだけで、適用入口ではない。
旧`apply_command(MotionCommand)`と`command_adapter.py`は退役した。

## 入力

`JointPositionCommand`、backend-local `JointCommand`、MJCF / XML / validated resource

## 出力

`MuJoCoState`

## 依存してよい層

`schemas`

## 依存してはいけない層

`plugins/input_sources`, `plugins/mappings`, `motion`, `transport`, `runtime`

## 禁止事項

入力読み取り禁止、Control Mapping直接呼び出し禁止、WebSocket server禁止、
Three.js描画禁止、runtime依存禁止。

現在のpublic surfaceは`mujoco_backend/__init__.py`、typed command境界は
`docs/contracts/motion-command.md`を正とする。将来file名をREADMEで予約しない。

## canonical routing

- [dependency boundary](../../../docs/architecture/dependency-boundaries.md)
- [MuJoCoState](../../../docs/contracts/mujoco-state.md)
- [Robot Plugin axis](../plugins/robots/README.md)
