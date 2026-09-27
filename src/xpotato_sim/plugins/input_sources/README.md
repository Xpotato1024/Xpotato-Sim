# Input Source Plugin

## 責務

Input Source axisは、browser、recorded data、programmed data、reader、serial等から値を取得し、
canonical sampleとhealthをruntimeへ渡す。Mapping selectionやrobot command生成は所有しない。

## 置けるもの / 置けないもの

- 置けるもの: acquisition、parser、reader lifecycle、source固有health / metadata
- 置けないもの: Mapping selection、Robot制御意味、MuJoCo step、viewer rendering

## contractとI/O

- required contract: [runtime Input Source registry](../../../../docs/contracts/runtime-input-source-registry.md)
- input: source固有parameterと外部またはdeterministic data
- output: declared sample schemaに従うsample、health、source metadata

## lifecycleとside effect

readerはfactoryで構築され、runtimeがread / health lifecycleを管理する。hardware access可能な
pluginはREADMEとoperationでoperator gateを示し、software-only sourceと混同しない。

## catalog / discovery / registration

[`discovery.py`](discovery.py)が固定`plugin.py` / `INPUT_SOURCE_PLUGIN`を読み、
[`catalog.py`](catalog.py)がimmutable catalogを構成する。[`registration.py`](registration.py)が
request builder、CLI alias、execution adapterとの結線を所有する。全candidateはfail-closedで検証する。

## shared private owner

共通のcatalog / discovery / registration以外にaxis-private algorithm ownerはない。

## concrete pluginの追加

direct-child packageにsource、固定entry point、README、unit / architecture testを追加する。
acquisitionとMappingを同じpluginへ統合せず、sample compatibilityをdeclarationで明示する。

## canonical document

- [runtime composition](../../../../docs/architecture/runtime-composition.md)
- [Input Source registry](../../../../docs/contracts/runtime-input-source-registry.md)
- [hardware safety](../../../../docs/operations/hardware-safety.md)
