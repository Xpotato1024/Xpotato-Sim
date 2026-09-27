---
status: canonical
owner: runtime
last_verified: 2026-09-20
canonical_for:
  - versioned physical output request and permission boundary
related:
  - docs/contracts/kinematics-command-contract.md
  - docs/architecture/data-flow.md
  - docs/architecture/runtime-composition.md
  - docs/operations/hardware-safety.md
---

# Physical output contract

## 目的

この文書はruntime内部のcommandとphysical output requestを分離するversioned contractを
定義する。defaultは`disabled`であり、request構築やmodule importだけではtransportを呼ばない。
#514は明示設定されたruntime adapterからgeneric OSC / UDP datagramを試行できるが、実送信、receiver
受理、robot actuationの実測証拠を与えない。

## Request

`PhysicalOutputRequest`は`physical-output-request/v1`であり、次を必須とする。

| field | 意味 |
|---|---|
| `target_robot_id` | 対象Robotのlogical identity |
| `endpoint_id` | commandを受理するRobot endpoint / joint groupのidentity |
| `command_semantics` | `endpoint_velocity_command/v1`または`joint_position_command/v1` |
| `command` | 既存のtyped `RobotCommand`（`EndpointVelocityCommand`または`JointPositionCommand`） |
| `session_id` | output sessionのlogical identity |
| `sequence` | session内の0始まりで単調に扱うsequence |
| `timestamp_s` | commandと一致するfinite timestamp（秒） |
| `cadence_s` | positiveなrequested cadence（秒） |
| `software_revision` | 実行ソフトウェアの明示的なrevision identity |

`MotionCommand`、dict、任意の未定義commandはphysical output requestへ投影できない。
requestはrequested intentの証拠であり、permission accepted、sent、acknowledgedを
意味しない。command semantics、timestamp、target、session、sequence、cadenceは
request内で照合してから保持する。

target / endpoint registryを所有するcallerは、`evaluate_physical_output_permission`へ
既知のidentity集合を渡してunknown target / endpointをrejectできる。同じくcallerが
monotonicな`now_s`と`max_age_s`を渡す場合、future timestampとstale requestをrejectする。
このvalidationはclock discoveryやregistry lookupを行わず、contextがない場合にも
implicit allowを作らない。

## Permission mode

modeは次のclosed vocabularyだけを受理する。

| mode | 意味 | network / robot side effect |
|---|---|---|
| `disabled` | default。outputを拒否する | なし |
| `dry_run` | requestとOSC previewを検査するmode | なし |
| `transmission_enabled` | explicit operator gate付きのtransport permission | P5 allow、active lifecycle、freshness、target / endpoint / revision / codec identityが一致した場合だけUDPを1回試行 |
| `physical_actuation` | explicit operator gate付きのphysical mode | #514 adapterは受け付けない |

`transmission_enabled`と`physical_actuation`には、`operator_id`とopaqueな
`enable_token_id`の両方を必須とする。`enable_token_id`そのものはsecretではなく、
operator gateのidentityだけを表す。`disabled`はgate identityを保持できない。
default `PhysicalOutputPermission()`は常にdisabledである。

## Evidence state

次のtruth levelを混同しない。

```text
requested -> accepted / rejected -> sent -> acknowledged
```

`PhysicalOutputDecision`はrequestとpermissionに対する`accepted`または`rejected`
だけを保持する。`accepted`はnon-disabled permissionに対するdecisionであり、
`transmission_enabled`または`physical_actuation`ではexplicit operator gateを
伴わなければならない。これはpermission decisionであり、送信実績ではない。
`sent`と`acknowledged`は後続のtrace / transport boundaryで別eventとして記録する。
traceの`permitted` eventも、non-disabled permissionに対する`accepted` decisionと
explicit operator gateを要求し、disabled permissionを成功として記録しない。
`simulated_acceptance`はfake providerの応答でありsocket送信を示さない。`accepted_by_local_socket`は
実UDP providerのlocal socketが返したbyte countだけを示す。どちらもreceiver受理、robot受理、movementを
示さず、receiver ACKは相関できる受信経路がないため`unavailable`である。

## P5 safety binding

