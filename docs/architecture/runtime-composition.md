---
status: canonical
owner: architecture
last_verified: 2026-10-03
canonical_for:
  - runtime composition root
related:
  - docs/architecture/data-flow.md
  - docs/contracts/parallel-work-contracts.md
  - docs/contracts/robot-profile-runtime-viewer-profile.md
  - docs/contracts/experiment-plugin-composition.md
  - docs/contracts/evaluation-manifest-readiness.md
  - docs/reports/audits/canonical-content-history-separation-2026-07-16.md
---

# runtime composition

## Runtime owner map

`src/xpotato_sim/runtime/`はflat facadeではなく、次の責務ownerへ分ける。

| owner | canonical responsibility |
|---|---|
| `composition/` | config、Robot Profile / Plugin / Bundle、typed provider adapter、pipeline assembly、production 6-axis catalog projection |
| `execution/` | route-bound `ControlMappedRuntimePipeline`、input step loop、typed command / input-source execution adapters、timing / pacing |
| `control/` | input source state / selection、endpoint target、viewer ingress、motion metadata |
| `safety/` | stale command safety、qpos feasibility |
| `output/` | P5 safety evaluationとexact request binding、allow-only sendable type、permission decision、lossless recording / dry-run trace、safety-aware lifecycle / bounded stop、generic transport adapterとFastArm accepted-evidence composition。robot actuationは証明しない |
| `contact/` | versioned contact manifest、backend-owned MuJoCo scene composition / reset、MuJoCo measured contact evidence、Task contractの共有型 |
| `experiment/` | 6軸のexperiment plugin contract、registry、readiness composition、software-only trial lifecycle |
| `evaluation/` | FK / endpoint metric、progress、evaluation manifest / freeze readiness |
| `application/` | Workbenchの制御要求、通信・停止監督、専用worker、headless client、process memory診断 |
| `runners/` | operational dry-run / smoke / publisherとexperimentのthin entry point |

`runtime.__init__`は`RuntimeConfig`と既存catalog resolver 5件だけをlazy exportする。
interpreter-based `RuntimePipeline`はC4で退役し、`ControlMappedRuntimePipeline`だけをexecution ownerに残す。
standalone WebSocketの既定replayと`sweep_x`は、いずれも`run_once()`が取得、Mapping、安全判定、MuJoCo step、
同stepのsnapshotを所有する。`sweep_x`のtarget / 軌道metadataだけを`control/input_step_diagnostics.py`の
`project_sweep_x_replay_state()`でpublish直前に投影する。typed qpos rejectionではtargetとendpoint evaluationを
nullに保ち、Robot Profile metadataはpipelineのidentityを正とする。投影はphysics stateを再取得しない。
generic input-source loopのhealth / input表示の投影は既存ownerに残し、standalone replayへ追加しない。
contractやrunnerをpackage rootからre-exportしない。catalog access前のlazy-load、resolved Bundleのtyped
provider identity、plugin identityはこの移動で変更しない。

#591の`execution/model_execution.py`は名前付きmodelの入力・commit・同一snapshot・Task観測を所有する。
`experiment/trial_condition.py`、`trial_runner.py`、`trial_record.py`、`trial_fixture.py`は実効条件、
有限試行寿命、排他的ローカル記録、明示fixtureを所有する。`runners/finite_trial.py`と既存model publisherは
同じ実行を呼び、physics/Taskを二重実装しない。旧publisherのframe予算と新trialのcommit予算を分ける。
具体契約は[有限試行](../contracts/finite-trial-runtime.md)だけを正本とする。

`experiment/edited_condition.py`はWorkbenchの展開条件/v1、descriptor、strict編集入口、clone/diffを所有する。
既存catalogとlaunch-profile decoderへ接続し、任意pathやcodeを含むGUI専用設定engineを作らない。
descriptorで公開する型・値域は同じ入口の検証に使い、scene/Task/physicsの追加検証は既存ownerへ渡す。

`application/workbench_control.py`は制御要求、`workbench_service.py`は通信/期限監督、
`workbench_worker.py`は同じTrialRunnerへの接続、`workbench_client.py`はheadless制御を所有する。
`runners/workbench.py`はCLIとworker/web起動だけを所有し、内部helperを再exportしない。
`runners/workbench_web.py`は明示buildのlocal配信、`application/workbench_metrics.py`は現在processのRSS/private bytesだけを所有する。
`application/workbench_projection.py`はprivate socketのreader、STOP slot、入力/制御FIFO、最新表示slotとsenderを所有する。
これらのthreadはMuJoCo/Mapping/Taskを変更しない。終端記録processは不変stateの検証済みstagingだけを所有し、
期限内の結果採用と完了marker公開はTrialRunnerのExecution ownerが行い、期限超過時は保存processを回収する。
固定buildがGUI起動の既定で、source devは明示optionだけとする。
phase、Task判定、記録形式、physicsをこれらに再実装しない。詳細は[Workbench契約](../contracts/workbench.md)を参照する。

