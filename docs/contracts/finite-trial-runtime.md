---
status: canonical
owner: runtime
last_verified: 2026-10-03
canonical_for:
  - finite model trial condition lifecycle and local result
related:
  - docs/contracts/launch-profile.md
  - docs/contracts/coordinated-arm-runtime.md
  - docs/contracts/object-scene-contact-diagnostic.md
  - docs/design/adr/0012-finite-trial-runtime.md
---

# 有限試行の条件・実行・結果

## 所有と対応範囲

`TrialRunner`はprofile未選択で生成でき、その時点ではモデルも入力も作らずphysicsを進めない。
名前付きモデルv2/v3/v4の原型・左・右・双腕、幾何診断・動力学を同じ`ModelExecution`へ接続する。
モデルproviderが唯一のMuJoCo step/reset所有者、Taskが課題の終端所有者、runnerが試行寿命と記録の所有者である。
CLIと[Workbench GUI](workbench.md)はrunnerを呼ぶ。v1、serial、OSC、未知Source/Mapping、任意plugin codeは新trialの対象外として拒否する。
旧publisherは同じ実行を使用するが、従来の表示frame予算、入力時刻、payload、旧CLIを維持する。

## 固定条件と来歴

`trial-condition/v1`は実効profile fieldを再検証し、解決済みMapping、Robot/model登録、生成model digest、
scene全体とEnvironment identity、Task selection/parameters、dynamics、dt、入力freshness、有限予算、
providerが公開する関節・actuator・controller数値条件をcanonical JSONへ固定する。
変更済みfieldを古い`document_json`や`effective_parameters_json`で置き換えない。矛盾するroute/providerは拒否する。
不変のJSON bytesを保存し、閲覧用objectはcopyとする。host/port/workspace/result root、旧profile epoch、trial ID、
profile名・保存pathは意味digestへ含めない。元profileのcanonical bytesとSHA-256は別のprovenanceとして保持する。
Evaluation、表示比較条件、participant、seedによる乱数試験は本経路で選択せず、未評価を明示する。

## 状態と時計

`unselected → preparing → ready → waiting_input → running → finalizing → terminal`。
`start`はreadyだけ、`retry`は正常に記録されたterminalだけで新trial ID/epochを発行してreadyへ戻す。
retry自体は開始しない。Task successとrunner停止理由は別fieldで、Taskなしをsuccessと補完しない。
保存失敗は`recording_failed`、prepare/reset失敗は`faulted`で停止し、同ownerの再実行を禁止する。
Workbenchのprepare/reset失敗からの明示prepareは旧runnerをcloseして新しいrunnerを作る。
記録失敗はfinalization完了後の失敗分類である。Workbenchは原因確認後の明示prepareで失敗runnerをcloseし、新runner・新trial ID/epochを準備できる。失敗fileを上書きせず、同ownerのretry・自動開始は行わない。
開始前だけ`discard_prepared()`でnative参照とticketを破棄して未選択へ戻せる。

simulation予算は成功したprovider commitによるtick数×control dtだけを消費する。
dynamicでは実際の`mj_step` substep、kinematicでは明示されたqpos反映とsimulation時刻更新を意味し、
kinematicを力学積分と呼ばない。ready、入力中立待ち、表示sample、terminalでは消費しない。
prepare上限、開始記録成立後の入力待ち上限、startからの総wall上限は正の有限秒として別指定する。
`started_monotonic_s`は開始要求処理時刻を維持する。`input_accepting_monotonic_s`は開始記録のflush/fsync/
read-back/排他的公開が成功した後のhost時刻で、statusとterminal記録へ残す。入力待ち期限は後者から測る。
Start前は`input_pre_trial`、Start以後でも記録成立前のreceiptは`input_pre_recording`として拒否する。
receiptの下限と共同runtimeの中立下限は同じ`input_accepting_monotonic_s`（以上）へ設定する。
future receipt、旧ticket、記録中のreceiptは区別して拒否する。同clock bucket内の時刻順序をns精度と主張せず、serviceのStart busy gateも維持する。
記録失敗では入力受付時刻を成立させない。renderer ACK、明示Start、記録、新epochのfresh neutralの順を維持する。
入力が空でもadvanceでwall/待機期限へ到達する。callerは同期advanceを継続して監督する必要がある。
実時刻はmonotonicで後退を拒否し、既存presetの入力freshness 0.2秒は変更しない。

