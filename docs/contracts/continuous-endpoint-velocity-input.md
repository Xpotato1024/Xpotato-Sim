---
status: canonical
owner: input contract
last_verified: 2026-10-19
canonical_for:
  - evaluation-ready continuous endpoint velocity input
related:
  - docs/contracts/endpoint-metadata-vocabulary.md
  - docs/archive/drafts/r7-e-p11-gamepad-publication-cadence.md
  - docs/archive/drafts/r7-e-followup-p12-control-frame-resolution-metadata.md
---

# continuous endpoint velocity input契約

## 目的とboundary

current contract（`#353`、parent `control-frame contract`、numbering SoT `roadmap`）は、keyboard、gamepad、
deterministic fixture-based analog inputに対して、typedかつimmutableなinput-side
contractを1つ定義する。device codeはraw stateを抽出し、pure common builderは、
すでに定義済みの3-axis inputをvalidateしてrequested continuous endpoint velocityへ
変換する。safety、frame resolution、motion generation、MuJoCo stepping、measurement、
publicationの責務は引き続きruntimeが持つ。

`ContinuousEndpointVelocityIntent`は`schemas`に置き、standard-libraryまたは
schema-local typeだけに依存する。builderとadapterは`input_sources`に置き、motion、
runtime、backend、hardware、transport moduleをimportしない。

## field契約

| field | 意味 | unit / frame |
|---|---|---|
| `source_kind` | 空でないsource identity | source vocabulary |
| `source_timestamp_s` | sourceが供給するtimestamp | second |
| `intent_kind` | 固定値`local_endpoint_velocity` | canonical vocabulary |
| `input_continuity` | 固定値`continuous` | canonical vocabulary |
| `axis_values` | すべてのnorm clamp後の最終normalized 3-axis input | dimensionless、normは最大1 |
| `deadzone_applied_axis_values` | component deadzone後、source supplement/final clamp前のsource axis | dimensionless |
| `local_endpoint_velocity_m_s` | scale適用後のrequested velocity | `control_frame`内のm/s |
| `control_frame` | requested `world`または`tool` frame | requestedでありresolvedではない |
| `source_active` | sourceが現在controlへ参加しているか | boolean |
| `stale_reason` | 存在する場合のmachine-readableなstale/inactive reason | stringまたはabsent |
| `zero_input` | supplementとすべてのnorm clamp後の最終normalized requested `axis_values`がzeroか | derived boolean |
| `local_endpoint_speed_m_s` | 設定されたvelocity scale | m/s |
| `local_endpoint_max_delta_m` | 保持されたmotion-policy bound provenance | m |
| `norm_clamped` | normalization/saturationによって1を超えるnormが変更されたか | derived boolean |
| `source_diagnostics` | raw/device diagnostic用のimmutable open extension | source-owned |

canonicalな`to_metadata()` serializationは全sourceで共有する。出力するのは
input-owned requested fieldだけである。`actual_tip_delta_m`、qpos、IK result、
progress、target rejection、runtime safety、transport state、trial ID、participant IDは
含めない。

## deterministicな変換順序

1. finiteなsource-axis valueをちょうど3つ必須とする。
2. component deadzoneを適用する（`abs(value) <= deadzone`をzeroにする）。
3. base vector normを1にclampする。
4. optionalなdevice-defined axis supplementを適用する。gamepad button 0/1は、
   positive/negative Zにこの確立済みboundaryを使用する。
5. final vector normを1にclampし、`norm_clamped`を記録する。
6. non-negativeな`speed_m_s`を乗算する。
7. immutableなrequested intentを構築し、canonicalにserializeする。

keyboard compatibilityは明示的なadapter optionを使い、legacy順序であるraw
key-bound axis、norm clamp、component deadzone、speed scalingを維持する。gamepadと
analog fixtureは上記のcommon orderを維持する。keyboardの`norm_clamped`には、
pre-deadzone clamp provenanceを含める。

deadzone、speed、max deltaはfiniteかつnon-negativeでなければならない。inputと
diagnostic mappingはcopy/freezeし、mutateしない。同じinputとconfigからは同じ結果を
生成する。

## requested frameとresolved frame

input contractは`local_endpoint_velocity_m_s`とrequested `control_frame`を所有する。
world requestはworld requestであり、tool requestはtool-frame requestのままである。
input layerはMuJoCo orientationを読まず、tool velocityをworld velocityとlabelしない。

