---
status: canonical
owner: runtime
last_verified: 2026-09-28
canonical_for:
  - fixed object definitions and instances in shared robot scenes
  - geometry-only contact diagnostic task and viewer presentation
related:
  - docs/contracts/experiment-plugin-composition.md
  - docs/contracts/launch-profile.md
  - docs/contracts/contact-task-manifest.md
  - docs/contracts/coordinated-arm-runtime.md
  - docs/operations/backend-viewer-startup.md
---

# 固定物体sceneと幾何接触診断

## 目的と責務

単腕/双腕、1物体/複数物体を同じmodel/scene/provider/Task/Viewer経路で組み合わせる。
本契約は`coordinated_joint_position_kinematic/v1`の診断であり、qpos直接更新と`mj_forward`で現在poseを観測する。
`mj_step`、物体の運動、押し返し、力、ばね、貫通防止を実装したという意味ではない。

| Owner | 責務 |
|---|---|
| Environment package resources | 物体定義・instance world配置・診断preset |
| `runtime/scene/objects.py` | strict immutable定義・配置・接触条件、canonical JSON |
| `runtime/scene/contracts.py` | Robot/Environmentを結ぶtyped request・binding |
| Robot model factory | 元scene、Robot-owned tool collider、既存モデル構築 |
| `runtime/scene/composition.py` | 固定物体と明示contact pairの合成 |
| `runtime/scene/measurement.py` | 同じnative model/dataの幾何観測・初期配置検査 |
| `runtime/scene/observation.py` | backend非依存のgeometry DTO、値整合検査 |
| `mujoco_backend/contact_geometry.py` | native contactのpoint/frame/distance共通読取り |
| Task Plugin | 対象物IDと有限観測のphase/terminal条件 |
| 共通ModelExecution | 同一lock/snapshot/frameの結線、Task観測、停止。publisherと有限trialから共用 |
| Viewer | 適用済みposeと同一frameの表示のみ |

旧`ContactTaskManifest/v1`は単一cube・force・press/holdという別contractである。新sceneを偽の旧manifestに
詰めず、旧成功条件/force status/digestを維持する。geometry primitiveは両方から共用し、旧APIの数値検査を緩めない。

## 物体定義と配置

`ObjectDefinition`は中心原点の均質box。定義ID/version、half-extents（m）、質量（kg）、
重心まわり対角慣性の算出方式、3成分摩擦、RGBA、値の来歴を保持する。
各半寸法hx,hy,hzからIxx=m(hy²+hz²)/3等を一箇所で計算する。密度と質量の矛盾する二重指定はない。
摩擦はsliding_friction（無次元）、torsional_friction_m、rolling_friction_mとして外観から分離する。

`ObjectInstance`はinstance ID、定義ID/version、world position_m、orientation_wxyzを持つ。
初期版のmotion_typeはfixed、frameはmujoco_worldのみ。未実装のdynamic/他frameを固定/worldへ代用しない。
world固定はjointなしで表現し、非常に重い自由物体やmocapによる迂回を使わない。
1物体でもobjects配列を使う。最大32 instance/definition、最大256 KiBのJSONという診断用software budgetを設ける。
instance/definitionをID順へ正規化し、入力順によってsceneの意味やdigestが変わらない。

presetはEnvironment package内で解決し、実効scene全体を`LaunchProfile.to_dict().resolved.scene`へ保存する。
scene identityはcanonical UTF-8 JSONのSHA-256。未知field/参照/version、duplicate、BOM、非finite、
不正quaternion/寸法/質量/RGBAは起動前に拒否する。実効値の変更はscene/model digestへ反映される。

## 接触とRobot collider

`tool_sphere_10mm/v1`は各手先siteに半径10 mmの診断球を置くRobot-owned profile。
既存の単腕/鏡映定義から名前を生成し、元mass/inertia/joint zero/home/actuator limitsを変更しない。
透明cyan球を実際の衝突形状として描画し、site自体を衝突形状とは扱わない。既存表示STLは衝突無効のまま。

Environmentは明示した全手先×全物体のpairだけを追加する。物体/taskの左右を名前から推測しない。
両surfaceの同優先度max規則を明示pairの5成分frictionへ展開し、condim、solref、solimp、margin、gap=0を固定する。
solref/solimpは数値接触条件であり、ヤング率・実物ばね定数・反発係数の測定値へ読み替えない。
床、自己、腕同士、物体同士はこの診断の接触観測scopeには含めない。

初期配置はtool-object、object-object、objectとworld planeをnative `mj_geomDistance`で検査する。
負の距離が明示toleranceを超えれば拒否する。完全一致するbox対ではnative distanceが0を返す反例があるため、
固定box同士はtoleranceより大きい隙間を要求する。0を非貫通として通さない。この保守的制約はfloor支持接触には適用しない。
全身干渉や到達可能性を保証する検査ではない。

## 観測とTask

`SceneGeometryObserver`はnative contact point、normal、signed distance、penetrationとnative object poseを読む。
normalはtool→object、geom順が逆なら符号を反転する。nearはdistance>0、touchingは=0、penetratingは<0。
nearとno-contactは別であり、浮動小数点の微小負値を都合よくzeroへ置き換えない。
`scene-contact-geometry/v1`はscene/model digest、frame_index、simulation_time_s、全object pose、contact recordsを保持する。
force_status=`not_evaluated_kinematic`、force_n=nullで固定し、zero-force/反力/物理安全の証拠にしない。