#406で成立したexperiment lifecycle / runnerと、#407で追加したexecution trace / motion-log recorderのownerは
`experiment/`である。`runners/`はthin entry pointだけを所有し、Task判定、metric、record projection、
artifact emissionを実装しない。validated v1 logからのmetric導出とcanonical artifact emissionは
`evaluation/artifact.py`が所有する。

`runtime/` is the only composition root。input、motion、kinematics、MuJoCo backend、transportを
layer横断で接続できるのはruntimeだけである。MuJoCo remains the physical source of truth。
viewerはrender-onlyであり、runtime stateを再計算しない。

`runtime.output.transport_adapter`はP5 allow、lifecycle、permissionと`transport/`のgeneric OSC / UDP
providerを接続するtransport compositionである。defaultは`disabled`で、explicit `transmission_enabled`と一致する
operator gateを要求する。adapterはcodec identity、request bytes、candidate、target、endpoint、revision、freshness、
sequenceを結び、送信は1回に制限する。config v2のexternal authorizationとencoder capabilityが必要な場合は、
composition発行のone-shot grantをguarded send時に消費する。`transport/`はOSC bytesとendpoint deliveryを所有し、
runtimeやrobot固有command mappingをimportしない。module-level contractをruntime package rootからre-exportせず、
adapter以外のoutput coreからtransportへ依存させない。

FastArmのRobot Profile、#509 accepted physical-measurement handoff、P5 lifecycle、physical-actuation / transmission
permission、operator gateを結ぶsessionは`runtime.output.fast_arm_adapter`が所有する。wire joint mappingとrouter
observation parserはplugin-local `adapter/physical_output.py`が所有し、runtime / transportをimportしない。
FastArm encoderのrequired-authorization declarationにより、generic adapterへの直接compositionもv1 / grantless configを
拒否する。simulated test observationはpending correlationを検証するだけで、receiver ACKやphysical behaviorの証拠にならない。

R7-Hの`runtime/contact/`はmanifestからMuJoCo scene variantを構成し、task objectのbackend body / geom、
model settings、trial reset、初期contact readinessを所有する。scene compositionはviewerへ装飾cubeを
追加せず、disabled sceneも明示的にobjectなしとして扱う。`evidence.py`は同じbackend model/dataの
`mjData.contact`と公式`mj_contactForce`からpoint、frame、normal、distance / penetration、force / wrenchを
測定し、target-object、self、environmentの分類とdeterministic aggregationを所有する。contact evidenceの
failure stateはno_contact、measurement_unavailable、invalid_contact、solver_invalidを分離する。
`virtual_reaction_force.py`はraw `ContactEvidence`から別manifest identityを持つsoftware-only signalを導出し、
frame変換とfilter pipelineを所有する。derived signalはraw evidenceを変更せず、
`ContactTaskOutcome` / terminal evidenceはraw contact contractに従う。`log.py`はexplicit callerがtrial単位の
raw evidence、derived signal、Task state、summaryをversioned JSONLへ投影する。summary outcomeはraw contact
evidenceに基づき、derived force filterから判定しない。writerは同一directoryの一時file、strict read-back、
atomic replaceを使い、default runtimeはfileを開かない。`presentation.py`は選択sampleを
`contact-task-presentation/v1`として同一MuJoCo snapshotの`metadata.contact_task_v1`へ投影する。
task outcomeのlifecycleは`plugins/tasks/contact_press_hold_task/`、canonical outcome / terminal shapeの
共有型は`task_contract.py`がownerである。scene compositionとviewerはforce filter、terminal判定、contact再計算を
行わない。#415のfixture runnerはraw measured evidenceのreplayだけを扱い、MuJoCoのphysical sceneや
Robot commandを二重に所有しない。

production compositionは明示的に選択した`RobotRuntimePlugin`を解決し、model、joint order、
startup keyframe、IK / FK、motion policy、qpos feasibility guardの整合を検証する。generic stub、
zero solver、退役したPlanar solverへ暗黙fallbackしない。

