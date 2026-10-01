---
status: canonical
owner: architecture
last_verified: 2026-07-30
canonical_for:
  - runtime input source registry
  - Input Source Plugin v1 ownership boundary
related:
  - docs/architecture/data-flow.md
  - docs/architecture/runtime-composition.md
  - docs/contracts/programmed-target-input-source.md
  - docs/contracts/runtime-input-source-state.md
  - docs/contracts/runtime-input-safety.md
  - docs/contracts/experiment-plugin-composition.md
  - docs/reports/inventories/input-source-plugin-ownership-inventory.md
---

# Runtime Input Source Registry

## 目的とownership

Input SourceはRobot、Environment、Control / Mapping、Task、Evaluationに加わる第6のversioned
composition軸である。production runtime selectionの正本は
`src/xpotato_sim/plugins/input_sources/catalog.py`であり、source identity、contract version、sample schema、
mode、factory、health、lifecycle、CLI alias、execution adapterを解決する。具体registrationの正本は
各`plugins/input_sources/<source_id>/plugin.py::INPUT_SOURCE_PLUGIN`である。

production source selectionの正本は`plugins/input_sources/catalog.py`だけである。
旧`src/xpotato_sim/input_sources/registry.py`、historical descriptor、frame/default registry、
mapping lookup facadeを別moduleへ移植しない。

## Production plugin catalog

`plugins/input_sources/discovery.py`は`plugins.input_sources`直下のpublic direct-child packageをsortし、固定
`plugin.py::INPUT_SOURCE_PLUGIN`からtyped registrationを取得する。private packageは対象外であり、
missing / wrong export、import failure、duplicate identity、package / logical identity mismatchを
fail-closedで拒否する。catalogはdiscovery結果からknown-IDの
`VersionedPluginRegistry[InputSourcePlugin]`とCLI alias mapを構築する。
registryは`INPUT_SOURCE_CATALOG.registry`から明示的に取得する。旧
`INPUT_SOURCE_PLUGIN_REGISTRY`互換名はrepo consumerがないことを確認して退役した。duplicate plugin ID、
duplicate alias、unknown alias、contract version mismatchをfail-closedで
拒否する。中央のconcrete import / tuple list、external package discovery、arbitrary dynamic import、
hot reload、implicit noop fallbackは持たない。

| plugin ID | contract | produced sample schema | mode | CLI alias | generic CLI | execution adapter |
|---|---:|---|---|---|---|---|
| `analog_fixture` | 1 | `analog_fixture_sample/v1` | replay | `analog_fixture` | no | `analog_fixture_input_execution/v1` |
| `noop` | 1 | `noop_sample/v1` | offline | `noop` | yes | `replay_compatibility_input_execution/v1` |
| `programmed_target` | 1 | `programmed_target_sample/v1` | offline | `programmed_target` | yes | `target_metadata_input_execution/v1` |
| `replay` | 1 | `replay_raw_input_frame/v1` | replay | `replay` | yes | `replay_compatibility_input_execution/v1` |
| `selfrionette` | 1 | `loadcell_vector_sample/v1` | live | `selfrionette` | no | `loadcell_input_execution/v1` |
| `viewer` | 1 | `viewer_control_sample/v1` | viewer_bridge | `viewer` | yes | `viewer_local_endpoint_input_execution/v1` |

Plugin identityとsample schema identityは別のversioned identityである。`selfrionette/v1`が7 channel
loadcell deviceのidentityであり、live serial、injected lines、recorded/offline dataはacquisition
backendまたはruntime dependencyであって別device identityではない。旧`loadcell_serial/v1`と
`loadcell_fixture/v1`はrepo-internal migrationを完了してproduction catalogから退役した。

Generic CLIへ公開する名前は従来どおり次の4件だけである。

```text
programmed_target
replay
noop
viewer
```

`selfrionette`と`analog_fixture`はgeneric replay CLI choicesへ追加せず、専用runnerまたは明示的な
runtime dependency / fixture boundaryから到達する。generic CLIの表示順は
`cli/main.py::CLI_INPUT_SOURCE_NAMES`だけが所有し、plugin registrationへglobal ordinalを置かない。

## Plugin contract

### Identity、parameter、factory

