# programmed_target Input Source

## 意味とresponsibility

programで構築したtarget trajectoryをdeterministicなsample列として供給する。
canonical declaration: [`INPUT_SOURCE_PLUGIN`](plugin.py)

## input / output

trajectory条件からtarget / desired endpoint metadataを持つframeを生成する。fixture fileは所有しない。

## parameters

step数、initial position、preset、loopをrequest contractで扱う。型、必須性、current値は
[`plugin.py`](plugin.py)と[`source.py`](source.py)を正とする。

## lifecycleとside effect

software-only readerで、loopしない場合は最終frameを保持する。device / filesystem accessはない。

## compatibilityとcomposition

target metadataをsampleとして渡し、Mapping / runtimeがcommandへ解決する。deterministic sourceであることと、
runのexperiment evidenceであることは別である。

## constraintsとnon-goals

- constraint: trajectoryは空にできず、3要素vectorと正のtime stepを検証する
- non-goal: observation、metric、experiment logを所有しない

## tests / validation

- [programmed target contract](../../../../../docs/contracts/programmed-target-input-source.md)

## canonical architecture / contract

- [programmed target input](../../../../../docs/contracts/programmed-target-input-source.md)
- [Input Source registry](../../../../../docs/contracts/runtime-input-source-registry.md)
