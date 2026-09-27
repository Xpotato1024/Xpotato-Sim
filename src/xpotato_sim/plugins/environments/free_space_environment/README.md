# free_space_environment Environment Plugin

## 意味とresponsibility

`free_space_environment/v1`はRobot所有のMuJoCo base sceneを使用し、task objectやcontact surfaceを
追加しないfree-space評価条件を表す。universal fallbackではない。

canonical declaration: [`ENVIRONMENT_PLUGIN`](plugin.py)

## composition role

scene providerはimmutableなfree-space conditionを返す。target、tolerance、初期状態は上位evaluation
manifestが所有し、このpluginは複製しない。

## parameters

なし。未知parameterはcomposition readinessで拒否する。

## lifecycleとside effect

importと`compose_scene()`はmodel load、MuJoCo step、I/Oを行わない。reset対象はtask objectを持たない
同一conditionであり、Robot base sceneのresetはrunner / Robot providerが所有する。

## compatibilityとcomposition

MuJoCo backendを要求する。Robot ID、Task ID、site / geom / joint名へ依存しない。

## constraintsとnon-goals

- constraint: free-space / no task objectという研究条件をversioned identityで固定する
- non-goal: cube、contact、force、grasp、object transport

## tests / validation

- [Environment plugin test](../../../../../tests/plugins/environments/test_free_space_environment.py)

## canonical architecture / contract

- [runtime composition](../../../../../docs/architecture/runtime-composition.md)
- [experiment plugin composition](../../../../../docs/contracts/experiment-plugin-composition.md)
