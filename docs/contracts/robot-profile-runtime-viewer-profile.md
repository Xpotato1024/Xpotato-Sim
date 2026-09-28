---
status: canonical
owner: architecture
last_verified: 2026-09-28
canonical_for:
  - Robot Plugin registration and bounded discovery
  - Robot Profile contract and registry
  - Robot Runtime Plugin contract and registry
  - Viewer Robot Profile contract and registry
related:
  - docs/architecture/runtime-composition.md
  - docs/contracts/transport-payload.md
  - docs/contracts/fast-arm-joint-limit-config.md
  - docs/contracts/experiment-plugin-composition.md
  - docs/contracts/evaluation-manifest-readiness.md
---

# Robot Profile / Runtime Plugin / Viewer Profile契約

## Ownershipの分離

Python上のgeneric contract ownerは`xpotato_sim.runtime.composition.robot_profile`、viewer declarationの
serialization contract ownerは`xpotato_sim.runtime.composition.viewer_robot_declaration`である。旧package-rootの
`xpotato_sim.robot_profile`と`xpotato_sim.viewer_robot_declaration`は退役し、compatibility facadeは持たない。

`RobotProfile`はimmutableかつversionedなdeclarationである。robot identity、
MuJoCo asset reference、canonical joint order、qpos/qvel dimension、initial keyframe、
endpoint reference、joint-limit configuration reference、coordinate/unit contract、
viewer-profile reference、capabilityを所有する。executable factory、module name、
class name、import pathは含まない。

`RobotRuntimePlugin`はruntime compositionだけが使用するtyped behavioral boundaryである。
pluginは選択したmodelをvalidateし、既存のrobot-specific IK、FK、motion policy、
qpos feasibility guard、endpoint accessorを構築する。fast_arm pluginは既存algorithmと
qpos feasibility TOML guardを再利用し、複製しない。

実験compositionでは、このprofile/plugin pairをadditiveな`RobotBundle`が参照し、reset、
endpoint pose/command、qpos feasibility、semantic scene role、optional contact evidenceを
versioned typed capability providerとして公開する。bundleは既存pairを置換せず、task lifecycle、
evaluation metric、viewer renderingを所有しない。詳細は
`docs/contracts/experiment-plugin-composition.md`を正とする。

evaluation manifestはprofile、runtime plugin、model contractのlogical versioned identityを記録する。
asset path、module path、package location、branch名はそのidentityに含めない。したがって#423の
package / import-direction migrationやcompatibility re-exportだけではmanifestのfreeze identityを
変更せず、profileまたはmodel contractの意味を変更するときだけversioned identityを更新する。

`ViewerRobotProfile`はbrowser-side rendering declarationである。model URL、
named startup keyframe、debug fixture URL、VFS asset、visual style、joint order、
qpos dimension、model compatibility versionを所有する。IK、FK、planning、
qpos generation、target generation、安全性は一切所有しない。現在のrendererは
MuJoCo compiled mesh geometryを消費し、独立したmesh-fallback routeを持たない。
したがってcurrent profile contractではOption Bを選択し、未使用のfallback mappingを宣言しない。
profile-owned VFS asset mappingをmodel-loading boundaryとして維持する。
fallback routeの追加には、別contract changeと明示的なdiagnostic、cleanup behavior、
profile-driven testが必要である。

`RobotPluginRegistration`はonboarding assembly boundaryであり、`RobotBundle`、
`viewer-robot-declaration/v1`、`RobotResourceDeclaration`、onboarding contract versionを一つの
immutable objectへ束ねる。`onboarding_contract_version`はregistration schemaのversionであり、robotの
logical identity versionではない。onboarding schema v1はlogical v1とlogical v2のrobotを同じregistryへ
登録できる。registration identityとBundle identityは一致し、Profile、Runtime Pluginが参照するProfile、
Viewer declarationの`profile_contract_version`はrobot logical versionと一致しなければならない。
registration、production concrete / replay compositionはruntime層の同じproduction consistency validatorを
使用し、selection、Bundle logical identity、Profile ID / contract version、Runtime Plugin ID / canonical
Profile objectのdriftを同じruleで拒否する。resource / viewer path ownershipはregistration固有検証に残す。
unsupported onboarding schemaはlogical versionに関係なくfailする。Bundleは引き続きtyped provider
assemblyだけを担い、execution中のservice locatorにはしない。各providerは
`ProviderAssemblyBinding`でBundle logical identityとassembly ownerを宣言し、ownerは同じBundleのcanonical
`RobotProfile`または`RobotRuntimePlugin` objectでなければならない。stale object、別robot、別logical versionを
registration / Bundle assembly時にfail-closedで拒否する。この検査はadapter class名ではなくgeneric binding
contractに対して行う。

