---
status: canonical
owner: architecture
last_verified: 2026-06-12
canonical_for:
  - naming
  - units
  - coordinate conventions
related:
  - docs/README.md
---

# conventions

## 用語

- MuJoCo: physical stateのsource of truth。
- Three.js: renderingのみを担当する。
- runtime: 唯一のcomposition root。
- schemas: layer contract。
- legacy: 参照専用。
- assets: model asset。

## layer名

次のdirectory名を固定layer identifierとして使用する。

- `schemas`
- `plugins/input_sources`
- `plugins/mappings`
- `motion`
- `kinematics`
- `mujoco_backend`
- `transport`
- `runtime`
- `apps/mujoco-viewer`

## 単位

canonical contractに別の定義がない限り、内部単位にはSI unitを使用する。

- 長さ: meter (`m`)
- 時間: second (`s`)
- 角度: radian (`rad`)
- 角速度: radian per second (`rad/s`)

degreeは表示、log、人間向け文書に限って使用できる。

## 座標系

最終的なmodel座標系は、`docs/contracts/assets.md`とMuJoCo model-load validationが
MJCF/STLのaxis、origin、scaleを固定するまでprovisionalである。axis、origin、unitの
前提を暗黙に変更してはならない。

## 命名

- Python module、function、file: `snake_case`
- Python class、protocol: `PascalCase`
- Markdown file、web package file: `kebab-case`
- Git branch: Codexが作成するbranchは`codex/<short-purpose>`
