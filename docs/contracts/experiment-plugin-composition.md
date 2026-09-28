---
status: canonical
owner: runtime
last_verified: 2026-08-28
canonical_for:
  - experiment plugin composition contract
  - Robot Bundle capability provider contract
  - environment, mapping, task, and evaluation plugin readiness
  - canonical evidence status and evaluator policy
related:
  - docs/architecture/runtime-composition.md
  - docs/contracts/robot-profile-runtime-viewer-profile.md
  - docs/contracts/experiment-motion-log-v1.md
  - docs/contracts/evaluation-manifest-readiness.md
---

# experiment plugin composition契約

## 目的とownership

実験runtimeは、Robot、Environment / Scene、Control / Mapping、Task、Evaluation、Input Sourceを
独立したversioned pluginとして明示選択する。multi-layer compositionとstartup readinessの
ownerは引き続き`runtime/`であり、MuJoCoはphysical stateのsource of truth、viewerは
rendering-onlyである。viewerはtask terminal判定、contact判定、metric導出を再実装しない。

| 軸 | owner | 所有するもの | 所有しないもの |
|---|---|---|---|
| Robot Bundle | robot-specific runtime adapter | `RobotProfile`、`RobotRuntimePlugin`、typed capability provider | task lifecycle、metric、viewer描画 |
| Environment / Scene | environment plugin | semantic role、scene composition/reset、typed parameter contract、presentation reference | robot joint名、solver class、task outcome |
| Control / Mapping | mapping plugin | input intentからcommand intentへのpure mapping、gain/deadzone等のparameter contract | physics state、task判定 |
| Task | task plugin | required capability/role、parameter contract、lifecycle、canonical task event、terminal classification | robot固有site/geom/joint、metric集計 |
| Evaluation | evaluation plugin | required canonical evidence、evidence policy、deterministic metric、provenance | backend固有state抽出、viewer表示 |
| Input Source | input source plugin | versioned source identity、mode、reader factory、health、initial metadata、produced sample schema、optional lifecycle | mapping algorithm、robot capability、task outcome、viewer frontend provider |

## versioned identityとregistry

plugin、capability、evidenceは`VersionedIdentity(name, version)`で識別し、canonical表記を
`name/vN`とする。manifestは各軸を`PluginSelection(plugin_id, contract_version)`で固定する。

`VersionedPluginRegistry`はknown IDのimmutable mappingである。一つのregistry内で同じIDを
重複登録できない。resolve時はunknown IDとcontract version mismatchをstartup failureとして
拒否する。configurationの文字列をmodule/class名または任意dynamic importへ渡さない。
registryの登録順とID一覧はdeterministicである。

`ExperimentPluginManifest`は次を明示する。

- Robot Bundle selection
- Environment Plugin selection
- Control Mapping Plugin selection
- Task Plugin selection
- Input Source Plugin selection
- Evaluation Plugin selectionのordered tuple
- `PluginParameterOwner(plugin axis, plugin ID, contract version)`に紐づくtyped parameter values

parameter ownerは6軸のselection identity全体を所有者とし、raw plugin IDだけでは識別しない。
異なる軸で同じIDを選べる一方、ownerのaxis、ID、versionのいずれかがselectionと一致しない場合、
同じownerへのparameter重複、未選択pluginへのparameterはstartup failureとして拒否する。
同じevaluatorの重複選択も拒否する。
R7-G-P1 / #405とR7-H-P1 / #411は、software revision、condition、canonical serializationを
含む上位manifestを追加できるが、この6軸selectionを別の暗黙規則へ置き換えない。

## self-contained packageとbounded discovery

plugin固有implementationは`plugins/<axis>/<plugin_id>/`の自己完結packageが所有し、generic
contract、registry、composition primitiveだけをpackage外へ置く。manifest、readiness、freeze、
experiment provenanceではversioned logical identityを正本とし、physical path自体をexperiment
identityへ含めない。一方、first-party bounded discoveryではdirect-child package basenameを
`logical identity.name`と一致させるstructural invariantを採用する。したがってfirst-party package
pathは完全に任意ではなく、mismatchはdiscovery時にfail-closedとなる。

#480以後、axis固有infrastructureもconcrete packageと同じaxis packageへ置く。root
`plugins/`に残すのは複数axisで共有する`bounded_discovery.py`だけである。

```text
plugins/
├── bounded_discovery.py
├── robots/{catalog.py,discovery.py,registration.py}
├── input_sources/{catalog.py,discovery.py,registration.py}
├── mappings/{catalog.py,discovery.py}
├── environments/{catalog.py,discovery.py}
├── tasks/{catalog.py,discovery.py}
└── evaluations/{catalog.py,discovery.py}
```

Discoveryは固定entryからcandidate pluginを発見する。Registrationはplugin本体以外の
axis-specific onboarding declarationを束ねる。Catalogはvalidated discovery / registrationを
application-facing resolverへ投影する。Robot registrationはBundle、viewer declaration、resource、
onboarding contractを、Input Source registrationはCLI alias、request builder、execution adapterを
束ねる。Control Mappingは`ControlMappingPlugin`自身が必要情報を保持するためregistrationを持たず、
file symmetryだけを目的とする`mappings/registration.py`を作らない。

6軸のcurrent production stateは次のとおりである。

| 軸 | current concrete owner / entry point | production catalog / discovery |
|---|---|---|
| Robot | `plugins/robots/fast_arm/plugin.py::ROBOT_PLUGIN` | `robots/{catalog.py,discovery.py,registration.py}` |
| Input Source | 6 packageの`plugin.py::INPUT_SOURCE_PLUGIN` | `input_sources/{catalog.py,discovery.py,registration.py}` |
| Control Mapping | 4 packageの`plugin.py::CONTROL_MAPPING_PLUGIN` | `mappings/{catalog.py,discovery.py}`。追加registration layerなし |
| Environment / Scene | `environments/*/plugin.py::ENVIRONMENT_PLUGIN` | `environments/{catalog.py,discovery.py}` |
| Task | `endpoint_reach_task/plugin.py::TASK_PLUGIN`、`contact_press_hold_task/plugin.py::TASK_PLUGIN` | `tasks/{catalog.py,discovery.py}` |
| Evaluation | 5 packageの`plugin.py::EVALUATION_PLUGIN` | `evaluations/{catalog.py,discovery.py}` |