`PhysicalOutputSafetyEvaluation`は既存の`runtime.safety.physical_safety_core`を一度評価し、
そのtyped `SafetyInput`、`SafetyDecision`、`candidate_id`を特定のoutput requestへ結合する。
`request_sha256`はcanonical `PhysicalOutputRequest` bytesから計算し、
`safety_input_sha256`はvalidatedなP2/P3/P4 DTOの公開typed contentから計算する。
`binding_sha256`はrequest digest、safety input digest、decision projection、candidate、
`checked_at_s`、Robot、software revision、status / reasonをまとめて識別する。
この結合はupstream safety formulaを複製しない。`physical_output_candidate_id(request)`は
canonical request bytesのversioned SHA-256であり、request identityの照合に使う。
このcaller-visible IDだけではallowを作れない。

`compose_physical_output_safety_input`は、`JointPositionCommand.joint_angles_rad`を
Robot-owned joint順序のtarget configurationとして解決する。P3の
`evaluate_mujoco_collision_configuration`が実際にforward・観測したqpos / qvelとjoint名を
保持している場合だけ、そのqposとrequest targetを完全一致で照合し、同じconfigurationをP4へ渡す。
qvelは観測した値を保持し、ゼロや有限差分を捏造しない。Jacobianとphysical limitsのevidence契約は維持する。

P3/P4 resultの`evaluated_candidate`は、公開constructorの任意IDではなく、P3 observation producer /
P4 evaluatorのowner-local originに保持した値から得る。output gateは両resultのjoint順序・qpos・qvel・
sample時刻を照合し、さらにrequestのtarget qposと照合する。result再構築でP3 observation originを
引き継げず、candidate Aの結果のcaller-visible IDをrequest Bへ合わせてもnon-sendableとなる。
`SafetyInput.candidate_id`と`SafetyDecision.candidate_id`のrequest一致、既存のrobot / revision検証も維持する。

同じ実評価configurationはP2の`LimitResolutionResult.expected_joint_names`ともcanonical順序で一致し、
`resolved_authoritative`な各joint position boundへ直接照合する。candidate qposはlower / upperを含む範囲内だけを
allow候補とし、1 jointでも範囲外なら`limit:limit_candidate_out_of_bounds`としてrejectする。境界内判定は
P2 ownerのcanonical helperを使い、output layerでrange / conversion / authority formulaを複製しない。
provisional / unknown / unavailable / mismatchなP2 resultは従来どおりnon-allowであり、この照合でauthorityへ昇格しない。

この経路はconfiguration-only評価であり、目標までの移動軌道・実機motionの安全性を証明しない。
`endpoint_velocity_command/v1`にはphysical requestから評価軌道へのcanonical resolverがないため、
`physical_safety_candidate_semantics_unresolved`としてnon-sendableにする。任意のbounded trajectoryも
単一joint targetから補間してallowしない。P4の実評価sample列は保持するが、outputに必要なresolverが
ない経路はfail-closedである。新しいplanner、#516、hardware observationは追加しない。

SafetyInput中のP2 `limit_resolution.robot_id`とP3 `collision.context.robot_id`は一致し、
requestの`target_robot_id`とも一致しなければならない。requestの`software_revision`に対応する
`software_revision:<id>` provenance tokenをSafetyInputとSafetyDecisionの両方で照合する。
identity不一致、revision不一致、missing / invalid safety evidenceはallowへ昇格しない。

P5の`allow`だけが`PhysicalOutputSendableRequest`を生成できる。`hold`と`unavailable`は
lifecycleを`hold`へ移し、`reject`はrequestを拒否し、`stop`はbounded stopへ移り、`invalid`は
terminalな`aborted`へ移す。非allow、staleなdecision、identity不一致、raw intentのsubmitでは、
直前のlatest requestとsendable wrapperを消去する。重複・逆順sequenceの拒否は既存sendable stateを
置き換えない。

Lifecycle submitはcallerの`now_s`と別々の`max_age_s` / `max_safety_age_s`を受け取り、requestと
safety decisionの時刻を個別に検査する。freshness contextが欠落・不正、またはdecisionがfuture / staleの
場合は受理せず、reasonとgate evidenceを記録する。operator permissionとsafety allowは独立したgateであり、
どちらか一方が他方を代用しない。

## Recording / dry-run trace