production concrete registrationは、固定namespace直下の`plugin.py` / `ROBOT_PLUGIN`を読むbounded
discoveryから`xpotato_sim.plugins.robots.catalog`へ投影する。catalogは具体robot importや具体IDを持たず、
discovered `RobotBundle`をknown IDでresolveし、ProfileとRuntime Plugin resolverは同じBundle objectの
`profile` / `runtime_plugin`へprojectionする。application compositionはBundleから必要なtyped providerを
assembly時に取得してconsumerへ渡し、処理中にBundleへ問い合わせるservice locatorにはしない。
`RuntimeConfig.robot_selection`は`robot_profile_id`と`robot_logical_version`から#405 / #406共通の
`PluginSelection`を作り、registration、Bundle、Profile、Runtime Plugin、runtime pipelineの全resolverへ同じ値を渡す。
shared production consistency validatorはselection、Bundle logical identity、Profile ID / contract version、
Runtime Plugin ID / canonical Profile objectを一致させる。raw Bundle identityだけをproduction ownership
proofにせず、aliased robot ID / logical versionをbackend build前に拒否する。このvalidatorはgeneric experiment
`RobotBundle` constructionへ適用しない。version省略時のfast_arm logical v1 behaviorは維持し、
requested / registered version不一致はmodel load前に拒否する。
onboarding schema versionをruntime selectionへ流用しない。
`RuntimeInputSourceStepLoopPlan`は`EndpointPoseProvider`、`EndpointCommandProvider`、
`QposFeasibilityProvider`、resolved `CommandSemanticsRoute` / `CommandExecutionBinding`を保持し、
`ResolvedRobotRuntime`またはRuntime Plugin全体をexecution edgeへ持ち越さない。endpoint poseの観測、
motion generator、qpos guard、Robot command applicationはそれぞれのtyped provider / bindingを使用する。
concrete MuJoCo pipelineのendpoint evaluation publisherも`ENDPOINT_POSE_V1` providerを受け取り、
site/body endpointの選択をgeneric runtime内で再構築しない。assembly時の初期stateでendpoint positionを
解決できない場合はfail closedとする。
Runtime Pluginを直接使用できるのはcomposition中のmodel validationとFK factoryに限定する。
各typed providerの`ProviderAssemblyBinding`はBundle logical identityとcanonical Profile / Runtime Plugin ownerの
object identityを固定する。custom providerを含め、stale Profile、stale Runtime Plugin、別robot、別logical versionに
bindされたproviderをregistration / assembly時に拒否する。
旧profile / runtime / bundle registry moduleは退役済みである。application compositionとruntimeのdeliberate
package-root resolverは`plugins/robots/catalog.py`のcanonical resolverへ直接到達し、intermediate facadeを通らない。

discoveryはapplicationがcatalog resolverへ初めて到達した時点で同期的に完了し、duplicate identity、
broken entry point、contract / capability不整合、missing / escaped resourceをpartial registryなしで拒否する。
`assets/mujoco/<robot_id>/...`と`configs/<robot_id>/...`はresourceのstable logical namespaceであり、
physical repository pathとは限らない。repository resourceは許可root内、package resourceは宣言package内へ
symlink解決後も閉じる。generic compositionはrobot IDやlogical identifierからphysical ownerを推測しない。
viewer URLはvalidated logical resourceのmappingであり、このresolved ownership gateを迂回できない。
readinessはdiscovered catalogからBundleを選択した後に行い、discovery順、package path、module / class名を
requested / resolved / freeze identityへ含めない。onboarding schema versionはdiscovery registrationのdecode軸、
Bundle identity versionはrobot selection / logical contract軸として別々に検証し、catalog resolverで混同しない。

viewer deliveryではruntime frameにfull declarationを埋め込まず、検証済みrepository declaration resourceの
public URLとcanonical digestだけをauthoritative metadataとして渡す。viewerはconnection開始後に一度だけfetchし、
steady-state frameではcompact referenceの一致だけを検査する。このdeliveryはrendering resourceの解決であり、
runtime execution edgeまたはreadinessへviewer serviceを持ち込まない。

実験compositionでは、Robot Bundle、Environment / Scene、Control / Mapping、Task、Evaluationを
versioned known-ID registryから明示解決する。`runtime/`はphysicsやrunner開始前にcapability provider、
axis-scoped parameter owner、typed semantic role、version-aware robot/environment/task compatibility、
evidence producer、evaluator requirementをfail-closedで検証する。詳細なtyped contractとreadiness順序は
`docs/contracts/experiment-plugin-composition.md`を正とする。

このgeneric experiment compositionはreadiness-onlyである。R7-G free-space用のproduction Environment /
Task / Evaluation catalogは各axis packageが所有し、`composition/production_experiment.py`がconcrete IDを
知らずに6軸registryを束ねる。`evaluation/r7_g_free_space.py`はproduction catalogだけで解決できるworld /
tool manifest fixtureを所有するが、scene compose/reset、task lifecycle、metric導出、artifact export、experiment runnerは所有しない。
R7-G readinessはupper `EvaluationManifest`のtarget、tolerance、dwell、timeout、initial tipをimmutable
Task contextへbindし、`EvaluationReadiness.task_execution_binding`としてrunnerへ渡す。runnerは
MuJoCo-owned measured endpointとstatusをtyped observationとして渡すだけで、terminal classificationや
canonical task evidenceを作成しない。Task pluginがpure transitionとproducer provenanceを所有し、trial
aggregation、artifact export、condition summaryは`evaluation/artifact.py`へ残す。

