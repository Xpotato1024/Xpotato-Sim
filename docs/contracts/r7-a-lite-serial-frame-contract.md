---
status: canonical
owner: architecture
last_verified: 2026-10-09
canonical_for:
  - R7-A-lite serial frame contract
related:
  - docs/README.md
---

# R7-A-lite Serial Frame契約

Current ownership: serial parsing、diagnostic accumulation、7-channel acquisition、intrinsic calibration /
normalization、sensor clamp、health、lifecycleは
`src/xpotato_sim/plugins/input_sources/selfrionette/`が所有する。operational deadzone、channel-axis
weights、gain、sign、endpoint delta、MotionCommand conversionは
`src/xpotato_sim/plugins/mappings/loadcell_endpoint_mapping/`が所有する。offline dry-run orchestrationは
`src/xpotato_sim/runtime/runners/selfrionette_serial_dry_run.py`が所有し、versioned Mappingを明示する。

current architectureではserial parserと7-channel `RawInputFrame` acquisitionを
`selfrionette/v1`へ接続する。recorded / injected linesは別production identityではなく、同じdevice
readerのhardware-free backendとして`loadcell_vector_sample/v1`を生成する。
channel-axis mapping、gain、endpoint delta、`MotionCommand`生成はこのsource contractの外に残す。

## 対象範囲

