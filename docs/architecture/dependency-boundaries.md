---
status: canonical
owner: architecture
last_verified: 2026-09-28
canonical_for:
  - import boundaries
related:
  - tests/architecture/test_import_boundaries.py
---

# dependency境界

この文書はimport境界だけを定義する。data flow、runtime composition、
viewer/transport contractはarchitecture文書とcontract文書で定義し、importから推測しない。

許可するdependency方向:

```text
schemas
  -> plugins/input_sources
plugins/mappings
kinematics
motion
mujoco_backend
transport
  -> runtime
```

許可する例:

```text
plugins/input_sources -> schemas, runtime input-source contract
plugins/mappings      -> schemas, runtime mapping contract
motion              -> schemas, kinematics
kinematics          -> schemas
mujoco_backend      -> schemas
transport           -> schemas
runtime             -> all layers
```

output transport compositionの許可例は次のとおりである。既存permission、safety gate、lifecycle、trace coreは
transportへ直接依存しない。

```text
runtime.output.transport_adapter -> runtime.output.permission / safety_gate / lifecycle
runtime.output.transport_adapter -> schemas, transport
runtime.output.fast_arm_adapter -> runtime.output.safety_gate / lifecycle / transport_adapter
runtime.output.fast_arm_adapter -> runtime.safety, runtime.composition.robot_profile
runtime.output.fast_arm_adapter -> plugins.robots.fast_arm.adapter.physical_output
plugins.robots.fast_arm.adapter.physical_output -> schemas.command
```

`runtime.output.fast_arm_adapter`はaccepted physical evidence handoff、FastArm mapping、P5、二重permission、
operator gate、generic transportを結ぶcomposition ownerである。robot固有mappingとrouter observation parserは
plugin-local adapterが持つpure contractであり、runtimeやtransportをimportしない。generic `transport_adapter`
はencoderのrequired-authorization capabilityをconstructorで照合し、FastArm codecにはv2 external gateを要求する。

このruntime-to-plugin importは限定例外である。`runtime/output/fast_arm_adapter.py`に加え、
`fast_arm_observation.py`はwire DTOとrouter parser/correlationだけ、`fast_arm_emulation.py`はmappingとwire DTO/
pure変換だけを同じ`plugins.robots.fast_arm.adapter.physical_output`からimportできる。
許可symbolsをfileごとにarchitecture testで列挙する。新しい2 ownerはUDP、serial、physical session、
permission/safety/grantの構築やthreadをimportせず、no-I/Oに限定する。別のconcrete FastArm moduleをimportすること、
および他のruntime/core/transportからこのpluginへ依存することは禁止する。一般的な許可には広げない。

Input Source Pluginのgeneric `InputSource.read_frame() -> RawInputFrame` contractは
`runtime/experiment/input_source.py`がdefinitionを所有し、production source implementationとregistrationは
`plugins/input_sources/`が所有する。Control Mappingのcanonical ownerは`plugins/mappings/`である。
C4で旧`input_sources/`、`input_interpreters/`、descriptor registry、source / mapping facadeを退役した。
これらの旧packageを再作成せず、consumerはcanonical ownerを直接参照する。
source contractからfast_arm、task / evaluation実装、viewer TypeScript、serial transportをimportしない。
Control Mapping Pluginはproduced / accepted sample schemaのversioned identityだけを参照し、device handle、
serial、browser eventを所有しない。P5の`tests/architecture/test_input_source_plugin_p5_boundaries.py`は
catalog identity、duplicate alias、source-name dispatch、source/mapping schema declaration、plugin-local owner、
source pluginの禁止import、mapping pluginのdevice/browser禁止importをAST / registry introspectionで検査する。
C4 guardは旧package directoryと旧import、legacy pipeline、registry、facade、CLI wrapperの不存在も検査する。
単純grepだけをboundaryの根拠にしない。

