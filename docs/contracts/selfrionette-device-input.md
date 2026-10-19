---
status: canonical
owner: runtime
last_verified: 2026-10-19
canonical_for:
  - normalized Selfrionette-Device input to finite simulation trials
related:
  - docs/contracts/runtime-input-source-registry.md
  - docs/contracts/finite-trial-runtime.md
  - docs/contracts/workbench.md
  - docs/contracts/continuous-endpoint-velocity-input.md
---

# Selfrionette-Deviceの正規化入力

## 責務と対応範囲

Selfrionette-Deviceの共有Rust `LiveService`が検証・補正した入力を、既存の
`ModelExecution` / `TrialRunner` / Workbenchへ渡す。単手は1個体、両手は明示した異なる2個体を
同じsimulation world内で扱う。Device側のserial取得、setup/profile再検証、tare、個人neutral、
極性、deadzone、filterをSimへ複製しない。

Deviceとのwire契約は
[Selfrionette-Device host bridge契約（公開revision）](https://github.com/Xpotato1024/Selfrionette-Device/blob/21e85ac58c90913da7096966df6a0d8cbb80d57c/docs/contracts/host-input-bridge.md)
を正とする。本書はSimのbinding、時刻対応、試行への渡し方、操作と終了を所有する。
共通の試行・記録は[有限試行runtime](finite-trial-runtime.md)、操作権・renderer・監督は
[Workbench](workbench.md)、登録規則は[Input Source registry](runtime-input-source-registry.md)に従う。

| 識別子 | 値 | 所有する意味 |
|---|---|---|
| Input Source plugin | `selfrionette_device/v1` | 品質付きの正規化済みDevice入力 |
| provider | `selfrionette-device/v1` | 有限試行のsession binding |
| produced sample schema | `selfrionette_device_normalized_sample/v1` | SourceからMappingへ渡す型 |
| application transport | `selfrionette-bridge/v1` | ローカルRust processの所有 |
| Rust command / event | `selfrionette-bridge-command/v1` / `selfrionette-bridge-event/v1` | stdin/stdoutのJSON Lines |
| Sim input envelope | `selfrionette-device-input/v1` | 明示roleと保守的なageを付けた1 sample |
| Source snapshot | `selfrionette-device-snapshot/v1` | 最大2 roleの最新sampleと元identity |
| Mapping | `normalized_endpoint_velocity/v1` | 無次元commandからendpoint並進速度への明示写像 |

旧`selfrionette/v1`のraw count / loadcell schemaとの暗黙変換はない。選択されたSourceと
Mappingが対応しなければ準備を拒否する。Gamepadとの切替は対応するpresetの選択で行い、
Deviceを選択した試行へbrowser Gamepad入力を混ぜない。

## 所有と明示準備

Workbenchのexecution workerが`DeviceBridgeGroup`を所有し、各roleにつき
`selfrionettectl bridge`を1 process起動する。Rust processは明示された1 port / stable IDだけを所有する。
GUIは既存の操作権、revision、ticket、busy、observed phaseの検査を通して操作を送る。
Sourceのimport・profile検証・reader構築ではprocessやserialを開始しない。

Workbenchの「検証・準備」はnative model / viewer assetを準備し、bridgeに`configure`と
時計対応用の`status`を送る。ここではserialを開かない。接続前にDevice Manager等の
既存serial所有を解放し、表示された個体・profile・operator条件を確認する。
以降は左右それぞれで別々の操作が必要となる。

| GUIの操作 | 必要な現在phase | Rust action | 完了後の状態 |
|---|---|---|---|
| 個体を確認して接続 | `configured` | `connect` | 明示portを開き、Protocol 3 / stable IDを検証して`connected` |
| 装置の無荷重を確認してtare | `connected` | `tare` | 新challengeでtareとprofile再検証を行い、producerを止めて`awaiting_neutral` |
| 使用姿勢で力を抜いてneutral取得 | `awaiting_neutral` | `neutral` | `capturing_neutral`から、取得barrier後の16 fresh sampleで`neutral_ready` |
| このprofileとneutralを適用 | `neutral_ready` | `apply` | `applied`。最初のfreshな補正出力が確認できるまでreadyはfalse |
| 試行を開始 | 全roleが`applied`かつready | `start` | `streaming`。共通TrialRunnerの新規入力待機へ |

4つの準備actionは`confirmed: true`を個別に要求する。古いphase、未確認action、暗黙のrole、
処理中の重複actionは拒否する。無荷重tareと使用姿勢neutralは異なる状態で実行するため、
一括確認や自動ready操作を設けない。firmware書込み、EEPROM設定、個人profileの作成・更新は
この操作経路に含まれない。

`apply`直後のready=falseは未準備扱いにし、純粋な`status`を最大10 Hzで取得する。
readyにはclock handshake完了、未応答要求なし、`session_neutral_valid: true`、
source/profile/operatorの存在と完全一致を要求する。片側だけのreadyではStartできない。
Rustは`neutral_ready` / `applied`でも取得を継続し、品質異常やstaleをfatal errorと切断へ進める。
したがって、放置した古いreadyを新たなStartの証拠として再利用しない。

Startは入力公開を始める操作であり、Deviceのchallengeを作り直さない。
TrialRunnerは開始記録の成功後、**その試行開始以後に取得した全roleの新しい中立command**を待つ。
Device準備時のneutral取得だけでsimulationを進めない。Mappingの中立条件は全7 channelの
`command`が厳密に0であること。初回の中立gateはphysics / Task tickを増やさない。

## 凍結する個体・profile・operator条件

launch profileの`input.parameters`は`bridge_revision`と`bindings`を必須とする。
`bindings`は`left` / `right`の1個または2個で、両手ではstable IDを重複させない。
Input、Mappingのweight、`coordination.side_to_endpoint`のrole集合は完全一致が必要である。

| binding内のfield | 必須内容 |
|---|---|
| `source` | `device_id`（`srn-` + 32桁小文字hex）、`firmware_version`、40桁の`source_sha`、整数`protocol: 3` |
| `profile` | `record`、整数`revision` / `assembly_revision` / `config_revision`、検証した`method` |
| `operator` | `operator`、`posture`、`grip` |
| `domain` | `normalized_input` |
| `settings_generation` | 元device設定の整数世代 |

`bridge_revision`はRust host binaryのsource commit、`source.source_sha`はdevice firmwareの
source commitであり、別々に固定する。bridgeの全eventで前者を照合し、準備statusと
各sampleで利用可能なsource/profile/operatorを照合する。sampleではbindingの全fieldを完全一致で検査する。
record名だけが同じ別revision、異なる個体、異なる姿勢・把持・手法、設定世代の変化を受け入れない。

GUIのAdvanced Inputではpresetのrole構造を維持したまま期待identityを編集できる。
exportされた`workbench-condition/v1`では`configuration.input.parameters`に保存され、
条件hashと開始記録の対象になる。実行fileやport、profile絶対pathはこの条件JSONに含めない。
保存済みprofileの実体・setup・held-out検証との対応はDevice共通serviceで再検証する。
Simが記入した期待値だけでDevice profileの妥当性を証明しない。

## ローカルconnection JSON

`workbench --device-config /absolute/device-connections.json`は次の閉じたschemaを読む。
ファイルは64 KiB以内、`executable`と各`profile_path`は存在する絶対file pathとする。
各roleは`port`と`profile_path`だけを持ち、同じportを複数roleへ割り当てない。
role集合は選択した試行のbindingと一致させる。

```json
{
  "schema_version": "selfrionette-workbench-connection/v1",
  "executable": "/absolute/Selfrionette-Device/target/release/selfrionettectl",
  "roles": {
    "left": {
      "port": "/dev/serial/by-id/explicit-left-device",
      "profile_path": "/absolute/device-store/srn-0123456789abcdef0123456789abcdef/calibration-1.srncal"
    }
  }
}
```

これはpathと形状を示す合成例である。実際のID・port・保存profileへ置き換える。
Windowsでは例えば`"executable": "C:\\absolute\\Selfrionette-Device\\target\\release\\selfrionettectl.exe"`、
`"port": "COM9"`のようにJSONのbackslashをescapeする。両手では同じ`roles`に`right`を追加する。

Device profileは`<store>/<stable-device-id>/<record>.srncal`の配置を維持する。
元storeに現在の個体setup recordも必要であり、profileだけを別directoryへ移動しない。
Rustはprofile / device directoryの形状とfileを検証する。`expected_device_id`、operator条件、
methodは凍結済みInput bindingから`configure.config`へ渡すため、connection JSONで上書きしない。
browserから任意の実行file、port、server上のpathを指定する操作は提供しない。

`--device-config`は`--fixture` / `--run-once`と排他である。live接続の起動例は、
事前に実個体の条件へ編集・exportしたJSONと準備済みWeb buildを使う。

```sh
.venv/bin/xpotato-sim workbench \
  --condition /absolute/device-condition.json \
  --device-config /absolute/device-connections.json \
  --temporary-root /absolute/existing-task-temp \
  --result-root /absolute/trial-results \
  --software-revision '<SimのHEADと未commit差分を識別する値>' \
  --open-browser
```

`--condition`には予算・期限も含まれるため`--profile` / `--ticks` / 期限optionと併用しない。
通常のWeb build整合検査は[Workbench起動契約](workbench.md)を維持する。
この起動だけでは接続・tare・neutral・Startを実行しない。

## exact revisionを埋め込んだRust binary

Selfrionette-Deviceのclean checkoutでHEADを確認し、build時だけrevisionを指定する。
build対象の公開bridge sourceは`21e85ac58c90913da7096966df6a0d8cbb80d57c`である。

```sh
# Selfrionette-Device repositoryで、差分がないことと対象HEADを確認する。
git status --porcelain
git rev-parse HEAD
SELFRIONETTE_BRIDGE_REVISION="$(git rev-parse HEAD)" \
  cargo build --locked --release -p selfrionettectl
```

PowerShellでは同じclean checkoutで次のようにbuildする。

```powershell
$env:SELFRIONETTE_BRIDGE_REVISION = (git rev-parse HEAD).Trim()
cargo build --locked --release -p selfrionettectl
Remove-Item Env:SELFRIONETTE_BRIDGE_REVISION
```

出力されたbinaryをconnectionの`executable`へ、同じ40桁revisionを
`configuration.input.parameters.bridge_revision`へ設定する。
未指定buildは`unknown`を返し、Rustはconnectを拒否する。実行時の`--revision`上書きはない。
build変数はtraceabilityの情報であり、sourceの署名検証やhardware acceptanceではない。
dirty checkoutにclean HEADだけを付けて同一buildと扱わない。

## sampleと時刻の保存

Rustのsample eventは元frameの`session`、`sequence`、`device_ms`、`settings_generation`、
`tare_epoch`、`quality_flags`を持つ。`host_received_us`はRust serviceの元受領時刻、
`host_emitted_us`は全event共通のstdout writerが書込み直前に付ける時刻である。
どちらも`host_clock: "bridge_process_elapsed_us"`の整数microsecondsで、Pythonの
`monotonic()`と原点を共有するとは仮定しない。

Simのenvelopeは次の4 fieldだけを持つ。

| field | 意味 |
|---|---|
| `schema_version` | `selfrionette-device-input/v1` |
| `role` | connectionと凍結条件で束縛した`left` / `right` |
| `source_age_s` | Python readerが受領した時点での、IPC滞留を含む保守的な入力age |
| `event` | 元Rust sample event。identityや値を再採番・再計算しない |

sampleの完全な形状は[共通の合成fixture](../../tests/fixtures/selfrionette_device/bridge-sample-v1.json)で検査する。
`normalized`と`command`は無次元の7 channelであり、Nや速度ではない。
Simは`command`をMappingへ渡す。未知・欠落field、重複JSON key、非有限値、null値、
範囲外値、全7 channelのfresh flag `1`以外、span超過、非対応方向、欠測、ready=falseを拒否する。
品質flagやepochの欠落を中立へ変換しない。

### IPCのclock handshake

configureの対応statusを受けた後、短い純粋な`status`を1回送る。
要求IDをechoする返信について、Python送信時刻を`L_send`、Rust writer時刻を秒換算した値を
`B_emit_status`とし、`C = L_send - B_emit_status`をprocessの存続中固定する。
Python受領までのRTTは0以上200 ms以下を要求する。超過・request不一致で校正できなければreadyにしない。
tareやneutralの長い処理でoffsetを取り直さない。

sampleのPython受領時刻を`L_recv`、Rust受領・writer時刻を秒換算した値を`B_recv` / `B_emit`、
`relative_backlog_ms / 1000`を`backlog`とすると、実装は次のageを保存する。

```text
C = L_send - B_emit_status
source_age_s = max(
    L_recv - (C + B_recv) + backlog,
    sample_age_ms / 1000 + L_recv - (C + B_emit)
)
effective_receipt_s = L_recv - source_age_s
```

statusのwriter時刻は要求送信より後にしか発生しないため、写像されたreceiptは保守的に過去側となる。
初回の固定IPC滞留を最小offset推定で消さない。emission逆行、受領より後に写像されるemission、
receiptがemissionより後のeventは拒否する。queueで待った時間も古さへ加算する。
ADC取得から最初のhost受信までの絶対latencyはこの方法で測定できない。

### freshnessとproducer順序は別に検査する

`sample_age_ms`は切上げ整数なので、正常なburstでもageから推定した`effective_receipt_s`が
1 ms未満程度過去側へ揺れる場合がある。この揺れを元sampleの逆順と扱わない。
`SessionInputMessage.producer_receipt_s`に元`host_received_us / 1000000`を保持し、Sourceと
TrialRunnerはroleごとにsession一定、sequence厳密増加、device time非逆行、producer receipt非逆行を検査する。

Source snapshotは全roleで最も古い保守的receiptをfreshnessに使い、順序検査済みの
producer receiptの最小値を`ordering_receipt_s`として共通`CoordinatedInput`へ渡す。
`ordering_receipt_s`は順序確認の値であり、Python上の取得時刻やrole間の同期時刻ではない。
共通runtimeのstale判定には引き続き保守的な`received_at_s`を使う。
Gamepadなどproducer receiptを持たない既存入力は従来のreceipt順序検査を維持する。

control tickやUI再描画、空poll、statusを新しいsampleにしない。同じsampleを参照する間も
session / sequence / device time / receiptを保持し、現在時刻との差としてageを増やす。
全roleが揃う前は入力待機を続け、実行開始後に片側が古くなればworld全体をtechnical invalidとして終了する。
登録Device presetの入力鮮度上限は0.2秒。Rust writer自身の250 ms失効とは別にSim側でも検査する。
開始記録前へ位置する保守的receiptのsampleはworkerが捨て、次の新規sampleを待つ。

## Mapping

各roleの`channel_axis_weights`は7行3列で、論理channel順の`command[i]`を使う。
各軸の速度は`speed_m_s * sum(command[i] * weights[i][axis])`で計算し、3次元速度のノルムを
`max_speed_m_s`以下に制限する。`control_frame`は`world` / `tool`を明示する。
全weight、速度gain、速度上限を条件に固定する。

身体部位・物理配線・左右極性から軸を推測しない。Device補正後の値に追加のdeadband、
filter、tare、neutral補正を掛けない。目標積分、座標変換、IK、制約とnative commitは既存の
[endpoint velocity経路](continuous-endpoint-velocity-input.md)が所有する。
Viewerは確定済みの入力・個体・profile・epochを表示し、別のphysicsや補正を計算しない。

## 有界受渡しとSTOP / retry

| 境界 | 上限と失敗時の扱い |
|---|---|
| Rust JSONL | LFを含め64 KiB、stdin 8 command、stdout 32 event |
| Rust取得owner | 1 tickで16 frame / 20 msまで。queue投入でpipe writerを待たない |
| Python bridge | 1行64 KiB、command 8 KiB、roleごとにevent queue 64 / 未応答command 1 |
| Python pipe workers | 各roleにreader / writer / stderr reader各1。stderrは直近8 chunk × 1024 bytesまで |
| Python要求deadline | 未応答15秒で失効。時計handshakeにはさらに200 msのRTT上限 |
| 共通TrialRunner | 1 input batchは64件、Sourceの最新値保持は最大2 role |

queue overflow、EOF、非対応schema / revision、clock逆行、fatal error、不正品質やidentity不一致を
入力の失効へ進める。片方のbridgeで失敗すればgroup全体をcloseし、片側の部分成功を再利用しない。
稼働中ならTrialRunnerへtechnical invalidとして記録する。準備中ならStartを許可しない。

STOPはbridge全processをcloseしてから既存の試行停止へ進める。開始前のSTOPはready ticketと
準備assetを取り消す。実行中のSTOPは`operator_abort`を記録し、正常な記録完了後の明示retryで
同じnative model / assetを再利用できる。retryは新しいticketとbridge processを作り、
connect / tare / neutral / applyを全roleでやり直す。前試行のDevice sessionは拒否する。
終端・fault・app終了でもbridgeをcloseし、prepared Deviceの操作権ownerが切断した場合も
既存のsupervised STOP経路を使う。

prepareのasset公開失敗、retryのbridge構築失敗でも所有済みbridgeをcloseし、未開始readyを撤回する。
adapter不在のDevice試行をGamepadへ置き換えてStartしない。retryが失敗しても過去の正式結果を上書きしない。
Pythonのcloseはstdin EOFから150 ms待機、必要ならterminate後150 ms、kill後150 msと進み、
process終了を確認する。pipe threadのjoinは各100 msまで。終了を確認できなければ失敗を表面化し、
Workbenchの既存process group / job監督で回収する。

## 合成preset・software fixtureと実機条件

[単手preset](../../profiles/fast-arm-single-device.json)と
[両手preset](../../profiles/fast-arm-bimanual-device.json)はsoftware検証のための具体的な合成条件である。
同梱のdevice ID、firmware / bridge SHA、profile record、operator、7×3 weight、速度gainは
実個体を同定・受入した値ではない。実接続には実際の証跡と対応する条件へ明示編集する。
fixtureに合わせるため実機の期待identityや品質検査を緩めない。

接触力の表示比較には[dynamic単手preset](../../profiles/dynamic-fixed-contact-single-device.json)と
[dynamic両手preset](../../profiles/dynamic-fixed-contact-bimanual-device.json)を使える。
上記Device bindingとMappingを、既存`fixed_contact` Environment / native dynamics /
`contact_observation_task/v1`へ合成しており、別の物理loopは持たない。
条件を展開したAdvanced表示欄で`no_force_cue` / `force_cue`を選び、物体位置も既存editorで明示編集する。
両表示条件の比較では入力・scene・モデル・Mappingを同じに保つ。単手と両手は初期手先位置が異なるため、
小さいsoftware入力で同じtick数以内の接触を試すときは、cube位置を各モデルについて条件へ固定する。

[単手fixture](../../tests/fixtures/selfrionette_device/fast-arm-single-device.json)と
[両手fixture](../../tests/fixtures/selfrionette_device/fast-arm-bimanual-device.json)は
`trial-input-fixture/v1`を使う。明示したprovenance、input plugin / provider / parameters、
各sampleの`offset_s`とmessageを保存する。1 fileは1 MiB、sampleは1..10000件、durationは3600秒以内、
1 messageは64 KiB。role内ではoffset / sequenceを厳密増加させ、同じroleのsession変更を拒否する。
異なるroleの同じoffsetは許可する。fixtureのInput bindingは選択profileと完全一致が必要である。

準備済みPython環境のSim repository rootで、例えば次を実行する。これは実serialやRust bridgeを起動しない。
`--software-revision`には実行したSim sourceのHEADと未commit差分を識別する値を入れる。

```sh
.venv/bin/xpotato-sim trial \
  --profile fast-arm-single-device \
  --fixture tests/fixtures/selfrionette_device/fast-arm-single-device.json \
  --ticks 12 \
  --result-root /absolute/software-trial-results \
  --software-revision '<Simの実行source identity>'
```

Workbenchと同じservice / workerをheadlessで通す場合は次を使う。temporary rootは既存の絶対directoryを指定する。
`--run-once`はこの明示fixture試験のStartに相当し、Web buildやrenderer ACKを要求しない。

```sh
.venv/bin/xpotato-sim workbench \
  --profile fast-arm-bimanual-device \
  --fixture tests/fixtures/selfrionette_device/fast-arm-bimanual-device.json \
  --run-once --ticks 12 \
  --temporary-root /absolute/existing-task-temp \
  --result-root /absolute/software-workbench-results \
  --software-revision '<Simの実行source identity>'
```

software fixtureは元messageを1回ずつ共通runnerへ渡す。fixture ID、SHA-256、bytes、sample数、
duration、provenance、Input bindingを開始記録へ残す。実入力ではbridge revisionとoperator準備状態を
開始記録へ残す。いずれも同じ正式記録・有限予算・明示再試行の契約を使う。

## 検証証拠と未実施の受入

source / Mapping検証は各plugin配下、clock / pipe / process検証は
[bridge tests](../../tests/runtime/test_device_bridge.py)、共通実行は
[Device trial tests](../../tests/runtime/test_device_trial.py)、workerの準備・STOP・retryと失敗cleanupは
[Device worker tests](../../tests/runtime/test_workbench_device_worker.py)が所有する。
[Deviceと反力の統合検証](../../tests/runtime/test_device_force_integration.py)は、単手・両手の同じ元入力で
両表示条件のqpos / qvel、raw dynamics、反力signal、Taskが一致し、表示だけの変更でnative modelを
再利用することを検査する。観測条件・実行方法・証拠の限界は
[software統合note](../experiment-notes/2026-10-19-device-input-integration.md)へ分けて記録する。

実Rust binaryとの互換確認では、build時の公開revisionを固定し、Simの実`BridgeProcess`から
serialを開かないconfigure → dedicated status → stdin EOFを実行する。
`configured`、clock同期済み、ready=false、sample 0件、exit 0と全pipe thread終了を確認し、
connect要求は送らない。観測したsource、binary digest、時刻と限界は上記software統合noteを参照する。

実serial、実board、実操作者による無荷重tare・使用姿勢neutral、個人校正、二台同時運転、
Windows配布binaryのserial / pipe挙動、ADC rateと絶対latency、robot運動の受入は未実施である。
fixtureや上記handshakeをhardware acceptanceへ読み替えない。
Rust sampleの`hardware_acceptance: false`はこの境界を明示し、受信成功や準備ready自体を
実機・研究条件の受入証拠にしない。
