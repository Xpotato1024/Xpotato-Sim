---
status: canonical
owner: runtime
last_verified: 2026-09-28
canonical_for:
  - coordinated arm diagnostic execution and output supervision
related:
  - docs/contracts/fast-arm-assembly.md
  - docs/contracts/gamepad-trigger-control.md
  - docs/contracts/gamepad-plane-control.md
  - docs/contracts/physical-output.md
---

# 複数手先の共同実行と出力監督

## 対象と実行意味

左右のGamepad入力を名前付き手先速度へ写し、FastArm assemblyの全腕を一つのpre-step snapshotから計算する。
原型・左単腕・右単腕・双腕を同じproviderとモデル登録型で扱う。
取付面の正しい基準姿勢・回転中心は[FastArm assembly契約](fast-arm-assembly.md)を正本とする。
source原点へのRxだけという以前の記述は誤りで、joint zeroやwire offsetで補正してはならない。

この入口は `coordinated_joint_position_kinematic/v1` の**運動学診断**である。
従来viewerのdirect-qpos意味を明示して共同更新へ拡張し、全腕の位置をまとめて反映して `mj_forward` を行う。
`mj_step`、servo追従・動的接触、全体collisionの実験経路ではない。時間は固定dtの診断時刻であり、
実測速度・実機性能とは呼ばない。元の衝突無効meshを安全性の証拠にしない。

## 責務

| 所有者 | 責務 |
|---|---|
| `schemas/coordinated.py` | 手先ID付き速度、取得情報、名前付き観測の型と入力検証 |
| Mapping `map_coordinated_input` | 標準trigger controlまたは互換plane controlの正規化・状態を共有し、明示side bindingからtyped要求を返す |
| Robot `adapter/coordinated.py` | 原本assembly、名前address、既存DLSと限界の適用、全腕候補と一括反映 |
| `runtime/execution/coordinated.py` | epoch、入力鮮度・系列、中立、実行・停止・fault latchと再開 |
| `runtime/composition/coordinated_input.py` | 登録providerとSource/Mappingを結ぶ共通実行。腕数による別loopを作らない |
| `runtime/composition/fast_arm_coordinated.py` | 既存保存assembly診断のthin入口。共通実行へ委譲 |
| `runtime/output/coordinated.py` | 全側prepare、追加scene veto、逐次dispatch、全体fault、全側停止試行 |

legacyのsingle-endpoint intentを二腕へ複製せず、診断metadataの左右velocityをcommandへ逆生成しない。
標準trigger controlは単一手先/共同入口で同じ `_build_trigger_intents` を使う。互換plane controlも同様に `_build_plane_intents` を共有し、各写像を二重実装しない。
`side_to_arm` は明示・copy/freezeし、選択した全armを重複なく覆う。旧 `output_side` はsingle-endpoint用であり、
共同入口のbindingを上書きしない。既存launch profile/v1・Robot Plugin/v1の意味は維持する。
このproviderは登録モデルの明示composition用である。#574の共通v2 profileへ接続するが、
単腕Robot Catalogのidentityや既存launch profile/v1を双腕へ読み替えない。

## 同一状態からの候補とcommit

各armのIKは同じMuJoCo snapshotのコピーを見る。一腕のcandidateを次の腕の初期状態へ混ぜない。
4関節分の既存 `LocalEndpointMotionGenerator` と同じ有限差分・減衰・step上限を各armへ用い、
元のjoint-limit設定を個別に照合する。tool方向は当該armの同じsnapshotのsite姿勢からworldへ解決する。
全腕を新しい作業dataへまとめ、finite値、MuJoCo warning、関節限界を検査する。

`PreparedCoordinatedStep` は当該providerが発行した同一objectだけを一度消費できる。
外部コピー、他provider、古いgeneration、reset後、候補の変更は拒否する。
失敗した作業dataを公開せず、成功時だけロック内で一つのlogical worldのdataを交換する。
これはソフトウェアでの共同反映であり、実機への通信を原子的にする保証ではない。

