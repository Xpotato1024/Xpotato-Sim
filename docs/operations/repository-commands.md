---
status: canonical
owner: operations
last_verified: 2026-10-07
canonical_for:
  - repository operation command routing
related:
  - docs/operations/backend-viewer-startup.md
  - docs/operations/validation.md
  - docs/contracts/workbench.md
---

# repository操作入口

初回準備と通常Workbench起動は[root README](../../README.md)の
`just init/setup/build/run/dev/doctor`を使う。以下は準備済みrepository rootで実行する。
justの導入方法もroot READMEへ集約する。

## 操作の対応

追加recipeは`scripts/repository/commands.py`へargvを渡し、既存CLI/script/npm commandを呼ぶ。
option、default、検証、physics、保存形式の正本は既存実装である。
`just --list`で一覧、各recipeの`--help`で既存実装のoptionを確認する。

| just入口 | 委譲先 |
| --- | --- |
| `just profile` | `xpotato-sim profile` |
| `just app --profile sim-keyboard --no-browser` | `xpotato-sim app` |
| `just replay` | `xpotato-sim replay` |
| `just viewer-publisher` | `xpotato-sim viewer` |
| `just trial` | `xpotato-sim trial` |
| `just test` / `just test-python` | `pytest tests` |
| `just lint` | repositoryのRuff correctness検査 |
| `just typecheck` / `just launcher-typecheck` | 既存mypy対象 / local launcher |
| `just compile` | `compileall src tests scripts` |
| `just docs-check` | `validate_markdown_docs.py` |
| `just github-body-check` | `validate_github_body_structure.py` |
| `just viewer-test` / `just viewer-typecheck` / `just viewer-build` | Viewerのnpm test/typecheck/build |
| `just viewer-smoke` | `run_live_viewer_smoke.py` |
| `just selfrionette-dry-run` | 記録済みserial fixtureのoffline検証 |
| `just fast-arm-motion-sanity` | 既存FastArm software診断 |
| `just check` | lint、2つのtypecheck、compile |
| `just check-all` | check、Python全件test、Viewer test/typecheck/build |

`check`と`check-all`は必須の順序だけを組み立てる。変更Markdownのbase指定は
`just docs-check --base-ref origin/main --strict-map --strict-links`を別に実行する。
CIは既存job/stepを保持し、失敗箇所を単一recipeへ隠さない。

## 引数と環境

追加recipeはjustのPython script recipeとpositional argumentsを使う。
引数をshell文字列へ埋め込まず、空白、日本語、引用符、空文字、shell記号をargvのまま転送する。
root/親のdotenvは自動読込みしない。uvは`--no-sync --no-env-file --offline`で準備済みprojectを使い、
recipeの実行で依存同期やPythonダウンロードを行わない。
不足環境は先にREADMEの明示setupで準備する。

同等の直接入口は次のとおり。justも共通scriptもruntimeの必須依存にしない。

```powershell
uv run --no-sync --no-env-file --offline xpotato-sim replay --robot fast_arm --steps 2
uv run --no-sync --no-env-file --offline python scripts/repository/commands.py replay --robot fast_arm --steps 2
just replay --robot fast_arm --steps 2
```

child終了codeはWindows/Unixで保持し、operator interruptionは130とする。
Python childはUTF-8 modeを明示し、Windows CP932環境でもUnicode回収ログを出力できる。
npmの実行fileはPATHから解決する。`viewer-build`は既存npm buildであり、
Workbenchの所有済み外部固定buildを更新する`just build`とは用途が異なる。

## 存続する入口

Workbenchの通常入力はGamepadだけである。Keyboard、旧v1 profile、replay、単独Viewer診断は
`app`/`viewer`を維持し、古いprofileをWorkbenchへ暗黙変換しない。
Selfrionette live、serial monitor/measure、Arduino、OSC、実機操作は既存operator gateを維持する。
PowerShellのmonitor/measure/plotとbrowser smokeは#456の残件であり、今回薄いlauncher化が完了したとは扱わない。
追加recipeはそれらの実機操作を自動実行しない。