discoverable first-party axisは、固定namespace直下のdirect child packageだけを対象にする。`_support`
等のprivate/shared packageを除外し、candidateをsortして`<package>.plugin`だけをimportする。
configurationやuser inputからmodule pathを生成せず、Python packaging entry point、remote plugin、
hot reload、import副作用によるself-registrationを使用しない。

各axis layerはfixed exportの型とpackage名 / logical identity一致を検証する。missing export、wrong
type、import failure、duplicate logical identity、package / declaration mismatchはwarningでskipせず
startup前にfail-closedとする。共通helperはdirect-child enumeration、private除外、sort、fixed
module import、import failure normalizationだけを所有し、Input Source registrationとControl Mapping
contractの検証はtyped axis discoveryへ残す。

Control Mappingの`plugins/mappings/_continuous_endpoint_velocity.py`はaxis-local shared algorithm primitive、
`plugins/mappings/_command_routes.py`はaxis-local shared declaration / route factoryである。どちらもprivate shared
ownerであり、plugin IDやfixed entry pointを持たずdiscoverしない。Input Sourceの旧`_common.py`と
`_loadcell/`はcurrent ownerではなく、Selfrionette固有処理、analog fixture、noop、viewer healthは
各owning packageが所有する。
真に複数sourceへ共通なimplementationが生じるまで`_support/`は作らない。
Robot resourceは従来どおりplugin declarationが所有する。Input Sourceのreader / parser /
trajectory、Control Mappingのalgorithm / parameterはowning packageが所有し、generic resolverが
logical IDからresource pathを推測しない。

Control Mappingの旧flat module importとpackage-root lazy compatibility facadeは、stable external
commitmentとrepo consumerがないことを確認して#478で退役した。canonical public importは各concrete
packageの明示的な`__all__`とcatalog resolverである。production catalogはdirect child packageの
`plugin.py::CONTROL_MAPPING_PLUGIN`から構築する。

新しいfirst-party plugin onboardingは、owning packageと明示所有resource / config、plugin-local
testsの追加だけでproduction discoveryへ接続する。central catalog、generic runtime、viewer、
unrelated pluginへ具体ID/importを追加しない。test-only packageは明示したtest namespaceからだけ
discoverし、production namespace / catalog / CLIへ混入させない。packageを除去した後のselectionは
unknown logical identityとしてfailする。

### Current namespace inventory

| 分類 | canonical owner / decision |
|---|---|
| generic contract | `bounded_discovery.py`、axis discovery、`input_sources/registration.py`、`runtime/experiment/`、schemas |
| concrete Input Source owner | `analog_fixture/`、`noop/`、`programmed_target/`、`replay/`、`selfrionette/`、`viewer/` |
| concrete Mapping owner | `analog_fixture_mapping/`、`loadcell_endpoint_mapping/`、`replay_mapping/`、`viewer_keyboard_gamepad_mapping/` |
| concrete Environment owner | `free_space_environment/`、`contact_cube_environment/`、`object_scene_environment/` |
| concrete Task owner | `endpoint_reach_task/`、`contact_press_hold_task/`、`contact_observation_task/` |
| concrete Evaluation owner | `success_within_timeout/`、`off_axis_drift/`、`completion_time/`、`final_endpoint_error/` |
| axis-local shared implementation | Mappingのalgorithm primitive `_continuous_endpoint_velocity.py`とdeclaration / route factory `_command_routes.py`。Input Sourceはshared owner不要 |
| canonical public surface | concrete packageの`__all__`、catalog resolver、fixed `plugin.py` export |
| retired compatibility / migration | root Input Source registration facade、Mapping root / flat facade、runtime移行alias、旧loadcell identity / package |
| test fixture / test-only namespace | `tests/plugins/input_sources/fixtures/`。production discovery対象外 |
| CLI / composition policy | CLI表示順は`cli/main.py`、convenience default pairingは`runtime/control/input_source_mapping_policy.py` |
| runtime composition | `runtime/control/input_source_selection.py`と`runtime/experiment/composition.py` |
| schema boundary | sourceのproduced schemaとMappingのaccepted schemaをversioned identityで照合 |

current ownership invariantは次のとおりである。

| boundary | current decision |
|---|---|
| identity | `selfrionette/v1`をdevice identityとし、serial / injected lines / recorded dataをbackendまたはfixtureとして分離 |
| ownership | concrete behaviorは各owning packageへ置き、無制限なshared dumping groundを作らない |
| lifecycle | `HealthyInputSource`、`ManagedHealthyInputSource`、`ViewerBridgeInputSource`でtyped化する |
| cross-axis | source registrationはMapping ID、default、parameter projectionを持たず、runtime policyがdiagnostic convenience pairingを所有 |
| ordering | catalogはlogical identity順、CLI表示順はCLI projectionだけが所有 |
| public surface | concrete package APIとoperator helperだけをcanonical維持し、移行facade / aliasを再導入しない |
| identity rule | logical identityをprovenance SoTとしつつ、first-party basename一致をstructural invariant化 |
| normalization | device intrinsic calibration / sensor clampはSelfrionette、operational deadzone / gain / sign / axis / command policyはMapping |

`loadcell_endpoint_mapping/v1`はsource packageをimportせず、versionedな7-channel normalized sampleと
configurable weightsを受けるため名称を維持する。`replay_mapping/v1`はacquisition deviceではなく
`replay_raw_input_frame/v1`のmetadata-preserving mapping semanticsを表し、programmed target / noopは
schema adapterを通じて接続するため名称を維持する。

## Robot Bundleとcapability provider

