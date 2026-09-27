# off_axis_drift Evaluation Plugin

## 意味とresponsibility

`off_axis_drift/v1`はinitial measured tipとtargetを結ぶ直線からmeasured tip trajectoryが離れた
perpendicular distanceの最大値を導出するR7-G secondary outcomeである。

canonical declaration: [`EVALUATION_PLUGIN`](plugin.py)

## input / output

inputは`endpoint_reach_measured_trajectory/v1`、outputはMuJoCo world / scene frameのmeter値である。

## parameters

なし。initial / targetはfrozen manifestへbindされたcanonical evidenceから取得する。

## lifecycleとside effect

pure deterministic geometryだけを行い、runner、log reconstruction、aggregation、exportを行わない。

## compatibilityとcomposition

空trajectory、non-finite値、非単調time、initial / target不成立をinvalidとして拒否する。

## constraintsとnon-goals

- constraint: requested / resolved / predicted trajectoryから計算しない
- non-goal: contact drift、condition summary、viewer-side metric

## tests / validation

- [Evaluation plugin test](../../../../../tests/plugins/evaluations/test_endpoint_reach_evaluations.py)

## canonical architecture / contract

- [evaluation design](../../../../../docs/evaluation/world-tool-frame-comparison-design.md)
- [experiment plugin composition](../../../../../docs/contracts/experiment-plugin-composition.md)