- pluginは`VersionedIdentity(name, version)`を持つ。
- produced sample schemaはplugin identityと別に宣言する。
- canonical parameterは`ParameterContract`で検証する。
- clock、serial object、line reader、replay frame object等の非manifest dependencyは
  `InputSourceRuntimeDependencies`で別に渡す。
- request builderとdirect factoryの両方でsource-specific semantic validationをfail-closedに行う。
- factory creationではframe先読み、lifecycle start、serial open、browser accessを行わない。

現在の追加validation:

- programmed target: positive `steps`、`preset=sweep_x`、boolean `loop`
- selfrionette: nonblank port、positive integer baud、tuple lines、string elements、port / linesの排他
- analog fixture: required tuple fixture

### Readerとhealth

factory outputは`HealthyInputSource`を満たし、`read_frame()`と`current_health()`をtyped contractとして
常に提供する。live / viewer bridgeは`ManagedHealthyInputSource`として`start()`と`close()`も提供する。
generic `InputSource` Protocolのcanonical definitionは
`src/xpotato_sim/runtime/experiment/input_source.py`に置く。
旧`src/xpotato_sim/input_sources/base.py`はC4で退役し、consumerはcanonical contractを直接参照する。

- `read_frame()`は毎回`RawInputFrame`を返す。
- `current_health()`は毎回`InputSourceHealth`を返す。
- invalid return objectをfallbackへ変換しない。
- delegate exceptionを隠さない。
- factory直後のhealthはpluginの`initial_health`と一致する。
- initial health検証ではframe read、start、close、device accessを行わない。

health statusは`active`、`inactive`、`stale`、`invalid`、`disconnected`のclosed vocabularyである。
`active`はcommand可能、`inactive`はreasonなしの意図的な非active状態、残る3状態はreason必須のfailureである。
source frameが`viewer_keyboard`や`viewer_gamepad`等のsource-specific `source_kind`を持つ場合、source-owned
health metadataがそのsubtypeを保持し、runtime projection、stale hold、final payloadまで同じ値を維持する。

live / viewer / fixture executionではtyped healthがsource-state truthである。frameにstate fieldがある場合は、
**存在するkeyだけ**をtyped healthと比較し、省略keyはhealth projectionで補完する。
custom replayは例外としてrecorded frame metadataがsource-state truthであり、plugin initial healthで上書きしない。

### Lifecycle

offline / replay sourceへmanaged lifecycleを要求しない。live / viewer bridgeだけが
`ValidatedManagedInputSourceReader`を通じて`start()` / `close()`を委譲する。

- pure execution argumentをstart前に検証する。
- start failureを含む各attemptでcloseを最大1回試行する。
- primary failureをcleanup failureで置換しない。
- normal終了後のcleanup failureは表面化する。
- start failure後にcleanupできたreaderは再startできる。
- close failure時はclosed扱いにせずcleanup retryを許可する。

loadcell liveはfactory直後と正常close後を`disconnected / not_started`、start成功後を`active`、start失敗後を
`disconnected / start_failed`としてhealthへ反映する。explicit startだけがserial portをopenする。正常close後は
portとsource参照を破棄し、read-after-closeを`Selfrionette input source is not started`で拒否する。restart時は
new sourceを構築し、real serialではportを再openする。close failure時は参照と直前healthを保持してcleanup retryを
可能にする。fixtureのone-shot `Iterable[str]`はrunner boundaryで一度だけtuple化する。

## Selectionとexecution

`select_runtime_input_source()`は次を解決する。

1. CLI aliasからregistrationを解決する。
2. `PluginSelection`とversioned pluginを解決する。
3. source-specific request validationを行う。
4. canonical parameterとtyped runtime dependencyを作る。
5. selected frameをcanonical化する。frameにstate metadataがある場合はその値を維持する。
6. validated runtime readerをfactoryから生成する。
7. produced sample schema、execution adapter、optional viewer capabilityをselectionへ保持する。

`RuntimeInputSourceSelection`は既存observable fieldsである`source_name`、`frames`、`loop`、
`initial_metadata`を維持する。plugin-backed selectionはregistrationが保持するtyped execution adapterを必須とし、
adapter欠落はruntime plan生成時にfail-closedで拒否する。repo内にproduction/public callerがないことを確認したため、
source-name tableを持つ`compatibility_execution_adapter()`は退役した。stale safety testもplugin-backed selectionを
`dataclasses.replace()`で変更するcanonical pathへ更新し、source-name fallbackを再導入しない。

