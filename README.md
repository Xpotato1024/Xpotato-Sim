# Xpotato-Sim

MuJoCoを物理状態の正本とするロボット実験環境です。Workbenchで条件を編集し、初期状態を確認して有限試行を反復できます。以下はrepository rootで実行します。

## 初回の準備

ローカルランチャーにはPython **3.12**、uv、Git、Node.js **20.xの20.19以上、または22.12以上（21.x／22.0〜22.11は対象外）**、npm、WebGL2対応ブラウザを用意します。既存の直接CLIのPython対応範囲は`pyproject.toml`を維持します。依存versionの正本は`uv.lock`と`apps/mujoco-viewer/package-lock.json`です。

標準ユーティリティのjustは[公式マニュアルのuv経路](https://just.systems/man/en/packages.html)で導入します。runtime依存ではありません。

```powershell
uv tool install --python 3.12 rust-just==1.58.0
uv tool update-shell
```

新しいterminalを開いて`just --version`を確認します。既にclone済みなら次の最初の2行は省略します。

```powershell
git clone https://github.com/Xpotato1024/Xpotato-Sim.git
Set-Location Xpotato-Sim
just init
# root .envの保存先・port・browser設定を確認してから準備する
just setup
just doctor
```

`init`はWindows／macOS／Linuxに適した保存先と、一時directory配下のbuild先をroot `.env`へ生成します。既存`.env`は決して上書きしません。設定はGit管理外、追跡するキーと汎用例は[.env.example](.env.example)です。`.env.local`の既存ignoreも維持します。uvがroot `.env`を明示読込みし、親directoryを探索しません。just独自dotenvや手書きparserは使いません。shellに同じキーをexportしている場合はuvの規則に従いshell値が優先します。

| キー | 用途 |
| --- | --- |
| `XPOTATO_TEMP_ROOT` | 一時資産・導出cacheのroot |
| `XPOTATO_RESULT_ROOT` | trial結果の永続保存先 |
| `XPOTATO_WEB_DIST` | 固定buildの出力先（repository外、resultと分離） |
| `XPOTATO_OPEN_BROWSER` | 操作browserを開くか、`true`／`false` |
| `XPOTATO_WEB_PORT`, `XPOTATO_BACKEND_PORT` | 異なるloopback port、1〜65535 |
| `XPOTATO_PYTHON` | `3.12`、`3.12.x`または既存Python 3.12の絶対実行file |

相対pathはrepository root基準です。空白・日本語はdotenvの引用で扱い、Windows絶対pathはforward slashで記載できます。drive相対pathは拒否します。不明な`XPOTATO_`キーは誤字として拒否し、エラーで設定値・秘密・`.env`全体を表示しません。運用キーを`VITE_`公開変数へ置きません。buildとtempを同じvolumeへ置いてください。未所有directoryはbuild先に流用できません。

`setup`は専用`.venv`を指定Python 3.12・固定lockで同期し、directoryを準備してnpm依存と固定production buildを作ります。appや試行は開始しません。自動Pythonダウンロードは行わないため、指定Pythonを先に用意してください。

## 通常の起動

```powershell
just run
```

`run`は既存の専用Pythonだけを使います。同期、ダウンロード、npm、build、lock変更、venvの再作成を行いません。設定／lockと準備済み環境の対応をmarkerと`uv sync --check --locked --offline`で検査します。不一致は`just setup`、欠落・古い・不正な固定buildは`just build`で起動前に止まります。devへの自動fallbackはありません。revisionはGit HEADから取得し、未commit差分・未追跡sourceを含む場合は`HEAD-dirty-<SHA-256>`として記録します。clean SHAと偽りません。

問題を調べるには`just doctor`を使います。日本語で設定、tool、Python／依存、build／source、directory、portを検査し、appは起動しません。just自体は検査対象runtime依存に含めません。

## コード更新後

Viewer sourceだけを更新した場合は`just build`で固定buildを作り直します。Python設定／pyproject／uv.lock、またはWebのpackage.json／package-lock／依存が変わった場合は`just setup`を実行します。buildとdevは準備時のWeb設定・lock・npm管理情報との一致も検査し、古いnode_modulesで新しいlockに対応したbuildを作ったことにしません。その後`just doctor`、`just run`です。buildは新しい出力を一時rootで検証してから所有済みbuildを置換し、失敗時は以前のbuildと結果を保持します。sourceと以前のbuildが不一致なら、保持された旧buildでも起動は拒否します。

## 開発とjustなしの同等入口

```powershell
just dev
```

`dev`だけが明示dev-serverを使います。準備済み環境を検査し、自動同期は行いません。WindowsはPowerShell、Unixはshから同じPythonランチャーを呼びます。justfileへ設定値やpathをshell文字列として埋め込みません。

justがなくても、全6操作は同じランチャーで実行できます。次の末尾を`init`／`setup`／`build`／`run`／`dev`／`doctor`へ置き換えます。bootstrapはproject同期を避け、既存Python 3.12だけを選びます。

```powershell
uv run --no-project --no-config --no-cache --no-env-file --offline --no-python-downloads --python 3.12 python scripts/workbench_local.py run
# 準備済みの専用Pythonを直接使う場合（Windows）
.\.venv\Scripts\python.exe -B scripts/workbench_local.py run
# Unixは .venv/bin/python -B scripts/workbench_local.py run
```

既存の`xpotato-sim workbench`などの直接CLIも維持します。直接CLIでは保存先・source identityを操作者が明示します。ローカルランチャーはprepare／Start／hardware操作を自動送信しません。

## Workbenchでの操作

Workbenchは未選択・無入力・無physics stepで待機します。`--open-browser`が自動で開いた操作ページで「操作権を取得」し、presetを選択してください。端末に表示されるURLは閲覧専用で、操作資格は印字しません。自動で開いた操作URLの一時資格は他人へ共有しません。資格なしのURLは表示できても試行を操作できません。通常の入力はGamepadです。実機、serial、OSCへ出力しません。

1. preset選択で次条件をcloneします。旧v1などの非対応presetは理由付きで無効です。
2. 「Advanced設定」でRobotモデル、Environment物体、Input、Mapping、Task、Evaluationと有限予算を確認・編集します。単位・可否はbackendが提供します。物体定義の半寸法・質量・摩擦とworld配置の位置・単位quaternion・fixed/dynamicは別項目です。
3. 「次条件を検証」でparameter契約を検査し、「検証・準備」でnative modelを構築して初期貫通も検査します。成功した同一MuJoCo worldだけをpreviewします。
4. 初期sceneとshaderの準備完了後に「操作画面へ」を選び、明示的に「開始」します。画面切替だけでは開始せず、Gamepadの新しい中立入力から有限試行が進みます。
5. 終端・保存完了後に「同じ条件で再試行」、または次条件を編集して「検証・準備」。ページの再読込は不要です。適用中の条件とtrial ID/epochは書き換えません。

モデル選択は登録されたendpoint bindingも明示変更します。bindingはフォームに表示されます。非対応軸・組合せは理由を表示し、代替を暗黙選択しません。接触観測Taskは診断であり、正式experimentの保持・持上げ評価や未実装metricを0・成功として報告しません。

「条件をexport」は検証済みの展開条件`workbench-condition/v1`をダウンロードします。「条件JSONをimport」はローカルで選んだfileの内容をbackendへ渡して再検証します。server path、任意XML、code、import参照は受け付けません。「適用条件との差分」で変更箇所を確認できます。物体集合・identityは選択presetに束縛され、任意物体追加や形状型追加は本editorの対象外です。

試行停止は「停止を要求」。結果が確定するまで次条件を適用しません。記録失敗は成功扱いせず、fileを保持して保存先等の原因を確認してください。その後「検証・準備」で新runner・新trialを明示準備できます。失敗trialのretryや自動開始は行いません。worker死亡時はアプリを再起動します。アプリ全体の終了は起動端末の **Ctrl+C** です。試行終了だけではGUIを閉じません。再接続は状態照会だけで、自動開始しません。

## 操作画面の表示

設定・準備画面と操作画面を分離しています。操作中はプロファイルeditorを隠し、自由視点、左右の関節計器、下部の入力計器、状態・時間・停止要求を表示します。Singleが初期表示、Assistは自由視点＋上面XY＋ロボット正面YZです。補助二面と計器は描画へ重ねません。「操作視点」はカメラを操作者基準へ戻し、「全体」は現在の向きを保って対象を収めます。どちらも入力方向を変更しません。

角度計はqposのdegree表示で、上0°、時計回りの正角を赤、負角を青で塗ります。±180°は表示範囲であって可動域・安全範囲ではありません。範囲外や失効を0°へ置換しません。

入力計器はGamepad／キーボード／Selfrionette／genericの共通表示枠です。**Workbenchの実行Sourceは引き続きGamepadのみ**で、表示対応だけでは別deviceを接続できるようにはなりません。Selfrionetteの7chは未校正rawで、指やNへの対応を推測しません。詳細は[Viewer表示契約](docs/operations/product-viewer-wasm-scene-renderer.md)を参照してください。

### 操作画面の見方

Assistは自由視点と上面・正面を2:1で配分します。「フォーカス」は観察方向を保ってRobotと物体へ寄せ、
「全体」は有限の支持台なども含めます。「操作視点」は固定operator方向へ戻ります。移動中の自動追従はしません。
Gamepad入力帯は中央の **左Z・左XY・右XY・右Z** のみです。Zは中央0から上が正、下が負の縦バーで、
backendが同じsampleで確定した符号付きtrigger量を表します。速度・力の表示ではありません。
ボタン押下やraw indexは設定画面の入力診断に残します。Selfrionette・Keyboardの既存表示も維持しますが、
これらをWorkbenchの実行入力へ追加したわけではありません。

## 条件をheadlessで再現する

exportしたfileを、次のコマンドが使用する`$env:LOCALAPPDATA/Xpotato-Sim/condition.json`へ保存します。fixtureは同梱の短いsoftware検証入力です。GUIでも`--fixture`に同じfileを指定すれば、同じ展開条件・有限予算から実効condition/model/scene/dynamics identityを再現できます。これは参加者や実Gamepadの結果ではありません。

```powershell
$condition = Join-Path $env:LOCALAPPDATA 'Xpotato-Sim/condition.json'
$workbenchTemp = Join-Path $env:TEMP 'xpotato-workbench-headless'
$results = Join-Path $env:LOCALAPPDATA 'Xpotato-Sim/results'
New-Item -ItemType Directory -Force -Path $workbenchTemp, $results | Out-Null
$revision = git rev-parse HEAD # この直接CLI例は実行sourceに未commit変更がない場合
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
| `--web-dist` | 任意・apps/mujoco-viewer/dist | 対応production buildのroot。source/lock/output byte不一致は起動前に拒否 |
| `--dev-server` | 任意・無効 | 開発時だけ明示するsource server。web-distと排他、自動fallbackなし |
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
