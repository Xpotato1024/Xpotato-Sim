# completion_time Evaluation Plugin

## 意味とresponsibility

`completion_time/v1`は成功したendpoint reach trialのdescriptive completion timeをsecondで返す。
primary outcomeではない。

canonical declaration: [`EVALUATION_PLUGIN`](plugin.py)

## input / output

inputは`endpoint_reach_terminal_classification/v1`、outputはframeなしのsecond値である。

## parameters

なし。timeoutは上位manifestが所有する。

## lifecycleとside effect

pure deterministic derivationだけを行う。failed trialには架空のtimeを生成せずvalueなし`unavailable`を返す。

## compatibilityとcomposition

technical-invalidはvalueなし`invalid`、missing / unavailableはvalueなし`unavailable`である。

## constraintsとnon-goals

- constraint: success evidenceだけが数値を持つ
- non-goal: trial aggregation、artifact export、primary outcomeへの昇格

## tests / validation

- [Evaluation plugin test](../../../../../tests/plugins/evaluations/test_endpoint_reach_evaluations.py)

## canonical architecture / contract

- [evaluation design](../../../../../docs/evaluation/world-tool-frame-comparison-design.md)
- [experiment plugin composition](../../../../../docs/contracts/experiment-plugin-composition.md)
