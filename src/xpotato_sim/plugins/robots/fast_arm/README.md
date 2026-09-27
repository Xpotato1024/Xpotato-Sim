# fast_arm Robot Plugin

## 意味とresponsibility

fast_armをSelfrionetteのRobot Bundleとして接続し、Profile、Runtime Plugin、typed provider、
viewer / resource declarationを一つのregistrationへ束ねる。

canonical declaration: [`ROBOT_PLUGIN`](plugin.py)

## composition role

[`adapter/bundle.py`](adapter/bundle.py)がProfileとRuntime PluginからBundleを構成し、
endpoint pose / command、qpos feasibility、initial state、scene roleと
`joint_position_command` execution providerを提供する。

## parameters

concrete Robot Plugin自体に自由形式parameterはない。logical identity、Profile、capability、
resourceのcurrent値は[`plugin.py`](plugin.py)とadapter declarationを正とする。

## lifecycleとside effect

import / discoveryはhardwareへ接続しない。runtime assembly後にpackage resourceからMuJoCo modelを
loadし、simulatorを構築する。serial、OSC、robot hardwareはopenしない。

## compatibilityとcomposition

Profile、Runtime Plugin、Bundle、registration identityとmodel / joint / resource contractを
fail-closedで照合する。viewer declarationはrendering resourceを宣言するだけで、physical state、
FK / IK、safety decisionを所有しない。

## command semantics route

Bundleはtyped `joint_position_command` execution providerを持つ。Mapping由来のendpoint intentは
runtimeのmotion / safety boundaryでjoint positionへ解決され、native endpoint commandとして
backendへ直接渡されない。

## FastArmの物理出力mapping

`adapter/physical_output.py`はFastArm固有のversion付きjoint mapping、rad-to-degree変換、OSC command semantics、
router observation parserを所有する。profile順、wire順、unit、source tokenは明示設定とし、暗黙defaultを置かない。
per-joint coordinate sign (`-1` / `1`)、angle offsetと`rad` / `degree` unitを必須にし、pure mapping digestへ含める。
変換はcommand座標のsign / zero offsetだけを扱う。`fast-arm-router` revision `8d8c3a6`の4DOF変換は
`(j0-j1, j0+j1, j2, j3)`であり、30 degreeのbase mount geometryは実装していない。
左右の30 degree shoulder mountはassemblyの固定姿勢としてMuJoCoへ反映し、output mapping / router offsetへ重複させない。
profile軸とrouter semantic軸、motor sign、encoder zeroは未確定で、実機用mapping値は#509 / #516 preflightで確認する。
`profile_id`はrobot type、`target_robot_id`はruntime上の出力先logical identityとして別に扱い、plugin / profileと
runtime target / transport / accepted evidenceをそれぞれ照合する。
pure mapping moduleはruntimeやgeneric transportへ依存しない。P5 lifecycle、accepted #509 physical-measurement handoff、
二つのpermissionとoperator gateからtransportまでのcompositionは`runtime.output.fast_arm_adapter`が所有する。
accepted evidenceにはreference / digestだけでなく`FastArmPhysicalEvidenceHandoff/v1`のexact JSON bytesが必要である。
strict parserはUTF-8 BOM、duplicate / unknown / missing field、non-finite value、non-canonical JSONを拒否し、exact byte digestと
`#509` / accepted status / physical-measurement class / profile / target / model / envelope digest / joint別measurement reference / accepted timeを照合する。
これはcontent integrityとidentity consistencyを示すだけで、source authenticityやGitHub stateを証明しない。P6 software-only
dry-run artifactはhandoffとして扱わず、実際のaccepted #509 artifactはfixtureに含めない。

FastArm output sessionはv2 external authorizationを要求するgeneric adapter構成だけを受け付ける。router observationは
correlated commandを示す範囲に限り、Pi / Robot受理、movement、physical stopは証明しない。現taskで使った測定・観測
fixtureはsynthetic testsのみであり、#509の実accepted measurement artifactやhardware validationではない。
`observe_router_datagram`はinjected observation用ingestion境界であり、actual receive socket producerとtimeout tick schedulerは
実装・検証していない。bounded receive wiringと#514 network validationは後続#516 preflight / scopeで具体化する。

## resource contract

physical model / joint-limit definitionを独立`fast_arm_core`が所有し、
`adapter/`がSelfrionette contract、P2のtyped TOML projection、MuJoCo / viewer resource
bindingへ投影する。`adapter/physical_limit_resolution.py`はcoreの
`FastArmJointLimitConfig`を型境界で再検証してからgeneric runtimeのresolutionへ渡し、
genericなDTO、resolver、providerはruntimeが所有する。
これはself-containedな巨大packageでもgeneric third-party installerでもない。

## constraintsとnon-goals

- constraint: model、joint order、endpoint site、resource manifestをregistration時とstartup時に検証する
- non-goal: generic runtime、viewer、Input Source / Mappingの責務をrobot packageへ吸収しない
- non-goal: mapping fixtureやrouter observation parserから実測 / receiver / physical successを主張しない

## tests / validation

- [Robot Runtime conformance](../../../../../docs/operations/robot-runtime-plugin-conformance-tests.md)
- [endpoint motion sanity](../../../../../docs/operations/r7-e-p1-fast-arm-endpoint-motion-sanity.md)

## canonical architecture / contract

- [Robot Profile / Runtime / Viewer contract](../../../../../docs/contracts/robot-profile-runtime-viewer-profile.md)
- [asset contract](../../../../../docs/contracts/assets.md)

## 片腕・双腕の組立API

[FastArm assembly契約](../../../../../docs/contracts/fast-arm-assembly.md)に、core原本からの
原型/鏡映model生成、名前によるjoint/site/actuator address、左右の出力要求bindingを定義する。
`fast_arm_core.assembly` / `assembly_model` は配置とモデルを所有し、
`adapter/assembly_output.py` は全armを既存typed requestへ投影する。
既存単腕v1 profileの8関節化や第二のcatalog登録は行わない。GUI双腕操作・全scene衝突・
連成物理出力の完了とは区別する。原本meshの衝突無効設定と実機校正unknownは保持する。

## 共同位置更新provider

`adapter/coordinated.py` は同じassemblyの名前付き全腕を一つのsnapshotから解き、一括反映する。
明示的な運動学診断経路であり、元のjoint limit/DLSを再利用する。動的接触や実機の安全認定ではない。
[共同実行契約](../../../../../docs/contracts/coordinated-arm-runtime.md) に操作入口、OSC接続と未実装範囲を示す。
