# analog_fixture_mapping

## 意味とresponsibility

recorded analog fixture sampleをlocal endpoint velocity intentへ変換する。
canonical declaration: [`CONTROL_MAPPING_PLUGIN`](plugin.py)

## input / output

declared analog fixture sampleとmapping configを受け、source stateを保持した`InputIntent`を出力する。

## parameters

`mapping_config`を必須とする。型とcurrent semanticsは[`implementation.py`](implementation.py)を正とする。

## lifecycleとside effect

statelessなsoftware変換で、device、filesystem、network accessはない。

## compatibilityとcomposition

analog fixture acquisitionとは別pluginであり、accepted sample schemaで結線可否を判定する。

## command semantics route

local endpoint velocityをjoint position commandへ解決するtyped routeを宣言する。route identityの
current値は[`implementation.py`](implementation.py)を正とする。

## constraintsとnon-goals

- constraint: channel mappingとfinite値を検証する
- non-goal: fixture acquisition、Robot IK / backend executionを所有しない

## tests / validation

- [analog fixture contract](../../../../../docs/contracts/analog-fixture-mapping.md)

## canonical architecture / contract

- [analog fixture mapping](../../../../../docs/contracts/analog-fixture-mapping.md)
- [experiment plugin composition](../../../../../docs/contracts/experiment-plugin-composition.md)
