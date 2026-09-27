---
status: canonical
owner: runtime
last_verified: 2026-08-06
canonical_for:
  - versioned evaluation manifest
  - canonical manifest serialization
  - software-only evaluation readiness and freeze identity
  - world/tool condition-pair invariants
related:
  - docs/contracts/experiment-plugin-composition.md
  - docs/evaluation/world-tool-frame-comparison-design.md
  - docs/contracts/experiment-motion-log-v1.md
  - docs/architecture/runtime-composition.md
  - docs/contracts/robot-profile-runtime-viewer-profile.md
---

# evaluation manifest / readiness契約

## ownershipとscope

`src/xpotato_sim/runtime/evaluation/manifest.py`が、実行開始前に固定する
`evaluation-manifest/v3`のtyped manifest、canonical serialization、software-only readiness、
world/tool condition-pair、freeze identityを所有する。manifestはimmutableなtyped modelであり、
内部にmutable mappingやlistを保持しない。6軸のplugin contractはR7-G-P0 / #421の
`ExperimentPluginManifest`、`PluginSelection`、`PluginAxis`、`PluginParameterOwner`、
`ExperimentPluginRegistries`、`compose_experiment()`、`ResolvedExperimentComposition`、
`EvidenceProducerBinding`を再利用する。同義のR7-G-P1専用plugin contractは作らない。

この契約はrunner、physics step、analog fixture playback、experiment log lifecycle、outcome / metric
calculation、participant workflow、viewer判定を実装しない。readinessはrunnerへ渡す前の静的な
software gateであり、model load、MuJoCo forward / step、hardware accessを行わない。

## manifest contract

`EvaluationManifest`は次のidentityとconfigurationを一つのcondition-level recordへ保持する。

- `schema_version=evaluation-manifest/v3`とcontract version。command semantics selection追加に伴う明示的なversion updateであり、v2以前へ暗黙補完しない。
- repository、software revision、Robot Bundle、Robot Profile、Runtime Plugin、model contract、canonical initial-state contract
- Environment、Control Mapping、Task、Input Source、Evaluatorの`PluginSelection`と各contract version
- selected `command_semantics_route_identity`。これはRobot command semanticではなくrequested
  runtime/controller route identityである。同じInput Source / Mapping / Robot selectionでもruntime
  conversionまたはnative command方式が異なる条件を区別する
- axis-scoped `PluginParameterOwner`とrecursive canonical JSON parameter values
- named initial keyframe、finite initial qpos、initial tip position / frame / unit、WXYZ unit quaternion
- target family / identity / world position、initial-tip-to-target distance identity
- tolerance、dwell、timeout、input source / fixture、normalized input range、gain、deadzone、cadence、
  maximum per-step delta
- requested control frame、condition / task order、deterministic seed
- camera、visual feedback、presentation identity

必須identityは空文字、absolute local path、NULを拒否する。unknown field、missing field、duplicate
evaluator / parameter owner、schema / enum / identity / version error、non-finite number、bool-as-number、
dimension error、invalid quaternion、invalid duration / rangeをfail-closedで拒否する。
`initial_tip_to_target_distance_m`はmanifest内のtipとtargetから再計算した値と一致しなければならない。
plugin parameter valuesは`null`、bool、int、finite float、string、array / tuple、string-keyed objectの
recursive canonical JSON valueに限定する。non-string key、non-finite number、任意のPython object、set、path、
enumのimplicit serializationは拒否し、`PluginParameters`のconstruction時にrecursive detach / freezeする。
`target_tolerance_m < initial_tip_to_target_distance_m`、`dwell_interval_s <= timeout_s`、
`cadence_s <= timeout_s`は意味上のcross-field invariantとしてfail-closedで検証する。
一方、deadzoneがnormalized range全体を無効化するか、maximum per-step deltaとgain / cadenceの
関係は、現行のevaluation design / runtime contractに定義された関係ではないためv1では追加の
数値拘束を設けない。これらは後続のmapping contractが意味をversion化した時点で、そのcontractの
根拠と同時に追加する。

## canonical serializationとdigest

`encode_evaluation_manifest()`はtyped manifestを、UTF-8、`ensure_ascii=false`、sorted object keys、
compact separators、固定array / enum表現、非有限値禁止のcanonical JSON bytesへ変換する。
`decode_evaluation_manifest()`は同じdocumentをstrictにdecodeし、入力mappingやnested listを参照共有
せずtuple / immutable mappingへdetachする。現versionはunknown fieldを保持して再出力する
forward-compatible fallbackを持たない。

`evaluation_manifest_digest()`のalgorithmは明示的な`sha256`で、identityは`sha256:<hex>`とする。
canonical outputにはabsolute local path、branch名、object repr、memory addressなどの非再現情報を
含めない。同じsemantic contentはfield insertion order、process、呼出順序に依存せず同じbytesとdigest
になる。
software revisionは任意の説明文字列ではなく、`git-sha1:<40 lowercase hex>`、`git-sha256:<64 lowercase hex>`、
またはfixture専用の`test-revision:<token>`というexplicit schemeを使用する。actual startup identityは
`SoftwareExecutionIdentity`で受け取り、manifest記載値とexact matchしないreadinessは成立しない。