`RobotBundle`は既存`RobotProfile`と`RobotRuntimePlugin`を置換せず、その上位で両者と
小さなproviderを束ねる。bundle construction時にprofile/plugin identity、contract、object
bindingを既存resolverと同じfail-closed ruleで検証する。generic experiment compositionではBundle
identityがProfile IDと異なる用途を許すため、Bundle construction自体は両者のlogical identity一致を
強制しない。first-party production runtimeだけがselection、Bundle、Profile、Runtime Pluginの
logical identity / versionを共有validatorで追加検証する。

current capability identityとtyped providerは次のとおりである。

| capability | typed provider | boundary |
|---|---|---|
| `reset_initial_state/v1` | `ResetInitialStateProvider` | named keyframe等のinitial-state referenceを解決する |
| `endpoint_pose/v1` | `EndpointPoseProvider` | backend stateからendpoint poseを観測する |
| `endpoint_command/v1` | `EndpointCommandProvider` | endpoint command用motion policyを構築する |
| `qpos_feasibility/v1` | `QposFeasibilityProvider` | whole-qpos candidateまたはtrajectory feasibility guardを構築する |
| `scene_role_binding/v1` | `SceneRoleBindingProvider` | `robot.tool_endpoint`等をbackend bindingへ解決する |
| `contact_evidence/v1` | `ContactEvidenceProvider` | optional contact evidence identityと観測を公開する拡張点 |

`CapabilityProviderBinding`はcapability identityごとのexpected Protocolとprovider identityを
runtimeで照合する。一つのbundleで同じcapabilityを複数providerが宣言した場合はambiguousとして
拒否する。capability identityからexpected Protocolへのcontract mappingはimmutableであり、
新しいcapabilityはtyped provider contractとの対応を明示登録する。未登録capabilityと未提供
capabilityへのlookupは例外であり、zero、empty、no-opを返さない。

R7-H #413の`runtime/contact/evidence.py`は、`ContactSceneInstance`の同じMuJoCo model/dataから
`mjData.contact`と公式`mj_contactForce`を読むbackend measurement ownerである。scene instanceの
`observe_contact_evidence()`はこのownerへのthin facadeであり、Robot Bundle、viewer、Task、
Evaluationへcontact force抽出を複製しない。`ContactEvidenceProvider`を選択するRobot Bundleは
optionalなcapability / evidence declarationを公開できるが、target filtering、frame変換、
sign convention、deterministic aggregationのsecond implementationを持たない。
extractorはsceneの`ContactTaskManifest`からcanonical bytesを再計算し、sceneが保持するdigestと一致する場合
だけ有効なmeasurementを返す。manifest不在、digest mismatch、または公開`ContactRecord` / `ContactEvidence`
constructorのframe・force・status・aggregate整合性違反は、推測やzero fallbackをせずinvalidとして扱う。
未提供または利用不能なcapability / evidenceはzeroまたは成功へfallbackせず、canonical evidenceの
unavailable / invalid semanticsへ残す。

共通処理は`NamedKeyframeInitialStateProvider`、`RuntimeEndpointPoseProvider`、
`RuntimeEndpointCommandProvider`、`RuntimeQposFeasibilityProvider`、
`ProfileEndpointSceneRoleProvider`のような小さなdelegating providerとして再利用する。
巨大なdefault robot継承階層は導入しない。

evaluation readinessでは`RESET_INITIAL_STATE_V1` providerが、同じprovider boundary上の
`InitialStateContractProvider.initial_state_contract()`を実装してcanonical initial-state contractを公開する。
このcontractはversioned identity、source、qpos、tip、tool orientation、frame、unit、quaternion orderを保持する。
fast_armは`home` keyframe由来のprofile-owned contractを再利用し、generic bundleも同じtyped provider boundaryを使う。

## semantic roleとenvironment

semantic roleはbackend固有名と分離したidentityである。current generic robot roleは
`robot.tool_endpoint`である。environmentは`environment.target_object`、
`environment.support_surface`等を後続pluginで宣言できる。

`EnvironmentPlugin`は次を持つ。

- typed `EnvironmentSceneProvider`によるruntime-owned compose/reset
- uniqueな`EnvironmentRole`（object kind、frame、unit）
- required robot capabilityとtyped `SemanticRoleRequirement`
- geometry、pose、mass、material、friction、contact parameter等を表すstrict `ParameterContract`
- produced canonical evidence identity
- compatible Robot Bundleのexact `VersionedIdentity` / backend kind
- optional viewer presentation reference

environment roleとrobot roleが同じsemantic roleを重複提供した場合は、暗黙優先順位を付けず
ambiguousとして拒否する。`SemanticRoleRequirement`はrole名に加えてobject kind、frame、unitを
要求し、`EnvironmentRole`またはrobot bindingとの一致をreadinessで検証する。任意の属性を許す場合は
省略せず明示的な`*` wildcardを指定する。missing roleと各属性の不一致はstartup failureである。

`free_space_environment/v1`はR7-Gのfree-space scene conditionである。Robot Bundleが所有するbase
sceneを使用し、task objectとcontact requirementを追加しないことを明示する。parameter、semantic
role、produced evidenceを持たず、MuJoCo backendとの互換性だけを宣言する。このidentityはobjectなしの
universal fallbackではなく、free-space条件を選択した場合だけ解決されるversioned production pluginである。

`contact_cube_environment/v1`はR7-Hのtyped `ContactSceneBuildRequest`を受け取り、
`ContactSceneComposer`へ委譲してRobot-owned base MJCFへcube body / freejoint / geom / materialを
追加する。scene providerがMuJoCo model/dataをloadし、同じinstanceのresetでmanifestのqpos、qvel、
`ctrl`（`data.ctrl`）、`act`（`data.act`）、object pose、simulation time、warm-startを再適用する。base model name衝突、identity / role /
capability mismatch、reset dimension mismatch、未知のMuJoCo setting、初期object contact / penetrationは
startup successへ変換しない。viewerはphysical objectを生成せず、contact evidenceやtask outcomeは後続の
typed provider / Task ownerへ残す。disabled contact sceneはobjectを構成せず、contact evidenceを暗黙に
生成しない。

