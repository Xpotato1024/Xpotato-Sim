---
status: canonical
owner: runtime
last_verified: 2026-07-19
canonical_for:
  - fast_arm TOML joint-angle limits and runtime qpos feasibility guard
related:
  - docs/architecture/runtime-composition.md
  - docs/contracts/runtime-input-safety.md
  - docs/reports/implementation/r7-e-p22-neutral-initial-pose.md
---

# fast_arm joint-limit configurationとqpos feasibility

## configurationのsource of truth

`fast_arm_core` packageの`resources/config/joint_limits.toml`がjoint-angle limitの唯一のsource of truthである。
`configs/fast_arm/joint_limits.toml`は互換用のstable logical identifierであり、repository fileではない。
Python 3.11の`tomllib`によるpure parse/validationは`fast_arm_core`が、MuJoCo model照合とruntime guardへの
接続はSelfrionette adapterが所有する。
input source、kinematics、viewer、transport、generic pipeline、MJCFはlimitを
読み込まず、複製もしない。schema versionは`1`で、`robot = "fast_arm"`と
`model = "fast_arm"`の両方を識別し、`angle_unit = "rad"`を必須とし、`status`には
`provisional`または`validated`を記録する。

標準のpre-identification configurationでは、MuJoCo orderで次のjointを必須とする。

`sholder_joint_1`, `sholder_joint_2`, `sholder_joint_3`, `elbow_joint`.

4つの標準値はすべて`lower_rad = -pi`、`upper_rad = pi`、
`status = "provisional"`である。これはphysical identification前の保守的な
software feasibility boundaryであり、authoritativeなmechanical envelopeではない。
physical identification後にTOMLの値とstatusを更新する。motor-spaceまたは
shoulder-coupled feasible regionには別のcontractが必要であり、これらの独立した
rangeから推論しない。

## startup validation

fast_arm runtime pipelineの開始前に、fast_arm production compositionはTOMLをparse・
validateし、load済みMuJoCo modelを確認する。schema version、robot/model identity、
unit、必須joint set、joint order、finite value、`lower_rad < upper_rad`のいずれかが
不正ならstartupは失敗する。modelのjoint nameとorderはTOMLに一致しなければならず、
canonicalなMuJoCo `home` keyframe qposは、設定されたすべてのrange内に
なければならない。fileが欠落または不正な場合、暗黙の`[-pi, pi]` fallbackはない。

## enforcement boundaryとsemantics

generic guard contractは、selected motion policyがcandidate commandを返した後、
`MuJoCoSimulator.apply_joint_position_command()` / `step()`の前に実行する。fast_arm production
compositionは、そのboundaryへfast_arm adapterをinjectする。generic builderと
compatibility builderは、このTOMLを暗黙にloadせず、fast_arm validationも適用しない。
productionのprogrammed、replay、keyboard/gamepad viewer、fixture/loadcell pathは、
すべて同じinjected fast_arm guardを受け取る。

`QposFeasibilityResult.accepted`がruntime accept/rejectのsource of truthである。
command metadataはdiagnosticとcompatibility observabilityのために保持する。
robot-specific metadataはgeneric runtime control-flow contractではない。

guardはlower boundaryとupper boundaryのちょうどの値をacceptする。candidate qposの
1軸以上が設定range外なら、candidate全体をrejectし、個々のaxisをclampせず、
current qposを含むhold commandを適用する。typed `FastArmJointLimitViolation` valueと
compatible command metadataは、joint name、candidate value、lower/upper bound、
`qpos_feasibility_action = "hold_current_qpos"`を公開する。

qpos-limit rejectionは、stale input、control-frame resolution failure、target rejectionと
区別する。ただし、そのstepではtarget feedbackのadvanceを抑止し、active/last-valid
targetとviewer rebase stateを変更しない。MuJoCo physical stateは引き続き
source of truthである。

current Robot Profile / Runtime Plugin / Viewer Profile registryでは、fast_arm pluginがprofile declarationを通じて
同じTOMLをresolveし、既存のgeneric guard boundaryをinjectする。limit valueは
複製しない。mesh collision、self-collision、motor-space limit、
torque/current/velocity safety、hardware characterization、serial、OSC、
viewer config editingは、このcontractの対象外である。
