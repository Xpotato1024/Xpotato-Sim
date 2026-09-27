# analog_fixture Input Source

## 意味とresponsibility

既に記録されたN-channel sample列をstrictに検証し、deterministicな`RawInputFrame`として供給する。
canonical declaration: [`INPUT_SOURCE_PLUGIN`](plugin.py)

## input / output

inputはcaller所有のfixture sample列、outputはdeclared analog fixture sampleである。
fixture fileの探索や生成は行わない。

## parameters

`samples`を必須とする。型とcurrent contractは[`plugin.py`](plugin.py)を正とする。

## lifecycleとside effect

readerはmemory上のsampleを順に読み、末尾を保持する。device、filesystem、network accessはない。
sampleのactive / stale状態をhealthへ投影する。

## compatibilityとcomposition

acquisitionだけを所有し、Mapping selectionはruntime policyが行う。sample semanticsの変換は対応する
Mapping側の責務である。

## constraintsとnon-goals

- constraint: sample field、finite number、active / stale整合性をstrictに検証する
- non-goal: recorded data自体をexperiment evidenceとして認定しない

## tests / validation

- [source test](../../../../../tests/plugins/input_sources/analog_fixture/test_analog_fixture_source.py)

## canonical architecture / contract

- [analog fixture mapping contract](../../../../../docs/contracts/analog-fixture-mapping.md)
- [Input Source registry](../../../../../docs/contracts/runtime-input-source-registry.md)