## mappingとtask

`ControlMappingPlugin`はtyped `ControlMappingStrategy`とstrict `ParameterContract`を持つ。
evaluation comparisonへ参加するmappingは、versioned `comparison_family_identity`、
versioned `mapping_semantics_identity`、`control_frame`を明示する。family identityはframe variantを
束ねるsemantic contractであり、strategy objectのhashやobject identityではない。strategyが宣言する
mapping semantics identityとplugin fieldが一致しない場合はconstruction/readinessをfail-closedにする。
world/tool mapping、gain、deadzone、assistance等はこの軸のpluginまたはparameterとして固定する。
world/tool pairでcontrol-frame差を許可するparameterは`ParameterField(condition_specific=True)`を
明示する。static `control_frame`を宣言するpluginはrequested frameと一致させ、dynamic frameを持つ
`analog_fixture_mapping/v1`はtop-level `control_frame` parameterをupper manifestから明示projectionする。
同じframeをnested `mapping_config`にも重複指定した場合は二重SoTとして拒否する。
mappingはrequired Robot capabilityを宣言し、利用不能時に別mappingへfallbackしない。

### control semanticsとRobot command semantics

Mapping output/control semantics、runtime/controller conversion semantics、Robot/backend command
semanticsは別契約である。`CommandSemanticsRoute`は次の3 identityとtyped executable strategyを
一つのversioned experiment conditionとして保持する。

- route identity: runtime/controller conversionまたはnative passthroughの方式
- `control_semantics_identity`: operator inputをMappingが何として解釈したか
- `robot_command_semantics_identity`: route後にRobot/backendが直接受理するcommand
- executable strategy: selected routeとRobot command providerをbindし、runtimeが実際に実行する変換

Robot command semanticは少なくとも`endpoint_position_command/v1`、
`endpoint_velocity_command/v1`、`joint_position_command/v1`、
`joint_velocity_command/v1`を区別する。class名、module名、metadata keyから推論しない。
Mappingはconcrete Robot IDを、Robotはconcrete Mapping IDを参照しない。generic compositionはselected
routeの最終semanticに対応する`RobotCommandSemanticProviderBinding`をRobot Bundleから解決し、route
strategyが返すtyped execution bindingのroute / control / Robot semantic identityが一致することを
検証する。Robot Bundleのsupported semantic集合はprovider bindingから導出し、identityだけを宣言できない。
実装済みsemanticではsemantic identity、providerの`command_type`、実際に渡すtyped commandを一致させる。
`joint_position_command/v1`は`JointPositionCommand`だけを、
`endpoint_velocity_command/v1`は`EndpointVelocityCommand`だけを受理する。
`MotionCommand`はruntime内部のmotion / safety envelopeであり、Robot command typeとしてbindしない。

productionの4 Mapping分類は次のとおりである。

| Mapping | accepted input schema | Mapping/control semantics | runtime conversion route | final Robot command semantic |
|---|---|---|---|---|
| `analog_fixture_mapping/v1` | `analog_fixture_sample/v1` | `analog_fixture_endpoint_velocity/v1` | `local_endpoint_velocity_to_joint_position/v1` | `joint_position_command/v1` |
| `loadcell_endpoint_mapping/v1` | `loadcell_normalized_input_intent/v1` | `loadcell_endpoint_delta/v1` | `endpoint_delta_to_joint_position/v1` | `joint_position_command/v1` |
| `replay_mapping/v1` | `replay_raw_input_frame/v1` | `replay_metadata_command/v1` | `replay_command_to_joint_position/v1` | `joint_position_command/v1` |
| `viewer_keyboard_gamepad_mapping/v1` | `viewer_control_sample/v1` | `viewer_keyboard_gamepad_semantics/v1` | `local_endpoint_velocity_to_joint_position/v1` | `joint_position_command/v1` |

continuous endpoint velocityを出力するMappingでも、現行routeはvelocityを`dt`で積分し、
endpoint delta / desired endpoint position、Jacobian allocation、qpos feasibilityを経て
`MotionCommand.joint`を生成し、safety後に`JointPositionCommand`へprojectionする。この経路をnative
`endpoint_velocity_command/v1` supportとは呼ばない。`endpoint_command/v1`もtarget / local endpoint
motion generatorを構築する上位capabilityであり、`endpoint_position_command/v1`または
`endpoint_velocity_command/v1`と同一ではない。

test-only namespaceではnative velocity passthrough strategyをvelocity-capable dummy Robot providerへbindし、
generic runtime planを実行する。typed `EndpointVelocityCommand`がprovider/backendへ到達し、joint-position
MotionGenerator、`dt`積分、endpoint delta、Jacobian allocationを通らないことを検証する。このdummy
provider / Robotはproduction catalogへ登録しない。composition compatibilityだけではexecution
swappabilityの証拠としない。

実装済みsemanticのcommand typeは`RobotCommandSemanticContract`のgeneric mappingをSoTとする。
route strategy、resolved execution binding、Robot providerの3者は同じsemantic contractのexact typeへ
一致しなければならず、identityだけ一致するwrong typeはsource lifecycle / backend構築前にrejectする。
production builderは`ResolvedCommandExecution`を外部入力として採用せず、selected Mappingのcanonical
route strategyとcurrent Robot Bundleのcanonical providerからbindingを内部生成する。providerの
`ProviderAssemblyBinding`はBundle logical identityおよびcanonical Profile / Runtime Plugin ownerと
同一でなければBundle construction時にrejectする。manifest / freezeにはversioned semantic identityを
保存し、Python strategy / binding / provider object identityは保存しない。
production replay compositionはconfig Robot selection、Bundle identity、Profile ID / contract version、
Runtime Plugin ID / canonical Profile objectをexactに照合する。同じBundleのRuntime Pluginがbackendを
構築してmodelを検証し、Bundle providerがqpos feasibility guardを構築する。raw Bundle identityを
ownership proofにせず、aliased Bundle、external simulator、別Robot / 別logical version backend、
foreign modelはprovider execute、source start、backend build、simulator stepより前にrejectする。
arbitrary simulator / guard injectionはtest-only helperの境界とする。
`ControlMappedRuntimePipeline`はroute / bindingを必須保持し、CLIから到達する全production runnerは
pipelineのtyped execution APIへ収束する。`MotionCommand -> simulator.apply_command()`はproduction
runtime入口として使用しない。