`PhysicalOutputRecordingSink`はnetworkやRobot providerを持たないrecording-only sinkであり、
`requested`、`permitted`、`rejected`、`dropped` eventを同じrequest bytes、permission bytes、
target / session / sequence / timestamp / cadence identityへbindする。`permitted`は
permission decisionのacceptedを表すだけで、`sent`または`acknowledged`ではない。

`PhysicalOutputTrace`は`physical-output-trace/v1`のstrict deterministic JSONL artifactである。
各lineのevent sequenceは0から連続し、session内request sequenceは増加順でなければならない。
requested predecessorのないevent、duplicate / late / out-of-order event、unknown / missing /
duplicate field、request / permission bytesとの不一致をrejectする。atomic write後にbytesと
decoded semanticをstrict read-backし、`replay_physical_output_trace`はsinkへ再生してbyte
equivalenceを確認する。複数writerからのsequence採番、validation、appendはsink内で直列化
する。lifecycle trace sinkへ渡せるeventはtyped `PhysicalOutputLifecycleEvent`に限り、
任意のserializable objectを証拠として受け入れない。trace replayはdry-runであり、transportを
実行しない。

## Lifecycle / bounded stop

`PhysicalOutputLifecycle`は`disabled`、`armed`、`active`、`hold`、`stopping`、`stopped`、
`aborted`、`failed`をclosed stateとして管理する。defaultは`disabled`であり、明示的な
permission付き`arm`だけが`armed`へ遷移する。`reconnect`は観測eventを記録するだけで、
自動re-armや過去requestの再送を行わない。

source stale / disconnectはactive requestを破棄して`hold`へ入り、source invalidは`aborted`
へ入る。requestはsession identityと単調増加sequence、caller-providedなfreshness policyと
現在時刻を必須で照合し、contextがない場合もacceptせず`hold`またはrejectとして記録する。
duplicate / late / stale requestもrejectする。最新request stateはtrace artifactとは別に保持し、
hold / stop / abort / failure時に再利用しない。

operator stopとruntime shutdownは`stopping`へ遷移し、明示されたdeadline内の
`complete_stop`だけが`stopped`を確定する。stopはidempotentで、deadline超過は`failed`となる。
既に`aborted`または`failed`のprimary stateへcleanup failureを記録しても、primary stateを
上書きしない。cleanup後の実測monotonic elapsedをdeadline判定へ使い、計算されたdeadlineが
finiteでない場合も`failed`とする。terminal stateだけでなく`hold`からの再-armにも新しい未使用
session identityと明示permissionが必要であり、session IDをlifetime内で再利用しない。
public transitionは一つのreducer lockで直列化し、event sinkの失敗はlifecycleをfail-closedにする。
各transitionのtimestampは有限値であることを状態、permission、session、sequenceのmutation前に
検証する。`complete_stop`はstop開始時刻より前のtimestampを拒否し、停止状態とtraceを変更しない。
新規lifecycle eventは`physical-output-lifecycle/v2`でP5のstatus / reason、action、candidate、
robot / revision、checked-at、provenance、request / safety-input / decision / binding digestsを保存する。readerは既存のv1
eventも受理し、新規v2の`request_accepted`にはsafety evidenceを必須とする。transport dispatchは同じreducer lockで
latest sendable wrapper、identity、freshness、permission、sequence、cadenceを再検査し、1回だけclaimする。
bounded provider callの途中でstopは割り込まず、in-flight datagramを取り消せるとは保証しない。UDP providerは
設定timeoutを使い、各datagram後にsocketを閉じ、自動retryを行わない。

## #514 generic OSC / UDP transport

`runtime.output.transport_adapter`はP5 allow-only wrapper、active lifecycle、permissionとgeneric
`transport/`をつなぐ。strictなconfig v1 / v2はtarget robot、software revision、endpoint、mode、
freshness / cadence、`expected_codec_identity`を保持する。codec identityはversion付きIDとimmutable
settingsのcontent digestから作り、adapterはencoder identityとの完全一致を要求する。v2は明示的な
`external_authorization_required` fieldを持つ。v1は既存generic encoderとの互換用に維持し、FastArm codecには使えない。

