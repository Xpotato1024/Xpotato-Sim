---
status: historical
owner: implementation
last_verified: 2026-09-13
canonical_for: []
related:
  - docs/operations/unified-cli.md
  - docs/operations/backend-viewer-startup.md
  - docs/operations/websocket-host-port-contract.md
---

# scripts inventory と退役判断

Issue #436 で `scripts/` の全 file を、repository consumer、current operations docs、tests、
CI、experiment evidence、historical reference の検索結果に基づいて分類した。historical document
の当時の command は書き換えていない。

## Decision table

| script | consumer / evidence | 分類 | 判断と canonical replacement |
| --- | --- | --- | --- |
| `export_wasm_qpos_fixture.py` | viewer operations、viewer README、script test、WASM fixture provenance | developer tool として維持 | package-local resource 化せず、既存 fixture export behavior を維持 |
| `measure_loadcell_channel_response.ps1` | hardware log、serial dry-run procedure | hardware/evidence 上の理由で維持 | hardware operator gate が必要で、production CLI へ移行しない |
| `monitor_loadcell_serial.ps1` | hardware log、recorded transcript、serial procedure | hardware/evidence 上の理由で維持 | serial monitor の provenance を維持 |
| `plot_fast_arm_endpoint_trajectory_log.py` | endpoint presentation report、export test | developer diagnostic tool として維持 | robot-specific plotting を generic CLI へ載せない |
| `plot_loadcell_vectors.ps1` | experiment note | historical/evidence 上の理由で維持 | recorded hardware evidence の再表示用 |
| `run_fast_arm_endpoint_motion_sanity.py` | canonical diagnostic procedure、複数 implementation report | diagnostic/evidence 上の理由で維持 | fast_arm 固有で typed CLI capability がない |
| `run_fast_arm_jacobian_mobility_diagnostics.py` | deterministic diagnostic module/tests | diagnostic/evidence 上の理由で維持 | production runtime command ではない |
| `run_fast_arm_neutral_pose_evaluator.py` | neutral-pose implementation evidence | diagnostic/evidence 上の理由で維持 | existing evaluation evidence を保持 |
| `run_fast_arm_neutral_pose_startup_smoke.py` | neutral-pose startup evidence | diagnostic/evidence 上の理由で維持 | startup limitation の再現入口を保持 |
| `run_live_loadcell_runtime.py` | canonical manual procedure、template、script test | hardware-gated operation として維持 | serial open を含むため unified CLI へ移行しない |
| `run_live_viewer_smoke.py` | current viewer operations、README | validation entry として維持 | viewer URL/build smoke の責務は replay publisher と異なる |
| `run_loadcell_serial_dry_run.py` | canonical recorded-fixture procedure | developer validation tool として維持 | hardware を開かない serial protocol validation を保持 |
| `run_mujoco_viewer_dev.py` | stale launcher docs/test、P26 inventory で deprecate 判定 | replacement 確認後に退役 | current route は `run_live_viewer_smoke.py`、publisher は `selfrionette viewer`、host contract は canonical docsへ分離。旧 `browser:build` / static URL案内を再現しない |
| `run_replay_mujoco_dry_run.py` | input-source compatibility tests と current specialized procedures | thin wrapper として一時維持 | canonical default command は `selfrionette replay --robot fast_arm`。`--input-source` consumer 移行まで削除しない |
| `run_replay_mujoco_websocket_publisher.py` | viewer-input/gamepad procedures、browser smoke、CLI compatibility tests | thin wrapper として一時維持 | canonical replay publisher は `selfrionette viewer --robot fast_arm`。`--input-source viewer` consumer を保持 |
| `run-browser-viewer-smoke.ps1` | browser visual smoke procedure | developer validation tool として維持 | frontend process orchestration を production CLI へ組み込まない |
| `validate_github_body_structure.py` | AGENTS.md、Git/PR workflow、script tests | repository developer tool として維持 | protected long-form body gate であり runtime command ではない |
| `validate_agent_skills.py` | repository-local Skill governance、architecture tests | repository developer tool として維持 | opt-in、candidate / eval、Skill構造、invocation policyを検証し、runtime commandではない |
| `validate_markdown_docs.py` | CI、architecture tests | repository developer tool として維持 | CI command pathを維持し、production CLIへ組み込まない |
| `view_fast_arm_native_mujoco.py` | archived operator procedure | historical/evidence 上の理由で維持 | native viewer check の過去 evidence を壊さない |

