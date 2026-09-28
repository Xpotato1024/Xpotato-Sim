# Contact Observation Task

`contact_observation_task/v1`は、指定物体に対する手先接触を観測する有限診断Task。
固定discoveryは`plugin.py::TASK_PLUGIN`。scene生成、MuJoCo API、接触力計算、Viewer表示は所有しない。

## 入力と終了

`GeometryTaskContext`の解決済みscene、手先ID、epochと、`target_object_ids`、`duration_s`をfreezeする。
Taskは同じscene/frame/timeの`GeometryTaskObservation`を受け、観測した対象pairを保持する。
近接（signed distance > 0）や対象外物体の接触は対象pairへ追加しない。

- RUNNING / observing: 正常な観測期間。
- SUCCESS / completed: 有限観測時間の終了。接触・搬送の成功ではない。無接触でも期間終了する。
- FAILURE / aborted: 入力停止/切断、または実行step budgetの先行終了。
- TECHNICAL_INVALID / invalid: 異なるscene、欠落instance、旧frame、時刻後退、不正なtyped観測。

終端後はadvanceしない。新試行は初期stateと新epochを使い、旧pair/clockを引き継がない。
保存されるevent frameと、凍結後も進むpresentation frameを混同しない。

## 未実装範囲

接触による押し返し、非貫通運動、力評価、物体移動、ばね・搬送判定、参加者実験。
forceは未評価/nullのまま。旧R7-Hのpress/hold Taskと異なり、接触を成功条件にしない。
詳細契約は`docs/contracts/object-scene-contact-diagnostic.md`。