pure `PhysicalOutputWireEncoder`はvalidated requestを含むtyped logical envelopeからOSC semanticsだけを返す。
共通の`encode_osc_message`がdatagram bytesを一度生成し、`PhysicalOutputEncodedDatagram`がlogical envelope、
codec ID / version / immutable settings digest、およびそれらから導く`identity_sha256`、OSC semantics、実byte列と
SHA-256を束ねる。attemptと`PhysicalOutputTransportRecordingSink`にも同じtyped値を渡す。providerはそのbyte列を
変更しない。generic defaultは`physical-output-wire/v1`でcanonical request bytes、
candidate、request / safety binding、target、endpoint、revision、session、sequence、attempt identityを含める。

`disabled`は処理を止め、`dry_run`はlocal previewを返し、`recording`は明示されたlocal-only sinkへencoded
datagram evidenceを渡す。これらはDNS、socket、network callをしない。`transmission_enabled`だけが、permissionと
全identity / freshnessが一致した後に1回のUDP attemptを行う。generic layerはrobot固有joint order、unit変換、
calibration、receiver mappingを持たない。送信attempt、simulated / local socket result、receiver ACKは別のevidence
levelとして扱い、ACKは`unavailable`のままとする。

v2でexternal authorizationを要求するencoderは、runtime compositionが発行した使い切りgrantなしにsendできない。
grantはrequest、P5 binding / candidate、codec / config、target、endpoint、revision、session、sequence、二つの
permission identity、authorization context、expiryへ結び付き、guarded send時に一度だけ消費する。adapterは
encoderが宣言するgeneric required-authorization capabilityをconstructorで照合する。robot IDやmapping semanticsを
generic transportへ埋め込まない。

## #515 FastArm固有の構成とoperator gate

`runtime.output.fast_arm_adapter.FastArmPhysicalOutputSession`がFastArm固有のaccepted physical evidence、Robot
Profile、P5 lifecycle、二つのpermission、operator enable、generic transportを結ぶ。mapping / wire semanticsの
ownerは`plugins.robots.fast_arm.adapter.physical_output`であり、profile joint orderとwire joint orderの完全な対応、
jointごとのcoordinate sign (`-1` / `1`)、offset値と`rad` / `degree` offset unitをversion付きmappingの必須fieldとし、
digestへ含める。request radにsignを適用し、明示unitのoffsetを加えてwire degreeへ純粋変換する。これはrouter側の
motor calibrationやshoulder-mount補正を複製しない。profile / router軸とzero基準は未確定のためmapping値を推測せず、
実際の対応選択は#509 / #516 preflightへ残す。joint command、router observation parser、endpoint、revision、cadenceも
明示してbindingする。

`profile_id`はrobot typeを識別し、`target_robot_id`は物理出力先のlogical identityを識別する。sessionは
`runtime_plugin_id == profile.profile_id`と`runtime_robot_id == target_robot_id == transport_config.target_robot_id`を
要求し、accepted evidence / envelope / P2 resolution / collisionもtarget identityへ一致させる。

sessionはtarget、resolved Profile / model contract、mapping、endpoint、software revision、session、cadence、
P5 candidate / safety evidence、#509 envelope provenanceを毎requestで一致させる。各jointのlimitはaccepted #509
physical-measurement source referenceと一致しなければならない。`physical_actuation` permissionと
`transmission_enabled` permissionは別々に必要で、両方のidentityを含むfresh authorization grantがgeneric
adapterへ渡される。FastArm public wire encoderはrequired-authorization capabilityを宣言し、generic adapterは
v1またはexternal authorizationなしのv2 configとのcompositionを拒否する。

FastArmのaccepted evidenceは、referenceとdigestだけでは受け付けず、callerが渡す
`FastArmPhysicalEvidenceHandoff/v1`のexact JSON bytesを必須とする。文書は`#509`、`accepted`、
`physical_measurement`、schema version、acceptance reference、target、profile / model contract、accepted envelope
SHA-256、jointごとのmeasurement reference、accepted timestampを束ねる。UTF-8 BOM、duplicate / unknown / missing
field、non-finite number、non-canonical JSONを拒否し、exact byte列のSHA-256と文書内の全identity / referenceをtyped
evidenceへ照合する。typed envelopeのrobot idはtargetへ、model idはresolved Profileのmodel contractへ一致させる。