## requested identityとresolved identity

requested identityはmanifestのcanonical bytesと`manifest_digest`が表す。resolved identityは
`compose_experiment()`がknown-ID registryから解決した次の内容を表す。

- requested plugin selectionとresolved plugin identity
- resolved capability identity
- typed semantic role descriptor
- `EvidenceProducerBinding`とevidence producer identity
- Robot Profile / Runtime Plugin / model contract identity
- versioned canonical initial-state contract identityとverification identity
- actual `SoftwareExecutionIdentity`
- resolved mapping comparison-family / mapping-semantics identityとcontrol frame
- requested / resolved command route identity、Mapping control semantics identity、final Robot command semantic

requested selectionは「何を要求したか」、resolved identityは「startupで何へ解決されたか」であり、
同一視しない。registry lookup、compatibility、required capability、semantic role、evidence producer、
evaluator evidence requirementは既存`compose_experiment()`のfail-closed contractに従う。

## software-only readiness

`build_evaluation_readiness()`は各conditionについて`compose_experiment()`を実行し、runner開始前に
mappingとrequested frame、plugin contract version、axis-scoped parameter ownership、required
capability、semantic role、Robot / Environment / Task compatibility、evidence producer uniqueness、
evaluator evidence、profile / runtime / model identity、named neutral home、qpos dimension / finite値、
tip / orientationのframe・unit・quaternion、target geometry、manifestとactual executionのsoftware identity、
Robot Bundle providerのcanonical initial-state contract（identity、keyframe、qpos、tip、orientation、frame、
unit、quaternion order）、mapping comparison family / semantics identityを検証する。
upper manifestの`initial_tip_position_m`、`target_world_position_m`、`target_tolerance_m`、
`dwell_interval_s`、`timeout_s`をimmutable `EndpointReachTaskContext`へprojectionし、selected Taskの
`TaskExecutionBinding`を構築する。Task parameterへ同じ値は保存せず、このbindingも既存manifest fieldから
決定されるためfreeze materialの第二SoTにはしない。
さらにselected command routeがMapping declarationに存在し、そのcontrol semanticsがMappingの
`mapping_semantics_identity`と一致し、final command semanticのtyped providerをRobot Bundleが持ち、
route strategyから解決したexecution bindingの3 identityがrouteと一致することを
source start、model step、external I/Oより前に検証する。さらに実装済みsemanticではroute strategy、
execution binding、Robot providerのcommand typeがgeneric semantic contractとexact一致することを
検証する。command class名やmodule pathはmanifestへ保存せず、versioned semantic identityを再現条件とする。

成功時だけ`EvaluationReadiness`を返す。resultはcanonical requested manifest identity、resolved
identity tuple、`EvidenceProducerBinding`、initial-state identity、`ReadinessStatus.READY`、
initial-state contract identity、software execution identity、mapping comparison identity、`FreezeRecord`を
immutableに保持し、`task_execution_binding`を#406のrunner boundaryとして公開する。失敗時は
`EvaluationReadinessError`を送出し、partial successの
runner-facing objectを返さない。このgateはmeasured reachability、physics feasibility、task success、
log validityを証明しない。

## world / tool condition-pair

`EvaluationConditionPair`は`world`と`tool`の二条件を一組として検証する。labelとrequested frameは
それぞれ`world/world`、`tool/tool`でなければならず、condition orderは`0`と`1`、task orderは一致する。
Control Mapping Plugin selectionは同一でも別selectionでもよいが、各conditionのresolved control frameを
static declarationまたは明示的なcondition-specific parameterから一意に解決できなければならない。

条件間で許可される差分は次だけである。

- `condition_id`
- `condition_order`
- `requested_control_frame`
- 対応するControl Mapping Plugin selection
- mapping pluginの`ParameterField(condition_specific=True)`として両条件で明示されたparameter value

二条件のControl Mapping Pluginは、同じversioned comparison family identityとmapping semantics identityを
持ち、world側frameは`world`、tool側frameは`tool`でなければならない。family identityだけを流用して
strategyが異なるsemantics identityを宣言する組合せは拒否する。

software revision、Robot / Profile / Runtime Plugin / model、Environment、Task、Evaluator、initial
state、target、input source / fixture、normalized range、gain、deadzone、cadence、maximum delta、
tolerance、dwell、timeout、deterministic seed policy、camera / visual feedback / presentation、task order、
evaluator parameterは完全一致しなければならない。その他の差分は差分field pathを示してfail-closedで
拒否する。pair readinessでは両conditionを個別にcomposeしてから、mapping/frameとcondition-specific
parameter boundaryを検証する。

R7-G canonical fixtureは両条件で同じ`analog_fixture_mapping/v1`を選び、top-level
`control_frame`だけを`world` / `tool`としてupper manifestから明示projectionする。target、tolerance、
dwell、timeout、initial state、input / gain / deadzone / cadenceと他5軸selectionは同一であり、nested
`mapping_config`へframeを重複保持しない。pair validatorはこの許可field以外の差分をfield path付きで拒否する。