`schemas/`内はwire domain間の依存も一方向に固定する。`input`、`command`、`state`、`endpoint`は
`types`だけへ依存でき、`experiment_log`は`endpoint`だけへ依存できる。`viewer_control`と`types`は
他schema domainへ依存しない。canonical groupingと退役moduleは`docs/contracts/schemas.md`を正とする。

Robot plugin compositionでは、上記layer境界に加えて次の方向を固定する。

```text
generic schema / domain / Protocol
  <- generic registry / provider adapter / Robot Bundle contract
  <- robot-specific profile / runtime / feasibility / initial state
  <- Robot Bundle assembly
  <- robot-specific plugin.py / ROBOT_PLUGIN registration
  <- bounded first-party discovery / plugins/robots/catalog.py
  <- application composition root
```

axis-specific catalog / discovery / registrationは`plugins/robots/`、
`plugins/input_sources/`、`plugins/mappings/`の各ownerへ閉じる。root `plugins/`は
cross-axis primitiveだけを所有し、旧root moduleをcompatibility aliasとして残さない。
Mappingに追加registration情報がない限り`mappings/registration.py`は作らない。
Mappingのaxis-local private shared ownerは、algorithm primitiveの
`plugins/mappings/_continuous_endpoint_velocity.py`と、declaration / route factoryの
`plugins/mappings/_command_routes.py`である。どちらもdiscoverable plugin entryではない。

- `xpotato_sim.plugins.robots.discovery`は`xpotato_sim.plugins.robots`直下packageだけを列挙し、
  固定`plugin.py`の固定`ROBOT_PLUGIN`だけを読む。configuration値、robot ID、external entry pointを
  import pathとして使用しない。
- 各robot packageの`ROBOT_PLUGIN`はBundle、viewer declaration、resource declaration、
  onboarding contract versionを一つのimmutable registrationへ束ねる。`__init__.py`の
  import副作用で自己登録しない。onboarding contract versionはregistration schema軸であり、
  Bundle / Profile / Viewerのrobot logical version軸とは独立させる。
- `xpotato_sim.plugins.robots.catalog`はproduction discovery結果の唯一のprojection入口であり、
  concrete robot package、具体robot ID、Bundle singletonを直接importまたは列挙しない。
- ProfileとRuntime Pluginのresolverは、別registryへ具体objectを重複登録せず、resolved Bundleの
  `profile`と`runtime_plugin`を返す。
- `RuntimeConfig.robot_selection`、catalog resolver、experiment compositionは同じ`PluginSelection`を使用し、
  robot logical versionをapplication compositionまで保持する。onboarding schema versionはselection軸にしない。
- generic experimentの`RobotBundle.identity`はProfile IDと異なるcomposition identityを持てる。一方、
  first-party production runtime selectionではshared consistency validatorによりselection、Bundle logical
  identity、Profile ID / contract version、Runtime Plugin ID / canonical Profile objectを一致させる。
  このproduction制約をgeneric `RobotBundle` constructionへ逆流させない。
- Bundleのtyped providerはgeneric `ProviderAssemblyBinding`でBundle logical identityとcanonical Profile / Runtime
  Plugin ownerへbindする。provider adapter class名ではなくbinding contractとobject identityを検査する。
- generic `runtime` contract、`kinematics`、`motion`、generic MuJoCo backendは
  `xpotato_sim.plugins`、catalog、Bundle assembly、evaluation manifestへ逆依存しない。
- application compositionはcatalogからBundleをresolveし、consumerへ必要なtyped providerだけを渡す。
- production pipeline builderはpre-bound executionを注入面として公開せず、current Mappingとcurrent
  Robot Bundleからcanonical route strategy / binding / providerを内部解決する。別Robot、別logical
  version、stale provider、同じroute identityを名乗るcustom strategyをcurrent simulatorへ組み合わせない。
  replay backendもBundleのcanonical Runtime Pluginが構築し、model contractをpipeline return前に検証する。
  config selection、Bundle identity、Profile identity / contract version、Runtime Plugin identity、
  provider assembly、model、qpos guardを一つのcompositionとして解決し、raw Bundle identityの自己申告だけを
  ownership proofにしない。external simulatorやarbitrary guard / profile metadataをproduction APIへ注入しない。
  runtime planはpipelineが保持する同一binding objectを参照する。
