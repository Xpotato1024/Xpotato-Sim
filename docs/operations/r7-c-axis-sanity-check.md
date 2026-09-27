---
status: canonical
owner: operations
last_verified: 2026-07-28
canonical_for:
  - R7-C axis sanity check protocol
related:
  - docs/README.md
  - docs/operations/r7-c-live-selfrionette-validation-log.md
  - docs/experiment-notes/templates/r7-c-axis-sanity-check-template.md
  - docs/operations/r7-c-keyboard-replay-demo-package.md
  - docs/operations/r7-c-viewer-fixture-demo-procedure.md
  - docs/operations/hardware-safety.md
---

# R7-C axis sanity check

## 目的

この文書は #236 の axis sanity check protocol を固定する。
これは中間発表前の sanity check であり、physical axis finalization、
force unit calibration、final mapping ではない。

Codex / CI は browser、WebSocket server、serial、COM、hardware、OSC を実行しない。
live Selfrionette の観測は #235 のtemplateに記録されたhuman-run evidenceを読むだけである。

## 判定範囲

- keyboard axis sanity check
- replay / fixture sanity check
- manual live Selfrionette observation checklist
- expected observation / actual observation の記録
- sign inversion / axis mismatch の記録
- pass / caution / fail の判定

## keyboard axis sanity check

keyboard path は no-hardware contract smoke として扱う。

```powershell
uv run pytest tests/plugins/mappings/viewer_keyboard_gamepad_mapping/test_keyboard_mapping.py
```

確認すること:

- WASD / Space / Shift が `desired_endpoint_m` を生成する
- x / y / z の期待方向を operator が説明できる
- `target_position_m` を primary command として扱わない
- browser-side keyboard controller の実操作とは主張しない

## replay / fixture sanity check

replay / fixture path は deterministic `sweep_x` を使う。

```powershell
New-Item -ItemType Directory -Force artifacts\r7-c | Out-Null
uv run xpotato-sim replay --robot fast_arm --steps 6 --preset sweep_x --output artifacts/r7-c/r7-c-236-replay-axis-sanity.ndjson
```

確認すること:

- `metadata["desired_endpoint_m"]` が存在する
- `target_position_m` は viewer feedback / compatibility field である
- x 方向 sweep の expected observation と actual observation を比較できる
- payload v0 schema を変更していない
- browser / WebSocket server をこの protocol では起動しない

## live Selfrionette manual observation checklist

live Selfrionetteは#235のlog templateに記録されたhuman-run observationだけを参照する。
Codex / CI は live serial を開かない。

確認すること:

- `metadata["source_kind"] == "selfrionette"`
- observed frame count が 0 ではない
- `metadata["desired_endpoint_m"]` が存在する
- no OSC / no robot output / no actuator command が確認済み
- pyserial unavailable の場合は caution または fail として記録されている

## expected / actual observation

記録は [r7-c-axis-sanity-check-template.md](../experiment-notes/templates/r7-c-axis-sanity-check-template.md)
を複製して行う。

最低限、次を記録する。

- input source
- expected axis direction
- actual observed direction
- expected sign
- actual sign
- sign inversion suspected
- axis mismatch suspected
- confidence
- pass / caution / fail

## pass / caution / fail criteria

### pass

- expected と actual の axis direction が一致する
- sign inversion が疑われない
- `desired_endpoint_m` が確認できる
- no robot output / no OSC / no actuator command が確認済み
- physical axis finalization と誤解していない

### caution

- expected と actual は大きく矛盾しないが、operator confidence が低い
- live Selfrionetteでpyserial unavailableまたはframe countが少ない
- sign inversion は未確定だが追加確認が必要
- viewer / payload の観測はできるが browser E2E としては未実施

### fail

- expected と actual の axis direction が逆または別 axis に見える
- sign inversion / axis mismatch が強く疑われる
- `desired_endpoint_m` が欠ける
- safety confirmation が欠ける
- OSC / robot output / actuator command の可能性が見える

## 非対象

- physical axis finalization
- force unit calibration
- final loadcell-to-axis mapping
- actuator command
- real robot output
- OSC send
- firmware upload / modification
- browser E2E automation
- WebSocket server launch by Codex / CI