## Test ownershipとreusable conformance

current test ownerはproduction packageの責務を鏡写しにし、cross-layerの挙動はintegration ownerへ残す。

| 責務 | current owner |
|---|---|
| generic contract / conformance | `tests/plugins/input_sources/contract/` |
| source-local reader / parser / health / lifecycle | `tests/plugins/input_sources/<plugin_id>/`。Selfrionetteも同じowner ruleを使う |
| mapping algorithm / parameter / frame | `tests/plugins/mappings/` |
| catalog / registry / composition | `tests/runtime/test_input_source_plugin_catalog.py`、`tests/runtime/test_experiment_plugin_composition.py` |
| source -> mapping -> runtime / stale hold | `tests/runtime/` |
| viewer browser provider | `apps/mujoco-viewer/tests/` |
| hardware/manual gate | `tests/selfrionette/`、manual runner tests |

`tests/plugins/input_sources/contract/conformance.py`の`InputSourceConformanceCase`は、plugin固有のvalid
parametersとinjected dependencyだけを受け取り、identity / contract version、produced sample、parameter
contract、factory、initial health、read-frame type、mode、lifecycle、health vocabulary、deterministic readを
共通検証する。production 6 sourceは各plugin-local ownerからこのhelperを再利用する。generic testはproduction
sourceのprivate implementationや別test moduleのprivate helperを参照しない。

test-only `test_dummy_input_source/v1`は`tests/plugins/input_sources/fixtures/`のcatalogへだけ登録する。
production catalog / generic CLIには登録せず、compatible / incompatible mappingを含むcatalog resolve、reader
creation、composition readinessを検証する。不一致schemaはstartupでfail-closedとなる。

## New source onboarding contract

新しいsource追加は次の順で行う。source固有parameter/specはplugin-local ownerへ置き、runtime coreへsource IDの
conditionalを追加しない。

1. acquisition/source責務とmapping責務を分離する。
2. versioned plugin ID、contract version、produced sample schema IDを定義する。
3. typed parameter contractとsource-specific validationを定義する。
4. reader、lifecycle、initial/current health、cleanupを実装する。
5. package-local `plugin.py::INPUT_SOURCE_PLUGIN`でtyped registration、CLI alias、request builder、
   execution adapterを宣言する。CLI表示順は`cli/main.py`が所有する。
6. compatible Mapping Pluginのaccepted sample schemaを宣言する。
7. generic conformanceとplugin-local source testsを追加する。
8. registry / schema compatibilityとminimal runtime/composition smokeを追加する。
9. hardware deviceはserial openを含めず、manual-gated別Issueへ分離する。
10. canonical docsとtest ownerを更新する。新source追加だけを理由にcentral catalogを編集しない。
11. focused validationを通した後、full regressionとviewer validationをmerge gateとして実行する。

focused feedback loopの標準commandは次である。

```bash
uv run pytest tests/plugins/input_sources/contract tests/plugins/input_sources/<plugin_id> tests/plugins/mappings tests/runtime/test_input_source_plugin_catalog.py tests/runtime/test_experiment_plugin_composition.py tests/runtime/test_runtime_input_source_step_loop.py tests/runtime/test_live_input_stale_command_safety.py tests/architecture/test_input_source_plugin_p5_boundaries.py -q
```

これはfull suiteの代替ではない。CI change-detection matrixや新しいlauncher体系は追加しない。

runtime step-loopのsource-state解決:

- replay compatibility: recorded frame metadataから復元する。
- replay以外: typed healthを取得し、frameに存在するstate keyだけ整合性を確認する。
- canonical projection後の同じframeをControl Mapping、record、diagnosticsへ渡す。
- runtime safetyはsource observationとは別にhold reasonを導出できる。

## Source-specific behavior

### Programmed target

- custom framesを拒否する。
- generic CLIでは`loop=False`を明示する。
- direct plugin parameterではoptional `loop=True`を許可する。
- non-loopではterminal frameをholdし、loopでは先頭へwrapする。
- selection materializationはruntime readerを先読みしない。

### Replay

- presetを拒否する。
- custom `RawInputFrame`の順序、timestamp、values、buttons、metadataを維持する。
- recorded `source_active`、`command_age_ms`、`stale_reason`をinitial healthで上書きしない。
- state fieldが部分的な場合はrecorded keyを維持し、省略keyだけcanonical defaultで補完する。
- loop / EOFと`StopIteration` messageを既存sourceに委譲する。
- custom framesはtyped runtime dependencyとして渡す。