- Mapping packageとRobot packageは互いのconcrete IDをimportしない。Mappingのcontrol semantics、
  selected runtime conversion route、Robotのcommand semantic providerを`VersionedIdentity`で照合する。
  routeはtyped executable strategy、Robotはtyped providerを所有し、generic runtimeが両者をbindする。
  class名やmetadata keyによるcompatibility判定、generic runtime内のconcrete route ID dispatchを行わない。
- executable semanticはgeneric contractで`joint_position_command/v1 ↔ JointPositionCommand`、
  `endpoint_velocity_command/v1 ↔ EndpointVelocityCommand`を固定する。selected route strategy、
  execution binding、Robot providerのcommand typeはこのcontractとexact一致しなければならない。
  application-facing production runnerは`MotionCommand`をbackendへ直接渡さず、route-bound pipelineと
  typed providerを経由する。診断も`project_joint_position_command`を再利用し、旧
  `apply_command(MotionCommand)`とbackendの旧pending分岐は退役した。
- generic Robot Profile contractは`xpotato_sim.runtime.composition.robot_profile`、viewer向けrobot declaration
  contractは`xpotato_sim.runtime.composition.viewer_robot_declaration`が所有する。旧flat moduleは退役済みである。
- Selfrionetteの7-channel protocol、intrinsic normalization、typed health、serial / injected backendは
  `xpotato_sim.plugins.input_sources.selfrionette`が所有する。旧`_loadcell`、`loadcell_serial`、
  `loadcell_fixture` production ownerは退役済みである。
- fast_arm固有implementationは`plugins/robots/fast_arm/`だけが所有する。旧`robots/fast_arm.py`、
  `robot_registry.py`、`runtime/fast_arm_*.py`、旧registry moduleは退役済みであり、再作成しない。
- fast_arm package内のshared coreは、`plugins/robots/fast_arm/core/`を物理mount pointとする独立Python
  distribution/package `fast_arm_core`である。root projectはuv workspaceの通常dependencyとして参照し、
  root distributionのpackage discoveryとsdist manifestから除外する。root package dataは必要なadapter
  resourceだけを明示し、core mount pointを暗黙収集しない。`fast_arm_core -> adapter`または
  `fast_arm_core -> selfrionette`を禁止し、`adapter -> fast_arm_core`と
  `adapter -> generic Protocol / schema`だけを許可する。
  generic layer、他robot、viewerはfast_arm core implementationへ依存しない。root `plugin.py`の
  `ROBOT_PLUGIN`を唯一のproduction discovery入口とし、coreまたはadapterに第二のentryを作らない。
  `xpotato_sim.plugins.robots.fast_arm.core`をshared import APIにせず、runtimeで`sys.path`を書き換えない。
- `plugins/robots/fast_arm/adapter/`はSelfrionette schema、runtime、MuJoCo backend、viewer、diagnosticsへの
  projectionだけを所有する。#605で旧root/diagnosticsの12転送moduleを退役し、内部consumerは正式な
  adapter ownerをimportする。数式、定数、resource resolver、factory、registrationを再実装しない。
- generic `kinematics`はsolver Protocolだけ、generic `mujoco_backend`はnamed reference / site extraction、
  model load / reset、simulation primitiveだけを公開する。fast_arm固有solver、name contract、endpoint wrapper、
  diagnosticはplugin packageから公開する。
- package root `xpotato_sim.runtime`は`RuntimeConfig`とcatalog resolverだけをlazy resolveし、package importだけで
  concrete catalogをloadしない。interpreter-based `RuntimePipeline`はexportしない。
