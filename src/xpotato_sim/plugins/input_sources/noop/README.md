# noop Input Source

## 意味とresponsibility

offline pathで明示的なno-op sampleを供給するdeterministic sourceである。
canonical declaration: [`INPUT_SOURCE_PLUGIN`](plugin.py)

## input / output

runtime requestからmetadataを受け、declared noop sampleをreplay-compatible frameへ渡す。

## parameters

`metadata`を受ける。current contractは[`plugin.py`](plugin.py)を正とする。

## lifecycleとside effect

常にactiveなsoftware-only readerで、device、filesystem、network accessはない。

## compatibilityとcomposition

offline pipelineの明示選択用であり、fallback defaultやhardware emulationではない。

## constraintsとnon-goals

- constraint: versioned sample / adapter contractを通す
- non-goal: Robot motion、fixture generation、experiment evidenceを生成しない

## tests / validation

- [Input Source boundary test](../../../../../tests/architecture/test_input_source_plugin_p5_boundaries.py)

## canonical architecture / contract

- [Input Source registry](../../../../../docs/contracts/runtime-input-source-registry.md)
- [runtime composition](../../../../../docs/architecture/runtime-composition.md)
