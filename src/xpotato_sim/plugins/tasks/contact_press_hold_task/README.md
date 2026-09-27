# contact_press_hold_task Task Plugin

## 意味とresponsibility

`contact_press_hold_task/v1`は、#413がMuJoCoから測定したraw contact evidenceだけを入力に、
cubeへのapproach、first contact、press、hold、success / failure / technical-invalidを
決定的に遷移させるTask lifecycleである。contact geometry、force filtering、reaction-force推定、
Robot command、viewer表示は所有しない。

canonical declaration: [`TASK_PLUGIN`](plugin.py)

## composition role

`reset_initial_state/v1`と`robot.tool_endpoint` roleを要求し、Task-owned
`contact_press_hold_terminal/v1`および`contact_press_hold_outcome/v1`をcanonical evidenceとして
生成する。入力の`ContactEvidence`は`mujoco_contact_measurement/v1` provenanceとmanifest / scene /
object identityを厳密に照合する。

## contextとlifecycle

`ContactTaskContext`はmanifestのtarget face、object-frame normal、world-frame approach direction、
penetration bandへ、明示されたdwell / timeout、任意のnormal-force band、pose / alignment / drift
gate、trial / repetition / attempt identityを一度だけbindする。

- `ready`から有効なno-contact observationで`approach`へ進む。
- 最初のtarget contactを`first_contact`として記録し、target band外は`press`、band内の連続dwellを`hold`とする。
- target band、任意のforce / alignment / drift gate、連続dwell、timeoutをすべて満たした場合だけ`success`とする。
- timeout、held / rejected / stale / operator timeoutは`failure`とし、completion timeは生成しない。
- measurement unavailable、invalid contact、solver invalid、reset failure、manifest identity drift、
  非単調時刻は`technical_invalid`とする。
- contact lossはdwellをresetしてcounterへ記録し、再接触は`recontact_count`へ記録する。

Task outcome artifactにはfirst-contact time、peak normal force、penetration overshoot、steady-state
error、force variability、tangential-force / slip proxy、final pose、contact-location drift、normal
alignmentを含める。さらにmanifest-bound penetration bandと、bind済みのdwell / timeout、normal-force /
alignment / drift gate、pose-measurement requirementをcanonical artifactへ保存する。未観測値は`null`のままとし、
failureへcompletion timeやforceを補完しない。各raw observationはtechnical-invalidへ遷移する場合もstate / replay
順序へ保持し、sample / simulation timeの後退・同値stale、または`measured` evidenceのrecord / aggregate不整合を
successへ流さない。

## runner / retry

`ContactTaskRunner`は事前取得済みのraw observation sequenceを使うsoftware-only fixture / replayであり、
MuJoCoをstepせず、Robot outputも行わない。retryは`technical_invalid`だけに限定したbounded policyで、
元trialを捨てずにretry attemptをdistinct identityと`retry_of_trial_id`で保持する。operator failureは
自動retryしない。`canonical_bytes()`と`derive_contact_outcome()`は同じvalid logから同じsummaryを再生成する。

## parameters / non-goals

plugin parameterはない。task条件は上位contextがownerである。

- non-goal: #414 filtered / clamped reaction force、grasp、object transport、多物体、deformable object、
  participant、force output、hardware、R7-G free-space outcomeの再利用

## tests / validation

- [Contact press/hold Task test](../../../../../tests/plugins/tasks/test_contact_press_hold_task.py)
- [Contact outcome Evaluation test](../../../../../tests/plugins/evaluations/test_contact_outcome.py)
- [Contact evidence contract](../../../../../tests/runtime/test_contact_evidence.py)

## canonical architecture / contract

- [contact task manifest](../../../../../docs/contracts/contact-task-manifest.md)
- [experiment plugin composition](../../../../../docs/contracts/experiment-plugin-composition.md)
- [runtime composition](../../../../../docs/architecture/runtime-composition.md)