- package root `selfrionette/`は`__init__.py`だけを持つ。空の`xpotato_sim.robots` namespaceと、
  `robot_profile.py`、`viewer_robot_declaration.py`、`loadcell_serial.py`をrootへ再導入しない。
- production discoveryを起動できるgeneric moduleはcatalogだけとする。test fixtureはproduction namespaceへ
  置かず、明示的なtest discovery rootを使用する。
- `assets/mujoco/<robot_id>/...`と`configs/<robot_id>/...`はstable logical identifier namespaceであり、
  physical repository path規則ではない。physical ownerは許可されたrepository fileまたはtyped Python package
  resourceとし、package resourceではowning packageとpackage-relative pathをtyped declarationが所有する。
  generic resolverはlogical identifierやrobot IDからpackage名、package path、filesystem pathを推測しない。
  repository rootまたはresolved package resource boundaryでphysical ownershipをfail-closedに検証する。
  viewer public URLは`assets/` logical identifierからdeterministicに生成するが、logical namespaceの維持は
  旧physical directoryへのduplicate維持を意味しない。shared resourceは暗黙許可しない。

禁止するdependency:

```text
plugins/input_sources -> plugins/mappings
plugins/input_sources -> motion
plugins/input_sources -> kinematics
plugins/input_sources -> mujoco_backend
plugins/input_sources -> transport

plugins/mappings      -> plugins/input_sources
plugins/mappings      -> motion
plugins/mappings      -> kinematics
plugins/mappings      -> mujoco_backend
plugins/mappings      -> transport

motion                -> plugins/input_sources
motion                -> plugins/mappings
motion                -> mujoco_backend
motion                -> transport
motion                -> runtime

kinematics            -> plugins/input_sources
kinematics            -> plugins/mappings
kinematics            -> mujoco_backend
kinematics            -> transport
kinematics            -> runtime

mujoco_backend        -> plugins/input_sources
mujoco_backend        -> plugins/mappings
mujoco_backend        -> motion
mujoco_backend        -> transport
mujoco_backend        -> runtime

transport             -> plugins/input_sources
transport             -> plugins/mappings
transport             -> motion
transport             -> kinematics
transport             -> mujoco_backend
transport             -> runtime
```

これらの境界を変更する場合は、この文書、import boundary test、PRのArchitecture Impactを
同じ変更で更新する。

`apps/mujoco-viewer/src`は`tests/architecture/test_layer_import_boundaries.py`で
検査する。`wasm-scene`だけは既存guardがMuJoCo WASMを許可し、受信qposの`mj_forward`と
scene描画に限定する。独立した`mj_step`によるphysics進行、入力からの独立IK/FK制御、backend
stateの第二SoT、Rapierは許可しない。他のviewer領域にこの例外を広げない。

## Input Source public compatibility retirement (#474)

C4ではpublic compatibility policyとしてimmediate removalを採用した。判断根拠は次の通りである。

- root distributionのversionは`0.0.0`であり、repositoryにrelease、tag、PyPI publish workflowがない。
- root READMEとcurrent operator docsは旧package APIをinstall / usage entryとして案内していない。
- C1–C3のcanonical migration contractは旧surfaceをC4までの一時compatibilityとして限定している。
- production/internal callerはC3でcanonical catalog、versioned mapping、`ControlMappedRuntimePipeline`へ移行済みである。

repository外consumerが存在しないとは断定しない。ただしstable external API、published compatibility
commitment、released package contractのevidenceがないため、deprecation windowを設ける根拠よりも
temporary migration surfaceを退役する根拠が強い。したがって旧package、registry、facade、interpreter、
legacy `RuntimePipeline`、loadcell optional mapping fallback、compatibility CLI wrapperをretained allowlistなしで
退役した。canonical source / mapping identityとobservable runtime behaviorは変更しない。

## Input Source runtime validation boundary

