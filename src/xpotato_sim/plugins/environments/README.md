# Environment Plugin

## 責務

Environment axisはgeneric experiment composition上のworld / scene条件を表す。

## 置けるもの / 置けないもの

- 置けるもの: production environment declaration、scene condition/providerと固有resource
- 置けないもの: Robot model、Task目標、Evaluation metric、viewer独自physics

## contractとI/O

- required contract: [experiment plugin composition](../../../../docs/contracts/experiment-plugin-composition.md)
- input: generic compositionのversion付きselection
- output: environment identity、scene condition/provider、composition roleとevidence declaration

## lifecycleとside effect

`free_space_environment/v1`はRobot-owned base sceneへtask objectを追加しないfree-space条件を返す。
`contact_cube_environment/v1`はtyped requestからbackend-owned MuJoCo task sceneをloadする。各pluginの
compose/resetはviewer独自physicsを持たず、contact sceneのmodel mutationはruntime ownerが行う。

## catalog / discovery / registration

`discovery.py`はpublic direct-child packageの`plugin.py::ENVIRONMENT_PLUGIN`だけをbounded discoveryし、
`catalog.py`がlogical identity順のproduction registryへ投影する。private package、test fixture、arbitrary
dynamic importは対象外である。production runner / UIは未実装である。

## shared private owner

現在はなし。

## concrete pluginの追加

axis直下へself-contained packageを追加し、`plugin.py::ENVIRONMENT_PLUGIN`、plugin-local README、
focused testsを同じ変更に含める。package basenameとlogical identityを一致させ、catalogやgeneric
runtimeへconcrete ID / importを追加しない。

## current concrete plugins

- [`free_space_environment/v1`](free_space_environment/README.md): Robot base sceneだけを使い、task objectとcontactを追加しないR7-G free-space条件
- [`contact_cube_environment/v1`](contact_cube_environment/README.md): manifestからMuJoCo cube body / geomを構成し、trial resetと初期contact readinessをbackendへ委譲するR7-H条件

## canonical document

- [runtime composition](../../../../docs/architecture/runtime-composition.md)
- [experiment plugin composition](../../../../docs/contracts/experiment-plugin-composition.md)
