# Control Mapping Plugin

## 責務

Mapping axisはcanonical sampleをcontrol intent / command metadataへ変換し、必要な場合だけ
command semantics routeを宣言する。source acquisitionとbackend executionは所有しない。

## 置けるもの / 置けないもの

- 置けるもの: sample validation、mapping parameter、intent変換、command semantics declaration
- 置けないもの: device / browser acquisition、Robot選択、MuJoCo step、hardware lifecycle

## contractとI/O

- required contract: [experiment plugin composition](../../../../docs/contracts/experiment-plugin-composition.md)
- input: declarationが受理するsample schemaとmapping parameter
- output: `InputIntent`と、該当pluginだけが宣言するcommand semantics route

## lifecycleとside effect

Mappingは純粋な変換境界であり、device、filesystem、networkをopenしない。runtimeが選択した
Input Sourceとschema compatibilityを検証してから呼び出す。

## catalog / discovery / registration

[`discovery.py`](discovery.py)が固定`plugin.py` / `CONTROL_MAPPING_PLUGIN`を読み、
[`catalog.py`](catalog.py)がregistryを構成する。Mappingはrequest builder、CLI alias、execution adapterを
束ねる独立registration layerを必要とせず、versioned `ControlMappingPlugin`自体が登録単位である。

## shared private owner

[`_continuous_endpoint_velocity.py`](_continuous_endpoint_velocity.py)は複数Mappingが使うvelocity
intent primitive、[`_command_routes.py`](_command_routes.py)はtyped route factoryを所有する。
どちらもdiscoverable pluginではなく、具体plugin IDを列挙しない。

## concrete pluginの追加

direct-child packageへimplementation、固定entry point、README、mapping testを追加する。
catalogやshared private ownerへ具体ID、source-specific fallbackを追加しない。

## canonical document

- [dependency boundary](../../../../docs/architecture/dependency-boundaries.md)
- [experiment plugin composition](../../../../docs/contracts/experiment-plugin-composition.md)
- [continuous endpoint velocity](../../../../docs/contracts/continuous-endpoint-velocity-input.md)
