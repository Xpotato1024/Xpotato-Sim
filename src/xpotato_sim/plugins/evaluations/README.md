# Evaluation Plugin

## 責務

Evaluation axisはgeneric experiment composition上の評価方法とresult contractを表す。

## 置けるもの / 置けないもの

- 置けるもの: production evaluator、metric unit / frame / provenance、metric固有parameter
- 置けないもの: Task目標、Robot control、source acquisition、viewer diagnosticだけの表示

## contractとI/O

- required contract: [experiment plugin composition](../../../../docs/contracts/experiment-plugin-composition.md)
- input: generic compositionのversion付きselectionとcanonical evidence
- output: status付き`MetricResult`、unit / frame / provenance declaration

## lifecycleとside effect

R7-G evaluatorはTask-owned canonical evidenceからpureかつdeterministicにmetricを導出する。
measured terminal / trajectoryはcross-axis contractで固定したTask producer provenanceを要求し、runner等が
同じshapeのpreclassified evidenceを作成して代用することを拒否する。
trial aggregation、JSON / CSV artifact、condition summary、viewer計算、external side effectは所有しない。
validated v1 logのidentity照合、Task evidence reconstruction、artifact serializationはruntimeの
`evaluation/artifact.py`へ委譲する。
`contact_outcome/v1`は`contact_press_hold_task/v1`のTask-owned terminal / outcome evidenceだけを
strict decodeする。#414のfiltered / clamped reaction-force、viewer診断、Task lifecycle、trial aggregationを
参照または所有しない。

## catalog / discovery / registration

`discovery.py`はpublic direct-child packageの`plugin.py::EVALUATION_PLUGIN`だけをbounded discoveryし、
`catalog.py`がlogical identity順のproduction registryへ投影する。private package、test fixture、arbitrary
dynamic importは対象外である。production runner / UIは未実装である。

## shared private owner

`_endpoint_reach_evidence.py`はendpoint reach evaluatorだけが共有するstrict evidence decoderであり、
private packageとしてdiscoverしない。

## concrete pluginの追加

axis直下へself-contained packageを追加し、`plugin.py::EVALUATION_PLUGIN`、plugin-local README、focused
testsを同じ変更に含める。package basenameとlogical identityを一致させ、missing / unavailable /
invalid evidenceを数値0またはsuccessへ変換しない。

## current concrete plugins

- [`success_within_timeout/v1`](success_within_timeout/README.md): terminal classificationから導くprimary boolean outcome
- [`off_axis_drift/v1`](off_axis_drift/README.md): initial-target axisからの最大world-frame drift（meter）
- [`completion_time/v1`](completion_time/README.md): success時だけ利用可能なdescriptive duration（second）
- [`final_endpoint_error/v1`](final_endpoint_error/README.md): measured final endpointとtarget間のdescriptive world-frame error（meter）
- [`contact_outcome/v1`](contact_outcome/README.md): Task-owned contact press/hold outcome artifactのstrict deterministic projection

## canonical document

- [evaluation readiness](../../../../docs/contracts/evaluation-manifest-readiness.md)
- [experiment plugin composition](../../../../docs/contracts/experiment-plugin-composition.md)
