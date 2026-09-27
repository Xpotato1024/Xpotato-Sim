---
status: canonical
owner: runtime
last_verified: 2026-09-26
canonical_for:
  - coordinated arm diagnostic execution and output supervision
related:
  - docs/contracts/fast-arm-assembly.md
  - docs/contracts/gamepad-plane-control.md
  - docs/contracts/physical-output.md
---

# 複数手先の共同実行と出力監督

## 対象と実行意味

左右のGamepad入力を名前付き手先速度へ写し、FastArm assemblyの全腕を一つのpre-step snapshotから計算する。
単腕original、単腕mirrored、双腕を同じproviderで扱う。左右の物理取付位置・実機校正は推測しない。

この入口は `coordinated_joint_position_kinematic/v1` の**運動学診断**である。
従来viewerのdirect-qpos意味を明示して共同更新へ拡張し、全腕の位置をまとめて反映して `mj_forward` を行う。
`mj_step`、servo追従・動的接触、全体collisionの実験経路ではない。時間は固定dtの診断時刻であり、
実測速度・実機性能とは呼ばない。元の衝突無効meshを安全性の証拠にしない。

## 責務

| 所有者 | 責務 |
|---|---|
| `schemas/coordinated.py` | 手先ID付き速度、取得情報、名前付き観測の型と入力検証 |
| Mapping `map_coordinated_input` | 既存平面状態機械・正規化・ゲインを共有し、明示side bindingからtyped要求を返す |
| Robot `adapter/coordinated.py` | 原本assembly、名前address、既存DLSと限界の適用、全腕候補と一括反映 |
| `runtime/execution/coordinated.py` | epoch、入力鮮度・系列、中立、実行・停止・fault latchと再開 |
| `runtime/composition/fast_arm_coordinated.py` | Source、Mapping、Robot provider、共同runtimeを接続する唯一の具体owner |
| `runtime/output/coordinated.py` | 全側prepare、追加scene veto、逐次dispatch、全体fault、全側停止試行 |

legacyのsingle-endpoint intentを二腕へ複製せず、診断metadataの左右velocityをcommandへ逆生成しない。
旧 `map_input` と新しいtyped入口は同じ `_build_plane_intents` を使い、写像を二重実装しない。
`side_to_arm` は明示・copy/freezeし、選択した全armを重複なく覆う。旧 `output_side` はsingle-endpoint用であり、
共同入口のbindingを上書きしない。既存launch profile/v1・Robot Plugin/v1の意味は維持する。
このproviderは明示composition/診断CLI用であり、既存GUI/汎用catalogへ双腕profileを登録したものではない。

## 同一状態からの候補とcommit

各armのIKは同じMuJoCo snapshotのコピーを見る。一腕のcandidateを次の腕の初期状態へ混ぜない。
4関節分の既存 `LocalEndpointMotionGenerator` と同じ有限差分・減衰・step上限を各armへ用い、
元のjoint-limit設定を個別に照合する。tool方向は当該armの同じsnapshotのsite姿勢からworldへ解決する。
全腕を新しい作業dataへまとめ、finite値、MuJoCo warning、関節限界を検査する。

`PreparedCoordinatedStep` は当該providerが発行した同一objectだけを一度消費できる。
外部コピー、他provider、古いgeneration、reset後、候補の変更は拒否する。
失敗した作業dataを公開せず、成功時だけロック内で一つのlogical worldのdataを交換する。
これはソフトウェアでの共同反映であり、実機への通信を原子的にする保証ではない。

## 時計・停止・復帰

`source_timestamp_s`、Sourceが実際に取得した `last_received_at_s`、host clock、simulation timeを分離する。
同一sampleを再評価してもreceiptを更新しない。系列tokenはprovider sessionとGamepad報告index/idを結合したhashであり、装置報告の変化も再preflight対象とする。これは装置固有serialや認証ではない。出力するJointPositionCommandのtimestampはhostの評価時刻であり、
simulation時間を実機要求の鮮度へ流用しない。モデル時刻はsnapshot側に保存する。

起動はwaiting_neutral。新鮮な中立とprovider preflight後にrunningへ進む。
モード切替中の一側ゼロは通常の操作であり、他側の正常入力を止めない。
欠落arm、invalid/stale/disconnect、provider変更、逆順、同sequenceの異内容、epoch不一致、
非finite、候補・commit・snapshot失敗は全体faultをlatchする。正常入力復帰だけでは解除しない。

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

`poll()` はcallerの周期schedulerで実行する。Python process停止・通信断・OS停止に備えるreceiver watchdog、
独立非常停止、停止指令の機種別実装・検証は別の必須条件であり、このクラスやUDP二送信では実現しない。
この変更には実機へ接続するlauncherや停止OSC commandの捏造を含めない。

## 有限診断CLI

```powershell
uv run python -m xpotato_sim.runtime.runners.coordinated_gamepad tests/fixtures/coordinated_gamepad/bimanual.json
```

入力は `coordinated-gamepad-diagnostic/v1`。明示assembly、side binding、Mapping parameters、epoch、dt、
入力期限、host時刻付き保存メッセージを指定する。標準入力デバイスやnetworkを開かず、最大10000 sample、
最大4 MiBのJSONを検査する。重複key・NaN・不正schemaを拒否する。fixtureは合成の操作条件であり実機設定ではない。
結果は各tickの入力、before/after、モデルdigest、関節要求とterminal状態をJSONへ出力する。
元の設定/入力、ソフトウェアrevisionと実行commandも一緒に保存する。正式なparticipant artifactには数えない。

## 残る接続

ブラウザの双腕scene declaration/同時操作、汎用catalog/GUI、二台Selfrionette取得、衝突geometry、
servo/contact経路、ばね/搬送taskは後続。OSCの具体receiver停止・全体scene評価と実機検証は未実施。
本経路の成功をそれらの完了や高トルク機体の安全認定へ読み替えない。