`TaskPlugin`は次を宣言する。

- required Robot capability
- typed `SemanticRoleRequirement`（role、object kind、frame、unit）
- strict parameter contract
- typed lifecycle strategy
- upper contextへbindしたimmutable `TaskExecutionBinding`
- observationごとの`TaskTransition`（次state、terminal classification、Task-owned evidence）
- versioned canonical task event identity
- produced evidence identity
- `running` / `success` / `failure` / `technical_invalid`のterminal classification boundary
- compatible Robot Bundle / Environmentのexact `VersionedIdentity`とbackend identity

canonical task event identityは`produced_evidence`にも含める。Task production codeはfast_armの
joint名、geom名、site名、solver classを参照せず、capability、semantic role、canonical evidenceを
入力とする。

`TaskLifecycleStrategy`はpreclassified terminal evidenceを入力として読み戻さない。upper ownerから受けた
contextを`bind_context()`で固定し、runnerはtyped observationだけを`TaskExecutionBinding.advance()`へ渡す。
classificationとcanonical task eventはTask transitionの出力であり、runnerが直接作成しない。

`endpoint_reach_task/v1`は`endpoint_pose/v1`、`reset_initial_state/v1`、typed
`robot.tool_endpoint` roleを要求し、`endpoint_reach_terminal_classification/v1`と
`endpoint_reach_measured_trajectory/v1`を生成するTaskとして宣言する。Task Pluginはtask stateと
`running` / `success` / `failure` / `technical_invalid`のclosed classificationを所有するが、target、
tolerance、dwell、timeout、initial stateはupper evaluation manifestを正本とし、plugin parameterへ複製しない。
readinessはこれら5条件をimmutable `EndpointReachTaskContext`へprojectionする。Task bindingはworld-frame
measured endpoint sampleとelapsed timeを消費し、tolerance内の連続dwell完了だけをtimeout以下のsuccessとする。
最初のsampleはMuJoCo-owned elapsed 0 measurementとし、frozen initial positionとのexact一致を要求する。
upper manifestのinitial positionをmeasured sampleへ自動変換しない。
tolerance外へ戻ればdwellをresetし、success前のtimeout、held / rejected / staleはfailure、measurement
unavailable / invalid、reset、non-monotonic stream、technical statusは`technical_invalid`とする。
両evidence identityとstrict value shapeはcross-axis layer contract
`runtime/experiment/endpoint_reach_evidence.py`を唯一の定義元とし、Task / Evaluation packageへ複製しない。

`contact_press_hold_task/v1`は`reset_initial_state/v1`とtyped `robot.tool_endpoint` roleを要求し、
`contact_press_hold_terminal/v1`と`contact_press_hold_outcome/v1`をTask-owned evidenceとして生成する。
Task contextはR7-H contact manifestのtarget face、object-frame normal、world-frame approach direction、
penetration bandへ、dwell / timeout、任意のnormal-force band、alignment / drift gate、trial / repetition /
attempt identityを一度だけbindする。Task bindingは#413のraw `ContactEvidence`をmanifest / scene / object
identityへ照合し、`no_contact`、measured target contact、measurement unavailable、invalid contact、
solver invalidを区別する。Taskは#414のfiltered / clamped reaction-forceを入力にせず、MuJoCo contactを再計算しない。

phaseは`ready`、`approach`、`first_contact`、`press`、`hold`、`success`、`failure`、
`technical_invalid`を区別する。target penetration band、任意のnormal-force / alignment / location-drift
gate、連続hold dwell、timeoutを満たしたときだけsuccessとなる。contact lossはdwellをresetし、再接触を
counterへ記録する。held / rejected / stale / operator timeoutはoperator-caused failureとし、measurement
unavailable、solver invalid、reset failure、identity drift、非単調時刻はtechnical-invalidとする。

`contact_press_hold_outcome/v1`はfirst-contact time、peak normal force、penetration overshoot、
steady-state error、force variability、tangential force / slip proxy、final tip / object pose、
contact-location drift、normal alignment、loss / recontact countをcanonical artifactとして保持する。
さらにmanifest-bound penetration bandと、Task contextへbindしたdwell / timeout、normal-force band、
alignment / drift gate、pose-measurement requirementをartifactへ含めるため、outcome条件をcanonical bytesから
再構成できる。Taskはtechnical-invalidへ遷移する入力もraw observationとしてstate / replay順序へ保持し、
`sample_time_s`または`simulation_time_s`の後退・同値をstaleとしてsuccessへ進めない。`measured` top-levelの
record status、aggregate count、force、wrench不整合もtechnical-invalid境界である。
failed trialのcompletion timeや未観測forceはnullのままとし、zeroやsuccessへ変換しない。Task-owned
evidenceは`ContactOutcome` Evaluation Pluginがstrict decodeし、terminal / outcome identity、trial、
manifest digest、classification、phase、completion timeの一致を検証してmetric resultへ投影する。
runningはunavailable、technical-invalidはinvalidであり、どちらもsuccessへ変換しない。

`ContactTaskRunner`は事前取得済みraw observation logを同じmanifestへ再生するbounded software-only fixture
であり、MuJoCo step、Robot command、hardware outputを行わない。retryはtechnical-invalidだけを宣言済み
attempt上限内で許可し、元trialとretryを`trial_id` / `retry_of_trial_id`で保持する。同じvalid logからの
outcome summary regenerationはcanonical bytesを再生成するが、formal experiment evidenceではない。