名前付きFastArm providerの`CoordinatedSnapshot`は、同generation内で一つの不変値を共有する。
全fieldはfrozen dataclassとtuple/scalarであり、native arrayや可変metadataへの参照を含めない。
commit・reset・invalidateで共有参照を失効し、未公開候補の改変検査はcandidate dataから再取得する。
geometry、force、Task、transport metadataをこの共有snapshotで代用しない。
commitのpre-step guardはcacheを使わずlive dataからfresh snapshotを作り、prepare後の
live qpos変更も従来どおり拒否する。providerの表示用sampleはreset/commitでforward済みの
native dataを読み取るだけで、追加のforwardによりintegration stateを変更しない。
generic `snapshot_mujoco_state` APIのforward動作は維持する。
各substepのfinite値・warning・関節限界・速度・tracking error検査は維持する。
完全なゼロ速度ではゼロ増分の有限差分IKだけを省き、dynamic servo targetと全物理substepを維持する。

## 時計・停止・復帰

`source_timestamp_s`、Sourceが実際に取得した `last_received_at_s`、host clock、simulation timeを分離する。
同一sampleを再評価してもreceiptを更新しない。系列tokenはprovider sessionとGamepad報告index/idを結合したhashであり、装置報告の変化も再preflight対象とする。これは装置固有serialや認証ではない。出力するJointPositionCommandのtimestampはhostの評価時刻であり、
simulation時間を実機要求の鮮度へ流用しない。モデル時刻はsnapshot側に保存する。

起動はwaiting_neutral。新鮮な中立とprovider preflight後にrunningへ進む。
waiting_neutral中の未取得・disconnect・staleによるavailable=falseは、まだ運動開始していないため
faultへ昇格させず同状態で待機する。fresh neutralを受け取れば同じsessionでrunningへ進める。
モード切替中の一側ゼロは通常の操作であり、他側の正常入力を止めない。
running後のinvalid/stale/disconnect、欠落arm、provider変更、逆順、同sequenceの異内容、epoch不一致、
非finite、候補・commit・snapshot失敗は全体faultをlatchする。正常入力復帰だけでは解除しない。
malformed schemaやmapping例外は起動前でも待機へ読み替えずfail-closedとする。

再開は停止後に新epochを指定し、provider reset/preflight、Source/Mapping再作成、新しいprovider系列と
新鮮な中立を必要とする。旧epoch・旧系列は拒否し、履歴は128実行までに限定する。
停止中のsnapshot取得が失敗した場合は観測欠落とし、ゼロ姿勢で補完しない。

## OSCの二段階処理

既存 `FastArmPhysicalOutputSession.submit` は互換を維持して `prepare_submission` と
`dispatch_submission` へ分離した。P5、正確なrequest/evidence結合、二重permission、cadence、
operator状態、one-shot grant、transportの最終再検証を迂回しない。prepareでdatagramを送信しない。
コピー・他session・消費済み・stop後のticketは使えない。例外時もgrantを残さない。

`CoordinatedPhysicalOutputGroup` は左右のfresh session、distinct target/session ID、全側の停止requesterと
追加のwhole-scene preflightを明示要求する。これらのcallbackは**追加veto/receiver依存処理**であり、
P5や実機evidenceを代替しない。未実装の全体collisionやreceiver停止を `True` で埋めて実機許可を作らない。

全armのevaluationが揃い、sequence/timestamp/cadence/revisionが一致し、全prepareとscene vetoが通った後だけ
既存dispatchを順に呼ぶ。途中停止・重複submit・一側の拒否・部分送信失敗・異常応答・timeout・入力期限切れは
全体をlatchする。全sessionの許可を先に失効させ、全側の停止requesterを試す。
一側の停止処理が例外になっても、残りの側を省略しない。再armingには新しいgroup/session・中立・preflightが必要。

`dispatched_arms` はローカルsend receiptが受理した側、`dispatch_call_arms` はdispatchを呼んだ側。
失敗したsendが相手へ到達していないとは保証せず、部分送信をrollback済みにしない。
`stop_results` は各側のlocal許可失効と停止要求の試行/不明/失敗。`physical_stop_confirmed` は常にfalse。
routerの処理相関は移動・停止完了のACKではない。合成観測は実機観測へ昇格しない。

### Router target healthの監督

