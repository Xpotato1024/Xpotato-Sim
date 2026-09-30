# Xpotato-Sim

MuJoCoを物理状態の正本とするロボット実験環境です。Workbenchで条件を編集し、初期状態を確認して有限試行を反復できます。以下はWindows PowerShell用です。初回は保存先directoryから始め、clone後はリポジトリrootで実行します。既にclone済みなら最初の2行を省略してください。

## 初回セットアップと起動

Python **3.11以上**、uv、Git、Node.js **20.xの20.19以上、または22.12以上（21.x／22.0〜22.11は対象外）**、npm、WebGL2対応ブラウザを用意します。依存versionの正本は`pyproject.toml`／`uv.lock`と`apps/mujoco-viewer/package-lock.json`です。以下のcommandで固定依存と同梱`fast-arm-core`をインストールします。

```powershell
git clone https://github.com/Xpotato1024/Xpotato-Sim.git
Set-Location Xpotato-Sim
uv sync --frozen --group dev
npm --prefix apps/mujoco-viewer ci
$workbenchTemp = Join-Path $env:TEMP 'xpotato-workbench'
$results = Join-Path $env:LOCALAPPDATA 'Xpotato-Sim/results'
New-Item -ItemType Directory -Force -Path $workbenchTemp, $results | Out-Null
$revision = git rev-parse HEAD
uv run xpotato-sim workbench --temporary-root $workbenchTemp --result-root $results --software-revision $revision --open-browser
```

Workbenchは未選択・無入力・無physics stepで待機します。`--open-browser`が自動で開いた操作ページで「操作権を取得」し、presetを選択してください。端末に表示されるURLは閲覧専用で、操作資格は印字しません。自動で開いた操作URLの一時資格は他人へ共有しません。資格なしのURLは表示できても試行を操作できません。通常の入力はGamepadです。実機、serial、OSCへ出力しません。

1. preset選択で次条件をcloneします。旧v1などの非対応presetは理由付きで無効です。
2. 「Advanced設定」でRobotモデル、Environment物体、Input、Mapping、Task、Evaluationと有限予算を確認・編集します。単位・可否はbackendが提供します。物体定義の半寸法・質量・摩擦とworld配置の位置・単位quaternion・fixed/dynamicは別項目です。
3. 「次条件を検証」でparameter契約を検査し、「検証・準備」でnative modelを構築して初期貫通も検査します。成功した同一MuJoCo worldだけをpreviewします。
4. 初期sceneとshaderの準備完了後に「開始」。Gamepadの新しい中立入力から有限試行が進みます。
5. 終端・保存完了後に「同じ条件で再試行」、または次条件を編集して「検証・準備」。ページの再読込は不要です。適用中の条件とtrial ID/epochは書き換えません。

モデル選択は登録されたendpoint bindingも明示変更します。bindingはフォームに表示されます。非対応軸・組合せは理由を表示し、代替を暗黙選択しません。接触観測Taskは診断であり、正式experimentの保持・持上げ評価や未実装metricを0・成功として報告しません。

「条件をexport」は検証済みの展開条件`workbench-condition/v1`をダウンロードします。「条件JSONをimport」はローカルで選んだfileの内容をbackendへ渡して再検証します。server path、任意XML、code、import参照は受け付けません。「適用条件との差分」で変更箇所を確認できます。物体集合・identityは選択presetに束縛され、任意物体追加や形状型追加は本editorの対象外です。

試行停止は「停止を要求」。結果が確定するまで次条件を適用しません。記録失敗は成功扱いせず、fileを保持して保存先等の原因を確認してください。その後「検証・準備」で新runner・新trialを明示準備できます。失敗trialのretryや自動開始は行いません。worker死亡時はアプリを再起動します。アプリ全体の終了は起動端末の **Ctrl+C** です。試行終了だけではGUIを閉じません。再接続は状態照会だけで、自動開始しません。

## 条件をheadlessで再現する

exportしたfileを、次のコマンドが使用する`$env:LOCALAPPDATA/Xpotato-Sim/condition.json`へ保存します。fixtureは同梱の短いsoftware検証入力です。GUIでも`--fixture`に同じfileを指定すれば、同じ展開条件・有限予算から実効condition/model/scene/dynamics identityを再現できます。これは参加者や実Gamepadの結果ではありません。

```powershell
$condition = Join-Path $env:LOCALAPPDATA 'Xpotato-Sim/condition.json'
uv run xpotato-sim workbench --condition $condition --fixture tests/fixtures/trial_gamepad/short-movement.json --run-once --temporary-root $workbenchTemp --result-root $results --software-revision $revision
```

