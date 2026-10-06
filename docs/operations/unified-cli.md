---
status: canonical
owner: operations
last_verified: 2026-10-07
canonical_for:
  - installable unified CLI
related:
  - docs/architecture/runtime-composition.md
  - docs/operations/runtime-dry-run.md
  - docs/operations/websocket-publisher-runner.md
---

# 統一 CLI

## 引継ぎ用ローカルランチャー

repository rootの`just init/setup/build/run/dev/doctor`は`scripts/workbench_local.py`への薄い入口である。
justは標準ユーティリティでありruntime依存ではない。justなしでも既存Python 3.12から同じscriptを呼べる。
実設定はGit管理外のroot `.env`に置き、uvの`--env-file`でこのfileだけを明示読込みする。
キーとOS別初回・通常・更新後・開発・直接Pythonの手順は[root README](../../README.md)を参照する。
`setup`が専用環境と固定buildを準備し、`build`は固定buildだけを更新する。
Web依存の準備時にはpackage.json、package-lockとnpmのinstalled lockを対応付け、build/dev/doctorで照合する。
sourceだけの変更はbuild、依存定義・lock変更や準備不一致はsetupとする。
`run`は既存Python／設定／lock／依存／固定buildを事前検査し、同期・ダウンロード・npm・buildを行わない。
`dev`だけがdev-serverを明示する。`doctor`は検査のみでappを開始しない。
既存正式CLIを組み立てるだけで、physics、入力鮮度、試行、保存formatのownerは変更しない。

## 待機型Workbench

`workbench --temporary-root <既存の絶対path> --result-root <保存先> --software-revision <source identity> --open-browser`
は未選択で待機する。profile選択・prepare・renderer ACK・Startを分離し、trial終了後もアプリを保持する。
`--web-dist <build root>`は対応固定build、省略時は`apps/mujoco-viewer/dist`。source/lock/asset byteを起動前に照合する。
開発用Vite serverは`--dev-server`を明示し、buildとの自動fallbackはない。`--run-once --profile <登録ID> --fixture <path>`は
同じservice/TrialRunnerの有限headless実行である。詳細なPowerShell起動例、期限、復旧、権限は
[Workbench契約](../contracts/workbench.md)を参照する。旧commandの引数・動作は維持する。

## 有限試行

`trial`は名前付きモデルv2〜v4と明示Gamepad fixtureを共通TrialRunnerへ渡し、ローカル結果を保存する。
GUI/control serverは`workbench`、構造化editorはWorkbenchへ接続済み。正式な全metric artifactは#584の範囲である。

```powershell
uv run xpotato-sim trial --profile dynamic-cube-drop --fixture tests/fixtures/trial_gamepad/short-movement.json --result-root <保存先> --ticks 4 --software-revision <実行sourceのrevision>
```

保存先・fixture・tick予算・software revisionは必須。既存の`profile`、`app`、`viewer`、`replay`の意味は変えない。
待機期限、終了理由、保存file、非対応経路は[有限試行契約](../contracts/finite-trial-runtime.md)を参照する。

installable entry point は `xpotato-sim` である。robot は暗黙選択せず、既存の Robot
Catalog と Robot Bundle から `--robot` で解決する。runtime command の実行前に、必要な
typed provider が Bundle に一意に存在することを検証する。

```bash
uv run xpotato-sim replay --robot fast_arm --steps 1
uv run xpotato-sim viewer --robot fast_arm --steps 1
uv run xpotato-sim replay --robot fast_arm --steps 1 --input-source noop
uv run xpotato-sim viewer --robot fast_arm --steps 18000 --input-source viewer
```

## 採用した entry point

| command | 既存処理 | 用途 |
| --- | --- | --- |
| `replay` | `run_replay_mujoco_dry_run` | deterministic replay と payload v0 NDJSON 出力。`--input-source`で`programmed_target` / `replay` / `noop`をcatalog解決する |
| `viewer` | `run_replay_mujoco_websocket_publisher` / `run_input_source_websocket_publisher` | replay payloadまたはtyped source step loopのWebSocket配信。`--input-source viewer`はviewer ingress lifecycleを有効にする |

commandごとの`--input-source` choicesは次のとおりである。

- `replay`: `programmed_target` / `replay` / `noop`
- `viewer`: `programmed_target` / `replay` / `noop` / `viewer`

`viewer` sourceはviewer ingressとruntime readerを必要とするため、`replay --input-source viewer`では受理しない。

repository内部とcurrent operator手順はinstallable CLIだけを使用する。旧compatibility scriptはC4で退役した。
wrapperのimplicit robot selectionや旧validation wordingは取り込まず、`--robot` requiredを含むcanonical
CLI behaviorを維持する。

## 今回採用しない候補

| 候補 | 理由 |
| --- | --- |
| live Selfrionette runtime | hardware / serial operator gate を含み、generic CLI の対象外 |
| robot diagnostics | 現在は `fast_arm` 固有で、Robot Bundle に対応する typed capability がない |
| evaluation | R7-Gの専用plugin-aware runnerは実装済みだが、generic CLIのsubcommandには統合していない。`docs/operations/r7-g-deterministic-e2e.md`を参照 |
| fixture export | repository developer tool であり、production CLI の責務ではない |