Robot Bundle、Environment、Taskのcompatible identityが空集合の場合はgeneric/unconstrainedとして
扱う。指定された場合はraw nameではなく`VersionedIdentity`をexact matchし、同名でもcontract
versionが異なるselectionを拒否する。本foundationではversion rangeを導入しない。

## canonical evidenceとevaluation

`CanonicalEvidence`はfield identity、status、value、provenance、reasonを分離する。statusは
次のclosed vocabularyであり、requested、resolved、predicted、measuredを相互に読み替えない。

- `requested`: caller intent
- `resolved`: resolverが確定したcommand/target
- `predicted`: solver/model prediction。physical measurementではない
- `measured`: backend-owned observation
- `unavailable`: 観測不能。valueを持たずreasonを必須とする
- `invalid`: evidenceとして利用不能。valueを持たずreasonを必須とする

同じversioned evidence identityを一つの`CanonicalEvidenceSet`へ重複登録できない。
task固有fieldはowning production pluginが新しいversioned identityとして追加する。R7-G endpoint
reachではterminal classificationとmeasured trajectoryを固定済みであり、R7-H固有fieldは後続pluginが所有する。

readinessはrobot/environment/mapping/taskの各`produced_evidence`を単なる集合和へ潰さず、
`EvidenceProducerBinding(producer axis, producer plugin identity, evidence identity)`へ解決する。
同じevidence identityを複数pluginが宣言した場合はambiguous producerとして拒否する。
`ResolvedExperimentComposition`はこのbindingと互換用の`available_evidence` viewを公開し、#405は
freeze identityへproducerを記録できる。複数producerを許すaggregation contractは本Issueに含めない。

`EvaluationPlugin`はrequired evidence、strict parameter contract、missing / unavailable /
invalidごとの`EvidencePolicy`、typed deterministic metric strategy、provenance、unit、optional frameを宣言する。
required evidenceのidentityがtask/environment/mapping/robot extensionのproduced evidenceに
存在しない場合はstartup readinessで拒否する。実行時にevidenceがmissing/unavailable/invalidの
場合はdeclared policyに従い、default値を捏造しない。metricを返せないpolicyではvalueなしの
`unavailable`または`invalid` resultとreasonを返す。
`EvaluationPlugin.derive_metric()`はstrategyが返した`MetricResult`について、metric identityが
selected Evaluation Plugin identityと一致し、provenanceがplugin宣言値と一致することも検証する。
`unavailable` / `invalid`のvalueなし・reason必須invariantは`MetricResult` constructionで維持する。
`evaluation-artifact/v1`のstrict decoderも同じproduction declarationを再解決し、identityに対応するunit、frame、
provenanceとmetric status/value/reasonのinvariantを再検証する。JSON内の宣言値だけを信頼して別evaluatorへ
付け替えることはできない。

R7-G production evaluatorは次のordered tupleで使用する。いずれもcanonical evidenceだけからpureかつ
deterministicに導出し、trial stream aggregation、artifact export、condition summaryを所有しない。
validated `experiment-motion-log/v1`からのcanonical reconstruction、ordered metric delegation、
deterministic JSON artifactの所有者は`runtime/evaluation/artifact.py`である。

| identity | outcome | required evidence | unit / frame | failure semantics |
|---|---|---|---|---|
| `success_within_timeout/v1` | primary success outcome | terminal classification | boolean / frameなし | missing / unavailableはunavailable、invalidはinvalid |
| `off_axis_drift/v1` | initial-target axisからの最大直交距離 | measured trajectory | meter / MuJoCo world | missing / unavailableはunavailable、invalidはinvalid |
| `completion_time/v1` | descriptive completion time | terminal classification | second / frameなし | success以外はvalueなしunavailable |
| `final_endpoint_error/v1` | descriptive final tip-target distance | measured trajectory | meter / MuJoCo world | missing / unavailableはunavailable、invalidはinvalid |
| `contact_outcome/v1` | closed contact press/hold outcome artifact | Task terminal / outcome evidence | contact_task_outcome / MuJoCo world | running / missing / unavailableはunavailable、technical-invalid / invalidはinvalid |

### Input Source runtime reader readiness

Input Sourceのcompositionはplugin、selection、parameter、produced sample schema、mappingのaccepted schemaを
解決するが、factoryを呼び出してruntime instanceを生成しない。runtime側でfactoryを実行する場合は、
出力が`InputSource`と`InputSourceHealthProvider`を満たすこと、factory直後のcurrent healthが
`initial_health`と一致することを確認する。

runtime readerは`ValidatedInputSourceReader`で`read_frame()`と`current_health()`の戻り値を毎回検証する。
offline / replayにはmanaged lifecycleを要求せず、live / viewer_bridgeだけがmanaged adapterを通じて
`start()` / `close()`を委譲する。P3ではproduction backend source catalog、concrete source migration、
source-owned healthから既存payload metadataへのprojection、typed execution adapterを実装済みである。
P4ではviewer frontend provider、backend source、keyboard / gamepad mappingを分離し、mappingの
`ParameterContract`とoptional semantic validation / normalizationをsource lifecycle開始、frame read、
mapping executionより前に実行する。plugin-local test ownershipとonboarding / completion auditはP5として
#462で固定した。generic conformanceはsource固有parametersをcaseへ注入するだけでproduction / test-only
pluginへ再利用でき、test-only dummy sourceはproduction catalog / CLIを変更せずにsource schema compatibility、
reader creation、composition readinessを検証する。

P5のfocused validationはgeneric conformance、対象plugin-local tests、catalog / registry、source-mapping schema
compatibility、minimal runtime integration smoke、architecture guardsを含む。full Python suiteとviewer test /
typecheck / buildはmerge gateとして維持し、CI change-detection matrixは追加しない。

## composition readiness

`compose_experiment()`は実行開始前に次の順で検証する。