generic source contractの`HealthyInputSource`、`ManagedHealthyInputSource`、
`ViewerBridgeInputSource`とvalidated reader adapterは`runtime/experiment/input_source.py`が
所有する。adapterは`fast_arm`、serial transport、browser/viewer implementation、task/evaluation implementationを
importせず、`RawInputFrame`とtyped `InputSourceHealth`だけをruntime boundaryで検証する。production source pluginは
deterministic known-ID catalogからのみ解決し、source packageがrobot command、task/evaluation、viewer TypeScriptを
importすることを禁止する。mappingはsource package外に残す。

`ViewerBridgeRuntimeCapability`はviewer registrationとruntime ingress / endpoint continuityだけが使用する
mode-specific typed capabilityであり、generic `HealthyInputSource` readerの必須interfaceではない。source pluginはviewer
control schemaを維持し、frontend providerやkeyboard / gamepad mappingを所有しない。
Health-to-frame projectionはruntime-owned helperに集約する。source pluginはtyped health truthを提供し、runtimeは
generic fieldsだけをprojectionする。Selfrionette pluginはfactory creation、invalid configuration、read-before-startで
serial portをopenしない。direct factoryはport、baud、injected linesをI/O前にfail-closedで検証する。

Input Source packageとControl Mapping packageは互いのconcrete logical identityを所有またはimportしない。
source registrationはsource identity、produced schema、factory、source-local parameter、health / lifecycle、
execution adapterだけを保持する。Mapping selectionのconvenience defaultは
`runtime/control/input_source_mapping_policy.py`、CLI表示順は`cli/main.py`が所有する。compatibilityは
source produced schemaとMapping accepted schemaのversioned identityでgenericに検証する。

first-party bounded discoveryでは`plugins/<axis>/<logical_id>/plugin.py`を固定entry pointとし、package
basenameと`logical identity.name`の一致をstructural invariantとして検証する。logical identityは
manifest / readiness / freeze / provenanceのSoTであり、physical path自体をexperiment identityにはしない。

## legacy参照と移行境界

`legacy/`は参照専用であり、新しい実装から直接importまたはexecuteしない。
legacyの責務を移行する場合は、script全体をcopyせず、次のownerへ責務単位で移す。

| legacyの責務 | current owner | 境界 |
|---|---|---|
| MuJoCo XML / STL asset | typed robot package resource（logical namespaceは`assets/mujoco/fast_arm/`） | canonical assetを参照し、legacy codeを実行しない |
| device input読取 | `plugins/input_sources/` | `RawInputFrame`を返し、IKまたはMuJoCo stateを書き換えない |
| inputの意味付けとscale | `plugins/mappings/` | mapping semanticsのcanonical owner。`input_interpreters/`とlegacy `RuntimePipeline`は退役済み |
| target更新とsafety limit | `motion/` | `MotionCommand`を生成する |
| physical output safety binding / permission / lifecycle | `runtime/output/{safety_gate,permission,lifecycle,trace}.py` | P5 decisionをexact typed requestへ結合し、permission、recording、allow-only lifecycleを所有する。transport importとhardware送信を持たない |
| generic physical output transport | `runtime/output/transport_adapter.py` | explicit configでP5 allow、permission、lifecycleとgeneric transportを結ぶ。encoder capabilityが要求する場合はv2 external authorization grantを必須にする。robot-specific mappingを持たない |
| FastArm physical output composition | `runtime/output/fast_arm_adapter.py` | accepted #509 evidence、Robot Profile、mapping identity、P5、physical-actuation / transmission permissions、operator gate、generic transport、router observation correlationを結ぶ。physical stopを証明しない |
| FastArm physical-output mapping | `plugins/robots/fast_arm/adapter/physical_output.py` | explicit profile-to-wire joint map、rad-to-degree conversion、FastArm OSC semantics、router observation parserを所有し、runtime / transportをimportしない |
| FK / IK / joint limit | `kinematics/`またはrobot-specific plugin | kinematics責務に限定する |
| MJCF model state | `mujoco_backend/` | MuJoCoをphysical stateのsource of truthとする |
| logging / replay / WebSocket / generic OSC / UDP delivery | `transport/` | runtimeやrobot mappingをimportせず、generic message encodingとendpoint deliveryを所有する |
| application composition | `runtime/` | 唯一のcomposition rootとする |
| visual rendering | `apps/mujoco-viewer/` | Three.js rendering-onlyとする |