`contact_observation_task/v1`はtarget_object_idsとduration_sを明示し、scene全体の接触を構成し直さず対象のみを選ぶ。
無接触でも期間終了ならcompleted。success classificationは「診断期間を終えた」の意味で、接触達成ではない。
入力停止/切断、先に尽きた実行step budgetはaborted。異なるscene/instance/epoch、旧frame、時刻後退はinvalid。
terminal後は再advanceせず、common runtimeも停止。新session/epochと中立確認なしに自動再開しない。

## 同一scene・frameの描画

`ModelStateSample`は同じlock内でrobot snapshot、全state、joint addresses、geometryを取得する。
Robot関節位置は名前解決したaddressesで全qposと照合し、腕数固定sliceを作らない。固定物体は自由度を追加しない。
新たなdynamic自由物体のqpos/qvel layoutは#582で別途成立させ、現時点で対応済みとはしない。
backend/Viewerへ渡すMJCF/assetは同じfinal artifact。別のcubeやFK/physics worldを描画側に作らない。

payloadは独立に解決したscene_contact_binding_v1、current geometry、Task presentationを同時に持つ。
Viewerはmodel/scene/epoch、明示endpoint/object ID、qpos適用frame/timeを照合してから表示する。
terminal Taskのevent frameは保持し、凍結後のpresentation frameはcurrent geometryと一致させる。
観測欠落、不正field、旧frame、scene変更、接続無効では古いpoint/normalを消し、unavailableとして扱う。

3D点と固定長法線矢印はpoolを再利用し、frameごとにmesh/materialを作り直さない。
青near、黄touching、橙penetrating。矢印長は力の大きさではない。右panelに対象ID、world pose、距離/mmを表示する。

## reset・範囲外

同一presetのresetはRobot state、simulation clock、contact、Task初期stateを戻す。Environment resetは生きた
scene ownerだけを受理する。CLI再起動は新epoch/中立でやり直し、page reload不要GUI retryは#565へ残す。
active中のspawn/設定変更、dynamic物体、全身衝突、実機/serial/OSC、参加者実験は追加しない。

## 明示worldと可動物体（#582 / object-scene/v2）

旧v1は上記の固定幾何診断を保持する。v2は同じObjectDefinitionとinstance集合へ、Environment所有の`world`を追加する。
`world.frame=mujoco_world`、`gravity_m_s2`、`support_planes[]`を必須とし、空の支持面集合を床なしと解釈する。
支持面はID、world位置/quaternion、摩擦、RGBAを持つ無限planeで、描画用sizeを有限な机の境界とはみなさない。
新worldはRobot bare assemblyへ直接合成し、Robotの旧sceneから床を削って差し戻す経路を作らない。
旧v1/v2/v3 launchの静的scene resourceは過去の条件・digestを維持するためだけに残す。

`motion_type=dynamic`では名前付きfreejointを追加し、`initial_velocity`のworld並進速度m/sとworld角速度rad/sを明示する。
fixedでは自由度を追加せず、非zero初期速度を拒否する。同じ質量/慣性/形状の定義をどちらにも使う。
MuJoCo freejointの並進はworld、回転速度はbody frameのため、初期角速度は構築時に一回だけ変換する。
`align=false`を指定して、物体原点と重心/慣性frameの暗黙変更を避ける。現在の物体は中心重心の均質boxのみ。

v2の物体と支持面はnative collision maskで接触し、Robot toolとの明示pairも同じworldに存在する。
物体同士の初期配置は保守的に正の隙間を要求する。床への支持接触は許可し、初期penetration tolerance超過を拒否する。
全身/自己干渉を検証した意味ではない。数値接触は有限の食い込みを持ち、厳密な非貫通や実機安全を保証しない。

## 数値条件・力の観測との境界

worldはgravity/支持面を所有し、数値積分のtimestep・integrator・solver・反復数・収束toleranceはExecutionの
`DynamicsSettings`が所有する。両方を同じ最終MJCFへ構成し、scene/model/settings digestを別々に記録する。
複数のglobal optionを順番依存で上書きしない。dynamic実行はworld/v2を要求する一方、fixed物体だけの同じworldはkinematic診断でも再利用できる。可動物体をkinematicへ黙って固定する選択は拒否する。

幾何観測v2は`force_status=separate_dynamics_evidence`とし、力をgeometry欄の0 Nへ捏造しない。
`scene-dynamics-observation/v1`に同一model/scene/frame/timeの物体pose・world速度、Robot command/実測状態、
actuator torque、native contact wrenchを記録する。物体poseは全scene qposからの派生観測であり第二の更新元ではない。
支持面・物体・toolのroleが解決済みの接触だけを観測し、inactive constraintはmeasurement_unavailable/nullとする。
force_on_geom2_world_n/torque_on_geom2_world_nmはgeom2へ働くworld wrench（トルクの基準は接触点）である。

`mujoco_backend/contact_wrench.py`がnative力とcontact-frame→world変換を一元化し、既存R7-Hも同じprimitiveを使う。
旧press/hold Task・単一cube identity・集約/分類を新worldへ偽装しない。geometry-only Taskは引き続き期間終了を観測するだけで、
力目標達成・搬送成功は判定しない。solver力はシミュレーションの数値解であり、実機の計測力ではない。


## 押し診断の有限固定台

`cube_push`はworldのfloorをz=0に置き、`push_pedestal/v1`の固定box instanceを台として追加する。
形状・慣性・摩擦・外観は物体定義、world固定と位置はinstance、観測目標cubeはTaskの対象選択とし、
Robotモデルの高さやsupport planeで台を代用しない。床はz=0に置き、押し診断では底面z=0・上面z=0.41 m・有限幅/奥行のworld固定box台を別objectとして置く。
初期cubeの底面は台上面から1 mm離し、既存の初期重なり検査を緩めず、開始後に自然に支持される。
