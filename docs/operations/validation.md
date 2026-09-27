---
status: canonical
owner: architecture
last_verified: 2026-09-28
canonical_for:
  - validation categories
related:
  - AGENTS.md
---

# validation

validationをcategory別に報告する。

- docs-only validation（文書のみ）
- unit / compile validation（単体・compile検証）
- MuJoCo model load validation（model load検証）
- Web typecheck / build（Web静的検証）
- dry-run（非hardware実行）
- hardware validation（実機検証）

dry-run、build、typecheck、MuJoCo loadをhardware validationと呼ばない。

次は過去のskeleton lock stageの例であり、全taskの必須command一覧ではない。現在の変更層とfailure modeから必要な検証を選ぶ:

```bash
uv run pytest tests/architecture
uv run python -m compileall src tests
git diff --check
git status --short --branch
```

project environmentを利用できずcommandを実行できない場合はNot Run Reasonを報告し、ad hoc environmentを
作成しない。

同条件の検証結果の再利用、環境障害からの再開、不完全レビューの補完、統合E2Eの時期は[Codex workflow](codex-workflow.md)を正とする。新しい実行と既存証拠の再利用を区別し、変更に必要な検証は先送りしない。
