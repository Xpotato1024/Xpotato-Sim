# endpoint_reach_task Task Plugin

## 意味とresponsibility

`endpoint_reach_task/v1`はmeasured Robot endpointを固定targetへ到達させるTask lifecycleを表す。
Taskはterminal classificationを所有し、metric集計は所有しない。

canonical declaration: [`TASK_PLUGIN`](plugin.py)

## composition role

`endpoint_pose/v1`、`reset_initial_state/v1`、`robot.tool_endpoint` roleを要求し、
`endpoint_reach_terminal_classification/v1`と`endpoint_reach_measured_trajectory/v1`をcanonical
evidenceとして宣言する。

## parameters

なし。target、tolerance、dwell、timeout、initial stateは上位evaluation manifestが唯一のcondition
ownerである。readinessがこれらをimmutable `EndpointReachTaskContext`へ一度だけprojectionする。

## lifecycleとside effect

`EvaluationReadiness.task_execution_binding`が`EndpointReachObservation`を受け取り、measured world-frame
endpoint、elapsed time、measurement status、held / rejected / stale / technical statusからpure transitionを
生成する。Taskは連続dwell開始時刻をstateに保持し、tolerance外へ出た時点でdwellをresetする。
最初のobservationはMuJoCoから取得したelapsed 0のendpointである。frozen initial positionとの比較では、
現行canonical initial-tip referenceの小数6桁表現とMuJoCo full-precision measurementの差を吸収するため、
`1e-6 m`以下の数値表現差だけを許容する。このsoftware toleranceは物理的なreset許容差ではなく、
それを超える差は`technical_invalid`とする。trajectory evidenceの`initial_position_world_m`には
manifest値ではなく最初のmeasured endpointを使用し、manifest値をmeasured sampleとして自動挿入しない。

- tolerance外かつtimeout前: `running`
- tolerance内でrequired dwell完了かつ`elapsed_time_s <= timeout_s`: `success`
- successせずtimeout到達、またはheld / rejected / stale: `failure`
- measurement unavailable / invalid、reset、non-monotonic stream、technical invalid: `technical_invalid`

transitionはTask-owned `endpoint_reach_terminal_classification/v1`と
`endpoint_reach_measured_trajectory/v1`を一意なprovenanceで返す。runnerはclassificationを作らず、
この結果を#407のlog boundaryへserializeできる。Robot command、MuJoCo step、metric集計、artifact出力は
Task pluginの責務ではない。

## compatibilityとcomposition

MuJoCo backendとmeter単位のRobot endpoint roleへ依存する。fast_arm固有ID、site、joint、solverは参照しない。

## constraintsとnon-goals

- constraint: missing / unavailable / invalid measurementをsuccessへ変換しない
- non-goal: contact、force、grasp、metric aggregation、JSON / CSV export

## tests / validation

- [Task plugin test](../../../../../tests/plugins/tasks/test_endpoint_reach_task.py)
- [Measured-origin regression test](../../../../../tests/plugins/tasks/test_endpoint_reach_task_measured_origin.py)
- [Production MuJoCo handoff test](../../../../../tests/runtime/test_r7_g_measured_initial_sample.py)

## canonical architecture / contract

- [evaluation design](../../../../../docs/evaluation/world-tool-frame-comparison-design.md)
- [experiment plugin composition](../../../../../docs/contracts/experiment-plugin-composition.md)