asset / configurationのstable logical pathはregistration declarationをSoTとする。Profileのtyped
`RepositoryResource` / `PackageResource`とviewerのdeclaration / model / fixture / VFS referenceはregistrationと
startup時に照合し、generic codeはrobot IDやlogical pathからpackage名、package path、filesystem pathを推測しない。
`assets/mujoco/<robot_id>/...`と`configs/<robot_id>/...`はlogical namespaceであり、physical ownerはrepository fileまたは
typed package resourceのどちらでもよい。resolved repository / package boundaryへ同じownership gateを適用し、viewer URL
mappingで回避できない。symlink解決後の実pathも宣言されたphysical owner boundary内に残す。logical namespace維持のために
旧physical directoryへduplicateを残さない。shared resourceは
暗黙許可せず、必要になった時点で別の明示contractを定義する。registration、viewer serialization、canonical identity
materialにはPython package、module、class名を含めない。

architecture test向けのcontract sentinelとして、ここでは`selects Option B`を固定し、
`does not declare an unused fallback mapping`を保証する。

## Registry解決

```text
RuntimeConfig.robot_selection
  = PluginSelection(robot_profile_id, robot_logical_version)
  -> bounded first-party Robot Plugin discovery
  -> registration resolver / discovered RobotCatalog / Robot Bundle registry
  -> same PluginSelectionによるRobot Profile / Runtime Plugin projection
  -> registry-set and profile/plugin consistency validation
  -> model load with explicit keyframe
  -> profile/model/joint/dimension validation
  -> IK/FK/motion/guard composition

profile-aware startup payload URL + digest
  -> validated repository declaration resourceをfetch
  -> viewer-robot-declaration/v1 strict decoder / digest validation
  -> asset/style/model composition
  -> steady-state frameのcompact reference / compatibility check
  -> qpos render only when compatible
```

production discoveryは`xpotato_sim.plugins.robots`直下の非private packageだけを候補とし、package名を
sortして固定`<package>.plugin`から固定`ROBOT_PLUGIN`を読む。external entry point、remote package、
hot reload、configuration stringまたはrobot IDからのarbitrary import、`__init__.py`副作用登録を
使用しない。candidate packageのentry point欠落、import failure、export欠落、不正型、package / declaration
identity不一致、duplicate identity、contract / capability / resource不整合をwarning skipせず、immutable
registryを返す前にfailする。

この境界はconfiguration-drivenな`arbitrary dynamic import`ではない。

## zero-core-change onboarding boundary

production robot追加時に変更する領域はrobot packageと、そこから参照するtyped resource ownerである。

```text
src/xpotato_sim/plugins/robots/<robot_id>/
repository-owned resource、またはdeclared Python package resource
```

`assets/mujoco/<robot_id>/...`と`configs/<robot_id>/...`は配置先ではなくstable logical identifierとして維持する。

robot packageは少なくともside-effect-freeな`__init__.py`、固定entry pointの`plugin.py`、Profile、
Runtime Plugin、Bundle assembly、viewer declarationを持つ。robot固有algorithmまたはmodel contractは同packageへ
置き、generic `runtime/`、`kinematics/`、`mujoco_backend/`、`plugins/robots/catalog.py`、viewer production source、
root compatibility facadeへ新robot固有import、ID、fallbackを追加しない。

registry IDとcanonical identity materialはlogical identityでsortし、candidate列挙順に依存しない。
production rootとtest discovery rootは別objectとして明示する。test-only robotはproduction namespaceまたは
catalogへ混入させない。

すべてのproduction Robot Profile / Robot Runtime Plugin pairには、`tests/`配下に
明示的なtest-only conformance caseも必要である。このcaseはproduction registry entry、
runtime composition dependency、public APIではない。

production concrete registrationのSoTは各robot packageの`plugin.py` / `ROBOT_PLUGIN`である。
`xpotato_sim.plugins.robots.catalog`はdiscovery結果だけからregistryとprojection resolverを構成し、具体robot
package、具体robot ID、Bundle singletonをimportしない。`resolve_robot_profile()`、`resolve_robot_runtime_plugin()`、
`resolve_robot_runtime()`、`resolve_robot_bundle()`は同じBundleのProfile / Runtime Plugin objectへ収束する。
Profile、Runtime Plugin、Bundleを独立したconcrete registryへ重複登録しない。旧registry moduleは
同じresolverとprojection registryをre-exportするcompatibility facadeである。既存のlogical v1呼出しはversion
省略時の既定値を維持する。明示選択では`PluginSelection`または`robot_logical_version`をregistration、Bundle、
Profile、Runtime Plugin、runtime compositionまで失わず伝播し、onboarding schema versionをselectionへ使用しない。

