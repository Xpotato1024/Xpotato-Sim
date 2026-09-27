---
status: canonical
owner: robot
last_verified: 2026-09-26
canonical_for:
  - fast_arm mirrored assembly and named output binding
related:
  - docs/contracts/robot-profile-runtime-viewer-profile.md
  - docs/contracts/physical-output.md
  - docs/architecture/dependency-boundaries.md
  - docs/reports/audits/fast-arm-router-mount-geometry-2026-09-26.md
---

# FastArmの片腕・双腕assemblyと名前付き出力対応

## 目的と現在の境界

既存のFastArm core modelを一つの正本として、明示した1個または2個のarm instanceを生成する。
同じinstanceの関節名対応を、MuJoCo状態参照と既存のOSC出力要求への投影に使う。
これはモデル組立・名前対応・要求変換のAPIであり、双腕をGUIから操作できる完成品ではない。
既存 `fast_arm/v1`、4関節のRobotProfile、単一手先routeを暗黙に8関節へ変更しない。

## 所有者と構成

| 対象 | 所有者 | 役割 |
|---|---|---|
| 元のXML・STL・関節規約 | `fast_arm_core` の既存resources/definition | 原本を維持し、左専用のコピーを作らない |
| 配置・instance名・関節address | `fast_arm_core.assembly` | 片腕/双腕を同じ型で宣言し、名前で状態参照 |
| 原型/鏡映型の生成 | `fast_arm_core.assembly_model` | package-owned XML/STLから決定的なモデルbytesを生成 |
| instance→物理targetの対応 | FastArm adapterの `assembly_output.py` | 全腕の設定を検証し、既存typed requestへ投影 |
| 実行・送信・応答・全体停止 | 既存runtime各ownerと後続の連成経路 | coreやviewerに移さない。実機permissionを維持 |

`FastArmInstance`ではarm_id、mirror_y、position_m、quaternion_wxyzを必須とする。
IDがleftだから鏡映する、接続順で右を決める、mount幅を実機値として補う処理はしない。
単位quatを検査し、q/-qを同じ姿勢へ正規化する。宣言後の設定はimmutable。
`FastArmAssembly`は重複のない1〜2個を宣言順に保持する。生成名は `arm_id__local_name`。
操作者の左右、機体arm_id、OSC target、画面左右は別のidentityである。

## 肩mountの30 degree開き

利用者から提示された双腕の取付構造は、正面視で`<arm>━/  \━<arm>`となり、
左右の取付板が鉛直からそれぞれ30 degree傾く。これは関節のzero offsetではなく、
arm全体より上流の固定base geometryとして扱う。

assembly座標では正面をYZ平面とし、既存の左右鏡映後にX軸まわりのmount rotationを適用する。
左armは`+30 degree`、右armは`-30 degree`とし、`quaternion_wxyz`はそれぞれ
`(cos(15 degree), +sin(15 degree), 0, 0)`、`(cos(15 degree), -sin(15 degree), 0, 0)`である。
これにより同じlocal joint configurationでもworld上のtip pose、Jacobian、workspaceはmount姿勢を含んで変化する。

この30 degreeはjoint q、MuJoCo joint `ref`、wire angle offset、motor zeroへ加算しない。
current `fast-arm-router`は差動肩関節のjoint-to-motor変換を持つが、3Dのmount frameを所有しない。
一方、現行diagnosticの`position_m=(0, +/-0.4, 0)`は合成fixtureであり、実機のmount間隔・高さを
測定済み寸法として扱わない。角度の反映から位置寸法や実機校正を推論しない。

## 鏡映の規約

原型のローカルXZ面を反射する `S=diag(1,-1,1)` を使い、その後に明示mount姿勢・位置を適用する。
位置・並進は `p'=S p`、回転は `R'=S R S`、回転軸は軸性ベクトルなので `a'=det(S) S a`。
慣性テンソルは `I'=S I S`。質量・主慣性値を保ち、慣性主軸の姿勢と重心位置を対応させる。
meshは同じSTLへのMJCF scaleで鏡映し、回転で代用したりThree.jsだけへ負scaleを置いたりしない。

この規約では、原型と鏡映型へ同じ一般化座標qを与えると姿勢が鏡映となる。
関節ref・home・ctrl・servo係数・force limitは対応するまま維持する。
実機の配線符号・zero offsetは別の校正問題であり、mirror_yから自動生成しない。

builderは現行core resourceが使うMJCF表現を対象とし、未知section、非対応の姿勢表現・default等は拒否する。
任意XMLを受け取る一般モデル変換器ではない。新しいsource表現は対応とテストを追加してから使う。
生成物は原本XMLと5個のSTLを読み取るだけで、追跡済みresourceを書き換えない。
configuration_sha256は配置宣言、source_sha256は元XML/STL、model_sha256は生成XML/STLを識別する。
これらは再現性のidentityであり、物理的妥当性・実機安全性を保証しない。

## MuJoCo状態と追加物体

各armのjoint/body/site/actuatorは名前からaddressを解決する。hinge型、actuator→joint、tip→bodyを照合する。
qpos、dof、actuator、siteのindexを混同しない。対象物にfreejointがある場合、位置7成分と速度6成分になるため、
単純な先頭4個/次4個という切出しは使わない。
全armsは同じmodel/dataに置く。追加物体や支持面の構成はEnvironmentの責務であり、このcore assemblyは床を生成しない。