R4の `router-target-health/v1` はcommand ACKとは別の入力として扱う。routerはPi telemetryの
fresh/staleとtarget-local watchdog状態を所有し、Xpotato-Simはhealth packetのtarget相関・受信鮮度と
「一側異常なら全側停止」という双腕policyを所有する。routerの `state_age_s` とXpotato-Simのhost clockは
別clockなので直接比較せず、routerが判定したstatusと、health packet自体を受信してからのhost経過時間を分けて評価する。

`max_router_health_age_s` を明示した `CoordinatedPhysicalOutputGroup` は、全armについてfreshな
`healthy` healthをarming・submit・各腕の送信直前・pollの条件とする。期限ちょうども失効する。health欠落、受信期限切れ、malformed schema、
target mismatch、`stale`、`watchdog_tripped`、`awaiting_state`、`unmonitored` はfail-closedで全体faultへ
遷移し、既存の全側stop requesterを試す。health受信はcommand ACKのpending/clearを変更せず、
`physical_stop_confirmed`もfalseのままである。

この監督は明示opt-inで、router側R4 contractが利用可能なphysical compositionで有効化する。
無効時の既存software-only経路を互換維持することは、実機運用でhealth supervisionを省略してよいという意味ではない。

受信不正時はarming前でも当該health記録を失効させ、直前のhealthyを使い回さない。
`state_age_s`はrouter内で測った経過時間であり、絶対時刻ではない。Pi側の判定閾値を上位で複製せず、受信後の経過時間は上位側で別に監視する。
このv1 wireには送信系列・起動epoch・認証がなく、再送や偽装の識別、故障箇所の一意な特定は保証しない。実接続では受信endpointの制限が別途必要である。

`poll()` はcallerの周期schedulerで実行する。Python process停止・通信断・OS停止に備えるreceiver watchdog、
独立非常停止、停止指令の機種別実装・検証は別の必須条件であり、このクラスやUDP二送信では実現しない。
この変更には実機へ接続するlauncherや停止OSC commandの捏造を含めない。

## 有限診断CLI

```powershell
uv run python -m xpotato_sim.runtime.runners.coordinated_gamepad tests/fixtures/coordinated_gamepad/bimanual.json
```

入力は `coordinated-gamepad-diagnostic/v1`。明示assembly、side binding、Mapping parameters、epoch、dt、
入力期限、host時刻付き保存メッセージを指定する。標準入力デバイスやnetworkを開かず、最大10000 sample、
最大4 MiBのJSONを検査する。重複key・NaN・不正schemaを拒否する。fixture内の左右mount orientationは
30 degree取付条件を意図して固定するが、position、入力sample、motor校正は合成値でありhardware evidenceではない。
結果は各tickの入力、before/after、モデルdigest、関節要求とterminal状態をJSONへ出力する。
元の設定/入力、ソフトウェアrevisionと実行commandも一緒に保存する。正式なparticipant artifactには数えない。

## 残る接続

汎用catalog/GUI切替、二台Selfrionette取得、衝突geometry、
servo/contact経路、ばね/搬送taskは後続。OSCの具体receiver停止・全体scene評価と実機検証は未実施。
本経路の成功をそれらの完了や高トルク機体の安全認定へ読み替えない。

## 単腕・双腕共通のViewer接続（#574）

v2の`model`を明示すると、Robot-ownedなassembly builderから選択モデルのViewer declaration、
MJCF、mesh、home fixtureを生成する。model digest別の一時URLへ置き、backend snapshotのmodel digest、
joint names、qpos順序・次元が宣言と一致した場合だけ配信する。原本XML/STLは複製管理しない。

同一Gamepad sampleを既存の共同runtimeで評価し、同じMuJoCo model/dataから既存payload-v0へ投影する。
表示frame_indexとsimulation tickを区別する。待機・fault中はframeが進んでもqpos/timeは進めない。
latest-state配信と既存の絶対deadline pacerを使い、遅い描画consumerへの送信待ちを制御計算に持ち込まない。

初回sample未取得は中立測定で補わず待機する。初回payloadにはMappingが決めた中立待ち表示を含め、
ブラウザが既存の中立heartbeatを開始できるようにする。不正入力やstaleは全体faultへ移り、
正常入力が戻っても両腕を再開しない。表示とWeb接続は有限session終了まで保持する。
操作手順は[backend/viewer起動手順](../operations/backend-viewer-startup.md)を参照する。