短いfixtureで試す場合、export前にAdvancedの`limits.max_ticks`を **5** にしてください。`--condition`と`--profile`／`--ticks`は排他です。条件内の有限予算を使用します。fixtureのないheadless実行や隠れた中立入力の補完はありません。

結果は`$results/<新しいtrial ID>/`に`condition.json`、`initial-state.json`、`start.json`、`final-state.json`、`terminal.json`として保存します。`terminal.json`が保存完了のmarkerです。過去結果は上書きしません。途中fileだけを正常結果と見なさないでください。CLIのexit 0は`recording=complete`かつ`runner_stop_reason=simulation_budget`または`task_success`です。任意のTask終端や診断期間終了だけではexit 0にならず、正式な課題達成も保証しません。

## Workbenchオプション

すべて`uv run xpotato-sim workbench`に続けます。pathは操作者がCLIで指定するもので、GUIからserverの保存先を指定できません。

| flag | 必須・既定 | 意味・単位・組合せ |
| --- | --- | --- |
| `--temporary-root` | 必須 | 事前に作成した絶対directory。一時server資産を起動ごとに子directoryへ隔離 |
| `--result-root` | 必須 | trial別の永続結果directory。例は上記`$results` |
| `--software-revision` | 必須 | 実行source revision。未commit変更があればそのidentityも記載 |
| `--profile` | 任意・未選択 | 登録preset ID。指定だけでは開始しない |
| `--condition` | 任意 | export済み条件JSON。profile/ticksと排他。GUIでは初期次条件、headlessでは適用条件 |
| `--web-port` | 任意・5173 | loopback Web port。control portと別にする |
| `--backend-port` | 任意・8766 | loopback control WebSocket／asset port |
| `--web-dist` | 任意・Vite dev | 明示production buildのroot。自動fallbackなし |
| `--open-browser` | 任意・off | 操作URLをブラウザで開く。run-onceと排他 |
| `--startup-check` | 任意・off | Web/worker起動・終了だけを確認。run-onceと排他 |
| `--run-once` | 任意・off | headless有限実行。profileまたはconditionとfixtureが必須 |
| `--fixture` | 任意・live Gamepad | 有限software入力JSON。browser入力とは排他 |
| `--ticks` | 任意・profileのsteps | 有限commit数。正整数。conditionと排他 |
| `--input-wait-s` | 任意・5 s | 開始後の入力準備上限。正の有限秒 |
| `--wall-s` | 任意・360 s | 開始後の総実時間上限。正の有限秒 |
| `--prepare-s` | 任意・30 s | native準備上限。正の有限秒 |
| `--diagnostic-memory` | 任意・off | Python allocation追跡を明示的に有効化 |
| `--control-stdin` | 任意・off | 自動検証用の一時操作資格をstdin 1行から読む。通常利用では不要 |

`--condition`使用時のticks・待機/wall/prepare上限は条件内`limits`に保存されます。CLIの期限optionとの同時指定は拒否します。

## その他のよく使うコマンド

```powershell
uv run xpotato-sim profile
uv run xpotato-sim profile dynamic-cube-drop
uv run xpotato-sim trial --profile dynamic-cube-drop --fixture tests/fixtures/trial_gamepad/short-movement.json --result-root $results --ticks 5 --software-revision $revision
uv run xpotato-sim replay --robot fast_arm --steps 3 --preset sweep_x
uv run xpotato-sim workbench --help
```

| command | 主なflag・既定 | 用途 |
| --- | --- | --- |
| `profile` | selector任意・省略は一覧 | 登録名または明示JSON pathを解決。起動・stepしない |
| `trial` | profile/fixture/result-root/ticks/software-revision必須 | 共通runnerによる有限headless。input-wait/wall/prepareは5/60/30 s |
| `replay` | robot必須、steps=1、preset任意 | NDJSON dry-run。WebSocketやGUIを起動しない |
| `app` | profile必須、`--check`任意 | 旧profile起動／依存検査。Workbench editorとは別の既存入口 |

全commandのflag、組合せ、publisher・legacy参照は[統一CLI](docs/operations/unified-cli.md)、条件と停止・資源契約は[Workbench](docs/contracts/workbench.md)、設計の入口は[Source of Truth Map](docs/README.md)を参照してください。ブラウザ閲覧URLとWebSocket接続URLは別です。Workbenchはloopbackのみで、LAN／公開bindを提供しません。
