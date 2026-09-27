---
status: canonical
owner: contracts
last_verified: 2026-07-16
canonical_for:
  - runtime forward kinematics evaluation contract
related:
  - docs/contracts/forward-kinematics.md
  - docs/contracts/kinematics-command-contract.md
  - docs/contracts/mujoco-model-name-contract.md
  - docs/architecture/runtime-composition.md
  - src/xpotato_sim/runtime/evaluation/kinematics.py
  - docs/reports/implementation/r7-e-followup-joint-convention-fast-arm-model-contract.md
  - docs/reports/implementation/r7-e-followup-viewer-backend-endpoint-separation.md
---

# Runtime Forward Kinematics評価契約

## 目的

この文書は、backend / runtime 側で joint angles から FK endpoint を評価する
評価パスの契約を固定する。viewer SoT ではない。
FK endpoint、desired endpoint、MuJoCo site endpoint、error metricは別fieldとprovenanceを保つ。

## 入力

- 入力は `JointCommand.joint_angles_rad` または qpos-like joint angles である。
- orderingは`JointCommand` / qpos command boundaryに従う。
- backend で padding された qpos-like 値を使う場合は、solver 側の有効 joint
  count を明示して先頭から解釈する。
- 空の joint angles は explicit failure とする。
- solver の前提と長さが合わない入力も explicit failure とする。

## 出力

- 出力は FK endpoint の `Vector3` である。
- unit は meter である。
- coordinate frame は solver-defined frame である。
- この評価結果は `desired_endpoint_m` と自動的に同一視しない。
- この評価結果は MuJoCo site endpoint と自動的に同一視しない。

## 失敗時のsemantics

- 空の入力は `ValueError` とする。
- 長さ不正な入力は `ValueError` とする。
- `solver_joint_count <= 0` は `ValueError` とする。
- solver の前提と長さが合わない場合は、その failure をそのまま返す。

## Viewer / transportのboundary

- viewer は FK endpoint を計算しない。
- transportへ出すevaluation fieldはpayload contractに明示されたものだけに限る。
- dry-run JSONへ暗黙にfieldを追加しない。


## Current physical alignment

FK endpointはsolver output、MuJoCo site endpointはpost-step physical measurementとして別々に保持する。比較時はframeとunitsを明示し、viewerはread-onlyに表示する。pre-audit implementation chronologyと測定値は`docs/reports/audits/canonical-content-history-separation-2026-07-16.md`へ保存した。