`experiment/world_tool_runner.py`はfrozen readinessからEnvironment scene condition、Input Source reader、
Control Mapping、selected command route、Robot Bundleのtyped provider、MuJoCo simulatorをassembly時に一度だけ
結線する。trial開始時にselected Environmentをresetし、MuJoCoをcanonical keyframeへresetした後、
actual qposとmeasured tool orientationをfrozen manifestへ照合する。照合後の`endpoint_pose/v1`実測値を
elapsed `0.0`でTaskへ渡す。各stepはmanifest cadenceのsimulation timeだけを進め、
post-step measurementとruntime statusをTaskへ渡す。Bundleをloop中のservice locatorにせず、wall-clock pacingも
正しさの条件にしない。step上限は`ceil(timeout / cadence)`で、Task terminalまたは明示上限で有限停止する。

canonical pairへ固定するmanifest revisionとstartup側が取得したactual `SoftwareExecutionIdentity`は別入力とする。
runner自身が同じcaller値から両者を合成せず、readinessのexact-match gateで不一致をfail closedにする。

Evaluation Pluginはproduction composition / readinessのordered tupleとしてresolveするが、metric導出や
evaluation artifact出力は実行しない。#408のartifact ownerがvalidated v1 logから順序付きpluginへ委譲する。#407のrunner resultはTask transition、step count、simulation elapsed time、
freeze identityに加え、既存execution loopでownerが生成したimmutable step traceを保持する。runtime recorderは
そのtraceを`experiment-motion-log/v1`へprojectionし、strict validation後だけatomic JSONLとして保存する。

application-facing replay / viewer / smokeはRobot、Input Source、Control Mapping、command semantics routeを
接続するdiagnostic / operational runtimeである。R7-G production experiment runnerはこの経路と別に6軸を
明示選択する。viewer control planeはplanned #486のscopeであり、既存diagnostic経路へ暗黙にEnvironment /
Task / Evaluationを補わない。

## composition-rootの責務分割

| stage | 現在のowner | 抽出可能なboundary | authoritative input | authoritative output / failure |
| --- | --- | --- | --- | --- |
| source planning | runtime entry | input-source registry resolver | configurationとsource ID | validated source planまたは明示的なunknown / incompatible failure |
| source lifecycle | runtime loop | source lifecycle coordinator | selected sourceとclock | latest `InputIntent`、source activity、age |
| control-frame resolution | runtime control-frame resolver | pure frame resolver | requested frame、pre-step orientation、`dt_s` | resolved world intentまたはunavailable status |
| motion policy | selected plugin / runtime coordinator | motion policy adapter | intent、current qpos、target lifecycle | `MotionCommand`またはhold / reject |
| backend update | typed Robot command provider / MuJoCo backend boundary | semantic-specific backend command applier | `JointPositionCommand`等のvalidated typed command | updated model stateまたは適用前failure |
| physical output safety / permission / trace / lifecycle | runtime output boundary | typed `PhysicalOutputRequest`、P5 safety evaluation / binding、permission evaluator / recording sink / lifecycle controller | exact request bytes、typed P2/P3/P4 evidence、candidate、Robot / software revision、freshness、explicit operator gate、stale / stop reason | allow-only sendable wrapper、accepted / rejected / permitted / dropped / safety-aware lifecycle evidence。送信実績とは別 |
| MuJoCo measurement | post-step measurement helper | pure measurement helper | post-step `MuJoCoState` | physical `tip` site measurement |
| diagnostic annotation | runtime diagnostics | pure annotator | intent、prediction、measurement、source state | precedenceを固定したmetadata |
| publication | runtime publication coordinator | `StatePublisher` | fully annotated state | publication completion |
| target lifecycle | runtime target resolver | pure lifecycle reducer | desired / active / measured target evidence | authoritative active targetまたはhold |
| experiment record construction | explicit caller-owned adapter | production loop外のrecord builder | completed step evidence | immutable record。default runtimeはfileを開かない |
| experiment plugin readiness | runtime composition | versioned plugin resolver | explicit 6-axis selectionとaxis-scoped typed parameter | resolved capability、typed role、source sample schema、evidence producer binding、freeze identityまたはstartup failure |

## Input Source reader boundary

Input Sourceのfactory outputは`HealthyInputSource`として`read_frame()`と`current_health()`をtypedに
満たし、factory直後のhealthがpluginの`initial_health`と一致しなければならない。live / viewer bridgeは
`ManagedHealthyInputSource`として`start()` / `close()`も満たす。`ValidatedInputSourceReader`はframeと
healthを呼出しごとに検証する。production runtime selectionのSoTは
`plugins/input_sources/catalog.py`であり、selectionはaliasから`PluginSelection`、resolved plugin、sample schema、
validated reader、typed execution adapterへ一度だけ解決する。