## 入力・所有権・reset

全mutatorはowner thread限定、非再入guard付き。二重start、active中prepare/retry、旧epoch、終端後入力を拒否する。
Gamepad inputは明示epochと元のtimestamp/sequence/sessionを保ち、一度受領したsampleを再受領して鮮度を更新しない。
fixtureは有限の時刻付きGamepad message列で、受領予定時刻を実monotonicへ一度だけ対応させる。
遅延dispatchで古いsampleを現在時刻へ繰り上げない。欠測・stale・切断をneutralへ補完しない。

ingressの受信時刻検査は`input_invalid_timestamp`（型・非finite）、`input_pre_trial`（Startより前）、
`input_future`（host現在時刻より後）、`input_stale`（ageが実効上限より大きい）を区別する。
有限な受信時刻では元のreceipt、host現在時刻、Start時刻、実age秒、実効limit秒をerrorへ残す。
pre-trialを先に判定し、重複するstaleとの分類を決定する。判定境界の0.2秒、元timestamp、
technical_invalid終端、新しい中立を伴う明示retryは維持する。
共同runtimeの鮮度違反も`input_stale_or_future`にcause・実age・limit・receipt・host時刻を付ける。
browserのsource timestampは同source内の順序検査用で、host receiptへの時刻変換には使わない。

resetは全object/Robotのqpos/qvel/actuator/control/time・warmstart等をhomeへ戻し、pending ticketを無効化する。
Source/Mapping/Task/filter/dwell/timerは新しい試行objectに置換し、前試行への参照を解放する。
model、固定asset、FK/candidate用MjDataは再利用可能。runnerは最新結果1件、入力1件、pending ticket1件までを保持し、
過去trial列・旧epoch列・frame列を蓄積しない。条件変更は旧execution参照を解放して再構築する。

## ローカル結果

caller指定rootの新規trial directoryへ`condition.json`、`initial-state.json`、`start.json`、
`final-state.json`、`terminal.json`を排他的に作成する。各参照にSHA-256を付け、terminalを最後のcommit markerとする。
start前のreadyではfileを作らない。start記録成功前には入力もphysicsも受け付けない。
結果objectは不変bytesからcopyを返す。既存trial/fileを上書きせず、再度finalizeしない。
各fileは同じtrial directoryの`.pending`へ排他的に書込み、flush/fsync/read-back完了後に
hard linkで最終名を排他的公開する。hard linkを提供するローカルfilesystemを要求し、非対応時は保存失敗となる。
公開前の失敗では最終名を作らず、途中記録は不完全なまま残す。公開後のstaging unlink失敗だけでは
成功を失敗へ反転させず、同じ不変byteの`.pending` linkが残り得る。terminalの最終名がcommit markerである。
stateはmodel identityと全MuJoCo integration stateを保存し、描画用関節sliceだけへ縮退しない。
保存失敗では可能な限り停止し、Task outcomeとは別の記録失敗を返す。途中fileを成功結果として読まない。
OS crash耐久性、改竄防止、完全metric、全frame replayの保証ではない。per-frame fileは生成しない。

Workbenchの終端保存は専用spawn processへimmutable final snapshotだけを渡し、ownerは`finalizing`で停止する。
保存processは開始記録成立後のStart gateで起動し、最大2秒でreadyを確認してから入力受付境界を設定する。
終端では準備済みprocessへ最大64 KiBの内部snapshotを渡す。外部pickle入力を受け付けない。
保存processは検証済みpendingまで作り、期限内にownerが結果を採用した時だけ`terminal.json`を公開する。
`advance`は保存結果をpollし、physicsを進めない。結果確定前はretryを許可しない。
保存期限は終端処理開始から2秒、かつ開始要求からwall予算＋2秒以内で、既存STOP監督の2秒に対応する。
期限超過は保存processをterminate/joinし、必要ならkill/joinしてから`recording_failed`と未確定の原因を報告する。
完了markerを偽装せず、期限後に遅延writerが成功markerを書かない。pending/最終stateは不完全な証拠として残り得る。
closeも最大2秒のpoll後に同じ回収を行い、無期限future/executor待ちを行わない。
serviceは明示STOPの既存2秒に加え、通常Task終端のfinalizingから4秒と開始受付から実効wall＋4秒で所有workerを強制回収する。
STOP完了応答は保存の成否確定後に返す。保存失敗は`recording_failed`を維持する。
同期CLIは従来の同期記録を維持する。開始記録は開始command内の同期gateで、running周期へdisk waitを入れない。

