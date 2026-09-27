---
status: canonical
owner: operations
last_verified: 2026-07-30
canonical_for:
  - R7-E-P1 fast_arm endpoint motion sanity
related:
  - docs/README.md
  - docs/operations/r7-d-p3-fast-arm-endpoint-command-check-procedure.md
  - docs/contracts/target-marker-desired-endpoint.md
  - docs/contracts/mujoco-model-name-contract.md
  - docs/architecture/runtime-composition.md
---

# R7-E-P1 fast_arm endpoint motion sanity

## 目的

この文書はcube task前のgateとして、`fast_arm` の初期 `tip`
site 位置から `x / y / z` 方向へ small endpoint command を与えたときの動きを
確認・記録・説明する手順を固定する。

ここでは cube scene、contact metric、比較評価には進まない。確認対象は
backend / MuJoCo runtime を source of truth とする `tip` site の変化、`qpos[0:4]`、
`desired_endpoint_m`、`target_position_m` の関係である。

## default initial-tip mode

cube task前のgateでは、default mode を使う。

```text
desired_endpoint_m = initial_tip_position_m + small command delta
```

この mode では、各 axis case ごとに pipeline を作り、最初に backend snapshot から
`initial_tip_position_m` を読む。その後に `desired_endpoint_m` を作り、1 step 実行
して `actual_delta_m = final_tip_position_m - initial_tip_position_m` を比較する。

`DEFAULT_CONCRETE_TARGET_POSITION_M + delta` は default sanity として扱わない。
それは absolute target へ向かう大きな移動を見てしまい、初期 `tip` 位置からの small
command sanity ではなくなるためである。

## explicit base mode

任意の base からの確認が必要な場合だけ、explicit base を指定する。

```powershell
uv run python scripts/diagnostics/fast_arm/run_fast_arm_endpoint_motion_sanity.py --base-desired-endpoint-m 0.6 0.0 0.1
```

この場合は次を使う。

```text
desired_endpoint_m = explicit_base_endpoint_m + small command delta
```

result では `base_endpoint_source=explicit` として記録する。

## 実行方法

標準確認:

```powershell
uv run python scripts/diagnostics/fast_arm/run_fast_arm_endpoint_motion_sanity.py
```

標準出力には axis ごとに少なくとも次が出る。

- `base_endpoint_source`
- `base_endpoint_m`
- `commanded_delta`
- `initial_tip`
- `final_tip`
- `desired_endpoint_m`
- `target_position_m`
- `qpos_before`
- `qpos_after`
- `direction_dot`

## 判定

- `pass`: command の主軸と `tip` movement の主軸・符号が一致した。
- `rejected`: solver / runtime が command を明示的に拒否した。
- `limitation`: command は通ったが、現 solver の制約や frame mismatch のため期待方向
  としては説明が必要。
- `unavailable`: initial `tip` が読めない、backend exception などで result を作れない。

`+y / -y` が `pass` しない場合は、現 solver / fast_arm IK v0 の limitation として
記録してよい。ただし backend crash や unexplained jump は許容しない。

## cube task に進める条件

- default initial-tip mode で x / z small command の結果が説明できる。
- y direction が limitation の場合は `reason` が明示される。
- `desired_endpoint_m` と `target_position_m` の役割が混同されていない。
- viewer は read-only のままで、MuJoCo / FK / IK / qpos recompute を持たない。

## 中間発表で言えること

- fast_arm の初期 `tip` site 位置から endpoint command を与え、MuJoCo 上の `tip`
  site の変化を axis ごとに確認する sanity procedure を追加した。
- `pass / rejected / limitation / unavailable` を明示的に記録できる。
- `desired_endpoint_m` は command-side endpoint、`target_position_m` は viewer /
  compatibility feedback として扱う。

## 中間発表で言いすぎてはいけないこと

- 完全な 3D IK が完成した。
- 任意の 3D target に自然に到達できる。
- 実機 fast_arm の軸整合が完了した。
- cube を物理的に押せることを確認した。

## 参考実装

- diagnostic owner: `src/xpotato_sim/plugins/robots/fast_arm/adapter/diagnostics/endpoint_motion_sanity.py`
- CLI script: `scripts/diagnostics/fast_arm/run_fast_arm_endpoint_motion_sanity.py` を使用する。
- 既存 procedure: `docs/operations/r7-d-p3-fast-arm-endpoint-command-check-procedure.md`