### Noop

- explicit registered pluginでありimplicit fallbackではない。
- 単一のdeterministic `RawInputFrame`を繰り返す。
- `source=noop`、`timestamp_s=0.0`、既存metadata、ACTIVE stateを維持する。

### Viewer backend bridge

- `ViewerBridgeRuntimeCapability`でmessage ingress、JSON ingress、endpoint rebase、clock rebindを公開する。
- readerとcapabilityは同じunderlying `ViewerInputSource`を参照する。
- planの`viewer_clock`は既存capabilityへrebindし、readerまたはcapabilityを交換しない。
- rebind前のcontrol messageとendpoint stateを維持し、clock domain間で既存command ageを連続させる。
- initial FK endpointとpublish後endpointを同じcapabilityへrebaseする。
- `viewer_keyboard` / `viewer_gamepad` subtypeをactive / staleの両経路でframe、command、payloadへ維持する。
- frontend providerはbrowser raw acquisitionとlifecycleを所有し、backend sourceはcanonical sampleとhealth、
  Control Mappingはdeadzone、axis/sign、button supplement、command intentを所有する。frontendのnormalized
  gamepad `axes`はcompatibility projectionであり、`raw_axes`がmappingのauthoritative inputである。

### Selfrionette device / acquisition backend

- `plugins/input_sources/selfrionette/`が7-channel protocol、serial framing、diagnostics、intrinsic
  calibration、sensor saturation clamp、typed health、explicit lifecycle、serial backendを所有する。
- `loadcell_fixture`というproduction identityは持たない。injected linesは同じSelfrionette readerへ渡す
  hardware-free backendであり、generic replayはrecorded canonical frame / traceの再生を所有する。
- module import、bounded discovery、catalog construction、factory constructionではserialをopenしない。
  `pyserial`のloadとport openは明示的な`start()`以後に限定する。
- operational deadzone、gain、sign、channel-to-axis assignment、coordinate frame、endpoint delta、
  command clampは`loadcell_endpoint_mapping/v1`が所有する。

### Analog fixture

- strict parsing、timestamp、raw values、active / inactive / stale stateをsource pluginが所有する。
- inactiveかつreasonなしは`inactive`を維持する。
- reason付きinactiveだけを`stale`へ投影する。
- frame / health parity、sequence ordering、terminal holdを維持する。
- normalizationとmapping semanticsはmapping側に残す。

## Current public surface

source-owned implementationのphysical ownerは次である。

| responsibility | canonical owner |
|---|---|
| generic `InputSource` reader contract | `runtime/experiment/input_source.py` |
| programmed target trajectory / frame / reader / defaults | `plugins/input_sources/programmed_target/source.py` |
| replay reader | `plugins/input_sources/replay/source.py` |
| viewer ingestion / lifecycle / health / diagnostics / defaults | `plugins/input_sources/viewer/source.py` |
| analog fixture sample / strict parser | `plugins/input_sources/analog_fixture/source.py` |
| Selfrionette protocol / intrinsic normalization / injected-line backend / diagnostics / health / lifecycle | `plugins/input_sources/selfrionette/` |

旧`input_sources/`、`input_interpreters/`、descriptor registry、source / mapping facade、
interpreter-based `RuntimePipeline`、old-path loadcell re-export、`mapping_plugin=None` fallback、
compatibility CLI wrapper、Mapping package-root lazy facade、旧flat Mapping modules、root
`plugins/input_source_registration.py`、旧identity alias、runtime input-source移行aliasはcurrent
public surfaceではない。axis-local `plugins/input_sources/registration.py`はtyped registration contractの
current ownerであり、退役root moduleと混同しない。

canonical public APIとして維持するのは各concrete packageの`__all__`、catalog resolverとcanonical helperである。
`runtime.runners.selfrionette_serial_dry_run.run_selfrionette_serial_dry_run_smoke()`はoffline fixture capabilityとして残すが、
versioned `loadcell_endpoint_mapping/v1`の明示指定を必須とする。

