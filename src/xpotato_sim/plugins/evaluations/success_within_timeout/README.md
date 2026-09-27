# success_within_timeout Evaluation Plugin

## 意味とresponsibility

`success_within_timeout/v1`はTask所有のmeasured terminal classificationからR7-G primary outcomeを
booleanで導出する。

canonical declaration: [`EVALUATION_PLUGIN`](plugin.py)

## input / output

inputは`endpoint_reach_terminal_classification/v1`、outputはunit `boolean`、frameなしの
`MetricResult`である。`success`だけを`true`、通常failureを`false`とする。

## parameters

なし。timeout / tolerance / dwellは上位manifestとTask terminal判定が所有する。

## lifecycleとside effect

pure deterministic derivationだけを行い、stream集計、artifact出力、runtime操作を行わない。

## compatibilityとcomposition

missing / unavailableはvalueなし`unavailable`、invalid / technical-invalidはvalueなし`invalid`とする。

## constraintsとnon-goals

- constraint: requested / predicted値をmeasured successへ読み替えない
- non-goal: condition summary、statistics、viewer-side calculation

## tests / validation

- [Evaluation plugin test](../../../../../tests/plugins/evaluations/test_endpoint_reach_evaluations.py)

## canonical architecture / contract

- [evaluation design](../../../../../docs/evaluation/world-tool-frame-comparison-design.md)
- [experiment plugin composition](../../../../../docs/contracts/experiment-plugin-composition.md)