current contract runtime resolutionは`requested_control_frame`、`resolved_control_frame`、
`resolved_world_endpoint_velocity_m_s`、orientation-unavailableのhold/reason
semanticsを所有する。既存のcompatibility compositionは、world requestに対して
world-resolved aliasを出力してよい。tool requestに対してrequested valueを無条件に
複製しない。

## activity lifecycle

- active zeroは有効なcontinuous intentである。`source_active=true`、zero velocity、
  stale reasonなしとする。
- `zero_input`は、pre-supplement base axisではなく、最終normalized requested
  `axis_values`から導出する。したがってgamepad Z-button supplementはinputとして数え、
  反対方向のsupplementを同時に入力した場合はzeroへ相殺できる。
- zero inputとzero velocityは異なりうる。`speed_m_s=0`で最終axisがnonzeroの場合、
  `zero_input=false`かつrequested velocityはzeroとなる。
- inactiveは`source_active=false`である。stale conditionがなければstale reasonなしでもよい。
- staleはmachine-readableな`stale_reason`を伴うinactiveである。
- blur、disconnect、gamepad stale、viewer zero-stateは、既存のinactive reasonと
  current contract cadence/timeout behaviorを維持する。
- activeとstale reasonの同時指定は矛盾としてrejectする。
- non-finiteまたはmalformed vectorは、zeroへ変換せずrejectする。

runtime input safetyは引き続きMuJoCo step前に実行する。viewer lifecycleがsourceを
activeとする場合、releaseはactive zero requestのままである。blur、disconnect、stale、
explicit zero-stateは、既存viewer message semanticsのもとでinactiveのままである。

## source抽出のboundary

keyboardはkey binding、active key-code handling、default、`pressed_keys` diagnosticを
維持する。`build_keyboard_continuous_velocity_intent()`がtyped adapterである。
publicな`build_keyboard_motion_command()`はcompatibility wrapperのままとし、
observable metadata、world alias、current-tip annotation、speed、deadzone、
max-delta behaviorを維持する。

gamepadはaxis ordering、raw axis/button、button 0/1のZ assistance、
connection/stale/zero-state lifecycle、message summary、cadence、設定済みdefaultを
維持する。`ViewerInputSource`はdeadzone、clamping、scaling、canonical input metadataを
common builderへ委譲し、composition annotationはviewer fieldとworld compatibility
aliasを維持する。

`build_normalized_analog_fixture_intent()`は、すでにnormalizedかつsemantically
definedなaxisを受け取り、hardware I/Oなしで同じcontractを生成する。これはstableな
current contract extension pointである。load-cell calibration、channel mixing、force-to-axis
mapping、gain tuning、sensor zeroing、serial I/O、recorded-force schema、
participant calibrationは定義しない。

## 対象外

frontend schema、research comparison design、viewer presentation、
composition-root redesign、logging record、raw force mapping、loadcell serial、
Arduino、OSC、hardware runtime、IK/FK/Jacobian、MuJoCo XML、transport serializer、
CI workflow、dependency changeは含めない。

## Gamepadの明示的な軸選択・符号（#563）

Mapping parameter `gamepad_axis_map`を指定した場合だけ、既存raw/legacy正規化後、
common builderのdeadzone前に、`axis_indices`の3軸を選択して`axis_signs`を乗じる。
省略時は従来の先頭3軸を同符号で使い、既存parameter projectionも変更しない。
index/signは共に3要素integerで、indexは相異なる非負値、signは+1/-1。
unknown field、bool、重複軸、不正長、nullをstartup前に拒否する。
active sampleの選択軸欠落はzero paddingせずrejectし、inactiveの停止は維持する。
keyboard、buttonのZ補助、速度・deadzone・frame resolution、最終指令意味は変更しない。
実装ownerと条件例は[Mapping README](../../src/xpotato_sim/plugins/mappings/viewer_keyboard_gamepad_mapping/README.md)を参照する。
画面方向との対応、個々のcontrollerの配置、左右armの割当はこの設定だけで実測済みとはしない。

## 左右独立の1スティックXYZ操作

