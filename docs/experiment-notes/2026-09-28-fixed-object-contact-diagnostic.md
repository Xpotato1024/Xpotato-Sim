---
status: supporting
owner: research
last_verified: 2026-09-28
canonical_for: []
related:
  - docs/contracts/object-scene-contact-diagnostic.md
  - docs/contracts/fast-arm-assembly.md
  - research/logs/2026-09.md
---

# 固定cube接触診断の条件と検証範囲

## 条件

本記録はIssue #585のsoftware-only診断条件。参加者実験、実測、反力性能の結果ではない。
main `e69fd1a64b3fc078f58e682ce8b0e183135b9481`の共通モデル・trigger/TPS操作を基点とする。
モデルの肩間隔290 mmと高さ0.7 mは従来どおり推定/合成条件であり、実機校正を行っていない。

| 項目 | 診断用の指定 |
|---|---|
| cube形状 | box半寸法(0.05,0.05,0.05) m、つまり各辺100 mm |
| 質量 | 0.1 kg、均質box慣性。固定物体の設定値であり実測ではない |
| surface friction | sliding 0.8、torsional 0 m、rolling 0 m |
| motion | world固定、jointなし。自由落下/押し返しは対象外 |
| 左/右cube | (0.36,+/-0.56,0.46) m、単位quaternion |
| 原型単腕cube | (0.24,-0.36,0.28) m、単位quaternion |
| tool collider | 半径0.01 m、元Robot質量/慣性へ加算しない |
| contact | condim=3、margin=0.003 m、gap=0、solref=(0.02,1)、solimp=(0.9,0.95,0.001,0.5,2) |
| 初期食い込み許容 | 1e-6 m。ただし固定box同士は正の隙間を要求 |
| Task | simulation time 60 sの観測、最大18000表示frame |
| force | not_evaluated_kinematic / null |

設定は各profileのresolved sceneとdigestに保存する。上記の値は合成診断値で、物性測定・材料弾性率・実物ばね定数ではない。

## 再現

```powershell
uv run xpotato-sim profile contact-debug-bimanual
uv run xpotato-sim app --profile contact-debug-bimanual
uv run pytest tests/runtime/test_object_scene_contracts.py tests/runtime/test_scene_model_publisher.py tests/integration/fast_arm/test_object_scene_diagnostic.py tests/plugins/tasks/contact_observation_task/test_contact_observation.py -q
```

原型/左/右は`contact-debug-single` / `contact-debug-left` / `contact-debug-right`を選ぶ。
Taskによる対象選択は物体の生成と独立し、近接や対象外の接触をcontact達成へ昇格しない。

## 独立な幾何oracle

sphere半径0.01 m、box半寸法0.05 m、box中心x=0.062 mの単純配置でnative距離を検査した。
sphere中心x=0の距離0.002 m（near）、x=0.01の距離-0.008 m（penetrating）と、法線+Xを確認する。
geom順を反転した場合もtool→objectの方向を維持する。期待値はproduction encoder/observerからコピーしない。
これは接触力やservoの妥当性試験ではない。

## 初期配置と表示の反例

同位置の固定box二個でnative distanceが0になり、負値だけの検査では初期重なりを通す反例を得た。
そのためfixed box同士は正の隙間を必要とする。floor上の支持接触はこの制約とは区別する。
ブラウザでは旧primitive色fallbackが物体定義のRGBAを上書きする点と、旧force欄の「接触情報なし」が新診断と
矛盾して見える点を修正対象にした。点/法線は食い込み中も見える診断overlayで、遮蔽や力の大きさを示すものではない。

## 完了境界

native geometry、旧contact契約、schema/role/identity、同じ適用frame、finite停止、同じ全モデル経路を検証する。
合成Gamepadブラウザでの表示確認と、人による実Gamepad受入は別である。
実行時はsource revision、resolved configuration/scene/model digest、MuJoCo/Viewer versionを併せて記録する。
GUI retry、動的物体、反力、非貫通制約、全身衝突、実機I/Oは未実装/未検証のまま保持する。


原型単腕の初回presetは別のmount条件のtip位置を仮定しており、ブラウザ操作でcubeへ到達しないことが判明した。
原型のnative home tip=(0.24,-0.2459512147,0.2843078062) mを確認し、cubeをworld (0.24,-0.36,0.28) mへ明示配置し直した。
モデルに応じた暗黙spawn計算は追加せず、4配布profileすべてでproduction Mappingを通した接触到達回帰を追加する。
