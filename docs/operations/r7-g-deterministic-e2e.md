---
status: canonical
owner: operations
last_verified: 2026-08-23
canonical_for:
  - R7-G deterministic software-only E2E
related:
  - docs/contracts/evaluation-manifest-readiness.md
  - docs/contracts/experiment-motion-log-v1.md
  - docs/contracts/experiment-plugin-composition.md
  - docs/evaluation/world-tool-frame-comparison-design.md
  - docs/reports/audits/r7-g-p5-completion-audit.md
---

# R7-G deterministic E2E

`xpotato-sim-r7-g-e2e`は、callerが明示したmanifest revisionと独立したactual execution
revisionで、固定したR7-G manifest / protocol contextを
production six-axis readiness、world/tool MuJoCo runner、
`experiment-motion-log/v1`、Task-owned canonical evidence reconstruction、
production Evaluation Plugin、`evaluation-artifact/v1`へ順番に渡す有限の
software-only completion audit入口である。runner、recorder、evaluator、artifactの
計算は各canonical ownerへ委譲し、この入口で複製しない。

## 実行

出力先は明示した絶対directoryを使用する。既存directoryを再利用する場合も、同じ
canonical namesをstrict read-back後にatomic replaceする。

```powershell
$out = 'C:\path\to\task-temp\r7-g-e2e-output'
$manifestRevision = $env:R7_G_MANIFEST_REVISION
$executionRevision = $env:R7_G_EXECUTION_REVISION
if ([string]::IsNullOrWhiteSpace($manifestRevision) -or [string]::IsNullOrWhiteSpace($executionRevision)) {
    throw 'R7_G_MANIFEST_REVISION and R7_G_EXECUTION_REVISION are required caller inputs'
}
uv run xpotato-sim-r7-g-e2e `
    --output-dir $out `
    --manifest-software-revision $manifestRevision `
    --execution-software-revision $executionRevision
```

`--manifest-software-revision`はmanifestへ宣言する`git-sha1:<40 hex>`、
`git-sha256:<64 hex>`、またはfixture専用の`test-revision:<token>`である。
`--execution-software-revision`はcallerがstartup時に独立取得したactual execution identityであり、
manifestから自動導出しない。2値がexact matchしない場合はreadinessでstatus `1`となり、
MuJoCo execution、motion log、artifact生成は開始しない。

installed wheelやrepository外の実行ではgit lookupを自動実行しないため、callerはactual revisionを
明示して渡す。fixture smokeでは両引数へ同じ明示的な`test-revision:issue-409-fixture`を渡せるが、
これはHEADの証明ではない。commandは同一のrevision、manifest、protocol contextでE2Eを2回実行し、次の値が
一致しなければ終了status `1`で停止する。

- world/toolのTask terminal classification
- step countとsimulation time
- canonical motion log bytes
- logから再構成したcanonical Task evidence
- production evaluatorのmetric result
- `evaluation-artifact/v1` bytes

wall-clock、temporary path、UUID、process identityはcanonical log / artifactへ記録しない。
current software-only fixtureではworldは`success`、57 samples、1.14 s、toolは
`failure`、250 samples、5.00 sであり、runnerのbounded semanticsを変更せず検証する。

## 出力契約

指定したoutput directoryへ次の3ファイルを作成する。

| 種別 | filename | 内容 |
| --- | --- | --- |
| motion log | `r7-g-e2e.motion-log.jsonl` | world/toolをcondition orderで含むstrict `experiment-motion-log/v1` stream |
| world artifact | `r7-g-e2e-world.evaluation-artifact.json` | world conditionのstrict `evaluation-artifact/v1` |
| tool artifact | `r7-g-e2e-tool.evaluation-artifact.json` | tool conditionのstrict `evaluation-artifact/v1` |

logとartifactはUTF-8、canonical serialization、strict decode、round-trip、read-backを
通過してから保存する。artifact writerが作成する
`.r7-g-e2e-world.evaluation-artifact.json.lock`と
`.r7-g-e2e-tool.evaluation-artifact.json.lock`はkernel advisory lock用のpersistent
operational stateであり、canonical artifactの一部ではない。lockのcontentをartifact
identityやowner情報として解釈しない。

determinismの受入境界は同一OS、Python / MuJoCo依存環境での反復実行である。OSや依存版を
跨いだartifact bytes / SHA-256の一致はこのcommandの仕様値としない。

stdoutはpathを含まないdeterministic summary（manifest / execution revision、freeze digest、bytes、
SHA-256、classification、metric status/value、negative-control名）を1行のJSONで出力する。

## negative controlsと終了status

次のcontrolを毎回実行し、受理またはsuccessへの変換があればstatus `1`で停止する。

- readiness mismatch
- malformed log
- held / rejected / stale sample
- measurement unavailable
- technical-invalid outcome
- artifact identity mismatch

全controlがfail-closedで確認され、canonical runとstrict outputが成立した場合だけstatus
`0`を返す。引数エラーは`argparse`のstatus `2`であり、hardware、serial、OSC、実機
robot output、participant pilot、formal experiment evidenceはこのcommandの対象外である。

このautomated E2Eはsoftware-only completion evidenceであり、mappingの普遍的優越、
participant performance、NASA-TLX、4-target participant pilot、authoritative physical
safety、contact task、physical robot output、long-duration robustnessを証明しない。