旧`input_sources/registry.py`はC4で退役した。source selection SoTは
`plugins/input_sources/catalog.py`だけであり、source固有のpreset、custom frame、factory
parameterはproduction registrationのrequest builderが所有する。plugin-backed primary pathはsource IDを比較せず、
registrationが保持するexecution adapterを必須とする。adapter欠落はfail-closedであり、source-name tableを持つ
`compatibility_execution_adapter()`はproduction/public callerがないことを確認して退役した。

module import、bounded discovery、catalog construction、factory constructionはexternal I/Oを行わない。
Selfrionetteの`pyserial` loadとserial openは明示的な`start()`以後だけである。composition readinessは
frame read、lifecycle startを実行しない。offline / replayにmanaged lifecycleを
要求せず、live / viewer_bridgeのruntime instanceだけがmanaged adapterを持つ。execution開始前に`steps`等の
pure argumentを検証し、無効な要求では`start()`も`close()`も呼ばない。managed executionを開始した場合は
start failureを含む各attemptでcloseを最大1回試行し、cleanup failureはprimary failureを置換せずdiagnostic noteへ
保持する。正常終了後のcleanup failureはfail-closedで表面化する。close完了後はlive delegateのresource参照を
破棄し、read-after-closeを拒否する。再start時はresourceを再構築する。

P3のexecution adapterは`target_metadata`、`replay_compatibility`、
`viewer_local_endpoint_compatibility`、loadcell、analog fixtureのversioned semanticsを明示する。
viewer backendは`ViewerBridgeRuntimeCapability`を介してingress、endpoint rebase、clock rebindを同一underlying
sourceへ結線し、generic readerへ任意attribute forwardingを追加しない。clock rebindはreader / capability identityと
既存message / endpoint stateを保持する。P4後のviewer adapterはsource ingress、health、timeout、canonical
sample projectionを保持し、local motion、orientation metadata、post-step measurement、publish後rebaseは
runtime composition側で保持する。

step-loopはreplay compatibilityではrecorded frame metadataをsource-state truthとして使用し、その他のsourceでは
typed healthをsource-state truthとして使用する。live frameにstate fieldがある場合は存在するkeyだけhealthと照合し、
省略keyをhealth projectionで補完する。canonical projection後の同じframeをversioned Control Mapping Plugin、
record、diagnosticsへ渡す。mapping selectionまたはtyped adapterが欠落するproduction planはfail-closedとし、
`InputInterpreter`へfallbackしない。
frontend keyboard / gamepad provider、backend source、mappingの分離とmapping readinessは#461で成立し、#462で
plugin-local test ownership、reusable conformance、test-only dummy onboarding、retained compatibilityの境界を
architecture guardとfocused validation contractへ固定した。source pluginからrobot / task / evaluationへの禁止
import、mapping pluginからdevice acquisitionへの禁止import、runtime source-name dispatchはAST / import graph
guardで検出する。

### P4 viewer source and mapping composition

P4ではviewerを次のtyped compositionとして扱う。

```text
ViewerInputProviderRegistry
        -> provider raw message
backend ViewerInputSource
        -> viewer_control_sample/v1 + typed health
ViewerKeyboardGamepadMappingPlugin
        -> typed endpoint-velocity intent
runtime step loop
        -> desired endpoint progression / rebase / MuJoCo command
```

provider registryは`keyboard/v1`と`gamepad/v1`の静的known-IDだけを解決する。frontend providerは
browser raw acquisitionとlifecycleを所有し、gamepadのnormalized `axes`はwire / overlay compatibility
projectionに限る。backend sourceはparse、schema、latest canonical sample、health、timeout、cleanupを
所有する。raw `raw_axes`がある場合もgamepad/v1のpublicな`zero_state`、`source_active`、heartbeatはlegacy
projected axesとbuttonsを反映し、connection / focus / visibility / stale / disconnectなどsource-owned stateと
合わせて決まる。mapping deadzoneやcommand zeroとは別概念である。mappingはtransportや
frontend APIをimportせず、canonical sampleから既存keyboard / gamepad semanticsを一度だけ実行する。
runtimeはmapping resultを適用し、publish-before-rebase orderingと同一source/capability instanceのidentityを維持する。