`resolve_robot_runtime()`は共通production boundaryである。test-onlyの明示registry injectionでは、
一方のregistryだけにあるID、requested/registered/plugin identity mismatch、profile/model contract
version mismatch、異なるdeclarative contract、canonical registered profile objectにbindされていない
pluginをrejectする。production defaultではcatalogのBundleを一度assembly SoTとして使い、object identityに
加えてsemantic comparisonも維持する。

## Productionとgenericの選択

Robot Bundleのtyped provider capabilityとbackend accepted command semanticsは別集合である。
`endpoint_command/v1`はtarget / local endpoint motion generatorを構築するprovider能力を表し、
backendがendpoint positionまたはendpoint velocityをnative受理するという宣言ではない。
最終backend boundaryは`RobotCommandSemanticProviderBinding`でsemantic identityとtyped execution
providerを一対一にbindする。`supported_command_semantics`はこのbindingから導出されるprojectionであり、
providerなしのidentity宣言はできない。

fast_arm / MuJoCoの現行motion generatorはendpoint targetまたはvelocity由来deltaをJacobianで
candidate qposへ変換し、MuJoCo adapterへ`JointCommand(joint_angles_rad=...)`を渡す。したがって
fast_armが実行providerを持つcommand semanticは`joint_position_command/v1`だけである。このproviderは
既存MotionGenerator / safety後の`MotionCommand.joint`をfiniteかつnon-emptyな
`JointPositionCommand`へprojectionし、typed backend boundaryへ渡す。providerは`MotionCommand`、
TargetCommand-only、joint-velocity-only envelopeを受理しない。MuJoCo backendはtyped commandを
model joint orderのqposへ適用し、既存どおりqvelをzeroにしてstep前後のcommanded qposを維持する。
`endpoint_velocity_command/v1`、`endpoint_position_command/v1`、`joint_velocity_command/v1`は
versioned identityとして区別できるが、未実装のfast_arm capabilityとして宣言しない。

production fast_arm entry pointは`RuntimeConfig(robot_profile_id="fast_arm")`を
明示的に構築するか、callerにそのIDの指定を要求する。解決済みprofile/plugin pairを通して、
model、`home` keyframe、endpoint reference、現在のIK/FK behavior、motion policy、
qpos feasibility guardを解決する。IDのないproduction config、unknown ID、incompatible modelを
与えた場合はstartupをfailする。

logical v2等を選択するcallerは`RuntimeConfig(robot_profile_id=<id>, robot_logical_version=2)`を使用する。
`RuntimeConfig.robot_selection`は#405 / #406と同じ`PluginSelection`を返し、catalog projectionとruntime compositionは
そのselectionを共有する。requested / registered logical version不一致はmodel load前にfailする。

`ControlMappedRuntimePipeline`はgenericのままとし、`build_replay_mujoco_pipeline()`は明示的な
`RuntimeConfig.robot_selection`とRobot Bundleを要求する。model pathまたはjoint nameからprofileを推論せず、
profileがない場合にfast_armを選択しない。replay builderはcurrent MappingとBundleからcanonical command
executionを内部解決し、pre-bound execution、simulator、keyframe、guard、profile metadataを受け取らない。
production consistency validatorはraw Bundle identityだけを信用せず、selection、Bundle、Profile、Runtime
Pluginのlogical identity / versionをbackend build前に照合する。BundleのRuntime Pluginがsimulatorを
構築してmodelを検証し、Bundle providerがguardを構築する。callerがmodel pathをoverrideしても、
selected Bundleのmodel contractをpipeline return前に通過しなければならない。
test-onlyのarbitrary simulator / guard injectionはproduction builderではなく明示的なtest helperへ閉じる。
stub-defaultの`build_mujoco_pipeline()`と
`build_noop_pipeline()`はproduction surfaceから退役した。したがって最小のnon-fast_arm MJCFは、
明示的に構築したtest-only `ControlMappedRuntimePipeline`により、fast_arm validationやconfigurationなしで
loadとstepを検証できる。

package resourceは宣言したimport packageとpackage-relative pathだけを許可し、stable logical pathとは別に検証する。
MuJoCo include/meshは`PackageResourceBundle`のrelative VFS layoutで解決し、filesystemへ永続materializeしない。