## 有限CLIと検証境界

`xpotato-sim trial --profile <name/path> --fixture <path> --result-root <path> --ticks <n> --software-revision <revision>`は
明示fixtureとrunnerだけを使い、独自physics/Taskを持たない。待機・wall・prepare予算はCLIから指定可能とする。
`--input-wait-s`、`--wall-s`、`--prepare-s`の既定は5、60、30秒。`--ticks`は必須で、
旧profileの`steps`を黙ってtrial予算にしない。headless cadenceはcontrol dtで、旧`interval_s`は配信用設定のままである。
`--software-revision`はcallerが実際のsource revisionと未commit変更identityを明記する。自動で検証済みと扱わない。
記録成功かつsimulation予算到達またはTask successはexit 0、Task failure・timeout・technical invalid・保存失敗はexit 1、
割込みは可能な範囲でabort記録してexit 130とする。exit 0を課題達成の証拠に読み替えない。

fixtureは`trial-gamepad-fixture/v1`、`fixture_id`（lowercase英字で開始、英数字/underscore、48文字以内）、
`provenance`（1〜1024文字）、`samples`を持つstrict JSONである。1 MiB、1〜10000標本、最大offset 3600秒。
各標本は単調増加する`offset_s`と既存`viewer_gamepad_sample/v1`の`message`を持ち、provider・session・sequenceを明示する。
同じsource session内のsequenceとoffsetを狭義単調増加、source timestampを非減少として検査する。
先頭に中立標本がない場合も中立を追加せず、既存中立gateと待機上限へ従う。
fixtureの元byte SHA-256、ID、byte数、標本数、期間、来歴をstart記録へ保存し、標本のtimestamp自体は変更しない。
例は`tests/fixtures/trial_gamepad/short-movement.json`。これは明示したsoftware検証用記録で、実機・参加者からの取得を主張しない。
隠れたneutral/noop defaultは存在しない。
100回以上のnative短試行で構築回数、参照、queue/cache、warm-up後のtracemalloc/object数/process memoryを測る。
これはsoftware validationで、RSS単独によるリーク断定、participant/実機性能、GUI完了、#584全体完了を主張しない。

## 有界入力batch（#610）

`TrialRunner.ingest_batch`は最大64件の同trial入力を元receipt順に検査・Mapping消費する。
過去sampleをphysicsへ再生せず、最新sampleのhost鮮度を消費後に検査する。各sampleのprovider/schema、
session/sequence/source timestampと利用可能性の検査は省かない。途中の不正入力は試行を無効にする。
receipt gapは診断だけに記録し、tick前に新鮮な入力を取得済みならgapだけでは終了させない。単一`ingest`もこの経路を使用し、stale入力を復帰手段にしない。
中立自身のreceiptがtick時に0.2秒以内で、同source epochかつ試行開始以後の最初のbatchは次のtickでpreflightだけを通し、Task/physicsの進行数を増やさない。
Workbenchはowner内の`pending_input` callbackをadvance入口で呼び、受付loop後の到着分も消費する。
さらにread/Mapping後のsampleとhost nowでstaleを検出した場合、fault前に同callbackを一度だけ呼ぶ。
新入力を順序消費した場合とdeferred制御操作がある場合はphysics/Taskを進めずownerへ戻る。
workerは`check_freshness=False`でbatch鮮度の最終判断をtickへ委ねる。通常の`ingest`/`ingest_batch`は従来のstrict検査を維持する。
空queueでのstale、future、pretrial、clock逆行、不正入力の拒否や旧ticketの境界は維持する。
STOP/close待ちや64件上限ではtickを延期するが、wall/input-wait監督は先に実行する。
tick時のstrict freshness、formal Task結果、記録と明示retryの契約は維持する。