このdigestとbindingはcontent integrityとidentity consistencyを確認するもので、source authenticity、署名、GitHub Issueの
state、またはcaller自身が意図的にfabricateした整合文書の真正性を証明しない。P6のsoftware-only dry-run artifactは
このhandoff schemaとして受理されない。自動testsのhandoff bytesはsynthetic fixtureであり、実際の#509 accepted physical
measurement artifactは取得していない。

local socket receipt後はcorrelated router observationを待つ。synthetic senderでも同じpending、correlation、timeout、
stop state transitionを検証できるが、simulated observationはpendingを解除するだけでstatusは`unavailable`のままとし、
`router_command_observed`へ昇格しない。実senderの一致する観測はrouterがcommandを処理したことだけを示し、Pi / Robot
受理、physical movement、physical stopは示さない。malformed / mismatched observationはACKとして扱わずpendingを保ち、
追加requestをblockする。timeoutまたはinvalid clockはlocal sessionをfail-closedにしてsend可能状態をclearする。
operator stop / abort / disconnectもlocal lifecycleを停止するだけで、physical robot stopの証拠ではない。

`observe_router_datagram`は受信済みdatagramをsessionへ渡すingestion境界である。#542はnonblocking受信callbackを結ぶ有限driverとcaller-driven expiryを追加するが、
actual receive socket/listenerとschedulerは所有しない。automated testsはfake datagramとinjected clockだけを使う。
実際のreceive wiringと#514 network validationは#516 preflight / scopeに残す。

このtaskのaccepted evidence fixtureとrouter observationはsynthetic test dataだけであり、#509の実物理測定record、
実DNS / UDP送信、receiver ACK、robot actuation、movement、安全性の確認ではない。FastArm output利用は#509のaccepted
physical-measurement handoffと専用hardware safety gateが成立するまで有効化しない。

## Serialization / failure

requestとpermissionはUTF-8 without BOMのsorted-key compact JSONへ deterministicに
serializeし、decode時にunknown field、missing field、duplicate key、non-finite値、
型不一致、identity不一致をrejectする。failure時にzero、success、implicit fallbackへ
変換しない。

## Ownership / safety

- `schemas.command`がshared request、permission、decision、serialization shapeを所有する。
- `runtime.output.permission`がpermission decisionを所有し、`runtime.output.safety_gate`がP5 safety
  evaluationとrequest binding、allow-only sendable wrapperを所有する。`runtime.output.trace`がrecording /
  dry-run request trace、artifact、replayを所有し、`runtime.output.lifecycle`がstate、bounded stop、
  safety-aware lifecycle traceを所有する。`runtime.output.transport_adapter`はgeneric P5/lifecycle/permission/
  config identityをtransportへ合成し、`runtime.output.fast_arm_adapter`はFastArm固有のaccepted physical evidence、
  mapping、二重permission、operator gateを合成する。plugin側mapping / wire encoderはgeneric transportへ依存しない。
- `transport/`がgeneric OSC encoding、endpoint設定、UDP providerを所有し、runtimeやrobot固有mappingをimportしない。
- testsはfake sender / fake socketを使い、DNS、実socket、network、serial、Arduino、robot outputは実行しない。
- 実機作動は`docs/operations/hardware-safety.md`と専用Issue / 明示許可の範囲に限る。

Runtime設定は`EvaluatedJointRoute(endpoint_id, joint_names)`で、既存endpoint設定とRobot-ownedの全joint順序を明示的に結ぶ。P3 producerはこのrouteのjoint名を実MuJoCo joint addressへ解決して観測し、routeもoriginへ保持する。P4は同じrouteをConfigurationState / TrajectorySampleの評価入力として保持し、policyのjoint順序との一致を要求する。output gateはrequest endpointも照合するため、同じqpos数値の別endpointへIDだけ付け替えても拒否する。routeはruntimeの構成情報であり、requestから任意の別joint groupを推測するresolverではない。FastArmでは既存endpoint設定とProfileのcanonical joint orderを使用し、route不明のgroupは評価しない。

## #542 no-I/O signal previewと有限応答driver

