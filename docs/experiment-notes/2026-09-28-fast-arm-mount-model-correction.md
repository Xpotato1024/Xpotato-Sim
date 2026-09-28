---
status: supporting
owner: research
last_verified: 2026-09-28
canonical_for: []
related:
  - docs/contracts/fast-arm-assembly.md
  - docs/contracts/launch-profile.md
  - research/logs/2026-09.md
---

# 肩取付モデルの訂正条件と幾何検証

## 根拠と旧条件

2026-09-28の利用者提示写真・30 degree取付条件と、原型`arm.xml`を照合した。
旧PR #580 head `7b24a94`はsource原点へのRx(+/-30 degree)だけを使用していた。
原型の取付面法線は+X、肩中心は(0,0,0.7) mであり、旧配置では法線方向を胴体左右へ向けず、
source高さを横方向変位へ混入させていた。旧モデルの描画成功を実機幾何の受入証拠としない。

## 訂正条件

胴体+X前方、+Y左、+Z上。leftは原型にRx(-30)Rz(+90)、rightはY鏡映にRx(+30)Rz(-90)。
肩中心cを(0,+/-0.4,0.7) mへ置き、p_world=R(p_source-(0,0,0.7))+cとする。
cの間隔/高さは合成値であり写真からの測量結果ではない。joint ref/homeと元のassetは不変。
`single_left` / `single_right` / `bimanual`は同じinstance定義を共有する。

## 観測結果と再検証

```powershell
uv run pytest tests/integration/fast_arm/test_fast_arm_model_mounts.py tests/integration/fast_arm/test_fast_arm_assembly_model.py -q
uv run xpotato-sim profile fast-arm-bimanual-gamepad
uv run xpotato-sim app --profile fast-arm-bimanual-gamepad
```

native MuJoCoの取付板法線はleft (0,+sqrt(3)/2,-0.5)、right (0,-sqrt(3)/2,-0.5)。
肩中心は指定cと一致し、homeで上腕は鉛直下向き、tipは左右とも前方に位置する。
home tipはleft (0.2459512147,+0.4,0.22) m、right (0.2459512147,-0.4,0.22) m。
原型単腕は旧canonical sourceのqpos/tipを保持する。単腕と双腕内の対応部品のposeは一致する。
32姿勢の鏡映FK/Jacobian/慣性/重力と、名前address・mesh反射・actuator時間発展の回帰も成功した。

ブラウザでは合成Gamepadにより8関節表示・同時移動・平面切替・切断時fault保持を検証した。
これはsoftware-onlyのモデル訂正記録で、実Gamepad操作受入・実機測定・参加者実験ではない。
再現にはcommit、`profile`出力のconfiguration/model digest、実行ソフトウェアversionを保存する。
旧誤配置のworkspace/到達姿勢と訂正後を同じ実験条件として比較しない。
