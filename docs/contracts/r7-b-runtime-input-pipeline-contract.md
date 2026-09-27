---
status: canonical
owner: architecture
last_verified: 2026-09-20
canonical_for:
  - R7-B runtime input pipeline contract
related:
  - docs/README.md
  - docs/architecture/data-flow.md
  - docs/architecture/runtime-composition.md
  - docs/contracts/parallel-work-contracts.md
  - docs/contracts/schemas.md
  - docs/contracts/motion-command.md
  - docs/contracts/mujoco-state.md
  - docs/contracts/transport-payload.md
  - docs/contracts/target-marker-desired-endpoint.md
  - docs/contracts/programmed-target-input-source.md
  - docs/contracts/r7-a-lite-serial-frame-contract.md
  - docs/reports/audits/r7-a-lite-completion-audit.md
  - docs/reports/implementation/r7-a-lite-websocket-viewer-smoke.md
---

# R7-B Runtime Input Pipeline Contract

## 目的

この文書は`InputSource -> MotionCommand -> runtime target update -> MuJoCo -> WebSocket -> viewer`のcurrent経路と境界、およびkeyboard input と loadcell input を同じ simulation-facing input pipeline に乗せる方針を明記し、
`desired_endpoint_m` を command-side endpoint として扱う契約を固定する。

## canonical flow

simulation-facing flowは次のとおり。

```text
keyboard event / key state
-> keyboard input intent
-> MotionCommand.metadata["desired_endpoint_m"]
-> runtime target update
-> MuJoCo
-> WebSocket payload
-> viewer read-only display
```

loadcell側はcanonical serial frame / normalization経路を使用する。

```text
serial frame lines
-> SerialInputSource
-> RawInputFrame
-> NormalizedLoadcellInputIntent
-> MotionCommand.metadata["desired_endpoint_m"]
-> runtime target update
-> MuJoCo
-> WebSocket payload
-> viewer read-only display
```

`keyboard` と `loadcell` のどちらも、viewer を直接動かすのではなく runtime の command-side pipeline に流し込む。

## command contract

- `desired_endpoint_m` は command-side endpoint である。
- `MotionCommand.metadata["desired_endpoint_m"]` は command-side endpoint の優先参照先である。
- `target_position_m` は viewer feedback / compatibility fallback である。
- `target_position_m` を primary command にしない。
- `MotionCommand.target` は command bucket であり、viewer state ではない。
- `MotionCommand.joint` は qpos command boundary であり、viewer feedback ではない。
- `MuJoCoState.target_position_m` は viewer-facing feedback であり、command-side truth ではない。
- `viewer` は read-only display である。
- `viewer` 側で FK / IK / qpos recompute をしない。
- `endpoint_evaluation` は optional diagnostic overlay であり、control truth source ではない。

## keyboard input contract

keyboard inputはsimulation-facing input sourceとして扱う。

### default keybind

default keybind は次のとおり。

| Key | Axis | Direction | Meaning |
|---|---|---:|---|
| `KeyW` | `y` | `+1` | `+Y` |
| `KeyS` | `y` | `-1` | `-Y` |
| `KeyA` | `x` | `-1` | `-X` |
| `KeyD` | `x` | `+1` | `+X` |
| `Space` | `z` | `+1` | `+Z` |
| `ShiftLeft` | `z` | `-1` | `-Z` |
| `ShiftRight` | `z` | `-1` | `-Z` |

ここでのaxis名はworld-axis labelであり、runtimeのworld / viewer / MuJoCo coordinate conventionと一致させる。

### keybind config contract

keybind は config file で変更可能にする。

既定keybindはMapping plugin package内の
`src/xpotato_sim/plugins/mappings/viewer_keyboard_gamepad_mapping/resources/keyboard_default.json`
を正本とし、wheel / sdistへ同梱する。repository-rootのconfig pathには依存しない。

```json
{
  "source_kind": "keyboard",
  "bindings": {
    "KeyW": { "axis": "y", "direction": 1 },
    "KeyS": { "axis": "y", "direction": -1 },
    "KeyA": { "axis": "x", "direction": -1 },
    "KeyD": { "axis": "x", "direction": 1 },
    "Space": { "axis": "z", "direction": 1 },
    "ShiftLeft": { "axis": "z", "direction": -1 },
    "ShiftRight": { "axis": "z", "direction": -1 }
  },
  "step_m": 0.01,
  "deadzone": 0.0,
  "max_delta_m": 0.03
}
```

- `source_kind` は `keyboard` に固定する。
- `bindings` は key code ごとの axis / direction マッピングである。
- `step_m` は 1 tick あたりの基準移動量である。
- `deadzone` は 0.0 を default とし、将来の拡張でも field を残す。
- `max_delta_m` は 1 tick あたりの合計変位上限である。
- config file が差し替わっても、shape はこの schema を保つ。
- keyboard event は key state に集約し、その state から per-tick intent を作る。
- held key の結果は simulation-facing delta intent として扱う。
- keyboard input は viewer を直接更新しない。

