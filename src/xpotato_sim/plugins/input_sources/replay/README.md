# replay Input Source

## 意味とresponsibility

callerが渡したimmutable `RawInputFrame`列を順序どおり再生するdeterministic sourceである。
canonical declaration: [`INPUT_SOURCE_PLUGIN`](plugin.py)

## input / output

inputはcaller所有のframe列、outputは同じframe referenceである。file format、file discovery、
fixture生成はこのpackageの責務ではない。

## parameters

metadataとloopを扱う。current contractは[`plugin.py`](plugin.py)を正とする。

## lifecycleとside effect

非loop時は末尾の次で`StopIteration`、loop時は先頭へ戻る。device / filesystem accessはない。

## compatibilityとcomposition

sample取得だけを担当し、replay semanticsをcommandへ変換するMappingは別に選択する。

## constraintsとnon-goals

- constraint: 空のframe列を拒否する
- non-goal: input fileのownershipやexperiment evidenceの妥当性を保証しない

## tests / validation

- [runtime dry-run](../../../../../docs/operations/runtime-dry-run.md)

## canonical architecture / contract

- [Input Source registry](../../../../../docs/contracts/runtime-input-source-registry.md)
- [runtime composition](../../../../../docs/architecture/runtime-composition.md)