source selectionとmapping selectionは別の`PluginSelection`として解決する。source registrationは
concrete Mapping identity、default、Mapping parameter projectionを持たない。operator convenienceの
default pairingは`runtime/control/input_source_mapping_policy.py`が所有し、callerが指定したmapping
identityを上書きしない。runtimeは
resolved sourceのproduced sample schemaとmappingのaccepted schemaをexact matchで検証し、mappingのgeneric
parameter contractとoptional semantic validator / normalizerをsource lifecycle開始前に実行してからmappingを
実行する。unknown parameter、negative / non-finite speed・deadzone・max delta、invalid keyboard axis / directionは
selection / plan readinessでrejectし、normalized / frozen parametersをstep loopへ渡す。unknown、duplicate、
version mismatch、schema mismatch、missing mapping capabilityはimplicit fallbackなしでfail-closedとする。

C3のinterpreter fallback退役では、`programmed_target`と`noop`の既存`RawInputFrame` semanticsを
`replay_mapping/v1`へ明示的に接続するdefault mapping selectionとidentity mapping adapterを追加した。
adapterはframe representationを変更せず同一objectを返し、sourceのproduced sample schemaも変更しない。
effective mapping-input schemaだけを`replay_raw_input_frame/v1`としてversioned contractに表し、
旧`ReplayInputInterpreter`と同じ`InputIntent` shallow-copy semanticsを維持する。

legacy messageはsourceでcanonical sampleへ変換され、別のlegacy mapping実装へ分岐しない。C2では
source-owned implementationを`plugins/input_sources/`へ集約した。C3ではproduction/internal consumerを
catalog、typed mapping selection、`ControlMappedRuntimePipeline`へ収束させた。
public compatibility evidenceの監査後、C4はimmediate removalを採用した。
`src/xpotato_sim/input_sources/`、`input_interpreters/`、interpreter-based `RuntimePipeline`、old-path helper、
compatibility scriptを退役した。canonical CLIの`--robot` requirement、validation wording、runtime behaviorへ
wrapper parityを逆流させない。

raw gamepad sampleでは`raw_axes`をmappingのauthoritative inputとして保持する一方、gamepad/v1の
`zero_state`、`source_active`、heartbeatはlegacy projected `axes`とbuttonsに基づくobservable semanticsを
維持する。したがって`raw_axes=[0.05]`、legacy `axes=[0.0]`、`zero_state=true`では、mapping deadzoneが
`0.0`でもsourceはinactiveのholdとなる。raw `0.15`はfixed frontend projection後の`1/18`をmappingへ渡し、
button-only sampleはactive provider sampleとしてmappingへ渡す。`raw_axes`を持たないlegacy messageは旧
`axes` / `zero_state`解釈を維持する。default behavior parity、disconnected / hidden / blurred / staleの
hold safety、malformed ingressの即時`invalid`遷移を維持する。

## failureとordering

command semanticsを含むstartup順序は次で固定する。

```text
resolve Input Source / Mapping / Robot selections
-> validate source-produced / Mapping-accepted schemas
-> normalize and validate Mapping parameters
-> resolve Mapping control semantics / runtime conversion route
-> resolve final Robot command semantic provider
-> bind and validate typed command execution
-> readiness / freeze
-> source start
-> serial / viewer / network I/O and MuJoCo stepping
```

semantic provider不在、provider command type不一致、selected route / execution binding identity不一致は
source lifecycle開始前にfail-closedとする。provider不在は
`mapping/Robot command semantics compatibility mismatch`としてrejectする。
`ControlMappedRuntimePipeline`はresolved routeと同じidentity / command typeへbindされた
`CommandExecutionBinding`を必須保持し、bindingなしでは構築できない。input step loop、`run_once()`、
default / explicit replay、default / explicit viewer、`sweep_x`、offline smokeはpipelineの
`execute_intent()`または`execute_motion_command()`だけをRobot command application入口として使用する。
productionのconcrete / replay builderは外部で解決済みの`ResolvedCommandExecution`を受け取らず、
current Control Mapping、route selection、current Robot Bundleからcanonical route / strategy /
binding / providerを内部解決する。step-loop planは完成したpipelineのrouteと同一binding objectを
authoritative objectとして保持し、別途解決したbindingを併存させない。
production replay builderは`RuntimeConfig.robot_selection`、Bundle identity、Profile identity / contract
version、Runtime Plugin identity / canonical Profile objectを共有validatorで照合し、Bundleのcanonical
Runtime Pluginだけでsimulatorを構築してmodel contractをpipeline return前に検証する。qpos feasibility
guardとprofile metadataも同じBundleから導出し、外部simulator、aliased Bundle、別Robot / 別logical
version backend、foreign modelをtyped providerと独立に組み合わせる注入面を持たない。
現行fast_arm local motion routeはendpoint velocityを`dt`積分し、
desired endpoint positionからJacobianで`MotionCommand`を構築し、safety / qpos feasibility後に
`JointPositionCommand`へprojectionする。missing joint、joint-velocity-only、empty positionは
provider/backend到達前にrejectする。`MotionCommand`はdiagnostics用runtime envelopeとして保持し、
Robot command semantic typeには使用しない。このconversionはruntime / controller ownerであり、
fast_arm backendのnative endpoint-velocity能力ではない。

