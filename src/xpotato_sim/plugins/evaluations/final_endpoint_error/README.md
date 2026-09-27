# final_endpoint_error Evaluation Plugin

## 意味とresponsibility

`final_endpoint_error/v1`はfinal measured endpointとtargetのEuclidean distanceをdescriptive metricとして
返す。primary outcomeではない。

canonical declaration: [`EVALUATION_PLUGIN`](plugin.py)

## input / output

inputは`endpoint_reach_measured_trajectory/v1`、outputはMuJoCo world / scene frameのmeter値である。

## parameters

なし。targetはfrozen manifestへbindされたcanonical evidenceから取得する。

## lifecycleとside effect

pure deterministic derivationだけを行い、log reconstruction、condition summary、artifact exportを行わない。

## compatibilityとcomposition

missing / unavailable / invalid trajectoryを数値0へ変換しない。

## constraintsとnon-goals

- constraint: final measured sampleだけを使用する
- non-goal: target selection、trial aggregation、viewer-side metric

## tests / validation

- [Evaluation plugin test](../../../../../tests/plugins/evaluations/test_endpoint_reach_evaluations.py)

## canonical architecture / contract

- [evaluation design](../../../../../docs/evaluation/world-tool-frame-comparison-design.md)
- [experiment plugin composition](../../../../../docs/contracts/experiment-plugin-composition.md)
