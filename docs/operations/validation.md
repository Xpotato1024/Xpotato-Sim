---
status: canonical
owner: architecture
last_verified: 2026-10-01
canonical_for:
  - validation categories
related:
  - AGENTS.md
---

# validation

validationをcategory別に報告する。

- docs-only validation（文書のみ）
- unit / compile validation（単体・compile検証）
- MuJoCo model load validation（model load検証）
- Web typecheck / build（Web静的検証）
- dry-run（非hardware実行）
- hardware validation（実機検証）

dry-run、build、typecheck、MuJoCo loadをhardware validationと呼ばない。

次は過去のskeleton lock stageの例であり、全taskの必須command一覧ではない。現在の変更層とfailure modeから必要な検証を選ぶ:

```bash
uv run pytest tests/architecture
uv run python -m compileall src tests
git diff --check
git status --short --branch
```

project environmentを利用できずcommandを実行できない場合はNot Run Reasonを報告し、ad hoc environmentを
作成しない。

同条件の検証結果の再利用、環境障害からの再開、不完全レビューの補完、統合E2Eの時期は[Codex workflow](codex-workflow.md)を正とする。新しい実行と既存証拠の再利用を区別し、変更に必要な検証は先送りしない。

## Python CIの構成と完了gate

全PRとmain pushで、`python-tests`の2 shard（`runtime`、`general`）を実行する。
`tests/runtime/`のfileはruntime、それ以外はgeneralが所有する。両方が`tests`全体を収集してから
file単位で選択するため、同一file内の順序を維持し、新しいfile・directoryも既定経路へ入る。
mutable MuJoCo data、controller、fixtureをprocess間で共有しない。worker数やtestの反復数は変更しない。

`general`はPRのchanged Markdown strict validation、Python compile、diff whitespace検査も担当する。
Viewerのtests、typecheck、buildは従来どおり`viewer-validation`で実行する。
path filter、変更test限定、nightlyへの移動は行わない。matrixは`fail-fast: false`である。
concurrency取消は同一PRの旧runだけに適用し、mainの異なるcommitを間引かない。
fork PRでもsecretを必要とせず、`pull_request_target`は使わない。

GitHubでの既存チェック名`python-validation`は、`if: always()`の集約jobとして維持する。
`viewer-validation`も保持する。branch protectionの有効化・設定変更は行わない。
集約は次の両方を要求する。

- `needs.python-tests.result`が`success`。failure、cancelled、skipped、missingは拒否する。
- 同じSHA・run ID・lock digestに限定し、各shardの最新attemptのreportを選ぶ。
  未来attempt、同じshard/attemptの重複、identity不一致を拒否する。
  最新attemptが失敗なら過去successへ戻さない。成功済みshardは前attemptから再利用できる。
  generalが同じattemptで保存した非分割collection参照と、両shardの収集node IDsが一致する。
  各shardが空でなく、選択集合の和集合が全収集集合と一致し、重複・欠落がない。
  全選択nodeのsetup/call/teardownが完了し、pytest exit statusが0である。

既存のOS条件によるtest skipは記録して維持する。jobのskipとは区別する。
artifact欠落、stale report、failed phase、途中終了を成功にしない。
callがある場合setupとteardownはpassedを要求し、setup skipはcallなしだけ受理する。
artifact名にはattemptを含め、取得は同じrun内の全attemptを対象にする。
これにより`rerun-failed-jobs`で成功済みjobを再実行せず再利用できる。
pytestとteeのpipelineは明示`bash`（`-eo pipefail`）でpytest非0をjobへ伝える。
集合・job状態・鮮度・実行完了の故障注入は`tests/support/test_python_ci.py`で検査する。

## 再現と計測

正規project環境を`uv sync --frozen --group dev`で準備する。CIはPython 3.12を使用する。
Linuxでの各shardの再現例は次のとおり。`CI_OUTPUT`はtask専用temporary root内の絶対pathとする。

```bash
export PYTHONPATH=.github
export PYTHONPYCACHEPREFIX="$CI_OUTPUT/pycache"
uv run pytest tests --collect-only -q -p python_ci \
  --ci-reference "$CI_OUTPUT/general/reference.json" \
  -o cache_dir="$CI_OUTPUT/reference-cache"
uv run pytest tests -q -p python_ci --ci-shard runtime \
  --ci-report "$CI_OUTPUT/runtime/report.json" --durations=30 \
  --junitxml="$CI_OUTPUT/runtime/junit.xml" \
  -o cache_dir="$CI_OUTPUT/runtime/cache" --basetemp="$CI_OUTPUT/runtime/tmp"
uv run pytest tests -q -p python_ci --ci-shard general \
  --ci-report "$CI_OUTPUT/general/report.json" --durations=30 \
  --junitxml="$CI_OUTPUT/general/junit.xml" \
  -o cache_dir="$CI_OUTPUT/general/cache" --basetemp="$CI_OUTPUT/general/tmp"
CI_NEEDS='{"python-tests":{"result":"success"}}' \
  uv run python .github/python_ci.py --reports "$CI_OUTPUT"
```

local再現ではSHA/run IDが`local`、attemptが`1`になる。revision-sensitiveな比較では`GITHUB_SHA`、
`GITHUB_RUN_ID`を参照・実行・集約に同じ値で設定し、`GITHUB_RUN_ATTEMPT`へ実際のattemptを設定する。
generalの独立参照は`pytest tests --collect-only`の収集IDを正とし、固定件数を正本にしない。
参照側の`-k`、`-m`、ignore、deselect、last-failed等の縮小optionを拒否する。
実行側が縮小される場合も参照との集合・完了照合で拒否する。collect-only reportは実行証拠にならない。
Windowsでは既存`.venv/Scripts/python.exe -m pytest`を利用できる。環境を新設しない。

CIは各shardのJUnit、`--durations=30`付きlog、収集・選択node IDsと各testのphase時間を含む
`report.json`を14日間保存する。job summaryには件数、collection時間、pytest時間、exit status、
processのpeak RSSを載せる。2 processのpeak RSSの和は同時刻の実測peakではない。
setupやartifact・gateを含むcritical pathと合計runner時間はGitHub job/step時間から別に評価する。
generalで追加する非分割collection一回分のコストは、修正前のWindows性能値には含まれない。
既存の217.27秒とparallel wall 123.6216秒は再利用するが、新gate構成の最終性能値とは呼ばない。

性能比較では同じrevisionのtest集合、入力、lock、Python、OS、計測optionを固定する。
旧CIは3322件を収集していたが、`tests/input_sources`、`tests/robots`、`tests/scripts`の92件と
root直下`tests/test_r6_l_viewer_control_message_schema.py`の25件が明示引数外だった。
新CIでは既存117件とCI回帰testを含める。旧集合での比較と追加集合の検証を分離し、
Windows実測をLinux CIの改善率として扱わない。単一testの時間だけで支配的コストを断定しない。

## Rollback

分割が環境固有の順序依存や資源増加を示した場合は、集約とpartitionの変更commitをrevertする。
単一jobへ戻す際も`pytest tests`による全集合、durations、JUnit、既存Markdown・compile・diff検査を
維持する。チェック名は`python-validation`と`viewer-validation`のままとし、testの削除・skip追加・
閾値緩和・反復縮小で性能やgateを回復させない。
