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

## 初回訂正条件（v2・傾斜方向は再訂正前）

胴体+X前方、+Y左、+Z上。leftは原型にRx(-30)Rz(+90)、rightはY鏡映にRx(+30)Rz(-90)。
肩中心cを(0,+/-0.4,0.7) mへ置き、p_world=R(p_source-(0,0,0.7))+cとする。
cの間隔/高さは合成値であり写真からの測量結果ではない。joint ref/homeと元のassetは不変。
`single_left` / `single_right` / `bimanual`は同じinstance定義を共有する。

## 初回訂正時の観測結果（現在の取付条件の証拠としては使わない）

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


## 再訂正：取付板の傾斜方向と共通床（v3）

利用者の「傾きが上向きで実物写真と逆」という指摘を受け、板の傾きと法線の向きを再照合した。
初回訂正の下向き法線は取付板を外上がりにしていた。正しい条件を上端内側・下端外側として固定し、
leftはRx(+30)Rz(+90)、rightはRx(-30)Rz(-90)へ変更した。肩中心の固定と原型/鏡映対応は維持する。
法線はleft(0,+sqrt(3)/2,+0.5)、right(0,-sqrt(3)/2,+0.5)。上端と下端の位置関係を別assertで検査する。
joint homeは不変で、home tipはleft(0.2459512147,+0.8156921938,0.46) m、right(0.2459512147,-0.8156921938,0.46) m。
肩位置は合成値であり、写真の関節姿勢に合わせるためのzero補正は行っていない。

床の欠落原因は新モデルfactoryがarm.xmlだけを構築し、旧scene.xmlを合成していなかったこと。
旧単腕のscene.xmlを再利用し、生成assemblyをincludeする共通factoryへ変更した。
床はz=0に一つ、関節数は4/8のまま。最終XMLと依存bytesを同じmodel digestへ含める。
変更前に傾斜3条件・床4モデルの計7条件で失敗を再現し、修正後は同条件を通過した。
合成Gamepadとheadless Edgeで床を含む13 geom/8関節の実描画・同時操作・停止保持を確認した。
本再訂正もsoftware-onlyのモデル条件修正で、実機寸法/校正・接触力学・参加者実験を示さない。


## 肩中心間隔の画像推定による短縮（8c33361の0.8 m設定を更新）

利用者は、添付CAD画像の98/93 mm表示は別部品の取付穴間に関するもので、肩間隔ではないと明示した。
98 mmをそのまま肩中心間隔へ使う案は採用しない。以下は画像の比例読取りと既存モデルのdatum変換による
simulation用推定で、CADの座標取得・実機測量・カメラ校正ではない。

### 画像から読み取った点と仮定

出典は2026-09-28に添付されたCADスクリーンショット（1328 × 878 px）。
画像bytesのSHA-256は`54a7274c9ddd00cf2b5254bf3be6cc4612419c6ad7743fe648773dd41909ec56`。
原点を左上として、青い選択穴の中心と、傾斜板の見えている輪郭端を手動で次のように読んだ。
輪郭点は近似値で、穴・板は異なる奥行きにあり透視誤差が残る。

| 対象 | 画像座標 (x, y) px |
|---|---|
| 左の選択穴中心 | (384, 500) |
| 右の選択穴中心 | (875, 505) |
| 左取付板の輪郭4点 | (368,107), (410,133), (177,562), (104,542) |
| 右取付板の輪郭4点 | (985,113), (915,166), (1137,580), (1220,522) |

4点平均を投影板中心の近似とすると、左(264.75,336.0)、右(1064.25,345.25) px。
穴中心の基線方向に射影した板中心間は約799.6 px、穴中心間は約491.0 pxで、比は約1.63。
98 mmを中心間の縮尺と仮定すれば板中心間は約159.6 mm。画像には93 mmも併記されているため、
表示の意味を断定せず93 mmでも計算すると約151.4 mmとなる。98/93の差から穴径等を確定しない。

### 板中心とモデルの肩中心は別datum

原本`BaseLink.stl`の平板はlocal y=0..6 mmにあり、中央面を(0,3,0) mmとした。
`arm.xml`をnative MuJoCoで解くと、この点はsource (-72,0,700) mm、
`sholder_joint_1`のanchorは(0,0,700) mm。したがって板中央面から肩基準までは片側72 mmである。
CAD上の板とsource平板を対応させること自体も推定であり、同じdatumだという実測証拠ではない。
30 degree取付では、左右肩中心の間隔は板中心のY間隔に両側の水平offsetを加える。

```text
板中心間隔 ≈ 799.6 / 491.0 × 98 = 159.6 mm
両側datum補正 = 2 × 72 × cos(30 degree) = 124.7 mm
肩中心間隔の一次推定 ≈ 284.3 mm
93 mmを縮尺基準にした感度計算 ≈ 276.1 mm
採用値: 290 mm（left Y=+145 mm、right Y=-145 mm）
```

270〜310 mmは読取点・奥行き・基準寸法の曖昧さに対する設計上の感度確認幅で、統計的信頼区間でも
機械部品の公差でもない。採用値を未校正の画像推定として記録し、直接CAD座標を得たときに置換する。
290 mm設定では、同じsource平板中央面間は約165.3 mmとなり、画像の約160 mm級と整合する。
高さ0.7 mは変更しない。取付角・回転順序・鏡映・joint home・原本mesh寸法も変更しない。

### 検証と再現性の境界

修正前にnative MuJoCoの肩中心間隔が約0.8 mとなる失敗を再現した。登録モデルの設定値だけでなく、
コンパイル済みjoint anchorが0.290 m離れることと、平板中央面が約0.1653 m離れることを検査する。
各腕について旧幅からY方向だけの平行移動であること、回転・body mass・actuator force limit・原本assetが
同一であること、左右単腕と双腕内の対応componentが一致することを回帰検証する。
床を含む共通sceneは維持し、Viewerだけの移動や縮尺変更は行わない。
model/configuration digestは変更するため、旧0.8 m配置のworkspace・到達位置を同条件の証拠へ再利用しない。
本変更はcollision-free配置・実機安全性・参加者比較条件の認定ではない。