この文書はSimの`selfrionette/v1`が受信する旧7ch serial frameと、backend source pluginへの
接続境界を固定する。firmwareのproducer仕様・build・physical calibrationはDeviceが所有する。
[旧producer仕様](https://github.com/Xpotato1024/Selfrionette-Device/blob/79c84bce463da2a2ff01d90ec81d9baac746c7d1/docs/contracts/legacy-sim-serial-producer.md)と
[firmware入手・build入口](../operations/selfrionette-firmware-location.md)を参照する。
現行Protocol v2のidentity、EEPROMやmanagement commandをこのv1互換契約で規定しない。
software-side intrinsic normalizationとDeviceのphysical tare/calibrationを同一視しない。

## 受信互換性

line-based ASCIIをbaud `115200`で受信する。CRLFを許容し、quoted CSV・escaping・multi-lineは扱わない。
vectorはexactly 9 comma-separated fields、channel orderはch0からch6である。

```text
status,<message>[,<channel>,<value>]
warn,<reason>,<channel>[,<value>]
vector,<timestamp_ms>,<ch0>,<ch1>,<ch2>,<ch3>,<ch4>,<ch5>,<ch6>
```

`timestamp_ms`はdeviceが送ったboot-relative millisecond値として保持し、host receiptと直接減算しない。
vector値はproducerのzero/spike handling後のsensor readingであり、未校正のraw値やNを推測しない。
finite numericを要求し、status/warnはsensor recordと分離する。
pin、sampling/calibration algorithm、timeout時のproducer動作はDevice側の旧producer仕様を正とする。
元のhardware bring-up noteは当時の観測を保持し、今回の移行やcompileを実機受入の証拠にしない。

## parser要件

parserは次のruleに従う。

- `vector` lineだけをsensor recordへparseする。
- 各`vector` frameにexactly 7 numeric channel valueを要求する。
- `timestamp_ms`を保持する。
- `status` lineをignoreするか、diagnosticとして別途公開する。
- `status,calibration_command_received`をdiagnostic eventとして扱う。
- `warn` lineをnon-vector diagnostic eventとして公開する。
- `warn,calibration_spread,<channel>,<spread>`をdiagnostic eventとして扱う。
- malformed `vector` lineをrejectする。
- missing channel fieldをrejectする。
- 将来のcontractが明示的に許可しない限り、extra `vector` channel fieldをrejectする。
- non-finite numeric valueをrejectする。
- parser testではserial portをopenしない。
- small text fixtureだけを使用する。
- parser testではfull transcript、CSV、PNG artifactを要求しない。

## Source plugin factoryとlifecycle

`selfrionette/v1` factoryはI/O前に次をfail-closedで検証する。

- port指定時はnonblank stringである。
- baud rateはpositive integerである。boolはintegerとして受理しない。
- injected linesはtupleで、各要素はstringである。
- portとinjected linesを同時に指定しない。
- portとinjected linesのどちらもないconfigを受理しない。

factory creationはserial moduleをimportせず、portをopenしない。managed runtimeのexplicit `start()`だけが
live portをopenする。`read_frame()`は未start時に
`Selfrionette input source is not started`でfail-closedし、暗黙startを行わない。
closeはnormal / failure / start failure後に最大1回試行し、primary failureをcleanup failureで置換しない。

injected backendは同じparserと`loadcell_vector_sample/v1`を使用し、real serialをopenしない。
runnerが受け取るone-shot `Iterable[str]`は一度だけtuple化し、同じrecorded linesをSelfrionette
factoryのruntime dependencyへ渡す。
parser、baud `115200`、diagnostic accumulation、7-channel vector semanticsは変更しない。


## 有限取得とhealth（R7-L-P2）

`selfrionette_acquisition/v1`はsource-ownedなsoftware保護policyである。
実機cadenceの測定値やhard real-time保証ではない。

| 上限 | 値 | 対象 |
|---|---:|---|
| 1回の取得deadline | 0.25 s | vectorを返すまでの全line読取りで共有 |
| 1回の取得line数 | 64 | diagnosticも1件として数える |
| 1lineの最大長 | 1024 bytes | UTF-8 bytes、CR/LF終端を含む |
| 診断保持件数 | 64 | 古い診断から破棄し、破棄数を記録 |
| stale境界 | 250 ms超 | 最後に成功したvectorのhost受信時刻からの経過 |

live backendは有限timeout付き`serial.Serial`とsize付き`read_until`を使う。
各読取り前に残り時間へtimeoutを縮め、復帰後にもdeadlineを確認する。
空readを繰り返さず、途中line、不正UTF-8、上限超過を完全なvectorとして受理しない。
詳細なAPIの根拠は[pySerial公式API](https://pyserial.readthedocs.io/en/latest/pyserial_api.html#serial.Serial.read_until)。
OS/driverの応答性は別の実機検証事項であり、任意のblocking callableを中断できるとは主張しない。
共有`SerialInputSource`もline数/bytes/diagnostic件数を制限するが、任意callerの処理時間は所有しない。

### 遷移と失敗の扱い

| 事象 | health | 呼出結果と回復条件 |
|---|---|---|
| NEW / CLOSED | disconnected / not_started | readは未開始エラー。明示startが必要 |
| start成功、vector未確認 | inactive、age=None | startそのものをsampleの証拠にしない |
| start後250 ms超でvector未確認 | stale / no_vector_received、age=None | 次の成功vectorでactiveへ戻れる |
| 完全なvectorを受信 | active | 全zeroも有効sample。command zeroとの判断はMappingの責務 |
| 最後の成功受信から250 ms超 | stale / sample_stale | 新しい成功vectorでactiveへ戻れる |
| 空read / 共通deadline超過 | stale / read_timeout | SerialAcquisitionError。sessionへラッチ |
| 64行でvectorなし | stale / no_vector_line_budget | SerialAcquisitionError。sessionへラッチ |
| malformed / partial / oversize / decode不正 | invalid / invalid_serial_frame | 元のparse/decode例外を保持。sessionへラッチ |
| host時計が逆行・非finite・不正 | invalid / invalid_monotonic_clock | readはSerialAcquisitionError、health照会はinvalid。sessionへラッチ |
| injected EOF | disconnected / end_of_stream | 既存StopIterationを保持。sessionへラッチ |
| serial I/O例外 | disconnected / serial_io_error | 元のOSErrorを保持。sessionへラッチ |
| start失敗 | disconnected / start_failed | 元例外を保持しclose前のretryを拒否 |
| close失敗 | disconnected / close_failed | 例外を隠さず、readによる再使用を拒否 |

read失敗をラッチしたsessionは、close成功 -> explicit startまで再read/再startを拒否する。
経過時間だけによるstaleとread失敗を区別する。自動再接続、過去vectorの再送、偽zero、None frameはない。
`read_frame()`の成功型は既存`RawInputFrame`、取得不能は例外であり、公開wire schemaは変更しない。
既存step loopはそのtickのcommand/stepへ進まず、finallyでcloseする。`run_once`のcallerは従来どおりcloseを所有する。
concrete readerの`diagnostics`ではclose後もboundedな診断履歴を確認でき、次のstartで履歴と受信時刻をリセットする。

### 二つの時計と再現性

`timestamp_ms` / `RawInputFrame.timestamp_s`はdeviceが送った値をそのまま保持する。
`acquisition_v1.receipt_monotonic_s`は成功受信時のhost clock値であり、device時計と直接減算しない。
frame内の`acquisition_v1`はpolicy値、backend、clock kindも記録する。変化するageはhealth側が所有し、
readからhealth照会までの微小な経過をframeとの不一致にしない。

liveの既定は`time.monotonic`、試験は既存`InputSourceRuntimeDependencies.clock`を使用する。
injected backendでclockを省略した場合は`clock_kind=injected_static`、時刻0の決定的fixtureとして扱う。
そのモードのage=0は実時間freshnessの証明ではない。時間経過を検証する試験は必ず明示fake clockを進める。
受信ageはhostでの取得時刻だけを表し、OS受信queueやfirmware内でのsampleの古さまでは証明しない。

### 互換性と既知の制約

既存v1のgrammar、7ch、source timestamp、normalization、Mapping、Robot許可境界は維持する。
変更はstart直後の未確認状態、有限の失敗境界、診断保持上限、および追加の取得metadataである。
大きな時刻がfloat秒に表現不能な場合はparse errorとして拒否する。

calibrationやsetup中にvectorが出ない場合も無限待機しない。streaming可能な状態をoperatorが確認する必要がある。
長いstartup待ち、自動retry、disconnect後の自動resume、実機のノイズ/時刻校正は本policyでは実装しない。
これらが必要になった場合は実測と要件を先に確定し、今回の値を根拠なく緩和しない。

## Non-goals

- firmwareを変更しない。
- serial protocol、parser semantics、mapping、frontend viewer behaviorを変更しない。
- physical axis mappingを確定しない。
- loadcell calibration algorithmを変更しない。
- runtime validationまたはtestでreal serialへaccessしない。
- firmwareをuploadしない。
- OSCをsendしない。
- real robotまたはactuatorへoutputしない。
- generated artifactをcurrent contractの根拠としてimportしない。

pre-audit implementation chronologyは
`docs/reports/audits/canonical-content-history-separation-2026-07-16.md`へ保存した。