## public export境界

package-root exportとmodule-level exportは別のpublic surfaceである。

- package-root `__all__`へ公開するのはcontract、concrete implementation、または
  canonical文書で維持理由を説明できるcompatibility helperに限定する。
- `xpotato_sim.runtime`は各public nameをowner moduleとattribute nameの明示mappingで解決する。
  module scan、transitive import、module orderingへ解決先を依存させない。generic contractの参照では
  concrete catalogをloadせず、catalog-backed resolverを参照した時点だけcanonical catalog ownerをloadする。
- 明示mappingのkey setは`__all__`と一致させ、全entryのowner object identityをarchitecture testで固定する。
- `NoOp*`、`Zero*`、`Static*`などのtest doubleをproduction packageへ置かず、package-rootのstable APIにしない。
- test doubleは`tests/support/`だけが所有する。production sourceは`tests`をimportしない。
- `src/xpotato_sim/**/stubs.py`、`build_noop_pipeline()`、stub-default builderを再導入しない。
- replayのordered state retentionやinput-loopのlocal latest-state retentionなど、実runtime semanticsを持つ
  private adapterはtest doubleと区別し、production ownerのmodule内へ閉じる。

このpublic surfaceを変更する場合は、`tests/architecture/test_public_export_policy.py`と
該当packageの`__all__` contract testを同じ変更で更新する。

## viewer provider / source / mapping direction

viewer frontend providerはbrowser-only boundaryであり、physics、robot、task、FK、IKをimportしない。
backend viewer sourceはtransport messageとviewer sample schemaを扱うが、keyboard axis assignment、
gamepad normalization semantics、gain、deadzone、control-frame command conversion、desired endpoint
progressionを持たない。Control Mapping Pluginはfrontend DOM、Gamepad API、WebSocket lifecycle、
source healthをimportしない。runtimeだけがsourceとmappingをtyped registrationからcomposeし、mapping
resultをendpoint runtimeへ渡す。

provider ID / sample schema、source produced schema / mapping accepted schema、provider lifecycle stateは
各boundaryで検証する。unknown、duplicate、version mismatch、missing capability、malformed provider
payloadはimplicit fallbackなしでfail-closedとする。これによりlegacy message compatibilityはsourceの
canonicalizationに限定され、mapping algorithmの二重実装にならない。

keyboard providerはcapture対象keyのallowlistとpressed key lifecycleだけを保持し、disable / dispose時に
capture stateをresetする。gamepad providerはbrowserから取得したfiniteな`raw_axes`をcanonical sampleへ
保持し、既存のnormalized `axes`はwire / overlay互換projectionとして残す。gamepad/v1のpublicな
`zero_state`、`source_active`、heartbeatはlegacy projected axesとbuttonsを反映する既存observable
semanticsを維持し、connection、focus、visibility、stale、disconnectなどprovider / source-owned stateと
合わせて扱う。raw axisのmapping deadzone結果やcommand zeroはsource healthの代替にしない。axis assignment、
binding direction、speed / gain、deadzone、button supplement、requested control frame、command zero判定は
Control Mappingのtyped parametersとcanonical sampleが所有する。

