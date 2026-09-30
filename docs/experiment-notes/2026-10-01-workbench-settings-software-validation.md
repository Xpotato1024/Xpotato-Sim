---
status: supporting
owner: runtime
last_verified: 2026-10-01
canonical_for: []
related:
  - docs/contracts/workbench.md
  - docs/contracts/finite-trial-runtime.md
  - research/logs/2026-10.md
---

# Workbench条件editorのsoftware検証

## 条件と観測範囲

baseは`8d9a4d96cbf3e230ed6133e339da469a3e458c26`、branchは`codex/593-workbench-settings`。
Windows、Python 3.14.3、MuJoCo 3.9.0、Node 24.14.0、npm 11.9.0、uv 0.10.12とlock依存で実行した。
固定Vite buildをEdge headlessのANGLE/SwiftShaderで表示し、実DOMのpreset選択・軸別フォーム・開始を操作した。
これはsoftware validationであり、参加者、実Gamepad、hardware、保持・持上げ課題の結果ではない。

入力は同梱の`tests/fixtures/trial_gamepad/short-movement.json`、予算は5 ticks。
`dynamic-cube-drop`からmassを0.3 kg、boxのx半寸法を0.04 m、滑り摩擦を0.7、world zを0.6 m、
Mapping速度を0.12 m/sに変更して、parameter検証・native build・preview・有限試行・結果保存を確認した。
export/import、同条件retry後、`single_left`と`bimanual`、mass 0.31/0.3 kg、Mapping速度0.13/0.12 m/sを
12回切り替えた。ページreloadなしで計14試行をtrial別に保存した。

## 実測

- export条件を同じfixtureで`workbench --condition --run-once`へ渡し、GUIの保存`condition.json`とbyte完全一致した。model/scene/dynamics identityもこの条件内で照合した。
- 12回の編集後のbrowser model build/deleteは13/12、native model/data各1、WASM module1、VFS残存0。
- 切替後のGPU geometryは片腕14、双腕22で各モデルごとに一定、texture1、program7。socket/timer/resize listener/observer/OrbitControls/rAFのowner数は各1。
- prepare時間はn=12、p50約2.43 s。disk cache消去までのcold起動やhardware latencyではない。
- Python heapは最初の編集後約4.60 MB、最後約5.32 MB、RSSは約158 MBから250 MB。bounded要求履歴・allocator保持を含み、短期の所有数安定だけで長時間のリーク不在を断定しない。
- 別のnative回帰でmass変更を伴う12回のrebuildを行い、弱参照がlive model1を示し、runner close後に全参照が退役した。
- 最終UIの追加smokeは4試行・2回の編集後rebuildとexport/import/CLI parityを確認した。数値JSON表記の`0.0`/`0`差、file読取中の世代変更、記録失敗からの次条件適用を検査対象に含める。

## 実行と証拠

詳細な実行command、DOM/CDP操作、固定build、条件export、資源counter、README commandのstdout、
focused test出力は利用者指定のrepository外evidence directoryへ保存した。
software revisionにはbaseと未commitの#593変更を明記し、正式成果revisionはPRで特定する。

主な検証commandは次のとおり。test・build出力先は共通の絶対temporary root配下へredirectした。

```powershell
python -m pytest tests/runtime/test_edited_condition.py tests/runtime/test_workbench_control.py tests/runtime/test_workbench_worker.py tests/runtime/test_workbench_transport.py tests/runtime/test_trial_runner.py tests/runtime/test_cli.py tests/runtime/test_object_scene_contracts.py tests/runtime/test_trial_resources.py tests/architecture -q -p no:cacheprovider
python scripts/repository/validate_markdown_docs.py --base-ref 8d9a4d96cbf3e230ed6133e339da469a3e458c26 --strict-map --strict-links
git diff --check
```

compile、Viewer全test script/typecheck/build、wheel同梱、READMEの6種helpと6種実commandも検査する。
独立reviewはこのnoteの証拠ではなく、実装者の自己レビューと区別する。CIはcurrent PR headで別に確認する。