1. 6軸すべてをknown-ID registryからversion一致でresolveする。
2. parameter ownerのaxis / ID / versionがselectionと完全一致することと、required field、unknown
   field、runtime typeを検証する。Control Mappingはgeneric contractに加えてoptionalなsemantic
   validator / normalizerを実行し、結果をdeterministicなfrozen parameter mappingとして保持する。
3. environment / mapping / taskのrequired capabilityをunionし、Robot Bundleのtyped providerを解決する。Input Source factoryは呼び出さない。
4. robot/environment semantic roleをtyped descriptorとして統合し、missing、attribute mismatch、
   ambiguous bindingを拒否する。
5. Robot Bundle / Environment / Taskのexact versioned compatibilityとbackend compatibilityを検証する。
6. robot/environment/mapping/task/input sourceのproduced evidenceをproducer bindingへ解決し、ambiguous producerと
   evaluator requirement mismatchを拒否する。
7. resolved capability、typed role、resolved input sample schema、evidence producer binding、available evidenceをimmutable readiness
   resultとして返す。

このboundaryはrunner execution、scene spawn、physics step、task advance、metric artifact出力を行わない。
readiness後に不足へ気付く設計や、特定robot/task/evaluatorの暗黙選択を許可しない。

## Generic experiment contractとproduction runtimeの区別

`compose_experiment()`、`ExperimentPluginManifest`、`EvaluationManifest` / readiness / freezeは、
6軸selectionと互換性を実行前に固定するgeneric contractである。R7-G free-space向けには
Environment、Task、Evaluationを含むproduction concrete pluginと各axis catalog、および6軸catalogを
束ねる`runtime/composition/production_experiment.py`が存在する。catalog/readinessとphysics executionの責務は
分離し、production runnerは`runtime/experiment/world_tool_runner.py`が所有する。

current application-facing CLI、replay、viewer、offline smoke、WebSocket publisherは、
Robot、Input Source、Control Mapping、command semantics routeを接続するdiagnostic / operational
runtimeである。Environment、Task、Evaluationを選択しないことはcurrent contract違反ではない。
R7-G production experiment runnerだけが全6軸を明示選択し、既存diagnostic runtimeへ暗黙default pluginを
補わない。viewer構成UIはplanned experiment control plane #486を先取りしない。

#406 runnerはcanonical pairをproduction readinessへ渡し、`PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES` /
`resolve_production_experiment()`と同じproduction catalogだけから6軸とcommand semantics routeを解決する。
R7-G world/tool pairのcanonical software-only fixtureは
`runtime/evaluation/r7_g_free_space.py::build_r7_g_free_space_manifest_pair()`が所有する。このfixtureは
Input Source生成、MuJoCo load / step、Task lifecycle、metric導出を開始しない。

runnerはEnvironment scene conditionを明示的にcompose/resetし、Input Source factory、Mapping strategy、
selected route binding、Robot-owned local endpoint motion generator / qpos guard / command provider、MuJoCoを
接続する。reset直後はactual qposとmeasured tool orientationをfrozen manifestへ照合し、Taskにはelapsed `0.0`
measured endpointと各simulation step後のmeasured endpointを渡し、
TaskTransitionのclassification / evidenceを変更しない。Evaluation tupleはresolve済みidentityとして保持し、#408のartifact ownerがこの順序を保持したまま
metric aggregation / artifact exportを行う。

## fast_arm migration

production fast_armは独立package `fast_arm_core`でpure kinematics、model/name specification、joint-limit
parse、canonical initial state、model/config resourceを所有する。`xpotato_sim.plugins.robots.fast_arm.adapter`は
Profile、Runtime Plugin、Selfrionette kinematics/schema変換、MuJoCo validator / endpoint wrapper、feasibility guard、
initial-state projection、diagnostics、scene/viewer resource、Robot Bundle assemblyを所有し、`fast_arm/v1` Bundleとして
`xpotato_sim.plugins.robots.catalog`だけへ登録する。bundleは同packageの
`FAST_ARM_ROBOT_PROFILE`と`FAST_ARM_RUNTIME_PLUGIN`の同一objectを参照し、generic
`runtime.composition.robot_provider_adapters`を使って既存のmodel validation、endpoint IK/FK、target/local motion、
qpos feasibility、endpoint state accessorへ委譲する。initial stateは既存`home` keyframe referenceと
`fast_arm_initial_state/v1` contractを返す。

```text
fast_arm_core
        -> plugins/robots/fast_arm/adapter/
plugins/robots/fast_arm/plugin.py::ROBOT_PLUGIN
        -> plugins/robots/catalog.py
        -> application composition
```

`robots/fast_arm.py`、`robot_registry.py`、`runtime/fast_arm_*.py`、`runtime/default_robot_providers.py`、
旧registry moduleは#429で退役した。internal consumerはplugin owner、`runtime/composition/robot_provider_adapters.py`、
`plugins/robots/catalog.py`を直接使用する。deliberate package-root resolverはcanonical catalog ownerへ直接mappingし、
intermediate facadeを再導入しない。
`plugins/robots/fast_arm/*.py`の既存module pathはadapterからのthin re-exportに限定する。

`build_concrete_mujoco_pipeline()`は既存Robot Profile / Runtime Plugin resolverを維持したうえで、
Robot Bundle registryとの同一性を検証し、initial state、endpoint command、qpos feasibilityを
typed providerから取得する。algorithm、home qpos、joint order、model contract、profile metadata、
generic pipelineのprofile-free behaviorは変更しない。fast_arm bundleは`contact_evidence/v1`を
暗黙提供しない。

## 後続Issueへのpublic boundary

- #405は`ExperimentPluginManifest`、`PluginParameterOwner`、`VersionedPluginRegistry`、
  `ExperimentPluginRegistries`、`compose_experiment()`、`EvidenceProducerBinding`を使い、world/tool条件の
  6軸selection、axis-scoped parameter、version compatibility、evidence producerを
  `EvaluationManifest` / `EvaluationReadiness` / `FreezeRecord`へ固定できる。requested selectionと
  resolved plugin/capability/role/evidence identityを混同せず、package location変更ではlogical identityを
  変更しない。
