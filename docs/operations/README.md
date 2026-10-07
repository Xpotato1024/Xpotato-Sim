---
status: supporting
owner: architecture
last_verified: 2026-07-30
canonical_for: []
related:
  - docs/README.md
---

# operations

反復利用する現在の操作手順と運用規則だけを置く。completion audit、inventory、Issue固有implementation evidenceは
`docs/reports/README.md`から辿る。

## repository運用

- `git-pr-workflow.md`: branch、diff、PR、head一致のgate
- `validation.md`: 変更層とfailure modeに応じたvalidation
- `codex-workflow.md`: Codex promptの共通ruleとtask-specific delta
- `japanese-doc-writing-guardrails.md`: UTF-8、BOM、mojibake、日本語方針
- `plugin-readme-templates.md`: plugin root / axis / concrete plugin READMEのsupporting template
- `code-documentation-review-checklist.md`: code documentation policyのreview checklist
- `hardware-safety.md`: serial、OSC、hardware accessのoperator gate

## runtime / viewer起動と診断

- `runtime-dry-run.md`: deterministic replayからpayload v0 NDJSONまで
- `websocket-publisher-runner.md`: local/dev WebSocket publisher
- `websocket-host-port-contract.md`: bind hostとbrowser-visible hostの分離
- `backend-viewer-startup.md`: backend、publisher、viewerの起動入口
- `live-viewer-smoke.md`: live viewer smoke
- `runtime-to-viewer-e2e-smoke.md`: backendからbrowser viewerまでのE2E診断
- `browser-visual-smoke.md`: browser-visible scene smoke
- `product-viewer-wasm-scene-renderer.md`: product-owned WASM scene renderer

## reusable test / manual procedure

- `generic-kinematics-test-doubles.md`: generic test-only FK/IK double
- `robot-runtime-plugin-conformance-tests.md`: Robot Runtime Plugin conformance suite
- `r6-l-keyboard-gamepad-live-viewer-smoke.md`: keyboard / gamepad live viewer manual smoke
- `r7-a-lite-serial-dry-run-smoke.md`: recorded fixtureによるserial dry-run
- `r7-b-manual-live-selfrionette-runtime-runner.md`: operator-gated live Selfrionette runner
- `r7-c-viewer-fixture-demo-procedure.md`: viewer fixture demo
- `r7-c-keyboard-replay-demo-package.md`: keyboard / replay demo package
- `r7-c-live-selfrionette-validation-log.md`: live Selfrionette validation procedure
- `r7-c-axis-sanity-check.md`: axis sanity protocol
- `r7-d-p3-fast-arm-endpoint-command-check-procedure.md`: no-hardware endpoint command smoke
- `r7-e-p1-fast-arm-endpoint-motion-sanity.md`: endpoint motion sanity gate
- `r7-g-deterministic-e2e.md`: R7-G manifestからevaluation artifactまでの有限deterministic software-only E2E
- `r7-h-p7-contact-e2e.md`: R7-H-P7 contact scene / raw evidence / Task / viewer payloadの有限software-only E2E

- [repository操作入口](repository-commands.md): justと既存CLI/script/npmの対応。
