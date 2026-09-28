---
status: supporting
owner: research
last_verified: 2026-09-28
canonical_for: []
related:
  - docs/contracts/object-scene-contact-diagnostic.md
  - docs/contracts/coordinated-arm-runtime.md
  - docs/design/adr/2026-09-28-scene-dynamics-ownership.md
---

# #582 worldとservo dynamicsのsoftware診断

## 条件と目的

旧fixed geometry診断を維持し、明示gravity、支持面、可動boxを同じRobot/scene経路で扱う。
元FastArmのgeometry、joint ref、mass/inertia、servo gain、actuator torque limitは維持する。
肩間隔290 mmは画像推定、高さ0.7 mは合成値のままで、#581のCAD/実測値確定ではない。
物体は100 mm均質box、mass0.1 kg、表面摩擦0.8/0/0。材料・摩擦は診断用仮定値で実測ではない。

world gravity=(0,0,-9.81) m/s²。drop/fixed-contactは床z=0、pushは無限支持面z=0.41 m。
control=1/60 s、physics=1/600 s、implicitfast、Newton50、tolerance1e-10、elliptic cone。
元actuator gain/force limitを使用し、velocity budget10 rad/s・tracking error budget0.75 radはsimulation診断条件とする。
MuJoCo3.9.0/native、Python3.14環境で検証した。version・model/scene/settings digestとprofile出力を保存して再現する。

## 検証と観測

- center z=0.50 mから自由落下し、2秒後の中心z=0.0498922446 m、支持反力合計は約0.981 N。
- 支持反力は4接触点の和であり、各点約0.245 Nを物体全体の重力と誤比較しない。
- 床なしfreefallは解析式z=z0-gt²/2とv=-gtに対してphysics timestepに応じた誤差境界を検査した。
- 無重力・非zero並進速度、回転した初期poseとworld角速度からのfreejoint速度変換も検査した。
- fixed cubeはworld固定を維持し、native反力と指令/実角差が生じることを確認した。終端サンプルの食い込みを
  診断上5 mm未満と検査するが、全条件・全時刻の非貫通や安全上限を証明したものではない。
- raised support上の可動cubeを左手先で押し、world X方向の移動をnative poseから検出した。
- 原型・左単腕・右単腕・双腕で同じ構築/積分/resetを検査。追加freejointはqpos7/qvel6で、Robot subsetと分離する。
- stale/非finite/片側失敗候補で未公開worldを破棄し、停止後はRobot・物体・時計を凍結する。
- 同じheadのheadlessブラウザでdrop/fixed-contact/pushの描画・数値表示・切断後freezeを確認する。

## 再実行

```powershell
uv run xpotato-sim profile dynamic-cube-drop
uv run pytest tests/integration/fast_arm/test_scene_dynamics.py tests/runtime/test_dynamic_model_publisher.py -q
uv run xpotato-sim app --profile dynamic-cube-drop
```

中立Gamepad待機中は重力積分も開始しない。停止は世界のpauseで、実機の物理停止には読み替えない。
contact observation Taskのcompletedは観測窓終了であり力目標達成や搬送成功ではない。
GUI retry、participant実験、統計比較、ばね機構、実機I/Oは未実施。