## Consumer migration

README、runtime dry-run、WebSocket publisher、backend/viewer startup、E2E smoke、fixture demo、
axis sanity の current canonical command は installable CLI に同期した。viewer control input を使う
specialized procedures は、統一 CLI に未実装の behavior を失わないよう既存 wrapper を参照し続ける。

退役した dev launcher の current docs、test、script consumer は除去した。archive と過去 audit に
残る path は historical statement であり、current executable consumer ではない。

## Impact

- runtime / viewer / payload / serial protocol の値と挙動は変更していない。
- research log は更新しない。既存操作入口の分類と退役だけで研究条件・仮説・結果は変わらない。
- experiment notes は更新しない。新しい experiment condition や観測結果を取得していない。
- #437 の external plugin architecture は実装していない。

## 2026-09-13 Issue #416 追加分類

この日付の追加分類はIssue #416で導入したviewer用の決定的な合成contact-task-log fixture生成入口を記録する。Issue #436当時の分類と履歴行は変更しない。

| script | consumer / evidence | 分類 | 判断と canonical replacement |
| --- | --- | --- | --- |
| `generate_contact_task_log_demo.py` | runtime contact-task-log contract、viewer fixture test | developer validation toolとして維持 | `scripts/viewer/`に配置し、型付きlog契約から固定の合成fixtureを再生成する。MuJoCo実行・hardware evidence・runtime captureを生成しない |

## 2026-09-13 Issue #417 追加分類

この追加行はIssue #417のfinite software-only contact E2Eを追加した時点のscript inventoryである。
Issue #436当時の分類と、Issue #416の合成log demo分類は変更しない。

| script | consumer / evidence | 分類 | 判断と canonical replacement |
| --- | --- | --- | --- |
| `generate_contact_e2e_artifacts.py` | `r7-h-p7-contact-e2e.md`、`tests/integration/test_contact_e2e.py`、completion audit | developer validation toolとして維持 | copied RobotBundle assetにsoftware proxyを加え、規定qpos / `mj_forward`でcontact solverからraw evidenceを取得する。log、same-state payload、strict summaryを有限に生成する。hardware、`mj_step`、physical experimentを実行しない |

## 2026-09-20: #543のsoftware-only統合入口

`run_prehardware_signal_e2e.py`（`scripts/diagnostics/run_prehardware_signal_e2e.py`）を追加した。local fixtureとoutputだけを受け取り、port/host/enableは持たない。既存retirementの履歴・移行数は変更しない。現行手順はpre-hardware-signal-emulation contractへ委譲する。

## 2026-10-02: #610の性能計測入口

既存の分類・退役履歴を維持し、今回追加した再測定用の4ファイルを明示登録する。

| script / fixture | consumer / evidence | 分類 | 判断 |
| --- | --- | --- | --- |
| `measure_simulation_hot_path.py` | #610性能note、実MuJoCoの基点・候補比較 | developer diagnostic | profiler付き内訳と通常tickの計測を分離する。production起動経路ではない |
| `measure_pinch_checkpoint.py` | #610反対面接触のstate/force parity | developer diagnostic | 同じ100 mm cube接触checkpointの有限比較。実機・把持成功の証明ではない |
| `pinch-checkpoint.json` | 上記pinch測定script | diagnostic fixture | 同じqpos/ctrlの入力条件を固定する。実測geometryの正本ではない |
| `measure_workbench_latency.py` | #610の固定ブラウザ→実MuJoCo→CPU描画提出 | developer diagnostic | 今回専用Chromiumのendpointだけを使い、既存ブラウザを操作・終了しない |

## 2026-10-07: 引継ぎ用ローカル起動入口

`workbench_local.py`（`scripts/workbench_local.py`）はroot `.env`と専用環境・固定buildの準備を所有し、
justと直接Pythonで同じ正式CLIを組み立てる。旧退役launcherの再導入ではない。
通常起動では同期・npm・buildを行わず、physics、入力、試行、保存formatのownerを追加しない。
現行操作手順は[root README](../../../README.md)と[統一CLI](../../operations/unified-cli.md)へ委譲する。