production/internal commandはcatalogからsourceとversioned mappingを解決する。
`selfrionette replay --input-source`は`programmed_target`、`replay`、`noop`だけを受理し、
`selfrionette viewer --input-source`はgeneric alias 4件すべてを受理する。`viewer` sourceはviewer commandの
ingress lifecycleを必要とし、replay commandへinitial frameとして投影しない。
旧wrapperのimplicit robot selection、validation wording差はcanonical CLIへ吸収しない。
`selfrionette replay ...`と`selfrionette viewer ...`が唯一のcurrent operator entry pointであり、
`--robot` required、exit status、stdout / stderr、NDJSON、viewer ingress behaviorを維持する。

## 関連canonical文書

- [runtime input source state](runtime-input-source-state.md)
- [runtime input safety](runtime-input-safety.md)
- [programmed target input source](programmed-target-input-source.md)
- [continuous endpoint velocity input](continuous-endpoint-velocity-input.md)
- [viewer control message schema](viewer-control-message-schema.md)
- [experiment plugin composition](experiment-plugin-composition.md)

棚卸しの根拠と時点別の詳細は
[Issue #458 input source ownership inventory](../reports/inventories/input-source-plugin-ownership-inventory.md)を参照する。
inventoryはhistorical evidenceでありcurrent contractの正本ではない。

## Viewer provider / source / mapping boundary

viewer pluginの`viewer_control_sample/v1`は、browser providerが送ったraw payloadをbackend
viewer sourceが検証済みcanonical sampleへ投影したschemaである。source registrationはconcrete
Control Mapping object、default mapping selection、Mapping parameterを所有しない。
runtimeはsourceとは独立してmapping selectionをresolveする。diagnostic convenience pairingは
`runtime/control/input_source_mapping_policy.py`が所有し、callerのexplicit selectionを上書きしない。
selection時にproduced sample schemaとmappingのaccepted schemaをexact matchで検証し、未知schemaやidentity
mismatchはmapping実行前にfail-closedとする。

mappingの`ParameterContract`とoptional semantic validator / normalizerもselection / plan readinessで実行する。
unknown parameter、negative / non-finite speed・deadzone・max delta、invalid keyboard axis / directionは
source lifecycle開始前にrejectし、normalized / frozen parameter mappingをstep loopへ渡す。invalid parameterでは
managed sourceをstartせず、frameをreadしない。

責務は次の通り固定する。

- frontend `ViewerInputProviderRegistry`: known static IDs、provider lifecycle、browser event / Gamepad API、keyboard focus、Gamepad visibility / disconnect、raw device neutral state、timestamp / sequence、raw payload。GamepadはDOM focusをactivation条件にせず、visible documentでpollを継続しhidden時にzero/suspendする。normalized `axes`はwire / overlay compatibility projectionである。
- backend `ViewerInputSource`: parse / validation、provider identity / schema、latest sample、active / stale / invalid / disconnected health、250 ms timeout、cleanup、canonical sample、legacy metadata projection。
- `ViewerKeyboardGamepadMappingStrategy`: canonical sampleのkeyboard binding、gamepad raw axis、sign、speed /
  gain、deadzone、button 0/1 supplement、world / tool frame、typed endpoint-velocity intent。mappingは
  `viewer_control_message`のlegacy summary、frontend module、transport implementation detailを読まない。
- runtime step loop: mapping resultの適用、desired endpoint progression、endpoint rebase、MuJoCo command composition。

raw gamepad sampleでは`raw_axes`をmappingのauthoritative inputとして保持するが、gamepad/v1の`zero_state`、
`source_active`、heartbeatはlegacy projected `axes`とbuttonsに基づくobservable semanticsを維持する。
mapping deadzoneの結果はsource healthと別のcommand zero semanticsとして扱う。button-only inputはraw axisが
zeroでもmappingへ渡す。`raw_axes`を持たないlegacy messageは旧`axes` / `zero_state`解釈を維持する。

frontend registryはarbitrary dynamic importを行わない。lifecycleが選択providerを一括activate / disposeし、
unknownまたはduplicate provider IDは安全なdefaultへ置換せずrejectする。provider disposal後は
publication、polling、heartbeatを停止し、再activationはzero / safe stateから開始する。

keyboard / continuous mappingのcanonical implementationは`src/xpotato_sim/plugins/mappings/`、
viewer sourceのcanonical implementationを`src/xpotato_sim/plugins/input_sources/viewer/`から直接使用する。
旧facade、low-level registry、retained compatibility symbolは存在しない。

## Current Mapping ownership and conformance

production Control Mapping catalog は次の deterministic registrations を持つ。

| mapping plugin | accepted sample boundary | owner |
|---|---|---|
| `viewer_keyboard_gamepad_mapping/v1` | `viewer_control_sample/v1` | viewer keyboard/gamepad mapping |
| `replay_mapping/v1` | `replay_raw_input_frame/v1` | replay intent/command compatibility |
| `analog_fixture_mapping/v1` | `analog_fixture_sample/v1` | analog axis/sign/scale/deadzone/frame/endpoint intent |
| `loadcell_endpoint_mapping/v1` | `loadcell_normalized_input_intent/v1` | loadcell endpoint delta and `MotionCommand` metadata |

analogのparser、timestamp、raw values、healthはsource-ownedで、mapping implementationは
`src/xpotato_sim/plugins/mappings/analog_fixture_mapping/`にある。Selfrionetteのserial parser、
diagnostic、intrinsic normalizationは`src/xpotato_sim/plugins/input_sources/selfrionette/`が所有し、
operational deadzone、channel-axis weights、gain、max delta、endpoint delta、command conversionは
`src/xpotato_sim/plugins/mappings/loadcell_endpoint_mapping/`が所有する。acquisition schemaはraw
`loadcell_vector_sample/v1`、source-owned `mapping_input_adapter`のoutputはeffective mapping-input schema
`loadcell_normalized_input_intent/v1`である。generic runtimeはadapter contractを検証してその結果だけを
mapping strategyへ渡し、Control Mapping Pluginはnormalized schemaをaccepted schemaとして宣言する。
adapter不在・input/output schema mismatch・別mappingとのschema mismatchはfail-closedにする。

`programmed_target`と`noop`を`replay_mapping/v1`へ接続するdiagnostic convenience policyは
`runtime/control/input_source_mapping_policy.py`が所有する。両sourceのproduced sample schemaはそれぞれ
`programmed_target_sample/v1`、`noop_sample/v1`のまま維持し、source-owned identity adapterが
`replay_raw_input_frame/v1`をeffective mapping-input schemaとして宣言する。adapterは同じ
`RawInputFrame` objectを返し、source、timestamp、values、buttons、metadata、reader behaviorを変更しない。
`replay_mapping/v1`はmetadataをshallow-copyして`InputIntent`を生成する。

canonical mapping testsは`plugins/mappings/` ownerを直接importし、source-owned parser/normalization typeは`plugins/input_sources/`のcanonical ownerから参照する。

generic conformanceはsource-specific valid parametersに加え、frame/metadata validator、
timestamp/sequence policy、sequence validator、optional typed health transition casesを受け付ける。
production 6 source casesはconstant timestamp、monotonic/indexed、preserved replay order、
terminal holdのいずれかを明示する。

`raw_axes`はprovider pathのcanonical mapping inputであり、frontend normalized `axes`はwire / overlay
compatibility projectionである。gamepad/v1の`zero_state`、`source_active`、heartbeatはprojected axesと
buttonsに基づくobservable semanticsを維持し、mapping deadzoneのcommand zeroとは分離する。
button-only sampleもmappingへ渡し、`raw_axes`を持たないlegacy messageは既存`axes` / `zero_state`
解釈を維持する。fixed frontend `0.1` projectionとconfigurable backend thresholdはmapping plugin内で
一元化し、default parity、custom `0.0`のraw `0.05` hold、raw `0.15`の`1/18`をgolden testで固定する。

runtime parameter precedenceは`explicit runtime mapping parameters > Mapping plugin defaults`とする。
plan readinessでMapping contractを正規化・freezeしてからruntimeへ渡し、source instance / frame metadata /
source registrationからMapping parameterを投影しない。source-owned mapping input adapterはsample
representation変換だけを行い、Mapping operational parameterを所有しない。

## 左右独立の1スティックXYZ操作

`sim-gamepad-left-xyz`／`sim-gamepad-right-xyz`のXYZ操作はstick XY + analog trigger Zを使用する。
取得session、trigger符号、表示は[Gamepad trigger操作契約](gamepad-trigger-control.md)を参照する。
旧XY/XZ切替は退役した。[Gamepad平面操作契約](gamepad-plane-control.md)はhistorical記録であり、旧設定は拒否する。
