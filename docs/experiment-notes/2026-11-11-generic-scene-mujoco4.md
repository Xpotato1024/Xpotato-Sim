---
status: supporting
owner: research
last_verified: 2026-11-11
canonical_for: []
related:
  - docs/contracts/object-scene-contact-diagnostic.md
  - docs/contracts/launch-profile.md
  - docs/contracts/finite-trial-runtime.md
  - docs/operations/product-viewer-wasm-scene-renderer.md
---

# MuJoCo 4.0.0：通常Sceneのsphere / flex / Robotなし実行

## 環境・権限

- 元のmain：`2bd8ca2b68bc57c6fece51675391eeb9e81de549`。別worktree `codex/generic-mj4-scenes-20261111` で変更。
- LLM-01、Windows、Python 3.12.13、MuJoCo native 4.0.0。
- ロボット、入力装置、OSC、実機、deployなし。3.9.0/4.0.0固定比較用worktreeは変更しない。
- 固定モデル・仮想環境のローカル実行であり、現物材料の同定や参加者評価ではない。

## 保存可能な通常Scene

`object_scene_environment/v1` のpresetから明示選択する。専用の物理loopなし。

| preset | 内容 | 物理の重要設定 |
|---|---|---|
| `sphere_wall` | 半径10 mmのsphere、厚さ10 mmの固定box壁、初速度12 m/s | 初期中心x=-120 mm、20 ms、Euler、CG、重力なし |
| `elastic_plastic_sheets` | 同じ球と2枚の13×13格子2D flex | 3 ms、discrete、CG、重力あり |

既存の `ObjectDefinition` へsphereをversion付きで追加。柔軟体は型付き `flex-grid/v1` をMJCFの`flexcomp`へ投影する。材料の値は上流の例を参考にしたデモ条件であり、実測値ではない。

## 観測された実行結果

- `examples/scene-sphere-wall` / `...-speculative` の同一MuJoCo 4.0.0 flag OFF/ONを比較。
- OFFは20 ms後の球中心x=+120 mm（壁を通過）、ONはx=-17.1 mm（壁手前にとどまる）。初期位置は両方x=-120 mm。3.9.0との比較ではない。
- `examples/scene-elastic-plastic` の120 tickを通常 `TrialRunner` で保存。simulation時間1.8 s、`recording=complete`、`runner_stop_reason=simulation_budget`、Task判定なし。
- 1.8 sの全flex頂点の初期状態との差のRMSは弾性0.0004889420600824247 m、弾塑性0.013594226370945893 m（約0.489 / 13.594 mm）。頂点は各169個。RMSは材料ひずみや最大へこみを示さない。
- 最終plastics状態L2ノルム21750.807697877644。L2ノルムそのものは物理的な塑性ひずみの単位付き評価値と呼ばない。

柔軟体への衝突球は半径30 mm・質量0.44 kg（左右とも同条件）。柔軟体側のYoung=10 MPa、Poisson=0.3、厚さ4.8 mm、減衰設定0.010で、弾塑性側に `yield="0.00016 0.0016"`。初期条件・材料設定の詳細は登録済みresourceのJSONを正本とする。

## 記録と媒体

通常有限試行の `condition.json`、`start.json`、`samples.jsonl`、`terminal.json`、`final-state.json` を保存。
各tickの完全なnative integration state（`mjSTATE_INTEGRATION`、塑性を含む）を`scene_state_checkpoint_v1`として保存し、再解析でversion/model/time/qpos/qvel/state長を照合した。

`scripts/viewer/export_trial_video.py` から読み出し、`mj_setState` / `mj_forward` で再描画する。export時に`mj_step`で再積分しない。FFmpegで同じフレーム列からGIF・MP4・WebMを出力する。

予備素材（800×500、20 fps、0.5倍速）の実ファイル容量は柔軟体GIF 699,098 bytes、MP4 44,196 bytes、WebM 31,432 bytes。別codec/品質条件であり同品質比較ではない。これらは**コミット前の開発worktreeで作成した予備観測**であり、最終PR headでの再実行・最終媒体hashが確定した場合はそちらを正として記録する。

**制限**：現時点のScene単独の動画はnative rendererで取得した。ブラウザ版WASM Viewer内でRobotなしSceneの描画を正式に受け入れたわけではない。GUI込みRTFや実装の統計的優越を示す測定ではない。

## 受入検証

対応する専用回帰は`tests/runtime/test_scene_only.py`、既存Scene/Trial/Exporterテストおよび型/静的検査を用いる。全体の検査・独立監査・CIは、最終headの実結果と区別してPRに記録する。
