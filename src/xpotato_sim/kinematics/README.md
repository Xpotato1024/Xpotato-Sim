# kinematics

## 責務

genericなFK / IK solver Protocolを所有するport package。
concrete robotの運動学、joint convention、joint limit、motor-space / joint-space変換は
各robot pluginが所有し、fast_arm実装をこのpackageへ戻さない。

## 入力

generic schema型またはProtocolで定義した純粋な数値入力。

## 出力

generic solver Protocolで定義した計算結果。

## 依存してよい層

`schemas`

## 依存してはいけない層

`plugins/input_sources`, `plugins/mappings`, `mujoco_backend`, `transport`, `runtime`,
concrete robot plugin

## 禁止事項

入力デバイス依存禁止、MuJoCo data直接操作禁止、WebSocket禁止、Three.js表示禁止、
runtime / input / transport依存禁止。concrete solver、robot固有定数、stubを置かない。

## canonical routing

- [dependency boundary](../../../docs/architecture/dependency-boundaries.md)
- [kinematics / command contract](../../../docs/contracts/kinematics-command-contract.md)
- [Robot Plugin axis](../plugins/robots/README.md)
