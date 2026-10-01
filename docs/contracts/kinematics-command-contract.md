---
status: canonical
owner: contracts
last_verified: 2026-07-30
canonical_for:
  - kinematics solver contract
  - JointCommand / MotionCommand boundary
  - target_position_m / qpos command boundary
related:
  - docs/contracts/forward-kinematics.md
  - docs/contracts/inverse-kinematics.md
  - docs/reports/inventories/r6-h-p1-stub-inventory.md
  - docs/contracts/motion-command.md
  - docs/contracts/schemas.md
  - docs/architecture/data-flow.md
  - docs/architecture/runtime-composition.md
---

# Kinematics / Command Contract

## 目的

current runtimeで`JointCommand` / `MotionCommand`
/ `target_position_m` / MuJoCo `qpos` / solver 入出力の contract を固定する。

## 前提

- `base.py` は Protocol / interface contract である。
- `base.py` に concrete implementation を直接書かない。
- production `kinematics/`は`base.py`のProtocolだけを所有する。fast_armのpure solver/数式は独立package
  `fast_arm_core`が所有し、Selfrionette Protocol/schema変換は`plugins/robots/fast_arm/adapter/kinematics.py`が
  所有する。旧`plugins/robots/fast_arm/kinematics.py`は退役済みであり、内部consumerもadapterを参照する。
- test doubleは`tests/support/kinematics_solver_doubles.py`だけに置き、production packageへ置かない。
- `viewer` は rendering-only であり、FK / IK / qpos recompute を行わない。
- 既存の wasm-scene product viewer path は MuJoCo model を描画に使うが、
  Python native backend / runtime / payload が source of truth である。
- browser viewerに独立したMuJoCo ownershipまたはmodel loading SoTを追加しない。
- MuJoCo backend / runtime が physical / command SoT を持つ。

## Source of Truth

- MuJoCo は physical source of truth である。
- runtime は composition root であり、複数層の結線だけを担う。
- schemas は layer contract である。
- viewer は transport / backend の payload を受け取って描画するだけである。
- `target_position_m` は viewer-visible feedback field と command target の
  境界を区別するための語である。
- MuJoCo site / body name contract は
  `docs/contracts/mujoco-model-name-contract.md` に固定済みである。
- FK runtime evaluationとMuJoCo site endpoint extractionはこのcontractを共有する。

## Solver interfaces

Concrete IK baseline は `docs/contracts/inverse-kinematics.md` に固定する。

`base.py` の solver contract は interface only である。

- `ForwardKinematicsSolver.forward(joint_angles_rad)` は joint-space / qpos-like
  input から `Vector3` を返す。
- `ForwardKinematicsSolver.forward()` は viewer-side FK の入口ではない。
- `InverseKinematicsSolver.solve(target_position_m, seed_joint_angles_rad)` は
  `target_position_m` と seed から `JointCommand` を返す。
- empty `JointCommand()` を通常成功として扱わない。必要な場合のみ
  explicit placeholder / exceptional empty result として扱う。
- `seed_joint_angles_rad` は solver 初期値であり、必要に応じて `None`
  を許容するが、失敗 semantics は別途明示する。

## JointCommand

`JointCommand` は solver output / joint command representation である。

- `JointCommand` は `MotionCommand.joint` へ接続されうる。
- `JointCommand` は viewer feedback field ではない。
- `JointCommand` は state snapshot ではない。

## MotionCommand

`MotionCommand` は command object であり、state snapshot ではない。

- `MotionCommand.joint` はruntimeのtyped projectionへの入力であり、backendの直接受付形式ではない。
- `MotionCommand.joint` は viewer feedback field ではない。
- `MotionCommand.target` は target-side command bucket であり、qpos boundary
  ではない。
- `MotionCommand.target` と `MotionCommand.joint` は混同しない。

## target_position_m

`target_position_m` は viewer-visible feedback / compatibility metadata である。

- viewer が `target_position_m` を解釈して FK / IK / qpos を再計算しない。
- `target_position_m` は command-side desired endpoint と自動的に同一視しない。
- programmed target input では `desired_endpoint_m` を優先し、
  `target_position_m` は trajectory sample / compatibility field として残る
  場合がある。

## target_delta_m

`target_delta_m` は command-side delta intent であり、`InputIntent` から
`TargetCommand(delta_m=...)` へ流れることがある。

- `target_delta_m` は `MotionCommand.joint` ではない。
- `target_delta_m` は qpos command boundary そのものではない。
- `target_delta_m` は viewer-side pose recompute の根拠ではない。

## qpos command boundary

MuJoCo `qpos` は backend / runtime SoT 側の joint state / command boundary
である。

- `MotionCommand.joint` はruntimeのtyped projectionへの入力であり、backendの直接受付形式ではない。
- runtimeの`project_joint_position_command()`が検証済み`JointPositionCommand`を生成し、backendの`apply_joint_position_command()`へ渡す。
- target-only、empty joint、joint-velocity-onlyはtyped projectionで拒否する。backendも異なるcommand型や不正joint形状を拒否する。
- `record_motion_command_envelope()`は診断情報だけを保持する。旧`apply_command(MotionCommand)`とその保留・step分岐は存在しない。
- browser viewer は qpos SoT ではない。

## Viewer boundary

viewer は rendering-only layer である。

viewer が行わないこと:

- FK
- IK
- qpos pose recompute
- command source 化
- state source of truth 化

viewer は backend / runtime payload を受け取り、描画と観測に使う。
既存の wasm-scene product viewer path は MuJoCo model を描画に使うが、
Python native backend / runtime / payload が source of truth である。
browser-sideへ新しいMuJoCo ownership pathを追加しない。

## Test-double boundary

test-only implementationは`tests/support/`だけが所有する。

- `ZeroForwardKinematicsSolver` は concrete FK ではない。
- `ZeroInverseKinematicsSolver` は concrete IK ではない。
- `NoOpMotionGenerator` は command generation の本線ではない。
- `NoOpMuJoCoSimulator` は MuJoCo backend integration の本線ではない。
- `NoOpStatePublisher` は production transport ではない。

これらをproduction runtime fallbackとして使用せず、`src/xpotato_sim/**/stubs.py`を再導入しない。
旧`InputInterpreter`経路も退役済みであり、Input SourceとControl Mappingをruntimeで直接composeする。

## Forward kinematics ownership

`ForwardKinematicsSolver`はgeneric protocolであり、production implementation
はselected `RobotRuntimePlugin`が構築する。generic testsはtest-only doubles、
fast_arm geometryはrobot-specific solver/conformance coverageが所有する。
`ZeroForwardKinematicsSolver`をproduction runtime FKとして使わず、viewer-side
FK/qpos recomputeも追加しない。

## Non-Goals

- schema breaking change
- viewer-side FK / IKまたはqpos recompute
- browser-sideの第二のMuJoCo ownership
- hardware / serial / OSC操作
- legacy import / execution

## Contact sceneの明示joint group

`HeadlessMuJoCoSimulator.bind_joint_position_group(names)`は実行開始前にuniqueなscalar joint名を
qpos/dof addressへ固定する追加境界である。未bind時の既存挙動は維持する。bind後の別groupへの変更、
unknown/free/ball joint、次元不一致を拒否する。指令はnamed groupだけへ適用し、cube等の非対象qvelを
ゼロへ置換しない。全scene snapshotとRobot-only projectionは別々に保持する。
