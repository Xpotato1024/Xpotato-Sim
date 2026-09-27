# replay_mapping

## 意味とresponsibility

replay-compatible frameの既存command / intent semanticsをruntime controlへ橋渡しする。
canonical declaration: [`CONTROL_MAPPING_PLUGIN`](plugin.py)

## input / output

declared replay sampleを受け、frame metadataとcommand情報を保持した`InputIntent`を出力する。

## parameters

追加parameterはない。accepted schemaとsemanticsは[`implementation.py`](implementation.py)を正とする。

## lifecycleとside effect

statelessなsoftware変換で、file read、frame storage、device accessはない。

## compatibilityとcomposition

Replay / noop / programmed target等のacquisitionとは別責務で、sample schemaに基づき結線する。

## command semantics route

replay commandをjoint position commandへ解決するtyped routeを宣言する。backendへ任意commandを
passthroughしない。

## constraintsとnon-goals

- constraint: declared replay schemaとversionを要求する
- non-goal: replay file ownership、loop lifecycle、Robot executionを所有しない

## tests / validation

- [runtime dry-run](../../../../../docs/operations/runtime-dry-run.md)

## canonical architecture / contract

- [experiment plugin composition](../../../../../docs/contracts/experiment-plugin-composition.md)
- [runtime composition](../../../../../docs/architecture/runtime-composition.md)
