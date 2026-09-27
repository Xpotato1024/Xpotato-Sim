---
status: canonical
owner: operations
last_verified: 2026-07-28
canonical_for:
  - R7-C keyboard / replay demo package
related:
  - docs/README.md
  - docs/operations/r7-c-viewer-fixture-demo-procedure.md
  - docs/reports/implementation/r7-c-manual-validation-preflight.md
  - docs/reports/audits/r7-b-completion-audit.md
  - docs/contracts/r7-b-runtime-input-pipeline-contract.md
  - docs/contracts/transport-payload.md
  - docs/operations/validation.md
---

# R7-C keyboard / replay demo package

## 目的

この文書はno-hardware demo packageを固定する。
ここで扱うのは keyboard demo command creation と replay fixture demo creation だけであり、
browser, WebSocket server, serial/COM, OSC, hardware validation は含まない。

## 範囲

- viewer はこの package では起動しない
- WebSocket server はこの package では起動しない
- serial port は開かない
- COM access は行わない
- OSC は送らない
- hardware validation は行わない

## keyboard demo command creation

keyboard demo は `build_keyboard_motion_command()` が作る `MotionCommand` を基準にする。
確認したいのは、keyboard input が `MotionCommand.metadata["desired_endpoint_m"]` を作り、
`target_position_m` を primary command にしないことだけである。

確認方法:

```powershell
uv run pytest tests/plugins/mappings/viewer_keyboard_gamepad_mapping/test_keyboard_mapping.py
```

補足:

- `resolve_desired_endpoint_from_motion_command()` で `desired_endpoint_m` を確認できる
- `MotionCommand.metadata["desired_endpoint_m"]` が command-side endpoint の確認点である
- `target_position_m` は viewer feedback / compatibility metadata のままである

## replay fixture demo creation

replay fixture demo は統一 CLI の `selfrionette replay` を使って作る。
`sweep_x` の deterministic fixture を使い、payload / metadata の形が崩れていないことだけを確認する。

推奨コマンド:

```powershell
New-Item -ItemType Directory -Force artifacts\r7-c | Out-Null
uv run xpotato-sim replay --robot fast_arm --steps 6 --preset sweep_x --output artifacts/r7-c/r7-c-234-replay-demo.ndjson
```

この出力は replay demo の local artifact であり、browser 用の viewer 生成物ではない。

## expected payload / metadata

replay demo artifact の top-level payload は payload v0 を保つ。

- `version`
- `frame_index`
- `time_s`
- `qpos`
- `qvel`
- `bodies`
- `sites`
- `target_position_m`
- `metadata`

metadata 側では少なくとも次を期待する。

- `desired_endpoint_m`
- `target_position_m`
- `source_kind`
- `frame_index`

`sweep_x` の fixture では、trajectory-specific metadata が追加されてもよい。
ただし `desired_endpoint_m` は command-side endpoint として読める形を保つ。

## desired_endpoint_m confirmation method

`desired_endpoint_m` の確認は、payload の見た目ではなく command contract で行う。

確認点:

- keyboard path は `build_keyboard_motion_command()` の戻り値を見る
- replay path は `build_motion_command_from_replay_frame()` の戻り値を見る
- どちらも `resolve_desired_endpoint_from_motion_command()` で最終確認できる
- `target_position_m` は primary command ではない

## no-hardware validation command

このprocedure自体のvalidationはdocs-onlyに限る。

```powershell
git diff --check
uv run pytest tests/architecture/test_docs_sot.py
@'
from pathlib import Path

paths = [
    "AGENTS.md",
    "docs/README.md",
    "docs/operations/r7-c-keyboard-replay-demo-package.md",
    "docs/operations/r7-c-viewer-fixture-demo-procedure.md",
]

bad_tokens = [
    "\u7e3a",
    "\u7e67",
    "\u8700",
    "\u9aea",
    "\u8b17",
    "\u9036",
    "\u8b5b",
    "\u83a0",
    "\u7e32",
    "\u0080",
]

for p in paths:
    data = Path(p).read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        raise SystemExit(f"BOM remains: {p}")
    text = data.decode("utf-8")
    found = [token for token in bad_tokens if token in text]
    if found:
        raise SystemExit(f"mojibake-like tokens remain in {p}: {found}")

print("Japanese docs encoding check passed")
'@ | uv run python -
```

## artifact / log naming policy

- `artifacts/r7-c/...` に出力する前に `artifacts/r7-c` directory を作成する
- 生成物はprocedure IDを先頭に含める
- 生成物は用途を `keyboard`, `replay`, `payload`, `log` のように明示する
- 実行ログは再利用せず、`MUJOCO_LOG.TXT` に流し込まない
- browser / WebSocket / serial / hardware の痕跡を artifact 名に混ぜない
- 長期保存が必要な成果物だけを残し、臨時検証は一時ファイルでよい

例:

- `artifacts/r7-c/r7-c-234-keyboard-command.json`
- `artifacts/r7-c/r7-c-234-replay-demo.ndjson`
- `artifacts/r7-c/r7-c-234-validation.log`
