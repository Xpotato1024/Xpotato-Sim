---
status: supporting
owner: architecture
last_verified: 2026-07-30
canonical_for: []
related:
  - docs/contracts/forward-kinematics.md
  - docs/contracts/inverse-kinematics.md
  - docs/contracts/kinematics-command-contract.md
  - docs/operations/robot-runtime-plugin-conformance-tests.md
  - docs/reports/inventories/r7-e-p26-profile-migration-cleanup-inventory.md
---

# generic kinematics test double

generic motion/runtime testは、特定robotのgeometryではなくsolver boundaryを検証する。production Planar solverを
使うと、偶然のlink length、reachable target、formula-specific outputによってtestが通り、generic contractがPlanar
contractに見えるcouplingが生じていた。

## ownershipとcapability

doubleは`tests/support/kinematics_solver_doubles.py`に置き、test suiteが所有する。current FK/IK protocolを
structure上実装し、schema typeだけを使う。可能な範囲でconfigurationをfreezeし、call recordはinspection用の単純な
mutable listとする。

対応するcapability:

- exact qpos call record付きfixed FK endpoint
- exact target/seed call record付きfixed IK `JointCommand`
- FKまたはIK向けに設定した`ValueError` failure
- seed-shape fallbackとcall orderを検証するseed-sensitive IK

doubleは設定済みliteral valueを返す。solver algorithmの再実装、input normalization、MuJoCo load、file discovery、
dynamic importは行わない。

## doubleを使う条件

test対象がmotion generation、solver argument propagation、seed selection、command conversion、endpoint
evaluation、metric、failure conversion、discontinuity handling、metadata、call orderの場合にdoubleを使う。

robot geometry、reachability、numerical solver behavior、robot/plugin conformanceには使わない。これらはrobot-owned
solverとplugin caseで検査する。

## Issue #387 migration

migrationしたgeneric consumer:

- `tests/motion/test_target_to_joint_motion_generator.py`
- `tests/runtime/test_endpoint_metrics.py`
- `tests/runtime/test_kinematic_evaluation.py`

後続#388/#389 cleanupではoffline smokeとlive-loadcell caller coverageをresolved `RobotRuntimePlugin`へ移し、
Planar implementation固有test、production class、package/module exportを削除した。generic testは引き続きtest-only
doubleを使い、fast_arm geometryはsolver testとplugin conformance caseでcoverする。

R6-H completion、stub inventory、concrete solver wiring、R6-I public-surface inventory noteを含むhistorical
implementation recordは変更しない。current FK/IK contractはgeneric Planar baselineではなくrobot-plugin ownershipを
記述し、generic-test ownership文書にはしない。

## handoffとboundary

#388/#389 cleanupで、selected runtime pluginがproduction IK/FK/motion/endpoint/home-seed/feasibility compositionを
所有し、このmoduleはgeneric test doubleだけを所有する境界を固定した。

production sourceは`tests.support`またはこのmoduleをimportしてはならない。doubleは`tests/`配下に留め、
`xpotato_sim.kinematics`、他production package、runtime compositionからexportしない。
