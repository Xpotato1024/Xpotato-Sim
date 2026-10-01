---
status: canonical
owner: architecture
last_verified: 2026-07-16
canonical_for:
  - MuJoCoState contract
related:
  - docs/contracts/target-marker-desired-endpoint.md
  - docs/contracts/mujoco-model-name-contract.md
  - docs/architecture/data-flow.md
  - docs/contracts/parallel-work-contracts.md
---

# MuJoCoState契約

これはbackend-to-viewer state snapshotのcanonical contractである。

`MuJoCoState`はMuJoCo backendが生成するphysical snapshotである。
controller state、transport state、viewer stateではない。

## field

- `frame_index`: runtime/backendのframe counter。
- `time_s`: backend step後のMuJoCo `data.time`。
- `qpos`: model orderのMuJoCo `qpos`。
- `qvel`: model orderのMuJoCo `qvel`。
- `bodies`: MuJoCo model/dataから得たbody transform。
- `sites`: MuJoCo model/dataから得たsite transform。
- `target_position_m`: optionalなtarget marker feedback。diagnostic contextおよび
  viewer-facing presentation inputであり、physics stateまたはcommand-side
  desired endpoint stateではない。
- `metadata`: diagnosticまたはtransport helper data専用。source of truthではない。

## transform契約

- positionのunitはmeterである。
- quaternionは`wxyz` orderで保存する。
- bodyとsiteの名前は`docs/contracts/mujoco-model-name-contract.md`を正とする。
- viewer codeはこれらのtransformをread-only inputとして扱わなければならない。
- viewer codeは`target_position_m`をtarget markerとして表示してよいが、FK、IK、
  qpos pose recompute、physics stateとして再解釈してはならない。

## 注記

- `base_link`、`fore_arm_link`、`tip`はfast arm assetのcanonical model nameである。
- `frame_index`はbackend stepごとに1増加する。
- 次のsnapshotを構築する前にbackendで`mj_step`を実行する。
- backendは、後続の`apply_joint_position_command()`が上書きするまでtyped pending commandを保持する。
  また、snapshotをdirect qpos reflection contractと整合させるため、`mj_step`後に
  joint qposを再適用する。
- 他の文書ではfield ruleを再記載せず、この文書へlinkする。
