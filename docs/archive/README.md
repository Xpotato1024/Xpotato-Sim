---
status: supporting
owner: architecture
last_verified: 2026-07-16
canonical_for: []
related:
  - docs/architecture/documentation-sot-policy.md
---

# archive

現在仕様または反復運用の責務を終えた文書を、provenanceを維持して保存する。archive内の文書は
historical / retired evidenceであり、current architecture、contract、operationのsource of truthとして扱わない。

[Gamepad平面操作契約](historical/gamepad-plane-control.md)は#605で退役したhistorical記録である。
過去リンクの所在を保持し、現行SoT Mapからは除外した。
既存本文を現在仕様に合わせて改稿せず、必要な場合だけstatus、current SoTへの参照、retired注記を追加する。

- `drafts/`: 未確定のまま終了したdraft
- `proposals/`: 採用前またはretiredしたproposal
- `design/`: 過去時点のdesign note
- `research/`: 過去の調査・比較記録
- `operations/`: 現在の反復手順ではない過去のoperation note
- `historical/`: その他のhistorical record
- `obsolete/`: current useを終了した文書
- `indexes/`: obsoleteな旧index本文のprovenance保存

retired operation note:

- [native MuJoCo fast_arm viewer check](operations/native-mujoco-fast-arm-viewer-check.md): PR #174時点の観察、判断、remaining riskを保存したdraft

全Markdownのmigration dispositionは`../reports/inventories/markdown-inventory.md`から確認する。
`indexes/docs-index.md`は旧R6-K indexの保存であり、全Markdown migration indexではない。