generic profile contractではjoint countを`nq`または`nv`と同一視しない。ball jointと
free jointは、joint nameが一つでもqpos/qvel dimensionが正当に`4/3`、`7/6`となる。
fast_arm pluginはstartup validationで、四つのcanonical joint、`nq=4`、`nv=4`、
exact joint orderを別途enforceする。

## fast_armのjoint、frame、startup契約

fast_armのcanonical joint orderとqpos indexは次のとおりである。

| qpos index | joint name |
|---:|---|
| 0 | `sholder_joint_1` |
| 1 | `sholder_joint_2` |
| 2 | `sholder_joint_3` |
| 3 | `elbow_joint` |

joint orderのsource of truthは`fast_arm_core.definition`およびcore package-owned `resources/model/arm.xml`である。
`RobotProfile`はそのSelfrionette projectionであり、runtimeはjoint nameを推論または並べ替えない。
`sholder_joint_2`のMuJoCo `ref=-90`に対するsolver adapterは、
`mujoco_qpos1 = solver_q1 - pi/2`、`solver_q1 = mujoco_qpos1 + pi/2`を維持する。
legacyのdifferential shoulder mappingをproduction qpos mappingとして暗黙適用しない。

solver local frameは`base_link`をrootとする。physical endpointのsource of truthは
MuJoCo world / scene frameの`tip` siteであり、viewer表示またはsolver-local FKではない。
world commandからsolver-local targetへの変換はrobot-specific runtime/plugin boundaryが所有する。

fast_armのinitial pose definitionは`fast_arm_core.reference.initial_state`が所有し、core package-owned
`resources/model/arm.xml`のnamed `home` keyframe、Selfrionette `InitialStateContract`、viewer fixture/profileは
同一値のprojectionとして検証する。selected `home` qposは
`(0, -0.5235987755982989, 0, -1.0471975511965976)`、すなわち
`(0, -pi/6, 0, -pi/3)`である。Python loader/resetとbrowser WASMのpre-payload
startupは同じXML keyframeを読み、runtime first state/payloadも同じqposを運ぶ。
`ViewerInputSource`とinitial target markerは、そのposeのMuJoCo
`tip = (0.240000, -0.245951, 0.284308) m`へrebaseする。
R7-G-P1の`FAST_ARM_INITIAL_STATE_CONTRACT`は、このnamed keyframeを
`fast_arm_initial_state/v1`として固定し、frame=`MuJoCo world / scene frame`、
position unit=`meter`、orientation unit=`unit_quaternion`、quaternion order=`wxyz`、
tool orientation WXYZ=`(0.8365163037378079, 0.1294095225512604,
0.4829629131445341, 0.22414386804201347)`を提供する。readinessはこのprovider contractとmanifestを
model loadなしで比較し、profile / runtime plugin / model contract identityも同時に一致させる。

collision geomがdisabledでcollision checkを利用できない状態では、このstartup poseを
collision-freeの物理証拠とは扱わない。joint range内であること、startup continuity、
first-input continuityと、physical collision feasibilityは別のacceptance boundaryである。

## Backend/viewer整合性とpayload v0

Runtimeは既存のopen payload-v0 `metadata` mapへ`robot_profile_id`、
`model_contract_version`、`robot_joint_names`、`robot_qpos_dimension`、
`viewer_robot_declaration_resource_path`、`viewer_robot_declaration_url`、
`viewer_robot_declaration_digest`を追加する。frameへfull declarationを含めず、後三つは検証済みrepository
resourceを指すcompactなsession referenceである。envelopeとpayload versionは変更しない。WebSocket viewerは
接続後の最初のprofile-aware payloadでURLからfull declarationを取得し、strict decodeとcanonical
SHA-256 digest検証を一度だけ行ってsession cacheを確定する。reconnect時は同じ手順で再取得する。
qpos適用前にloaded modelのdimension/joint orderと四つすべてのbackend compatibility keyを
確認する。profile-aware production viewerでは、`robot_profile_id`、
`model_contract_version`、`robot_joint_names`、`robot_qpos_dimension`が
解決済みViewer Robot Profileと完全一致しなければならない。compatibility metadataが
missing、unknown、malformed、mismatchedの場合は明示的なinvalid diagnosticを生成し、
qposを適用しない。このviewerではprofile-free legacy payloadまたはgeneric payloadから
暗黙にfast_armへfallbackしない。static profileを明示したgeneric payloadではsession referenceがない既存挙動を
維持する。

