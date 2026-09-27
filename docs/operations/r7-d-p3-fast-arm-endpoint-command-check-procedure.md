---
status: canonical
owner: operations
last_verified: 2026-07-16
canonical_for:
  - R7-D-P3 fast_arm endpoint command check procedure
related:
  - docs/README.md
  - docs/reports/implementation/r7-d-p1-fast-arm-4dof-endpoint-ik.md
  - docs/contracts/target-marker-desired-endpoint.md
  - docs/contracts/transport-payload.md
  - docs/architecture/data-flow.md
  - docs/operations/backend-viewer-startup.md
  - docs/operations/websocket-host-port-contract.md
  - docs/operations/live-viewer-smoke.md
  - apps/mujoco-viewer/README.md
---

# R7-D-P3 fast_arm endpoint command check procedure

## Purpose

この手順はfast_arm endpoint commandをno-hardwareで再現確認し、runtime / viewer / transportの観測点をoperatorが確認する方法を固定する。

## Scope

- fast_arm endpoint command の no-hardware 確認手順を固定する。
- viewer / backend の起動手順を固定する。
- `qpos[0:4]`、`qpos[2]`、`qpos[3]`、`target_rejected`、`endpoint_evaluation` の確認点を固定する。
- reject / hold / recovery と MuJoCo stability warning の扱いを固定する。
- 実装コードの behavior change は原則行わない。

## Preconditions

- current canonical runtime / viewer implementationを含むcheckoutであること。
- local treeがcleanであること。
- no-hardware で実施すること。

## No-hardware policy

- serial port は開かない。
- OSC は送らない。
- Arduino upload は行わない。
- real robot output は行わない。
- hardware validation は行わない。
- browser-side FK / IK / qpos recompute は行わない。
- browser-side MuJoCo model loading を手順に追加しない。

## Startup procedure

### Backend command

viewer control を使った manual 確認では、backend は viewer input source で起動する。

```powershell
uv run xpotato-sim viewer --robot fast_arm `
  --host 127.0.0.1 `
  --port 8766 `
  --steps 120 `
  --interval-s 0.033 `
  --grace-period-s 60 `
  --input-source viewer
```

### Viewer command

```powershell
cd apps\mujoco-viewer
npm ci
npm run dev -- --host 127.0.0.1 --port 5176 --strictPort
```

### Browser URL

```text
http://127.0.0.1:5176/apps/mujoco-viewer/?websocketUrl=ws://127.0.0.1:8766
```

### Startup logs to confirm

- backend stdout に `serving on ws://127.0.0.1:8766` が出ること。
- backend stdout に `Viewer connected; publishing started.` が出ること。
- viewer status で `Connection: open` が見えること。
- viewer status で `Qpos status: ready` が見えること。
- viewer status text で `browser-side IK/FK/qpos recompute: disabled` が残っていること。

### Connection check

- viewer の `Runtime` パネルで `Connection` が `open` になること。
- `Status` パネルで `Endpoint evaluation` が更新されること。
- `Input overlay` で `input source`、`active`、`stale reason` を確認できること。

## Basic endpoint command check

1. viewer を開いた状態で、small positive `x` command を 1 回だけ送る。
2. 同じ要領で small positive `y` command を 1 回だけ送る。
3. 同じ要領で small positive `z` command を 1 回だけ送る。
4. 各入力ごとに target marker と tip site の変化を確認する。

入力は viewer の既存 keyboard / gamepad binding を使う。新しい binding は追加しない。

確認点:

- target marker が入力方向に動くこと。
- actual MuJoCo tip site が desired endpoint 方向へ動くこと。
- viewer は read-only のままであること。
- viewer が FK / IK / qpos を再計算していないこと。

確認先:

- `Canvas` フッターの `Current qpos`
- `Endpoint evaluation` パネルの `Desired` / `Site` / `Desired -> site error`
- browser DevTools の WebSocket frame payload

## qpos[0:4] check

`qpos` は transport payload の top-level フィールドとして観測する。

確認方法:

- browser DevTools の WebSocket frame で payload の `qpos` を見る。
- viewer `Canvas` フッターの `Current qpos` を見る。
- `Endpoint evaluation` パネルの `qpos-like joint angles` を見る。

期待値:

- `qpos[0]`、`qpos[1]`、`qpos[2]`、`qpos[3]` が出力されていること。
- `endpoint_evaluation.qpos_like_joint_angles_rad` が 4 要素であること。
- `qpos[2]` / `qpos[3]` が fast_arm endpoint command に対して非ゼロの solver output として出ていること。

## qpos[2] / qpos[3] zero-padding regression check

このprocedureでは、`qpos[2]` / `qpos[3]` が zero padding に戻っていないことを確認する。

確認方法:

- small x / y / z command を入れた後に `Current qpos` の 3, 4 番目が `0.0, 0.0` のままではないことを確認する。
- browser DevTools の WebSocket frame で `qpos` を確認し、`qpos[2]` / `qpos[3]` が更新されていることを確認する。
- `endpoint_evaluation.qpos_like_joint_angles_rad` の末尾 2 要素が 0 固定でないことを確認する。