`build_r7_g_free_space_manifest_pair()`は+Y 100 mm target 1件だけのsingle-target
execution-candidate smoke fixtureであり、canonical 4-target pilot designを置換または縮約しない。
input列はinitial zero、50個の+Y sample、terminal zeroからなり、cadence 0.02 s、gain 0.1 m/sの
nominal command budgetは`50 * 0.02 * 0.1 = 0.1 m`である。これは
`target distance 0.1 m - tolerance 0.01 m`以上だが、static command budgetにすぎず、MuJoCo measured
reachabilityを証明しない。source EOF後はterminal zeroをholdする。readinessはこのsource factoryを呼ばず、
model load / stepも行わない。

## freeze identityとpackage migration

`FreezeRecord`はcanonical manifest bytes、resolved identity bytes、manifest digest、resolved digest、
freeze digestを保持する。freeze digestはmanifestとresolved identityを`evaluation-freeze/v1`と
明示的に結合して計算するため、manifest value、software revision、plugin version、parameter owner、
evidence producer、semantic role descriptor、profile / model contract、initial-state contract identity、
actual software execution identity、mapping comparison family / semantics identityの変更を
検出できる。
requested fieldは`requested_command_semantics_route_identity`、resolved fieldは
`resolved_command_semantics_route`とし、route identity、control semantics、final Robot command
semanticを混同しない。これらの変更はcanonical requested / resolved materialへ含め、route差分が
manifest digest、resolved identity digest、freeze identityを変更する。

#423のpackage / import-direction migrationは、これらのlogical identityとcanonical bytesを維持する。
source file path、package location、compatibility re-exportだけの変更はfreeze identityを変更しない。
identityを変更する場合は、manifest contract versionまたは対象のversioned identityを明示的に上げる。

## production runner boundary

#406が使用できるpublic APIは、`EvaluationManifest`、`encode_evaluation_manifest()`、
`decode_evaluation_manifest()`、`evaluation_manifest_digest()`、`SoftwareExecutionIdentity`、
`InitialStateContract` / `InitialStateContractProvider`、
`build_evaluation_readiness(manifest, registries, execution_identity=...)`、
`EvaluationReadiness`、`FreezeRecord`、`EvaluationConditionPair`、
`build_evaluation_condition_pair_readiness(pair, registries, execution_identity=...)`、既存の`compose_experiment()`とresolved composition /
provider boundaryである。runnerはready resultとfreeze identityを受け取り、別のimplicit selectionや
default補完を行わない。
runnerは`EvaluationReadiness.task_execution_binding.initial_state()`と
`advance(state, EndpointReachObservation(...))`だけをTask実行境界として使い、terminal classificationを
直接作成しない。transitionが返すterminal / trajectory evidenceをcanonical producer provenanceのまま
後続logへ渡す。

production catalog boundaryは
`runtime/composition/production_experiment.py::PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES`と
`resolve_production_experiment()`、canonical R7-G pair builderは
`runtime/evaluation/r7_g_free_space.py::build_r7_g_free_space_manifest_pair()`である。#406 runnerはこれらを使い、
test-only fixtureまたは`plugins.robots.fast_arm`のconcrete moduleから6軸を再構築しない。

robot selectionが必要なcomposition rootは`xpotato_sim.plugins.robots.catalog`のresolverを使用し、
resolved Bundleから必要なtyped providerをassembly時に取得する。`plugins.robots.fast_arm`のconcrete
moduleと旧compatibility facadeは#406のimport boundaryではない。Bundleはprovider assemblyの境界であり、
runner処理中のservice locatorとして使用しない。

readiness contract自体はrunner、experiment-motion-log/v1のrecord lifecycle、participant / repetition / retry、
physics execution、measured tipの取得、metric集計 / artifact、contact outcomeを実装しない。
`runtime/experiment/world_tool_runner.py`はこのhandoffを受け、freeze再検証後にEnvironment、typed provider、
Input Source、Mapping、command route、MuJoCo、Task observationを接続する。manifest initial tipをmeasured sampleへ
変換せず、reset後のactual qpos / measured tool orientationをfrozen manifestへ照合してからMuJoCo measurementを
elapsed `0.0`で渡す。このactual runtime measurement照合は共通のabsolute numerical tolerance `1e-9`を使用し、
manifest同士または`experiment-motion-log/v1`内部のstatic canonical validationが使用する`1e-12`とは分離する。
#408 artifact再構成も、logに保存されたactual initial qpos / orientationをfrozen manifestへ照合するこのruntime境界だけは
同じ`1e-9`を使用する。manifest revisionとactual `SoftwareExecutionIdentity`は独立入力であり、runnerが同一値から
両方を生成しない。#407のruntime recorderは`FreezeRecord.identity`を
`ConfigurationRecord.configuration_id`へ直接bindし、runner traceと明示protocol contextから既存
`experiment-motion-log/v1` lifecycleを構築する。readiness contractへparticipant、trial、retry、filesystem fieldを
追加しない。metric validity、evaluation artifact、full pilotは引き続き未実装である。