### keyboard output contract

- keyboard event / key state
  -> keyboard input intent
  -> `MotionCommand.metadata["desired_endpoint_m"]`
  -> runtime target update
  -> MuJoCo state
  -> WebSocket payload
  -> viewer read-only display
- keyboard source は `MotionCommand` の command-side endpoint を作る。
- keyboard source は viewer state を直接書き換えない。
- keyboard source は `target_position_m` を primary command にしない。

## loadcell input contract

loadcellのcontinuous pathは次のchainを使用する。旧per-frame absolute-target smokeは
互換経路として残るが、この連続実行と同一とは扱わない。

### current loadcell chain

```text
injected serial frame / operator-gated serial frame
-> selfrionette/v1 parser + intrinsic normalization
-> loadcell_endpoint_mapping/v1 + same-step measured context
-> endpoint_delta_to_joint_position/v1 typed binding
-> local DLS policy + qpos feasibility
-> JointPositionCommand -> MuJoCo post-step observation
```

### contract rules

- live serialはoperator gateを持つ専用manual pathに限定する。
- keyboard / replay / programmed input fixtures を先に使う。
- `target_position_m` は viewer-facing feedback / compatibility fallback に留める。
- `target_position_m` を primary command にしない。
- continuous pathはMappingの位置増分をtyped delta routeへ渡す。`desired_endpoint_m`はcommand-side目標を示し、観測値ではない。
- `RawInputFrame.metadata` に入る command-side intent は、下流で再利用できるように保持する。
- `NormalizedLoadcellInputIntent` は raw frame と command-side endpoint の橋渡しを担う。

### existing Selfrionette bridge facts

- `plugins/input_sources/selfrionette/`はlive serialとinjected linesをbackendとして扱う。
- module import、discovery、catalog、factory constructionではserial portをopenせず、明示的な
  `start()`だけがlive serial acquisitionを開始する。
- parser / intrinsic normalization / operational endpoint mappingはsource / mapping ownerへ分離されている。
- WebSocket / viewer smoke は offline chain を前提にしている。

## viewer / transport contract

- viewer は read-only display である。
- viewer は payload v0 を受け取り、表示だけを更新する。
- WASM scene rendererは受信qposの`mj_forward`と描画に限定する。独立したphysics stepや入力からのFK/IK制御はしない。
- viewer は `target_position_m` を marker / feedback として扱うだけである。
- viewer は `endpoint_evaluation` を read-only diagnostic として扱うだけである。
- transport は serialization / delivery only である。
- transport は `target_position_m` と `metadata` を運ぶが、physics source of truth にはならない。

## input source state observability

- runtime payloadの`metadata`はoptionalなinput source stateを持てる。
- 対象は `source_kind`, `source_active`, `command_age_ms`, `stale_reason` であり、いずれも observability 用の補助情報として扱う。
- これは command-side endpoint の contract 変更ではなく、`desired_endpoint_m` や required payload fields の意味を変えない。
- normal path では `source_active=true`, `command_age_ms=0`, `stale_reason` は省略または `null` を許容する。


## Historical provenance

pre-audit inventoryとimplementation chronologyは`docs/reports/audits/canonical-content-history-separation-2026-07-16.md`へ保存した。

## 連続Selfrionetteのstep-loop経路

`select_runtime_input_source("selfrionette", line_source=..., steps=...,
control_mapping_parameters=...)`と`build_runtime_input_source_step_loop_plan`を使用する。
`run_runtime_input_source_step_loop`の一度の呼出しで複数sampleを処理し、readerの
start/close、Robot-owned model生成はsessionごとに一度とする。

continuous selectionでは固定weights/gainだけを指定できる。`current_tip_position_m`は
resolved delta routeが同stepのpre-step snapshotから供給し、selectionを変更しない。
source adapterではなくtyped routeがlocal generatorとdelta APIを選択する。
位置増分にはdtを掛けず、既存Gamepad velocityのdt積分と区別する。
同一routeの`pipeline.run_once`でも同じ指令になる。直接呼ぶ場合のreader lifecycleはcallerが管理する。

Mapping要求と運動制約後の要求は別々に記録し、実機校正値やpost-step観測と混同しない。
7x3 weights/gainは明示する。既定のzero weightsは実機calibrationではない。
観測tip欠落・非finiteはfail-closed。malformed/EOF時にはreaderをcloseする。
silent/staleのbounded取得はR7-L-P2、統合runnerはP4のgateとする。
`live_selfrionette`の旧per-frame smoke helperは互換のため残るが、連続sessionの正本ではない。
詳細は`docs/contracts/pre-hardware-signal-emulation.md`を参照する。