`HeadlessMuJoCoSimulator.apply_command(MotionCommand)`と旧pending分岐は退役した。
fast_arm診断も`project_joint_position_command`からtyped入口へ接続し、診断envelopeは
`record_motion_command_envelope`で保持する。旧call、`motion_command_to_qpos_command()`使用、
`command_type = MotionCommand`再導入をarchitecture guardで拒否する。

- unknown profile、incompatible model、invalid joint orderはcomposition前に失敗する。
- qpos feasibilityはcandidate全体を検証し、invalid candidateを部分適用しない。
- stale / inactive sourceはhold-current semanticsを優先し、新しいactive targetを捏造しない。
- malformed JSON、schema不一致、provider identity不一致はsource-owned typed ingress failureとして即時
  `invalid` healthへ反映し、timeout待ちで旧active frameを継続しない。次のvalid sampleだけが明示的な
  recoveryとなる。
- unavailable diagnostic fieldは欠落のままとし、stale値を保持しない。
- `publish-before-ViewerInputSource-rebase` orderingを維持する。
- transport failureをphysics successへ読み替えず、viewer failureをbackend stateへ反映しない。
- evaluation manifest readinessはrunner / log / outcomeを開始せず、canonical requested identityとresolved
  identityをfreezeするsoftware-only gateである。world/tool pairの条件差分は
  `docs/contracts/evaluation-manifest-readiness.md`の許可リストに限定する。
- experiment runnerはreset後のactual qpos / measured tool orientationをfrozen initial stateへ照合し、
  manifest initial tipをmeasurementへ変換せず、reset直後と各step後の
  `endpoint_pose/v1` observationだけをTaskへ渡す。stale、hold、rejection、unavailable、invalidは
  typed status/reasonとして投影し、nominalまたはsuccessへ変換しない。

この文書はcurrent responsibility boundaryを固定する。
fast_arm固有diagnosticsは`plugins/robots/fast_arm/adapter/diagnostics/`が所有し、generic runtime public surfaceや
plugin discovery entry pointからeager importしない。production builderは`ControlMappedRuntimePipeline`を構築する。
test-only mapped wiringは`tests/support/`が所有する。
pre-audit composition chronologyとrefactor proposalは
`docs/reports/audits/canonical-content-history-separation-2026-07-16.md`へ保存した。

### Current gamepad / Mapping parameter boundary

gamepadのraw pathは、`raw_axes`をmappingのauthoritative inputとして保持する。default `gamepad_deadzone=0.1`では、fixed frontend deadzone `0.1`のprojectionとbackendの第二thresholdをControl Mapping Plugin内で同じ順序に適用し、raw `0.15` / `0.19`はzero、raw `0.20`はlegacyと同じ非zero結果になる。`gamepad_deadzone=0.0`でもraw `0.05`はfrontend projectionとlegacy `zero_state=true`によりholdとなり、raw `0.15`は`1/18`の非zero結果になる。normalized `axes`はwire / overlay compatibility projectionに限る。

source activity / healthとmappingが生成するcommand zeroは別概念である。gamepad/v1のlegacy zero-state
projectionはobservable source activityの互換条件として維持し、button-only sample、disconnect、hidden、
blur、stale、invalidの既存hold safetyも維持する。

Control Mapping parametersは`explicit runtime mapping parameters > Mapping plugin defaults`の順で
解決する。Input Source instance、frame metadata、source registrationからMapping parameterを投影しない。
selection / plan readinessでMapping contractを正規化・freezeし、source lifecycle開始前に確定する。

### Physical output評価候補のcomposition

`runtime/output/safety_gate.py`はjoint-position requestとP3 observation producerが実評価したconfigurationを照合し、`compose_physical_output_safety_input`で同じqpos / qvelをP4へ渡す。P3/P4は`runtime/safety/evaluated_candidate.py`のimmutableな値projectionを返し、それぞれのowner-local originをauthorityとする。outputは両projectionとrequest targetを照合し、formulaを再実装しない。endpoint-velocityおよびcanonical resolverのないtrajectoryはnon-sendableであり、plannerやphysical observation architectureは追加しない。

Runtimeが構成する`EvaluatedJointRoute`はendpoint設定とRobot-owned joint順序の対応をP3観測からP4評価まで保持する。requestのendpoint変更を単なる数値qpos一致で許可せず、評価したrouteとの一致を検証する。

## 実機前の連続入力検証