未知 robot、必要 capability の欠落、runtime failure は終了 status `1`、help は `0`、引数
構文エラーは `argparse` の status `2` とする。diagnostics module は診断 command が存在しない
限り import しない。

## 起動設定の検査

`uv run xpotato-sim profile`でcheckoutの起動profileを一覧表示し、
`uv run xpotato-sim profile sim-gamepad`で検証・解決済み設定をJSON表示する。
この操作はSource、model、サーバー、ブラウザを開始しない。
JSONの仕様とpath解決は`docs/contracts/launch-profile.md`を参照する。

## 統一起動

`uv run xpotato-sim app --profile sim-gamepad`はprofileからWeb/backendを起動する。
`--check`は依存と設定だけ、`--startup-check`はloopback serverの起動/終了だけを検証する。
profileにrobotが明記されるので、このsubcommandに別の`--robot`はない。
既存replay/viewerの選択肢と引数は維持する。手順は`backend-viewer-startup.md`を参照する。

## 操作引数の詳細

Workbenchの全option・単位・必須/任意・組合せとcopy/paste初期起動は[root README](../../README.md#workbenchオプション)を参照する。`--condition`は展開済み条件/v1を共通resolverで検証し、`--run-once`でもGUIと同じ適用条件を使用する。profile/ticks/期限optionは同時指定しない。

以下は既存の補助commandで、browser制御を暗黙に開始しない。PowerShellでrepository rootから実行する。

| command | flag | 必須・既定・単位 | 意味と組合せ |
| --- | --- | --- | --- |
| `profile` | `selector` | 任意・一覧 | 登録名またはJSON path。Source/modelを開始しない |
| `app` | `--profile` | 必須 | 既存launch profile名/path |
| `app` | `--web-port`, `--backend-port` | 任意・profile値、1〜65535 | WebとWebSocketのportを明示override |
| `app` | `--no-browser` | 任意・off | 自動browser起動を止める |
| `app` | `--check`, `--startup-check` | 任意・off、相互排他 | 依存検査のみ／server起動と終了のみ |
| `trial` | `--profile`, `--fixture`, `--result-root`, `--software-revision` | 必須 | 検証済みprofile、有限入力、保存先、実行revision |
| `trial` | `--ticks` | 必須・正整数 | 有限commit予算。旧profile stepsを予算へ代用しない |
| `trial` | `--input-wait-s`, `--wall-s`, `--prepare-s` | 任意・5/60/30 s | 正の有限秒。入力準備／総実時間／native準備 |
| `replay` | `--robot` | 必須 | Robot Catalog ID。例`fast_arm` |
| `replay` | `--steps` | 任意・1、正整数 | dry-run step数 |
| `replay` | `--dt-s` | 任意・Robot既定、s | 正の制御周期 |
| `replay` | `--preset` | 任意・なし | `sweep_x`。明示sourceと不整合な組合せは拒否 |
| `replay` | `--input-source` | 任意・未指定 | `programmed_target`/`replay`/`noop`。未指定時は既存replay経路 |
| `replay` | `--output` | 任意・stdout | NDJSON出力path。GUI結果fileとは別 |
| `viewer` | `--robot` | 必須 | 同じRobot Catalog ID |
| `viewer` | `--host`, `--port` | 任意・127.0.0.1/8766 | publisher bindとport。browser page URLとは別 |
| `viewer` | `--steps`, `--dt-s` | 任意・1、1/60 s | 有限配信数と制御周期 |
| `viewer` | `--interval-s`, `--grace-period-s` | 任意・0/0.05 s | 非負の配信間隔と終了猶予 |
| `viewer` | `--preset`, `--input-source` | 任意・なし | presetは`sweep_x`。sourceは上記3種と`viewer` |

```powershell
uv run xpotato-sim app --profile sim-gamepad --check
uv run xpotato-sim replay --robot fast_arm --steps 3 --preset sweep_x
uv run xpotato-sim viewer --robot fast_arm --host 127.0.0.1 --port 8766 --steps 3
```

`viewer`はpublisherだけでWebページを起動しない。別端末のViewer dev serverは次の手順とする。

```powershell
npm --prefix apps/mujoco-viewer run dev -- --host 127.0.0.1 --port 5173
```

閲覧URLは`http://127.0.0.1:5173/apps/mujoco-viewer/?websocketUrl=ws://127.0.0.1:8766`。
`ws`は既存のquery互換alias、`websocketUrl`が優先する。Workbenchの操作資格とは異なる。
終了は各起動端末のCtrl+C。Workbenchと違い旧appはprofileの有限実行完了でも終了する。
Workbenchの端末表示URLは閲覧専用であり、制御資格付きページは`--open-browser`で開く。
GUIのpreset cloneは明示CLI予算を保持する。`--condition`とprofile/ticks/input-wait/wall/prepare optionは、`--flag=value`形式も含め排他である。
headless exit 0は記録完了かつ`simulation_budget`または`task_success`だけである。
記録失敗の原因確認後は明示prepareで新runnerを準備できるが、失敗trialのretry・自動開始・file上書きは行わない。
独立wheelの配布は`uv build --wheel src/xpotato_sim/plugins/robots/fast_arm/core --out-dir`とrootの`uv build --wheel --out-dir`で両distributionを用意する。出力先にはtask用の絶対temporary root配下を指定する。