四つのcompatibility keyと三つのviewer declaration reference keyはreservedかつauthoritativeである。
production compositionでは
general state metadataと分離し、state、replay frame、input intent、motion command、
input-source metadataの後に最後に適用する（overwrite-protection Option A）。したがって
spoofed valueは、qpos-rejection pathを含めて解決済みprofile valueに置換される。
authoritative profile metadataを持たないgeneric pipelineはこれらのkeyを追加せず、通常の
metadata behaviorを維持する。fieldはopen payload-v0 metadata mapへのadditive fieldのままだが、
profile-aware production compositionではprofile-aware viewer compatibilityのため四つのkeyと
compact declaration referenceをauthoritativeかつmandatoryとする。steady-state frameではreferenceの
resource path、URL、digestだけを比較し、full declarationのJSON decode / canonicalizeを反復しない。
session中のreference欠落またはdigest / URL / resource path変更はfail-closedに拒否する。

viewer declarationはmodel URL / resource path、fixture URL / resource path、VFS mapping、joint order、
qpos dimension、startup keyframe、rendering styleを持つ。stable logical `assets/` identifierからpublic URLを
deterministicに導出する。plugin-owned resource binding manifestがlogical identifier、URL、owning package、package path、
bundle pathのconcrete inventoryを一意に所有し、Python registrationとVite dev/buildが同じdocumentをdecodeする。
viewer declarationのmodel、fixture、VFS mappingはmanifestと完全一致しなければならず、generic viewerはrobot固有inventoryを
持たない。Vite dev serverとproduction buildは同じdecoded bindingからresource bytesを配信する。
unknown field、missing field、schema version、remote / escaped resource、resource / URL mismatch、duplicate
mapping、backend compatibility mismatchは描画前またはqpos適用前にfailする。
viewerはdeclarationを使ってMuJoCo WASM sceneを構成するだけで、runtime state、IK / FK、planning、target、
safety decisionを再計算しない。

未接続時の既存fast_arm表示はTypeScript compatibility facadeがplugin-owned
`/mujoco/fast_arm/viewer-profile.json`をloadして維持する。facadeは宣言内容を再定義せず、
new robot onboarding registryとして使用しない。WebSocket pathのgeneric viewer sourceは具体robot IDを知らず、
新robot追加時に編集しない。

## #406 runnerへのhandoff

#406はcatalog-backed resolverへ到達した時点でbounded discoveryを完了させ、`PluginSelection`で解決したBundleを既存
experiment registryへ渡す。orderingは`discovery -> registration / resource validation -> Bundle resolution ->
readiness / freeze -> runner lifecycle`であり、runner開始後にpluginを追加・再探索しない。

runner execution edgeへ渡すのはassembly時に取得した`EndpointPoseProvider`、
`EndpointCommandProvider`、`QposFeasibilityProvider`等の必要なtyped providerだけとする。
readinessでresolveしたlogical versionと`RuntimeConfig.robot_selection`が一致しなければrunnerを開始しない。
`plugins.robots.<robot_id>`、旧compatibility facade、viewer declarationをruntime service locatorとして直接
使用しない。viewer deliveryはauthoritative runtime metadataが担い、readiness / freeze logical identityへ
package / module / class pathまたはdiscovery順を追加しない。


実装・cleanup・fixture hashのevidenceは`docs/reports/audits/canonical-content-history-separation-2026-07-16.md`へ保存した。


## Robot内の同列モデル選択（#580）

`RobotPluginRegistration.models`はoptionalなtupleで、generic `RobotModelRegistration`を保持する。
logical Robot identityとモデルidentityを分離し、単腕・双腕を別の登録階層へ置かない。
既存Robot Catalogだけが`resolve_model(robot, model)`を行い、未知ID/versionを拒否する。

モデル宣言は手先ID、joint順序、configuration digest、固定factoryを持つ。factoryはRobot側に置き、
runtimeはgeneric `RobotModelInstance(provider, viewer)`を受け取る。生成artifactのdigestとjoint順序・次元が
providerとViewerで一致しなければ構築を拒否する。profile表示/カタログ列挙ではmodel実行やI/Oを開始しない。

旧RobotBundle単一手先APIを偽の代表手先で双腕へ流用しない。新モデルの名前付き手先経路は共通v2起動設定へ
明示し、単一手先の旧v1比較・replayは互換境界で維持する。viewer resourceは同じ意味のdeclaration/v1を使うが、
モデルidentity/contract/digestと4/8関節順序は選択モデルごとに厳密に照合する。