失敗条件:

- `qpos[2]` / `qpos[3]` が zero padding に戻る。
- viewer 側で 2-link planar 由来の古い挙動が再発する。
- target marker は動くが tip site が追従しない。

## actual MuJoCo tip site check

actual MuJoCo tip site は payload の `sites` に含まれる `tip` を基準に確認する。

確認方法:

- `Endpoint evaluation` パネルの `Site` 行を見る。
- browser DevTools の WebSocket frame で payload の `sites` 配列から `name == "tip"` を見る。
- `Desired -> site error` の norm が入力後に意図方向へ改善するかを見る。

確認の見方:

- `Desired` は command-side endpoint である。
- `Site` は MuJoCo world / scene frame の実 tip site である。
- `Desired -> site error` の norm が小さくなる、または少なくとも悪化しないことを確認する。

## repeated input check

small な連続入力を数 step 入れて、安定性と継続性を確認する。

確認方法:

- 連続する small x / y / z command を数 step 入れる。
- viewer `Input overlay` で `active: yes` と `stale reason: none` を維持できているか見る。
- `Current qpos` が不連続に大きく飛ばないことを見る。
- `qpos[2]` / `qpos[3]` が zero padding に戻らないことを見る。

期待値:

- `target_rejected` が通常は出ない。
- `qpos` が滑らかに更新される。
- `Desired -> site error` が妥当な範囲で推移する。

## reject / hold / recovery check

boundary / unreachable / non-convergence / discontinuity が起きた場合は、拒否・保持・回復の順に確認する。

確認方法:

- browser DevTools の WebSocket frame で `metadata.target_rejected` を確認する。
- `target_rejection_reason` と `target_rejection_message` を確認する。
- `rejected_desired_endpoint_m` を確認する。
- その frame で `endpoint_evaluation` が欠けるか、少なくとも available にならないことを確認する。

確認の見方:

- `target_rejected` が出た入力は次回入力基準にしない。
- hold では current qpos を保つ。
- reverse direction input で recovery できることを確認する。
- recovery 後は `target_rejected` が消え、`Current qpos` と `Site` が再び更新されることを確認する。

## MuJoCo stability warning handling

runtime 系では `Nan, Inf or huge value in QACC` 系の warning が出る可能性がある。

扱い:

- warning-only か crash / fail かを分けて記録する。
- warning が出ても targeted tests が pass し、backend が継続し、payload が有効であれば warning-only とする。
- warning が出た frame の `frame_index`、`qpos`、`endpoint_evaluation`、`target_rejected`、`target_rejection_reason` を記録する。

この issue では warning の完全解消を主目的にしない。

## Pass / Warning / Fail criteria

### Pass

- backend と viewer が接続できる。
- small x / y / z command で target marker が入力方向に動く。
- actual MuJoCo tip site が desired endpoint 方向へ動く。
- `qpos[0:4]` が確認できる。
- `qpos[2]` / `qpos[3]` が zero padding に戻らない。
- repeated input で不連続な大ジャンプがない。
- reject / hold / recovery が説明どおりに動く。

### Warning

- `Nan, Inf or huge value in QACC` warning が出るが、process は継続し、payload は有効で、観測結果が壊れていない。
- reject は出たが、理由と保持挙動が期待どおりで、recovery もできる。
- `endpoint_evaluation` が一時的に unavailable だが、理由が説明できる。

### Fail

- viewer が接続できない。
- backend が crash する。
- `qpos[2]` / `qpos[3]` が zero padding に戻る。
- target marker だけ動いて tip site が追従しない。
- reject 後に rejected endpoint を次回入力基準にしてしまう。
- viewer が FK / IK / qpos を再計算する。

### 中間発表で言えること

- fast_arm endpoint command の no-hardware 再現確認ができた。
- qpos と tip site の両方で、4DOF endpoint command の追従を確認できた。
- reject / hold / recovery の境界が観測できた。
- warning は記録済みだが、今回は warning-only として扱う。

### 中間発表で言わないこと

- QACC warning を完全解消した。
- hardware validation を実施した。
- real robot output を出した。
- viewer 側で FK / IK / qpos を再実装した。

## Manual smoke record template

```text
Date:
Branch / PR:
Commit SHA:
Backend command:
Viewer URL:
Input source:
Small x check:
Small y check:
Small z check:
qpos[0]:
qpos[1]:
qpos[2]:
qpos[3]:
qpos[2:4] zero padding? yes / no
target_rejected observed? yes / no
target_rejection_reason:
actual tip moved toward desired endpoint? yes / no
MuJoCo warning observed? yes / no
Warning text:
Recovery after reject checked? yes / no
Result: pass / warning / fail
Notes:
```

## Known limitations

- `Nan, Inf or huge value in QACC` warning の完全解消はこのprocedureの主目的ではない。
- no-hardware 確認なので、実機性能や物理接触は検証しない。
- viewer は read-only であり、FK / IK / qpos recompute は行わない。
- serial / OSC / hardware との実通信はしない。
