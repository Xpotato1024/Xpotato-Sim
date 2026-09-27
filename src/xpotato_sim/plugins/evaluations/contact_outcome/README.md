# contact_outcome Evaluation Plugin

## 意味とresponsibility

`contact_outcome/v1`はTask-owned `contact_press_hold_outcome/v1`と
`contact_press_hold_terminal/v1`をstrict decodeし、closed contact-task outcome artifactを返す。
raw MuJoCo contactの再計算、#414 reaction-forceの参照、viewer診断、trial aggregationは所有しない。

canonical declaration: [`EVALUATION_PLUGIN`](plugin.py)

## input / output

inputは`contact_press_hold_task/v1`が生成したprovenance付きcanonical evidence、outputは
`MetricResult`の`contact_task_outcome` artifactである。terminal / outcome identity、trial、manifest
digest、classification、phase、completion timeの一致を検証する。outcome artifact内のdwell / timeout、
target band、force / alignment / drift gate、pose requirementはTask contextの再構成用にstrict decodeされる。

## failure semantics

- `success`または通常の`failure`は測定済みartifactを返す。
- `running`はvalueなし`unavailable`であり、successへ投影しない。
- missing / unavailableはvalueなし`unavailable`、invalid / technical-invalidはvalueなし`invalid`である。
- failed trialのcompletion timeや未観測forceを0や架空の値へ変換しない。

## parameters / side effect

parameterはない。derivationはpure deterministicで、MuJoCo step、Robot command、hardware、外部書込みを
行わない。同一のTask outcome evidenceからは同一のcanonical artifactを再生成できる。

## tests / validation

- [Contact outcome Evaluation test](../../../../../tests/plugins/evaluations/test_contact_outcome.py)
- [Endpoint Evaluation regression tests](../../../../../tests/plugins/evaluations/test_endpoint_reach_evaluations.py)

## canonical architecture / contract

- [contact task manifest](../../../../../docs/contracts/contact-task-manifest.md)
- [experiment plugin composition](../../../../../docs/contracts/experiment-plugin-composition.md)
