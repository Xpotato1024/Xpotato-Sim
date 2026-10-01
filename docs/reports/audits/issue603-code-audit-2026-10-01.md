---
status: historical
owner: architecture
last_verified: 2026-10-01
canonical_for: []
related:
  - docs/README.md
  - docs/architecture/dependency-boundaries.md
  - docs/operations/validation.md
  - docs/contracts/launch-profile.md
---

# Issue #603 全体静的監査と内部整理

## 対象と主張範囲

[Issue #603](https://github.com/Xpotato1024/Xpotato-Sim/issues/603)の基点は
`7590ef2a9eb0b063160e8bcdd574a452679bb886`、作業branchは`codex/603-audit-refactor`。
全tracked treeの棚卸し・候補抽出と、選択した経路の意味的精査を区別する。
全行の意味的精査、全欠陥不在、外部consumer不在、実機性能は保証しない。
本記録は時点の監査証拠であり、current contractの第二SoTではない。

## 静的coverage

`git ls-files -z`の1056ファイルを列挙し、全ファイルのbytesを読み取った。
UTF-8で扱える非NULテキスト1050件に、旧名称・validation・例外・registry/export・TODO/suppressionの
行検索を実施した。残る6件はbinary assetとして分類し、内容の意味をテキスト検索から推定していない。
Python587件はすべてAST解析し、構文解析失敗なし。ASTのLoad参照からimport候補95件、
引数名を含む関数本体の完全一致から重複候補26群、private定義の全テキスト単一出現から4候補を抽出した。
byte完全一致は3群。型annotationの文字列、`__all__`、動的discovery、特殊methodを考慮する前の候補数であり、欠陥件数ではない。

| tree | 基点件数 | 方法と重点対象 |
|---|---:|---|
| `src/` | 357 | Python299、JSON13、Markdown36、STL5、XML2、TOML2。AST・registry・public facade・resource path |
| `apps/` | 101 | TS71、TSX17、CSS3、MJS1、JSON5、HTML1、JSONL1、README1、設定1。識別子・import/export・入口・CSS consumer |
| `tests/` | 285 | Python271、fixtureと説明14。production候補のtest consumer、architecture guard、故障注入 |
| `scripts/` | 21 | Python16、PowerShell4、README1。CLI delegationとhardware/diagnostic入口 |
| `profiles/` | 17 | 全JSONのversion・consumer・decoder・digest。保存byteを変更しない |
| `firmware/` | 15 | C++4、header2、INI2、Markdown7。装置用途・既存REVIEWとの照合。build/uploadなし |
| `docs/` | 227 | Markdown220、CSV1、PNG1、TXT1、設定4。正本mapとhistorical分類、旧名称・path参照 |
| `research/` | 5 | policyとhistorical logを分類。今回のpure refactorに研究能力変更なし |
| `.agents/` | 18 | opt-in、active metadata、candidate/evalの追跡。変更なし |
| `.github/` | 2 | CI YAMLとPython shard集約owner、test集合とgate。変更なし |
| root files | 8 | AGENTS、README、Git設定file、pyproject、pytest、MANIFEST、lockの入口・packaging |

行検索hit数は旧名称等506、validation11783、例外573、registry/export1207、TODO/suppression418。
hit数は重複・test・歴史本文を含み、意味的精査済み行数ではない。
TS系exportの単一テキスト出現候補は8件。親のTypeScript未使用import監査は87 root fileで2件を検出し、
同一lock照合済みの依存コピーを使った最終compiler検証では未使用判定のerrorが0件となった。
生のpath一覧・件数・候補・検索行はtask証拠の`inventory.json`、`search-hits.json`、`supplement.json`に保存した。

## 意味的に精査した範囲

- Mappingの`gamepad_planes.py`／`gamepad_triggers.py`：binding構造検証、軸・符号・button制約、session reset、neutral待ち、左右独立、stream/sequence、coordinated projectionのowner。
- Python公開面：`runtime/__init__.py`のlazy文字列export、`schemas/__init__.py`、`transport/__init__.py`、Mapping facade、FastArm adapter Bundleの`__all__`、Robot/Environment固定discoveryとdeclaration。
- LaunchProfile decoderのv1〜v4と旧schema、CLI package入口、catalog alias、replay経路、PowerShell smokeのprofile生成・既存applicationへの委譲。
- `runtime/scene`の合成・world・dynamic観測、trial condition、motion log recorder、Workbench runner、local endpoint motionについて、削除したimportと実処理の関係・consumerを確認。safety moduleのprivate関数2件は、参照・decorator・exportsと残りmodule ASTを追加照合。
- Viewerの`main.tsx`からWorkbench/ProductViewer、InputStrip/InputInstruments、Gamepad status、診断固定、表示cadence、WebSocket query、static profile registry/resource tooling、qpos同期を重点確認。
- CIの全test収集とshard集約、pyproject/MANIFEST/core package-data、firmwareの現役用途と既存REVIEWの注意事項を確認。firmware各行の安全性・装置上の正しさを再認定していない。

その他の関数本体、全assetのgeometry、全CSS cascade、すべての例外経路、動的な外部importは意味的精査を完了していない。

## findingと処置

| ID | 根拠・影響 | 処置 |
|---|---|---|
| F01 | Plane/Triggerのprivate `_pair`は引数・実行本体がAST完全一致。callerは各bindingの`__post_init__`、公開入口は各config/Mapping/LaunchProfile。registry、文字列path、docs、testsにprivate関数consumerなし | 同じplugin内の`_binding_validation.integer_pair`へ移動し、private aliasでimport。旧2本から新1本へ集約。日本語function docstringを追加し、同値比較はdocstringを除いた実行本体と引数を対象とする。エラー文字列、Sequence判定、bool拒否、tuple順序・状態機械を保持 |
| F02 | `local_endpoint_motion`／`motion_log_recorder`の`Mapping`、`trial_condition`の`replace`、`workbench`の同期`time.sleep`、scene compositionの`dataclass`、dynamic observationの`isfinite`、worldの`number`は実参照なし。文字列annotation・`__all__`・import consumerにも該当用途なし | 7 import bindingのみ削除。`asyncio.sleep`、`os.replace`等の現役処理は維持。validation・physics・recordingの式や分岐は変更しない |
| F03 | `docs/README.md`の現行repository名が旧名。名称移行の正本は`docs/operations/xpotato-sim-migration.md` | 見出しとdocumentation root説明だけをXpotato-Simへ訂正。historical inventoryは改変しない |
| F04 | `InputInstruments`はProductViewer Setupの現役consumerを持つ。InputStripは共通操作帯、GamepadDiagnosticDetailsとraw overlayも別consumer。単なる未使用componentではない | 維持。Setup診断と操作帯には目的差があり、診断情報の同等性なしに一律統合しない。通常Viewer回帰で現行経路を検証 |
| F05 | FastArm Bundleの初期state定数、Environmentの`ENVIRONMENT_PLUGIN`、互換facadeの`__all__`等はAST Load上未使用でもpublic export/discovery consumerを持つ。`runtime.__dir__`はPython特殊method | 削除しない。95候補を95欠陥へ読み替えない。未精査候補は自動削除しない |
| F06 | `_pair_id_parts`（collision policy）と`_unknown_projected_limit`（limit resolution）は基点の全tracked treeで定義のみ。registry・文字列path・public exports・decorator・getattr・tests consumerなし | このprivate関数2件だけを削除。削除以外のmodule ASTは基点と2/2完全一致、decoratorなし。liveなsafety条件・式・閾値・出力契約は無変更。関連回帰を追加実行 |
| F07 | Analog Source/Mappingの数値検証、programmed input／motion／evaluationのvector検証、hardware CLI／dry-runのCSV検証などは同一本体候補でも異なるownerと入力契約 | 維持。同じ文字列だけを理由にcross-owner utilityへ統合しない |
| F08 | Viewer単一出現export候補：Three ambient型3件、transport型2件、`loadQposFixtureFromUrl`／`getCurrentFrame`、`QPOS_FIXTURE_SOURCE_LABEL`。export/型/fixture用途と外部consumerは別評価 | 維持。compilerでexportを未使用importと同一扱いせず、外部consumer不在を推定しない |
| F09 | discoveryの広いcatchはimport失敗をdiscovery errorへ包む。application cleanupの例外とTrialRecorderのunlink例外は実行成否とcleanupの区別を持つ | 維持。例外検索のhitだけで握り潰しと判定しない |
| F10 | `tests/runtime/test_collision_policy.py`の`_RuntimeErrorSequence`は単一出現候補。productionではなく未使用test helper候補 | 今回は維持。既存testを整理・削減して通過させず、safety test ownerの別整理へ残す |
| F11 | `gamepadLifecycle.ts`の`ViewerGamepadPublicationController`型importと`mujocoSceneRenderer.ts`の`formatQpos`importはcompiler未使用判定とsource参照で一致 | import2件のみ削除。定義・public export・別のtest consumerは維持。通常test/typecheckと`noUnusedLocals`／`noUnusedParameters`を通過 |

重要なsupported-path behavior bugの再現は今回取得していない。これは欠陥不在の証明ではない。
新機能、public contract変更、schema/wire変更、大規模format変更、dependency/CI変更は行っていない。

## 互換・保護対象

現役17 profileを維持し、全件を副作用禁止fixture付きでdecodeする回帰を追加した。
`sim-keyboard`、`sim-gamepad`、`replay-sweep`、`sim-gamepad-world-xy`、左右`sim-gamepad-*-xyz`、
4件の`fast-arm-*-gamepad`、4件の`contact-debug-*`、3件の`dynamic-*`を含む。
保存JSONのcanonical内容とconfiguration digest、読込み前後のfile byte一致を検査した。
旧`selfrionette-launch-profile/v1`について、schema値・canonical JSON・digestを自動移行しない回帰を追加した。
`?ws=`は既存`websocketEndpoint.test.ts`のconsumerとquery優先順契約を確認し、そのまま維持した。
static ViewerのFastArm facadeはregistry・plugin-owned JSON/resource consumerを持ち、削除しない。

profiles、firmware、resource、fixture、XML、STL、lockの80ファイルは、変更前に用意した検証sandboxと
raw bytesを比較して全件一致。Git blobとのhashも別記録した。
基点checkoutには`core.autocrlf=true`由来のCRLFがあり、一部のGit LF blobとworktree bytesは元から異なる。
設定変更や資産の改行正規化は行っていない。
MuJoCo SoT、入力方向、deadzone、neutral/stale/STOP、物理・課題・記録意味、既存digestを変更していない。

## 検証と残件

| 区分 | 結果 |
|---|---|
| Mapping focused回帰 | 147 passed。binding公開入口からlist/tuple、順序・符号、bool・形状・button拒否を追加検査 |
| Python全suite | 3579 passed / 12既存skip、223.46秒。正規interpreterを使用し、import先は本worktreeの`src`とcore `src`で確認 |
| 最終focused | 240 passed。後から追加した17 profileと旧schema計18回帰、およびMapping・LaunchProfile既存回帰を検証 |
| 追加focused | 372 passed。collision policy、physical limit resolution、binding構造の関連回帰 |
| 全suite再利用 | 全suite後のprivate alias・docstring・private関数退役・TS import整理は、同値ASTと関連Python/Viewer回帰で閉じた。その他のproduction経路・tests/lock/資産は不変。全suiteをもう一度実行したと装わない |
| 保護bytes／共通化AST | 80/80一致、旧2関数と新関数の引数・実行本体2/2一致（docstring除外）。private退役後の残りmodule ASTも2/2一致 |
| 最終architecture | 225 passed。private helperの追加と既存所有境界・resource契約を検査 |
| Python compile | `src tests scripts .github`のcompileall、exit 0。一時pycacheはtask tempへ隔離 |
| Markdown／diff | 279文書・85 SoT topic、error 0、既存absolute-path warning 6。未commitの変更と新規文書を明示したharnessで既存validatorを実行。diff checkもexit 0 |
| wheel build | 通常backend呼出しは`setuptools`不足でexit 1。既存cacheをtask tempへコピーし、実際のuv cache形式へ合わせた後、canonical build-systemによる`uv build --offline --wheel`はmain/coreともexit 0。新helper収録とresource等23 entryのraw bytes一致。独自venv・新pin・network・共有cache更新なし |
| Viewer test/typecheck/未使用判定 | すべてexit 0。tscのcompileと30 test entryを実行（node:test形式46 case＋既存直接assert群）。最初の`npm ci`はtarball取得`EACCES`でexit 1。親の73 package version/resolved/integrity照合とoffline npm ls証拠を再利用し、task所有コピーでnpm lsもexit 0を確認して復旧。権限緩和・dependency変更なし |
| Viewer build | `npm run build`はexit 1。esbuildによるVite設定読込みがsandbox外directoryへのアクセス拒否で停止。設定loaderや権限を変更して迂回せず、build gateを未達として引き継ぐ |
| 実機・全面実操作E2E | 未実行。serial/OSC/upload/実機出力・deployはscope外 |
| 独立review／current-head CI | 親担当へ引継ぎ。子agent禁止・commit/push/PR操作禁止のため、この作業内では実施しない |

実行command、pytest JUnit、skip理由、棚卸し全path、package/Markdown/diff検査結果、最終diffとhygieneは
task証拠の`evidence/handoff.md`へ集約する。未達Viewer build gateとcurrent-head CIをPASS扱いしない。

Documentation impactは名称訂正、plugin-local実装owner説明、本監査とreports入口の同期。
Research log impactはなし：研究能力・実験条件・指標・解釈・主張範囲を変えないpure refactorのため月次log不要。
Experiment evidence impactはなし：unit/CI検証を研究実験へ昇格せず、新条件・実測結果を取得していない。

## 公開前の親側照合と残存一時領域

親側でも基点のGit archive（1056ファイル）とPython AST（587ファイル）を照合した。
private関数2件だけを除外した元moduleと変更後moduleのASTは2/2一致し、残りのsafety実行処理は変更されていない。
検証件数はnpm scriptの31 commandを31 testと数えず、compile 1回とnode test entry 30本に分けた。

Workspace hygieneはFAIL。task所有の一時領域と依存コピーの削除が実行ポリシーで拒否された。
別APIや権限変更で迂回せず、残存2領域・17557 pathの完全一覧をtask証拠の`remaining-paths.json`へ保存した。
これらはrepositoryの変更・commitに含めない。Git source treeのclean状態と一時領域の清掃完了を区別する。
CIと公開後のGit/PR HEAD照合は当該PRの検証記録で追跡し、ローカルbuild失敗を成功へ書き換えない。
