---
status: canonical
owner: architecture
last_verified: 2026-07-29
canonical_for:
  - schema contracts
related:
  - src/xpotato_sim/schemas/README.md
  - docs/contracts/motion-command.md
  - docs/contracts/mujoco-state.md
---

# Schema契約

これは共有schemaのcanonical contractである。他の文書ではfield一覧を再掲せず、
この文書を参照する。

`JointCommand` / `MotionCommand.joint` / `target_position_m` / MuJoCo `qpos`
の command boundary は `docs/contracts/kinematics-command-contract.md` を参照する。

## Schema一覧

canonical importはpackage public surfaceの`xpotato_sim.schemas`を使用する。実装moduleは次のwire domainを
ownerとし、1型1fileの旧pathはcompatibility facadeなしで退役する。

| wire domain | canonical module | 主な型 |
|---|---|---|
| input frame / intent | `schemas.input` | `RawInputFrame`、`InputIntent`、`ContinuousEndpointVelocityIntent` |
| command | `schemas.command` | `TargetCommand`、`JointCommand`、`MotionCommand`、`JointPositionCommand`、`EndpointVelocityCommand`、`PhysicalOutputRequest`、`PhysicalOutputPermission`、`PhysicalOutputDecision` |
| MuJoCo / render state | `schemas.state` | `BodyTransform`、`SiteTransform`、`MuJoCoState`、`RenderState` |
| endpoint metadata | `schemas.endpoint` | `EndpointMetadata`とframe / status vocabulary |
| viewer control message | `schemas.viewer_control` | viewer-to-backend control envelopeとstrict decoder |
| experiment log | `schemas.experiment_log` | JSONL record、encoder、decoder、stream validator |
| primitive types | `schemas.types` | `Vector3`、`QuaternionWXYZ`、`JointVector`、`ScalarVector` |

domain間依存は`input / command / state / endpoint -> types`、`experiment_log -> endpoint`だけを許可する。
`viewer_control`と`types`は他domainへ依存しない。`schemas.__init__`は全public symbolを明示的な
`__all__`で公開し、consumerは退役した実装moduleへ依存しない。

| 退役module | canonical replacement |
|---|---|
| `input_frame`、`input_intent`、`continuous_endpoint_velocity` | `input` |
| `target_command`、`joint_command`、`motion_command` | `command` |
| `mujoco_state`、`render_state` | `state` |
| `endpoint_metadata` | `endpoint` |
| `viewer_control_message` | `viewer_control` |
| `experiment_motion_log` | `experiment_log` |

- `Vector3`、`QuaternionWXYZ`、`JointVector`、`ScalarVector`: layer contractで
  共有するtuple alias。
- `RawInputFrame`: Input Source Pluginが取得するdevice/replayのraw input。
- `InputIntent`: versioned Control Mapping Pluginから次のlayerへ渡す、解釈済みの
  replay/input-layer contract。`MotionCommand`ではない。
- `TargetCommand`: motion generationで使用するtarget-space command。
- `JointCommand`: solver output / joint command boundaryの入力。
  `docs/contracts/kinematics-command-contract.md`を参照する。
- `MotionCommand`: runtime内部のmotion / safety envelope。
  `docs/contracts/motion-command.md`と
  `docs/contracts/kinematics-command-contract.md`を参照する。
- `JointPositionCommand`: `joint_position_command/v1` providerが受理する
  joint-position専用Robot/backend command。
- `EndpointVelocityCommand`: `endpoint_velocity_command/v1` providerが受理する
  endpoint-velocity専用Robot/backend command。timestampとexactly 3のvelocity componentは
  bool / numeric stringを拒否するfinite numericで、保存時にfloatへnormalizeする。frameは
  `world | tool`だけを受理する。
- `PhysicalOutputRequest`、`PhysicalOutputPermission`、`PhysicalOutputDecision`: typed
  RobotCommandを物理出力要求へ投影する際のtarget / session / sequence / cadence / timestamp、
  permission mode、operator gate、requested / accepted / rejected evidenceを表す。
  物理出力のmodeと責務境界は`docs/contracts/physical-output.md`を参照する。
- `BodyTransform`、`SiteTransform`: backendが抽出するrigid transform。
- `MuJoCoState`: transport layerとviewer layerへ渡すbackend snapshot。
  `docs/contracts/mujoco-state.md`を参照する。
- `RenderState`: viewer-side state boundary用のplaceholder render contract。
- `ViewerControlMessage`、`ViewerControlKeyboardMessage`、
  `ViewerControlGamepadMessage`、`ViewerControlGamepadButtonMessage`: 厳密な
  viewer-to-backend control envelope。
  `docs/contracts/viewer-control-message-schema.md`を参照する。

## 責務に関する注記

- Schemaは共有data contractだけを定義する。
- Schemaはruntime composition、MuJoCo、WebSocket、Three.jsのbehaviorを
  importしてはならない。
- Schema追加では`docs/architecture/dependency-boundaries.md`に記録された
  layer boundaryを維持する。
- `MotionCommand`はcommandであり、stateではない。
- `InputIntent`はreplay/input-layerの結果であり、motion commandではない。
- `InputIntent.values`はraw replay/input payload dataであり、現時点では
  motion semanticsを持たない。
- motion layerは`InputIntent.target_delta_m`を
  `TargetCommand(delta_m=...)`へ変換してよい。
- joint commandはbackend qpos boundaryで直接反映するため、`InputIntent.joint_delta_rad`を
  joint commandへnormalizeしない。
- `desired_endpoint_m`はconcrete programmed-target pathが使用する
  command-side endpoint termである。`target_position_m`はcompatibility /
  viewer feedback metadataのままとする。
- `MotionCommand.target`はtarget-side command bucketであり、qpos boundaryではない。
- `MotionCommand.joint`は`JointPositionCommand` projectionの入力であり、viewer feedbackではない。
- `ViewerControlMessage`はschema-onlyのcontrol intentである。viewer-sideの
  simulation mutation、FK / IK recompute、physics mutationを許可しない。
- `MuJoCoState.target_position_m`はviewer-visible feedbackであり、command sourceではない。
- `MuJoCoState` snapshotの生成は`mujoco_backend`が所有し、`mj_forward`から
  供給される。`mj_step`はbackend steppingの一部であり、snapshot contractには
  含まれない。
- Transport payloadは`MuJoCoState`から派生し、schema ownershipを変更しない。