`runtime.output.fast_arm_emulation`は、実機用sessionとは別のno-I/O境界である。
`build_fast_arm_signal_preview`はtyped requestと明示mappingから、既存pure joint変換とOSC codecで
wire bytesを生成する。結果は常にsyntheticであり、sendable wrapper・permission・grantではない。
`FastArmSignalSession`は明示Robot Profile/mappingを検証し、最初の完全requestのtarget、endpoint、
session、revision、cadenceを固定する。単調sequenceとrequest timestampを検査し、pending中の追加preview、
重複sequence、終了後の再利用を拒否する。request timestampと受信側clockの絶対値は直接比較しない。
実機のjoint sign/offsetを推測してdefault値を作らず、利用者が渡したmappingを試験条件として保持する。

`emulate_fast_arm_peer`はOSC bytesと明示target/wire joint orderだけを入力に取り、受信したaddress、
float32 typetag、joint数を検証する。expected commandを入力としてコピーせず、decodeした値から
`/router/<target>/command`の3-string応答を生成する。これはrepo-owned wire contractの疑似peerであり、
実配備router/controllerの互換性、認証、motor calibration、実機受理を確認したことにはならない。

`runtime.output.fast_arm_observation`は既存ACK DTOとpendingの共通定義、codec/parser/correlation/expiryの
副作用なし判定を所有する。`fast_arm_adapter`からのACK DTO importは互換aliasとして維持する。
既存physical sessionもこの判定を使うが、permission、#509 evidence、P5、grant、transport lifecycleは
従来のownerから移さない。preview側はevidence kindをsimulatedに固定し、相関成功でもstatusはunavailable。
不正packetとidentity不一致はpendingを解除せず、deadlineちょうどまたは超過はtimeoutとなる。
有効packetだけでなくmalformed packet到着時も、期限超過が優先される。

`BoundedFastArmObservationDriver`はexplicitなnonblocking `receive_nowait`、clock、正整数`max_datagrams`を
受ける。callerが`tick()`を呼び、tick開始、read復帰後、tick終了でexpiryを確認する。
Noneは現時点の無受信、空bytesはmalformed packetとして区別する。1 tickで上限以上をreadしない。
無受信・stormでも次tickで期限を確認し、受信OSErrorはdisconnect、不正/逆行clockはfailedにする。
その他callback例外もpendingを無効化し、元の例外を再送出する。後始末も失敗した場合は元の例外へ注記し、driverを閉じたままにする。stop/disconnect後は追加readしない。
callback自体のblockingは中断できず、wall-clock deadlineやOS/driver応答性を保証する機構ではない。
thread、timer、auto retry、socket listenerは持たない。actual receive wiringは#516のoperator gate下に残す。

この段階はprogrammaticなprotocol E2Eである。Input Source、Task/contact、trace/artifactをまとめる
実験runnerは#543に残る。previewや疑似受信だけをphysical stop、実測ACK、検証環境全体の完成と呼ばない。

## #551 入力runtimeとphysical sessionの有限接続

`runtime.runners.fast_arm_input_runtime.FastArmInputRuntime`は既存のresolved input planと
新しいdisarmed `FastArmPhysicalOutputSession`を専有する。session生成を複製せず、accepted evidence、
Robot Profile、mapping、transport configの検証は既存constructorへ委譲する。
constructorはSource開始、arm、socket、送信を行わない。plan/Source/Mapping/route/Profileと明示revision、
正整数のtick上限/受信packet上限を検査する。未知の実機設定を埋めるdefaultやtest-only acceptance flagはない。

### 時刻と出力証拠

callerはSourceのreceipt clock、runtime、session、transportを同じhost monotonic基準で構成する。
装置timestampはraw frameとsource commandに保持し、absolute値をhost timeと比較しない。
physical requestは生成時のhost timestampを持つ別JointPositionCommandを作り、qposは変えない。
request発行、P5確認、実dispatch開始の時刻を混ぜず、次のcadenceは実dispatch開始から測る。
MuJoCo snapshotはlocal command生成用simulationで、physical stateの測定値ではない。

P5用のSafetyInputはcallerの明示producerから受ける。ownerが既存evaluate_and_bindを呼び、
exact request/candidate/evidenceを既存sessionへsubmitする。未取得の根拠からallowを作らない。
試験の正常系は既存test-only evidenceとin-memory senderで検証し、productionへimportしない。