viewer sourceはtyped ingress failureを受けてlatest canonical sampleを`source_active=false`、healthを
`invalid`へ遷移させる。runtimeはinvalid / stale / inactive / disconnectedでhold-currentを適用し、valid
sampleが来るまで古いactive intentを再開しない。source registrationはconcrete mapping objectを保持せず、
default Mapping selectionやMapping parameterも保持しない。diagnostic convenience pairingは
`runtime/control/input_source_mapping_policy.py`が所有し、explicit mapping selectionを上書きしない。
produced sample schemaとaccepted sample schema、mapping `ParameterContract`、optionalな
mapping-specific parameter normalizationはreaderのlifecycle開始・frame read・mapping execution前に検証する。
検証済みparametersはdeterministicなfrozen mappingとしてplanへ渡し、invalid parameterではmanaged sourceを
startせず、frameもreadしない。

C4以降、`src/`、`scripts/`、`tests/`から旧Input Source / interpreter packageをimportしてはならない。
production runtimeはcatalogとversioned Control Mapping Pluginを直接composeする。canonical
`run_selfrionette_serial_dry_run_smoke()`はoffline fixture validation用に残すが、
`loadcell_endpoint_mapping/v1`の明示指定を必須とし、optional fallbackやold-path re-exportを持たない。

## Current source / Mapping readiness

frontend providerはbrowser raw acquisitionとlifecycleを所有し、normalized gamepad `axes`はwire / overlay compatibility projectionとして残す。canonical `raw_axes`、source lifecycle / activity、backend health、Control Mappingのcommand zeroを同じ責務に戻さない。gamepadのlegacy two-stage transfer functionはControl Mapping Plugin内で一元化し、frontendまたはsourceへmapping semanticsを戻さない。

sourceとmappingは別identityでruntimeがcomposeする。mapping parametersはexecution / source start前に
generic contractとmapping-specific semantic validatorで検証し、explicit Mapping selection parameters、
Mapping plugin defaultsの順に解決する。Input Sourceからのparameter projectionは持たない。
public facade、helper fallback、old packageをcurrent pathへ再導入しない。

generic experiment compositionはEnvironment / Task / Evaluationを含む6軸readiness contractを所有する。
Environment / Task / Evaluationの各production catalogはowning axisのbounded discoveryだけから構築し、
concrete package間のcross-axis import、fast_arm concrete import、generic runtimeのconcrete ID分岐を持たない。
`runtime/composition/production_experiment.py`は6軸catalogのprojection ownerだがexperiment runnerではない。
replay / viewer / smokeはRobot、Input Source、Mapping、command routeのdiagnostic / operational runtimeであり、
planned experiment control plane #486まで暗黙のfull compositionへ変更しない。

`runtime/experiment/world_tool_runner.py`は#406のproduction execution ownerである。production readinessに
含まれるresolved compositionからEnvironment、Input Source、Mapping、Task、ordered Evaluation、command routeを
受け取り、Robot Bundleのtyped providerをassembly時に一度だけ取得する。runnerから
`plugins.robots.fast_arm`のconcrete module、test registry、viewer、logging schemaをimportしない。
execution loopはroute-bound `ControlMappedRuntimePipeline`とtyped command providerを使用し、Bundleへの反復lookup、
独自IK / FK / Jacobian / frame transform、`MotionCommand`のbackend直渡しを行わない。

#407のmotion-log recorderは`runtime/experiment/`でrunnerのimmutable traceを既存
`schemas.experiment_log`へprojectionするfilesystem consumerである。schema moduleはruntimeまたはfilesystemへ
依存せず、runnerはrecordを生成するためにMapping、motion policy、MuJoCo step、Task判定を再実行しない。

#513のoutput compositionはP3 observation producer由来の実評価configurationをP4へ渡し、request / P3 / P4の値bindingを検証する。`runtime/safety/evaluated_candidate.py`はchecker間で共有する値projectionだけを持つ。P2へcandidate評価責務を移さず、collision / dynamic formulaとhardware / transport責務は既存ownerに保持する。

## Specialized signal/contact runner

`runtime/runners/signal_contact.py`はSource/Mapping/catalogと既存contact owner、output previewを結ぶ
専門orchestrationであり、generic `runtime/experiment`のimport禁止を緩めない。
同じrunner群のartifact readerは既存Task/Evaluationとwire変換を再利用する。contact Robot viewは
model/dataを複製せず、backendが名前で固定したjoint groupへ委譲する。

