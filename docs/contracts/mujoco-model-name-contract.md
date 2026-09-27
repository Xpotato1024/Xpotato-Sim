---
status: canonical
owner: architecture
last_verified: 2026-07-19
canonical_for:
  - fast_arm MuJoCo model name contract
related:
  - docs/contracts/mujoco-state.md
  - docs/contracts/transport-payload.md
  - docs/contracts/kinematics-command-contract.md
  - src/xpotato_sim/plugins/robots/fast_arm/adapter/model_contract.py
  - docs/reports/implementation/r7-e-followup-joint-convention-fast-arm-model-contract.md
  - docs/reports/implementation/r7-e-followup-viewer-backend-endpoint-separation.md
---

# MuJoCo Model Name Contract

この文書は `fast_arm` を canonical model として扱うときの body / site name contract を固定する。
ここでいう contract は backend / runtime 側の source of truth であり、viewer 側の推定ロジックではない。

## Canonical model

- canonical model: `fast_arm`
- canonical physical model owner: `fast_arm_core:resources/model/arm.xml`
- canonical scene owner: `xpotato_sim.plugins.robots.fast_arm.adapter:resources/mujoco/scene.xml`
- stable logical scene identifier: `assets/mujoco/fast_arm/scene.xml`

## 採用する名前

### End effector / tip

- primary site: `tip`
- primary body: `fore_arm_link`
- compatibility body fallback: `fore_arm_link`

`tip` site が canonical endpoint reference である。`fore_arm_link` body は wrist / tip frame の基準 body として使う。
site が欠けた場合に body fallback を使う処理は、明示的な opt-in があるときだけ許可する。

### Wrist

- primary body: `fore_arm_link`
- separate wrist site: なし

fast_arm には wrist 専用の site 名を追加しない。wrist frame は `fore_arm_link` body を使う。

### Arm body / link naming

- `base_link`
- `sholder_link_1`
- `sholder_link_2`
- `upper_arm_link`
- `fore_arm_link`

`world` / `origin` / `base` は構造上の body であり、arm link 名としては扱わない。

## Primary / fallback 方針

- primary は `tip` site
- body fallback は explicit opt-in のみ
- viewer は fallback を推定しない
- backend / runtime が fallback を解決する

fallback の用途は互換性維持だけであり、通常の contract validation の代替ではない。

## Units / Frame

- position unit: meter
- coordinate frame: MuJoCo world / scene frame
- `data.xpos`, `data.site_xpos` 由来の位置は meter として扱う

## Missing site/body failure semantics

strict validation では silent fallback をしない。

- required site `tip` がない場合は `ValueError`
- required body `fore_arm_link` を含む arm body がない場合は `ValueError`
- error message には missing name と expected role を含める
- body fallback を使う処理は、`allow_body_fallback=True` のような explicit opt-in を要求する

例:

- `missing site name 'tip' for expected role 'end_effector / tip'`
- `missing body name 'fore_arm_link' for expected role 'wrist'`

## Backend / Runtime source of truth

名前とframe/unitのpure specificationは`fast_arm_core.model_spec`がsource of truthである。
MuJoCo model inspectionとの接続は
`src/xpotato_sim/plugins/robots/fast_arm/adapter/model_contract.py`に置く。旧module pathは
adapter objectのthin re-exportだけを提供する。
generic named-reference contractとsite extractionは`src/xpotato_sim/mujoco_backend/`が所有し、
fast_arm名、fallback選択、convenience constructorを公開しない。
`apps/mujoco-viewer` はこれを推定しないし、MuJoCo を再ロードして検証しない。

## site endpoint helper contract

- MuJoCo site endpoint は backend / runtime の evaluation field であり、viewer SoT ではない
- primary endpoint は model contract の `tip` site である
- 入力は MuJoCo model / data、または backend snapshot 相当である
- 出力の unit は meter である
- 出力の coordinate frame は MuJoCo world / scene frame である
- FK endpoint の solver-defined frame とは自動的に同一視しない
- `desired_endpoint_m` / `target_position_m` とも自動的に同一視しない
- missing site / body は `ValueError` にする
- body fallback は `allow_body_fallback=True` のような explicit opt-in のみ許可する
- desired / qpos / FK / site / error metricsはframeとprovenanceを分けて統合する