FastArmRuntimeTickはsource frame/health、simulation before/after、source command、host request、
P5 evaluation、physical-session result、受信driver resultを別fieldに保持する。
P5が拒否してもlocal simulationが候補生成のため進んだ場合がある。simulationのstepを実機dispatchと数えない。
ownerはlast_tickだけ保持し、履歴の保存はcallerが既存recording機構へ渡す。新しいartifact schemaは設けない。

### lifecycle

`start(physical_permission, transmission_permission)`は既存の二重permission gateを通し、受理後にだけreaderを開始する。
reader startが途中で失敗してもlocal authorizationを撤回してcloseする。自動arm/rearm/reconnectはしない。

各tickは受信/expiryを先に処理する。pendingとcadence待ちの間は入力を消費せず、追加commandを生成しない。
pending/cadence待ちでstaleを検出した場合もstopする。最後のbudget tickは応答検査とcloseに使い、新規dispatchしない。
新しい入力のread時は、frameに明示されたsource subtypeとtyped healthのsource subtypeを照合する。
P5 callback後とtransport prepare後にもhost clock、source health、同一commandのsource subtypeを再検査し、
age不明・期限超過・identity変化をfresh扱いしない。
inactive/stale、local hold/reject、P5 non-allow、dispatch不受理で閉じる。正常ゼロ入力は新しい有効sampleとして処理する。
取得/評価/clock等の例外ではabortし、原例外を伝播する。cleanupも失敗した場合は原例外へ注記する。
既存sessionがfailedになった場合は、そのACK/timeout理由をstopによって上書きしない。

stop/abortは同じownerから明示実行する。stop後の再start/tickは拒否する。
同一threadの専有運用を前提にし、tick再入やactiveなSource/Mapping/route/出力identityの差替えを拒否する。
callback中にstopされた場合も、そのtickを続けて新規dispatchしない。

### 呼出側に残る責務

ownerはschedulerではない。callerがtickしなければ期限を検査できず、blocking callbackを中断もしない。
finite tick budgetはwall-clock上限やhard real-time watchdogではない。
receiver callbackはnonblockingとし、callerは例外を含む終了経路でstopを呼ぶ。
実送信するcallerは既存hardware authorization、配備receiver、#509の実測証拠、校正、
clearance/stop/rollbackを別途満たす。local stopは許可撤回であり、実機停止packetや実機停止の証明ではない。
本段階ではactual socket receiver/bind、hardware CLI、独自thread、汎用schedulerを追加しない。

viewer bridgeのstart/closeは既存のno-op契約を維持する。ownerのcloseはブラウザやWebSocket取得の停止を意味しない。
close呼出しと出力ownerのterminal性を検証し、外側の取得停止はその取得ownerへ委譲する。

### prepare復帰時の追加拒否境界

FastArmPhysicalOutputSession.submitのoptional `pre_dispatch_check`は、送信先prepareから戻った後、
既存のgeneration/grant/permission最終照合の前に実行する。結果は厳密なTrueだけを通し、それ以外や例外で
許可を撤回する。callback自体は既存gateを許可に変更できず、既存callerは未指定で従来動作を保つ。
FastArmInputRuntimeはclock・source age・構成・終了状態を確認するcallbackを必ず渡す。
開始済みのsendを中断する保証はなく、callerのbounded transport contractを維持する。

## FastArm assemblyの左右対応

[片腕/双腕assembly契約](fast-arm-assembly.md)のnamed request batchは、全armの
target/endpoint/個別mappingを必須とし、既存のrequest・wire codecへ投影する。
片側欠落やOSC target重複を拒否する。batchはrequested-levelであり、既存P5評価、
#509 evidence、二重permission、transport grantを置き換えるものではない。
左右の名前対応・codec・応答試験と、連成preflight/部分失敗/協調停止を含む実機用runtimeは
別の受入範囲である。独立した2 sessionを、原子的送信・実機同時動作と表現しない。

## 複数腕の準備・送信・停止

共同runtimeの新しい入口は [共同実行契約](coordinated-arm-runtime.md) を参照する。
既存単腕submitのgateを維持し、全側prepare後の逐次dispatchを監督する。
一側拒否・部分送信・timeoutで両側をlatchし停止要求を試行するが、実機停止確認ではない。
受信側watchdog/停止命令/独立非常停止が未検証のまま、このsoftware testから実機を有効化しない。