- #411は`EnvironmentPlugin`、`EnvironmentRole`、`SemanticRoleRequirement`、`TaskPlugin`、
  `EvaluationPlugin`、`contact_evidence/v1` extension pointを使い、typed object/frame/unit requirementと
  cube/contact固有fieldをgeneric contractへ追加できる。contact task/object manifestのphysical condition、
  reset、target、MuJoCo setting、canonical serializationは`docs/contracts/contact-task-manifest.md`を
  正本とし、このgeneric composition contractへ複製しない。
- どちらもTask/Evaluationへfast_arm固有nameまたはsolver classを持ち込まず、viewerへ判定を追加しない。
- #406のproduction compositionは`xpotato_sim.plugins.robots.catalog`の
  `resolve_robot_bundle()` / `resolve_robot_profile()` / `resolve_robot_runtime_plugin()` /
  `resolve_robot_runtime()`、または既存のresolved experiment compositionを使用する。runtime consumerには
  `RobotBundle.provider()`でassembly時に取得した`EndpointCommandProvider`、
  `QposFeasibilityProvider`、`InitialStateContractProvider`等の必要なtyped providerだけを渡す。
- #406は`xpotato_sim.plugins.robots.fast_arm.*`や旧compatibility facadeを直接importして
  concrete objectを組み立てない。Bundleをruntime service locatorとしてstepごとに参照しない。
- #407は`ExperimentConditionExecutionResult`へ既存loop由来のimmutable step traceを保持し、別のruntime recorderが
  `WorldToolExperimentExecutionResult`とreadinessを既存`experiment-motion-log/v1` lifecycleへprojectionする。
  Evaluation Plugin、metric集計、condition summary、CSV / JSON evaluation artifactは実行しない。#408の
  artifact ownerはこのvalidated logとreadiness identityを照合し、Task evidenceを再構成してから各pluginへ委譲する。

## non-goalsと主張範囲

R7-G production runnerとvalidated logからのmeasured evaluation artifactは#406/#407/#408で成立する。このfoundationは
pilot、#409 full E2E / completion audit、R7-H cube scene、contact extraction、
virtual reaction force、viewer feature、hardware/serial/Arduino/OSC/robot outputを実装しない。
conformance testはcontractとreadinessの成立を示すが、実験結果、metric妥当性、接触物理、physical
safetyを証明しない。

## Input Source / Mapping readiness

Input Sourceが提供するsample identityとControl Mappingが受け付けるsample identityはcomposition
boundaryでexact compatibilityを検証する。source registrationはMapping object、default selection、
Mapping parameterを所有しない。diagnostic convenience pairingは
`runtime/control/input_source_mapping_policy.py`、explicit experiment selectionはmanifestが所有する。

source parser、provider acquisition、intrinsic normalizationはsource ownerに残し、axis assignment、
sign、gain、scale、deadzone、control frame、endpoint / command conversionはMapping ownerに置く。
`selfrionette/v1`のacquisition schema `loadcell_vector_sample/v1`はsource-owned typed adapterを通して
`loadcell_normalized_input_intent/v1`となり、そのeffective schemaだけを
`loadcell_endpoint_mapping/v1`へ渡す。adapter不在、adapter input / output mismatch、Mapping schema
mismatchはsource lifecycle開始前にfail-closedとする。

viewer providerはraw acquisitionとlifecycle、backend sourceはcanonical sample / health / timeout、
Control Mappingはaxis / sign / gain / deadzone / button supplement / control frame / command intentを
所有する。Mapping parameterの解決順位は
`explicit runtime mapping parameters > Mapping plugin defaults`とし、source instance、frame metadata、
source registrationから投影しない。

### 実行入口に依存しないlocal route

`local_endpoint_velocity_to_joint_position/v1`と`endpoint_delta_to_joint_position/v1`は
それぞれのtyped strategy/bindingがlocal motion generatorと変換APIを選ぶ。
velocityはdt積分、deltaはworld位置増分/sampleであり、入力metadataのlabelで選び直さない。
`ControlMappedRuntimePipeline.map_input`と`execute_intent`をstep loop / run_onceから共用する。
source取得、表示用annotation、pacing、caller-owned lifecycleまでを同一APIへ詰め込まない。

Mappingが宣言する`runtime_context_parameters`はroute側の供給集合と完全一致させる。
continuous selectionの`normalize_runtime_parameters`はこの項目だけを後段供給可能にする。
固定configと明示されたcontextは選択時に検証し、不正値を観測値で隠さない。
完全なpure mapping向け`normalize_parameters`と正式manifestの既存検証は維持する。
新しいpartial-context contractをformal experiment manifestへ暗黙適用しない。

構築・context供給・world/tool解決のoptional capabilityは、既存routeを壊さず必要な処理を
共有するためのものに限定する。新しいregistry、独立pipeline、汎用middlewareは設けない。
legacy replay/absolute-targetは従来の明示builder契約を維持する。

## 状態を持つMappingの実行session

optionalな`ControlMappingPlugin.session_strategy_factory`はruntime pipelineごとのstrategyを生成する。
省略した既存Mappingはstatelessな共有strategyを維持する。実行中の可変modeをcatalogや固定parameterへ保存しない。
利用例とresetの責任は[Gamepad平面操作契約](gamepad-plane-control.md)を参照する。


## 物体群の接触診断Plugin（#585）

Environmentの`object_scene_environment/v1`とTaskの`contact_observation_task/v1`をfixed discoveryへ追加する。
新しいplugin軸や外部registryは作らない。Environmentはobject定義/配置とnative scene composition、Taskは対象IDと
有限観測期間を所有する。generic runtimeへconcrete IDやimport fallbackを追加せず、typed Provider/Task contextを照合する。
sceneとmodel artifactは一体、観測はMuJoCo、Viewerは同一適用frameのread-only表示。
旧cube force/press-hold plugin群は別contractのまま維持する。