`examples/sim-gamepad-left-xyz`／`examples/sim-gamepad-right-xyz`の標準XYZ操作は、stick XY + analog trigger Zへ移行した。
trigger量・bumper符号ラッチ・取得session・表示の規約は[Gamepad trigger操作契約](gamepad-trigger-control.md)を参照する。
旧XY/XZ切替は退役した。[Gamepad平面操作契約](gamepad-plane-control.md)はhistorical記録であり、旧設定は拒否する。

## 接近速度の独立したcommand Mapping（#632）

初期版は`approach-resistance/v1`のsoftware条件であり、固定した単純なTask対象面への
world速度を変更する。通常のInput Source/Control Mappingを通ったm/sの要求を入力とし、
正規化された無次元の指入力をNへ読み替えない。能動力覚や知覚効果の成立を意味しない。

`n`はtoolから対象面へ向かうworld単位法線で、対象object IDとともに条件へ固定する。
当該pairの全native接触法線が同じ向きにcos値0.99以上（条件でさらに厳格化可）で一致する場合だけ、
`F_n = max(0, -dot(raw_object_on_tool_force_n, n))`を使う。表示のclampやfilter値は使わない。
別面、反対面、欠測、partial、対象不一致、時刻/epoch不一致を平均法線へ丸めない。

出力は `v_out = v_in - (1-s) * max(dot(v_in,n),0) * n` とする。

| mode | 係数s | 条件差 |
|---|---|---|
| baseline | 1 | 元のvelocityを保持 |
| fixed_gain | 固定した0〜1の値 | 荷重と無関係に接近成分だけを減衰 |
| contact_dependent | `max(minimum_gain, 1 / (1 + F_n / half_gain_load_n))` | 圧縮荷重に対し非増加 |

離脱と接線成分は維持し、ゼロ入力から運動を生成しない。fixed gainの比較でも方向の選別を同じにし、
単に速度が下がった結果を知覚効果と解釈しない。実機gainや普遍的な安定性の証明は対象外である。

確定済みtick kの観測をcommand k+1へ使う。freshな中立でrunningへ移るときだけ初期状態の観測を取り直し、
以後はcommit後だけ更新する。原力のbrowser表示・再送では制御観測を更新しない。
`contact_dependent`は前commitの品質が不明、固定法線が不正、期限切れ（最大0.2 s）、対象変更なら
次のprovider.prepareより前にfaultをlatchする。baseline/fixed gainは原力欠測をtraceへ残し、
力を使わない式を維持する。全modeで元入力の鮮度・sequence・中立・STOP検査は共通である。

最終velocityだけを既存providerへ渡すため、抑制前のtargetを積み上げる独自integratorはない。
可変dtは既存command routeの積分規則に従い、このprimitive自身には時間積分や仮想ばねを持たせない。
STOP/new epochで保持sampleとtraceを失効/交換する。同じnative sampleを描画側へ再利用して、
原力とgeometryの重複取得を避ける。参照数は最新sample・trace各1件である。
Taskの自然終了・予算終端では、停止を反映した同じframeへ失効後のtraceを複製する。
既に公開したframeや写像時のtraceを変更せず、終端projectionのためのnative観測・prepare・commitは追加しない。

条件は既存の展開済みJSONへ`command_mapping`として明示してimportする。未選択時はnullであり、
既定presetへ抵抗を自動追加しない。例は[software比較note](../experiment-notes/2026-10-19-contact-resistance.md)を参照する。
WorkbenchのAdvanced「Mapping」でmodeと数値を編集し、準備時に固定する。
提示効果と運動応答を混同しないため、この比較では`display_comparison`を同じ値へ固定する。

`command_mapping_v1`の最新traceにはinputのsource epoch/sequence/timestamp/receipt/payload SHA、
原力のsource tick/simulation time/host観測時刻、command tick/host時刻/dt、
処理前後velocity m/s、対象ごとのraw force N・圧縮成分N・係数を別fieldで記録する。
Deviceでは`input_ref.received_at_s`がSim時計上の保守的な最古receiptであり、
producerの順序時計はoptional `input_ref.ordering_receipt_s`へ分けて保存する。
異なる時計原点を直接減算して遅延と扱わない。ordering時計のない従来入力では追加fieldを省略する。
raw dynamicsと実際のqposは既存payloadへ残す。これは最新状態の診断であり、
#583/#584の正式metricや連続記録の完成を主張しない。