位置増分のresolved command routeは、同じMuJoCo snapshotのmeasured endpointを
そのstepのMapping contextへ渡す。source adapterは数式の切替authorityではない。明示readerはconcrete pipelineへ直接注入し、
一時replay frameや別modelで取得経路を置換しない。実行側のcontextは固定configと分離する。
設計正本は`docs/contracts/pre-hardware-signal-emulation.md`とする。

## 信号/contactのsoftware-only統合

`runtime/runners/signal_contact.py`が既存Source/Mapping/route、contact Environment/Task/Evaluationとno-I/O
outputを結ぶ。`runtime/contact/robot_view.py`は単一sceneのRobot joint viewであり、第二のphysicsを持たない。
`signal_contact_artifact.py`がlocal traceと既存contact log/payloadを検証する。
実機permissionとP5のauthorityを作らず、詳細は`docs/contracts/pre-hardware-signal-emulation.md`へ委譲する。

## 入力とphysical outputの有限owner

`runtime/runners/fast_arm_input_runtime.py`が既存のresolved input planとdisarmed physical sessionを専有し、
明示start、有限tick、受信/expiry、P5 submit、stop/cleanupを接続する。生成や判定のownerを移さない。
出力requestのhost clockとSource/MuJoCo時刻は別々に記録する。詳細は`docs/contracts/physical-output.md`を正本とする。

## 共同armの診断実行

[共同実行契約](../contracts/coordinated-arm-runtime.md) に従い、compositionがSource/Mapping/assembly providerを結ぶ。
`execution/coordinated.py` は候補の一括反映と全体latch、`output/coordinated.py` は全側の既存prepare/dispatchを監督する。
`schemas/coordinated.py` は名前付き複数手先と時計を分離する。旧単腕v1のrouteを暗黙に双腕へ拡張しない。
`runners/coordinated_gamepad.py` は有限な保存入力の診断入口であり、participant Task/metricの第二SoTではない。

## 固定物体sceneの共通model composition（#585）

共通`LaunchProfile/v3`が既存Environment/Task catalogを解決し、`ModelScenePlan`を登録済みRobot model factoryへ渡す。
Robotは診断colliderの名前と形状を所有し、Environmentはworldに置く物体・接触対を所有する。
同じ構築済みartifactからprovider/Viewerを作り、`ModelStateSample`でrobot snapshot、全qpos、名前address、幾何観測を
同じlock内から取得する。物体数や腕数による専用runnerを設けない。
`runtime/scene/`はtyped manifest、binding、composition、純粋観測DTO、native measurement、Task contextを持つ。
Taskはpure DTOを入力とし、MuJoCoやbackend measurementをimportしない。
旧R7-H force/contact manifestは互換維持し、共通geometry抽出だけをbackend primitiveへ移す。
詳細は[固定物体scene契約](../contracts/object-scene-contact-diagnostic.md)。

## world条件とdynamic execution（#582）

Environment-owned world/v2はbare Robotと支持面・物体を構成し、Execution-owned DynamicsSettingsを一回だけ合成する。
旧Robot-owned base sceneは旧versionのrecord互換のための経路であり、新worldはそこから床やgravityを継承しない。
Robot dynamic providerは既存共同prepare/commitへ実行hookで接続し、単腕/双腕と物体数でbranchしない。
`runtime/scene/world.py`は世界条件、`execution/physics.py`は数値条件、`scene/world_composition.py`はnative MJCF投影、
`schemas/scene_state.py`はpure coordinate layout、`mujoco_backend/state_layout.py`はnative address解決を所有する。
`scene/dynamics_observation.py`は同じlocked dataからRobot指令/実状態・物体状態・native接触力を取得する。
Viewer bundleは構築時に宣言digestとresource pathを確定する。metadataの可変containerは取得ごとに生成し、
宣言を置換したbundleはidentityを再計算する。providerはmodel lifetimeのbody/site名と力学観測の物体・geom役割・
joint addressをimmutableなplanへ準備し、resetをまたいでもpose、速度、ctrl、gravity、contact、force、timeを
毎回同じlocked live dataから取得する。力学観測のscene/settings/arm bindingを置換した場合はplanを再構築し、
snapshotのplanへ別modelを渡した場合は拒否する。physics substepの検査や観測項目は省略しない。
`ModelExecution.sample()`は同一snapshotを照合してTaskを更新後、metadataを一回だけ合成する。
snapshotの入力metadataとTask/bindingの可変containerはconsumerから切り離し、前の表示や入力・Task状態を
consumerの変更で書き換えない。Mappingの検証済みtrigger表示はJSON用containerへコピーし、表示取得のための
encode/decodeを行わない。wireと保存結果のfield・値・分類は維持する。
同一frameのGUIは既存publisher/rendererを使い、別のphysics service・device取得・hardware出力を増やさない。
設計比較と停止意味は[設計記録](../design/adr/2026-09-28-scene-dynamics-ownership.md)を参照する。
