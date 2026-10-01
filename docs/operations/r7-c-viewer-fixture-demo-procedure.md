---
status: canonical
owner: operations
last_verified: 2026-07-28
canonical_for:
  - R7-C viewer fixture demo procedure
related:
  - docs/README.md
  - docs/reports/implementation/r7-c-manual-validation-preflight.md
  - docs/operations/backend-viewer-startup.md
  - docs/operations/websocket-publisher-runner.md
  - docs/operations/runtime-to-viewer-e2e-smoke.md
  - docs/reports/audits/r7-b-completion-audit.md
  - docs/contracts/r7-b-runtime-input-pipeline-contract.md
  - docs/contracts/transport-payload.md
  - apps/mujoco-viewer/README.md
---

# R7-C viewer fixture demo procedure

## 目的

この文書はmanual validationで使う viewer launch, fixture demo, keyboard demo の手順を固定する。
ここでいう demo は docs-only の procedure であり、CI や bot が actual browser, WebSocket server, serial, COM, hardware を触る手順ではない。

## 前提

- viewer は rendering-only である
- MuJoCo は physical source of truth である
- `desired_endpoint_m` は command-side endpoint である
- `target_position_m` は viewer-facing feedback / compatibility field である
- `endpoint_evaluation` は optional diagnostic overlay である
- `file://` ではなく HTTP server 経由で viewer を開く

## viewer launch procedure

1. `cd apps/mujoco-viewer`
2. `npm ci`
3. `npm run dev -- --host 127.0.0.1 --port 5173`
4. browser で `http://127.0.0.1:5173/apps/mujoco-viewer/?websocketUrl=ws://127.0.0.1:8766` を開く

旧`?ws=`は退役エラーとして起動を停止する。`?websocketUrl=ws://127.0.0.1:8766`を使用する。
viewer page URL と WebSocket endpoint URL は別であり、`websocketUrl` を primary とする。
この手順は人間が実行する manual demo 用であり、Codex / CI は dev server や browser を起動しない。

## fixture demo procedure

fixture demo は deterministic replay fixture の `sweep_x` を使う。
publisher は loopback の `127.0.0.1:8766` を基本にする。

1. 端末 A で repo root から publisher を起動する

```powershell
cd <repository root>
uv run xpotato-sim viewer --robot fast_arm `
  --host 127.0.0.1 `
  --port 8766 `
  --steps 6 `
  --interval-s 0.033 `
  --grace-period-s 60 `
  --preset sweep_x
```

2. 端末 B で `apps/mujoco-viewer` から dev server を起動し、viewer URL を開く
3. `Connection` が `open` になることを確認する
4. `Status` panel と canvas が最新 frame を保持することを確認する
5. publisher 側の replay が終わっても、viewer が last payload を保持することを確認する

## keyboard demo procedure

keyboard demo は現時点では offline-only であり、browser-side keyboard controller は前提にしない。
manual demo ではなく contract smoke として扱い、次の tests を確認する。

```powershell
uv run pytest `
  tests/plugins/mappings/viewer_keyboard_gamepad_mapping/test_keyboard_mapping.py `
  tests/runtime/test_r7_b_offline_input_runtime_stepping_smoke.py `
  tests/runtime/test_r7_b_input_driven_payload_smoke.py
```

確認ポイント:

- 既定keybindが
  `src/xpotato_sim/plugins/mappings/viewer_keyboard_gamepad_mapping/resources/keyboard_default.json`
  にあり、Mapping plugin package resourceとして配布される
- WASD / Space / Shift が `desired_endpoint_m` を作る
- `build_keyboard_motion_command()` が `MotionCommand.metadata["desired_endpoint_m"]` を埋める
- offline runtime smoke が `target_position_m` を viewer feedback / fallback として扱う
- `endpoint_evaluation` は optional のままで、欠けても smoke は落ちない

## expected UI / overlay confirmation items

viewer launch または fixture demo では、少なくとも次を確認する。

- `Connection` が `open`
- `Renderer mode` が `wasm-scene`
- `Pose source` がMuJoCo `home` keyframeかreceived payloadに応じて切り替わる
- `Qpos status` が `ready`
- canvas に floor, axes, fast_arm mesh が見える
- target marker が見える
- tip marker が見える
- target / tip の差分を示す error vector が見える
- arm skeleton fallback が見える
- DoF ring が presentation-only として見える
- `Endpoint evaluation` section が表示される
- `Endpoint evaluation: unavailable` は optional diagnostic が欠けたときの正常表示である

Endpoint evaluation が存在する場合は、overlay の次の行を読む。

- `Desired`
- `qpos-like joint angles`
- `FK`
- `Site`
- `Desired -> FK error`
- `Desired -> site error`
- `FK -> site error`
- `Frames`
- `Note`

## how to read `desired_endpoint_m`, `target_position_m`, `endpoint_evaluation`

- `desired_endpoint_m` は command-side endpoint である
- viewer は `Desired` 行を `desired_endpoint_m` として読む
- `target_position_m` は viewer-visible feedback であり、marker positioning と compatibility のために残る
- `target_position_m` は primary command ではない
- `endpoint_evaluation` は read-only diagnostic overlay である
- `endpoint_evaluation` がある場合でも control truth source にはしない
- `endpoint_evaluation` が missing または malformed なら `Endpoint evaluation: unavailable` として扱う
- `desired_endpoint_m` と `target_position_m` は同じ frame で異なっていてよい
- `desired_endpoint_m` と `endpoint_evaluation.desired_endpoint_m` は整合を確認するために読む

## smoke pass / fail checklist

### pass

- browser で HTTP URL を開いた
- `Connection` が `open` になった
- publisher が `payload v0` を配信した
- last payload frame が保持された
- target marker, tip marker, error vector, arm skeleton, fast_arm mesh, DoF ring が見えた
- `Endpoint evaluation` が `available` のときは overlay の各行が読めた
- `Endpoint evaluation` が `unavailable` のときは viewer が落ちなかった
- `desired_endpoint_m` と `target_position_m` の意味を取り違えなかった

### fail

- `file://` で viewer を開いた
- `Connection` が `disabled`, `connecting`, `closed`, `error` のまま終わった
- browser で payload v0 が読めなかった
- viewer が FK / IK / qpos を browser 側で再計算しているように見えた
- `endpoint_evaluation` を control truth source として扱った
- serial, COM, OSC, hardware access をこの手順で実行した

## known limitations

- browser automation は含まない
- actual browser launch は人手で行う
- actual WebSocket server launch は人手で行う
- live serial, COM, hardware, OSC は含まない
- keyboard demo は browser-side interaction ではなく offline contract smoke である
- `assets/mujoco/fast_arm/fixtures/fast_arm_sweep_x_qpos.json` は fast_arm Robot Plugin resourceが所有する canonical debug fixture である。生成は `uv run python scripts/viewer/export_wasm_qpos_fixture.py --preset sweep_x --steps 30` を使う
- fixture generation が失敗した場合、exporter は既存の canonical file を変更しない。生成後は frame index の連続性、simulation time の単調増加、qpos の有限性と dimension、sweep progression、BADQACC warning がないことを確認する。現在の canonical fixture SHA-256 は `4925D77535A67ED0E4EB68BDCC0B66C262D2D11AE5E1F7DCA99C3AE5E38D312A` である
- viewer は rendering-only のままで、FK / IK / qpos recompute をしない

## CI boundary

CI はこの手順の実地部分を実行しない。
CI / tests で行うのは docs-only validation と contract smoke までであり、actual browser, WebSocket server, serial, COM, hardware access は行わない。