## 有限physical runtime接続

`runtime/runners/fast_arm_input_runtime.py`は既存input planとoutput session/driverを合成する。
Robot-specific codec、P5判断、Source取得は再実装しない。testsの合成acceptance/senderをproductionへimportしない。

## 起動設定のcomposition

`runtime/composition/launch_profile.py`はJSON検証と既存Robot/Input/Mapping/command resolverへの
結線を所有する。CLIは設定の選択・表示だけを担当する。profile解決はprocess、network、
serial、model stepを開始せず、実機permissionを生成しない。

`runtime/runners/application.py`はLaunchProfileから既存publisherとWeb dev serverを起動する。
`application_process.py`はそのworkerのprocess/job所有権と有限cleanupだけを所有する。
`runtime/runners/workbench.py`は既存TrialRunner・transport・application_processを結線する待機制御入口である。
`workbench_web.py`のHTTP配信と`workbench_metrics.py`のprocess計測は同入口の補助で、simulation/Taskのownerではない。
CLIやPowerShellはこのownerへ委譲し、別control loop、physics、hardware permissionを持たない。

## 共同arm診断の具体composition

`runtime/composition/fast_arm_coordinated.py` だけがRobot-ownedな
`plugins.robots.fast_arm.adapter.coordinated.FastArmAssemblyMotionProvider` を直接構築する。
この限定依存はarchitecture testでfile/symbolを固定し、他のconcrete Robot importへ一般化しない。
`runtime/execution/coordinated.py` はtyped providerとschemasだけを使い、Robot IK・geometry・送信を所有しない。
`runtime/output/coordinated.py` は既存physical sessionを監督し、codecやevidence判定を複製しない。

## 同列モデル選択と共通描画資源（#574 / #580）

`RobotPluginRegistration.models`がRobot所有のモデル集合を宣言する。`RobotCatalog.resolve_model`は
既存Robot選択からそのモデルを解決し、concrete Robot名や腕数でdispatchしない。
`runtime/composition/robot_model.py`は型と整合検査を持ち、Robot側factoryが同じ生成artifactから
providerとViewer resourceを構築する。原型・左単腕・右単腕・双腕は同じ登録型を使う。

共通`LaunchProfile/v2`はモデルidentityと入力bindingだけを持ち、配置quaternionやXMLを複製しない。
`model_websocket_publisher.py`と`coordinated_input.py`は登録済みprovider・Mappingを結び、
Robot coreやconcrete builderをimportしない。旧`coordinated_viewer_profile.py`への具体import例外と
双腕専用publisherは退役する。保存assembly診断の`fast_arm_coordinated.py`は共通入力runtimeへのthin入口に限定する。

`application.py`はモデル選択時の生成資源を同じworker所有権で配信する。1手先も2手先も同じ経路で、
Viewerはmodel digest、関節名・順序・次元を検査して受信stateを描画する。
旧LaunchProfile/v1の単腕keyboard/replay等の実行意味は互換境界として維持し、旧形式を新モデルへ暗黙変更しない。

## 固定物体のgeometry観測（#585）

`runtime/scene/observation.py`はMuJoCo非依存のpure DTO。Taskがこの型を受けることは許可する。
`runtime/scene/measurement.py`だけがbackend/native contactからそのDTOを生成する。
`mujoco_backend/contact_geometry.py`はnative point/frame/distanceを読む共通primitiveで、旧R7-H force evidenceと
新geometry診断から共有する。raw force、Task、Viewerの処理は共有primitiveへ逆流させない。
Task/Evaluationの境界検査は曖昧な`geom`部分文字列ではなくAST上のnative geom属性/API参照を拒否し、
geometryというDTO名だけで誤検出しない。Robot固有名のgeneric layer禁止は維持する。