元XMLのrobot meshはcontype/conaffinityとも0である。この設定は左右へそのまま継承する。
モデルの描画・FK・actuatorの時間発展が成功しても、腕間衝突、自己干渉、物体接触が検証済みとはしない。
接触用形状・pair分類・全sceneのfeasibilityを整備するまで、接触研究や実機許可の根拠に使わない。
±0.4 m等のテスト配置は合成fixtureであり、実機mount値・共通作業域・実験配置の採択ではない。

## 左右のOSC要求への共通投影

`FastArmOutputBinding`でarm_id、target_robot_id、endpoint_id、個別のFastArmOutputMappingを明示する。
`build_fast_arm_assembly_requests`は全関節名を持つJointPositionCommandと全armのbindingを要求する。
順序が変わっても名前で分割し、各armのlocal core順へ戻して、既存PhysicalOutputRequestを生成する。
一方のbinding欠落、重複、unknown、関節名不一致、非finite、OSC float32不成立では全batchの返却を拒否する。
右だけ成功を返し、左を無視するfallbackはない。

既存wireのアドレスは `/source_token/target_robot_id/joint` でありendpoint_idを含まない。
本bindingは誤配置を避ける保守的な規約として、腕ごとに異なるtargetを必須とする。
同じtarget名を異なる通信endpointで使う構成が一般に不可能という意味ではない。その構成は今回の対象外であり、
必要な場合は実配備routerとtransport endpointを含む対応・応答相関を確認して、明示契約として追加する。

各腕のrequestは同じsequence/時刻/cadence/software revisionを持ち、session_idは元sessionとarm_idで分離する。
batch identityはassembly/modelと全request/mapping digestを含む。model_sha256はcallerの明示provenanceであり、
このpure関数自身が実modelをロードして同一性や安全性を認定するわけではない。
既存FastArm wire変換、OSC codec、response parserを再利用し、左用encoderやleft-only例外を作らない。

## 物理出力との境界

batchはrequested-levelの資料であり、送信可能wrapperでも許可証でもない。sender/socket/permissionを受け取らない。
両腕の実機出力には、各armに対応するProfile・model・校正・物理evidenceと既存P5/二重permissionが必要。
テストの合成mappingや左側の鏡映modelから、受理済み実機evidenceを生成してはいけない。
同じ4関節順を持つ既存profileをsynthetic試験に使うことは、左右実機のprofileが同一と認定することではない。

既存FastArmSignalSessionと共通応答判定は左右それぞれで使える。逆腕の応答はpendingを解除しない。
ただし独立sessionを2個作っただけでは、全体衝突の確認・両側preflight・部分送信失敗時の協調停止は完成しない。
UDP二送信の同時到達・原子的送信・実機同時停止も保証しない。後続の連成runtimeで両側分を一つの操作として監督する。

## 双腕ユースケース全体の完了matrix

| 経路 | 今回の到達点 | 完成までの残存事項 |
|---|---|---|
| 片腕/双腕生成 | 原型・鏡映・同一world、名前対応API、双腕diagnosticの左右30 degree mount姿勢 | 起動profile/GUIからの選択、実機mount位置・高さ、初期姿勢の実測確定 |
| 入力→両手先 | 入力側は別PR #568、今回とは独立 | multi-endpoint provider/typed route、単一snapshotの両側候補と共同更新 |
| Selfrionette二台 | 従来の単台経路を維持 | 個体binding・別校正・skew/切断・同側例外なしの取得経路 |
| scene/接触 | 状態addressはfreejoint追加へ対応 | 接触用geometry、腕間/自己/対象の区別、全体feasibility |
| OSC変換・応答 | 両armへ同じrequest/codec、個別target、誤相関/timeout試験 | 連成preflight・部分失敗/停止、actual router互換と実機測定 |
| 表示・ログ・再試行 | 共通arm/joint identityの定義 | 双手先表示、同一epoch、全状態reset、task/物体spawnの接続 |

未完項目は右側だけを実装して完了にしない。次のruntime変更は片腕/双腕の両方を受入matrixに含める。
#568とこのfoundationを統合した後、複数手先を扱う実行経路を作り、その同じ名前対応をGUI・記録・OSCへ接続する。
実機オプション化はsimulation開発の依存を外すが、実機の受入手続きや不足ソフトウェアを完了扱いにはしない。

## 検証

core宣言のstrict検査、単腕original/単腕mirror/双腕、32姿勢のFK/Jacobian/慣性/重力、meshのworld bounds、
actuator時間発展、freejointを前置したaddress、明示校正を使う左右OSC bytes、逆腕応答、timeout/停止を検査する。
検査の実行記録は [実装報告](../reports/implementation/fast-arm-assembly-2026-09-25.md) を参照する。

設計時に参照した公式仕様（2026-09-25確認）：
[MuJoCo MJCF](https://mujoco.readthedocs.io/en/stable/XMLreference.html)、
[MuJoCo型定義](https://mujoco.readthedocs.io/en/stable/APIreference/APItypes.html)。
実行試験は導入済みMuJoCo 3.9.0で実施し、実物の鏡映精度・出力校正の測定ではない。

## 共同runtime接続の追加

[共同実行契約](coordinated-arm-runtime.md) に、左右Gamepadから同一snapshotの全腕候補・一括反映、
名前付き関節指令から既存OSC要求/permission/codecを経る接続を定義する。
運動学診断と全側出力監督のsoftware経路を追加した。GUI同時操作、contact/全体collision、
実機receiver停止とwatchdogが完成したことを意味しない。上記の全体完了条件は維持する。