登録モデルはbare armだけでなく、旧単腕と同じbase scene（床・照明・材質）を一度だけ合成する。
共通factoryで構成し、1腕/2腕どちらも同じsceneをbackend・Viewerへ渡す。保存assembly診断はbareのまま保持する。
床の復元は接触判定・力学評価の追加ではなく、モデルのscene欠落修正である。

## 固定物体を含むモデルの観測（#585）

登録model factoryはoptionalなtyped scene planを受け、Robot-owned tool colliderとEnvironment-owned物体を合成する。
common publisherは`ModelStateSample`で同一lockのrobot snapshot・全state・geometryを扱い、名前addressで関節を照合する。
Task終端後はqpos/simulation timeを凍結し、表示frameだけを進める。terminal eventとpresentation frameを分離する。
旧のstate.qpos==全Robot qposという仮定はaddress照合に変更するが、fixed物体が自由度を増やさないことは検査する。
これはkinematic geometry診断であり、dynamic freejoint/servo/反力は#582の後続。詳細は[固定物体scene契約](object-scene-contact-diagnostic.md)。

## actuator servoの動力学実行（#582）

`coordinated_actuator_servo_dynamic/v1`は同じnamed-endpoint runtime、Source/Mapping、prepare/commit ticketを使う。
構築時に実行方式を選び、片腕/双腕ごとに別loopを持たない。旧kinematic semanticsの意味・更新値は維持する。

Robot側dynamic providerは、元のposition-servo actuator形式/gear/ctrlrangeを検証し、runtime reset時のctrl targetを
home qposへ合わせる。原本XML/STL、joint ref、質量/慣性、gain、force limitは変更しない。
中立入力でmeasured qposへtargetを戻し続けると重力で沈下するため、targetは前回ctrlから保持/更新する。
IK seedはtarget状態、tool-frame方向のworld変換は同じpre-stepの実測姿勢を使用し、commanded方向へ読み替えない。

全armのcandidate targetを同じpre-stepから算出後、未公開の一つのMjDataで全ctrlを設定して`mj_step`を行う。
control_dt/physics_dtは1〜1000の整数比に限定し、余りを切り捨てない。substepは設定targetを保持する。
各substepでfinite値、solver warning、関節限界・明示した速度/追従誤差budgetを確認し、失敗候補は公開しない。
最後の`mj_forward`は積分後qposの派生pose/contactを同期する処理で、追加の時間積分ではない。
予測stateとmeasured公開stateは同じcandidateからcommitされる一方、command targetと実際のjoint angleは別値である。

数値設定はphysics_dt_s、integrator（implicitfast/Euler）、Newton solver、iterations、toleranceを明示する。
coneはこの実行versionではellipticで固定し、残るengine条件は固定されたMuJoCo versionとfinal modelに従う。
速度/追従誤差budgetはsoftware診断値であって実機安全包絡ではない。既定profileではcontrol=1/60 s、physics=1/600 s。

waiting_neutral/stop/faultではRobotだけでなく全worldを凍結する。非zero qvelをゼロと捏造せず最終snapshotのまま残す。
これはsimulation pauseであり、実機が同様に停止するという主張ではない。restartは既存の新epoch/中立条件を使用する。
全scene resetは物体の初期pose/速度、Robot状態・ctrl、時計・solver cacheを戻す。GUIの無reload再試行は#565で別途扱う。


## 制御周期の作業域再利用

providerはモデル構築時にpre-step、計画用、候補world、各腕FK用のprivate MjDataを確保する。
prepareごとに現在のlive dataから再読込みし、前のframe・腕・失敗試行の状態を持ち越さない。
commitの検証を全て通過した後だけlive/candidateの所有権を交換し、以後のprepareでは旧liveを作業域として使う。
コピーされたticket、消費済みticket、reset前のticketの拒否は維持する。

計画用の手先位置取得はnative mj_kinematicsに限定する。有限差分epsilon、DLS、gain、限界、
physics/control dt、全substepの異常検査は変えない。実積分と観測整合用のmj_forwardは従来どおり実施する。
これは数値精度・制御頻度を落とす高速化ではなく、不要な確保・計画FKでの接触solver実行を除くもの。
