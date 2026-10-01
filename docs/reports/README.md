---
status: supporting
owner: architecture
last_verified: 2026-07-30
canonical_for: []
related:
  - docs/README.md
---

# reports

implementation report、completion audit、inventory、review結果をevidenceとして保存する。current architecture、
contract、反復operationのsource of truthにはしない。現在仕様は`docs/README.md`から辿る。

## directory責務

- `audits/`: completion audit、retirement audit、close-readiness evidence
- `implementation/`: Issue / PR固有の実装・診断・validation report
- `inventories/`: file、public surface、stub、migration dispositionの時点snapshot
- `issues/`: Issue固有の調査report
- `reviews/`: review結果と監査記録

## 主要な入口

- [Issue #603 全体静的監査と内部整理](audits/issue603-code-audit-2026-10-01.md): 基点1056ファイルの棚卸し、精査範囲、互換維持、修正と未解決事項。

- [R7-G deterministic E2E completion audit](audits/r7-g-p5-completion-audit.md): #409の実装済み範囲、software-only観測、未証明事項、R7-H/I/J/K handoffを分類したcompletion audit。
- [R7-H-P7 contact E2E completion audit](audits/r7-h-p7-completion-audit.md): #417のfinite software fixture、contact solver / Task / log / viewer payload evidence、未実施gateを分類するcompletion audit。

- [2026-07-30 current documentation SoT audit](audits/current-documentation-sot-audit-2026-07-30.md):
  baseline `baabf057e02a8f5e29e51987b3ea25b92ecf6bc4`に対するIssue #482のcurrent SoT監査。
  #483 policy、#484 README、#485 code documentationのhandoff inventoryを含むhistorical evidence。
- [#483 documentation policy remediation inventory](inventories/documentation-policy-remediation-inventory.md):
  comment、docstring、JSDoc、README policy owner候補と未決定事項。
- [#484 README remediation inventory](inventories/readme-remediation-inventory.md):
  existing README 24件とmissing plugin / directory entry pointのremediation入力。
- [#485 code documentation remediation inventory](inventories/code-documentation-remediation-inventory.md):
  production sourceとarchitecture-sensitive support codeのdocstring、JSDoc、comment、suppression候補。
- [Viewer PoC / Planar kinematics retirement audit](audits/viewer-poc-planar-kinematics-retirement.md):
  #391で固定した#385〜#389のcompletion evidence。current architecture / operation SoTではない。
- [2026-07-16 Markdown migration snapshot](inventories/markdown-inventory.md):
  baseline `cf17fe830645c99b591615b6ffb55a42979c0d5e`に対する#398分類、#399 migration disposition、
  migration時点のrole / action / destinationを記録したhistorical evidence。current registryとして更新しない。
- [#423 fast_arm plugin boundary inventory](inventories/fast-arm-plugin-boundary-normalization.md):
  baseline `e0311688f8d9738689434a82895616c42e965c0f`に対するproduction owner、consumer、移行action、
  compatibility / defer判断のsnapshot。current boundaryはcanonical architecture / contract文書を正とする。
- [#444 fast_arm core ownership inventory](inventories/fast-arm-core-ownership-inventory.md):
  baseline `678c25d65a627cd612b4293c9160bf459ef8d5fe`に対するshared core、Selfrionette adapter、
  integration、repository operation、generic ownerの分類と#445〜#448のmigration / acceptance判断。
- [#458 Input Source Plugin ownership inventory](inventories/input-source-plugin-ownership-inventory.md):
  baseline `5ce12be54038d2a5b9d33d1ba91ac7b36bfb4dc9`に対する現行source、mapping、lifecycle、
  test ownershipと、Issue #458のhistorical snapshot。current contractの正本ではない。
- [#468 Input Source post-migration retirement inventory](inventories/input-source-post-migration-retirement-inventory.md):
  baseline `82415f5a9a62c557bdfe53afd2f1e78d61ed6a4c`に残る旧path、compatibility facade、
  wrapper、fallbackのcaller / owner分類とC2〜C4 handoff。current contractの正本ではない。
- [2026-07-16 canonical content / history separation audit](audits/canonical-content-history-separation-2026-07-16.md):
  全canonical文書のcontent review、history extraction、status再分類、抽出元commitを記録する。
  追加抽出本文は[separation supplement](audits/canonical-content-history-separation-supplement-2026-07-16.md)へ
  同じpre-audit commitのprovenance付きで保存する。
- [R7-A-lite WebSocket viewer smoke](implementation/r7-a-lite-websocket-viewer-smoke.md): #204時点のoffline smokeと完了判断。
- [R7-B input-driven WebSocket viewer smoke](implementation/r7-b-input-driven-websocket-viewer-smoke.md): #221時点のinput-driven smokeとhandoff evidence。
- [R7-C manual validation preflight](implementation/r7-c-manual-validation-preflight.md): #232 branch時点のmanual validation preflight evidence。

Markdown migration全体のpath、role、action、destinationは上記snapshotを参照する。
`docs/archive/indexes/docs-index.md`はobsoleteな旧`docs/index.md`の本文保存であり、
全Markdown migration indexではない。

historical evidenceの本文とprovenanceを現在仕様へ合わせて改稿しない。

- [R7-L全体整合の実装前調査](audits/r7-l-consistency-baseline-2026-09-20.md): #545の基点、再現欠陥、前回self監査の訂正、未確認範囲。
