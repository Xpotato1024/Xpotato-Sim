---
status: canonical
owner: runtime
last_verified: 2026-09-13
canonical_for:
  - software-only virtual reaction-force signal derived from MuJoCo contact evidence
related:
  - docs/contracts/contact-task-manifest.md
  - docs/architecture/runtime-composition.md
---

# Virtual reaction-force signal contract

## 目的と責務

runtime/contact/virtual_reaction_force.pyは、#413で定義したraw ContactEvidenceから、
実デバイスへ送信しないsoftware-onlyのvirtual reaction-force signalを導出する。
raw measured forceと派生signalは別type、status、manifest identityで管理し、派生処理で
raw evidenceを書き換えない。

sourceはContactEvidence.aggregate.object_on_tool_force_world_nのみであり、
target objectとtool間で測定したforceを使う。self contact、environment contact、未分類contactは
再集計しない。単位はnewton、符号はobjectがtoolへ加えるobject_on_toolである。

ContactTaskOutcomeとterminal evidenceはraw contact evidenceの契約に従う。
deadband、filter、rate limit、magnitude clamp後のsignalをTask successへ代用しない。

## Versioned manifest

VirtualReactionForceManifestはsource ContactTaskManifestのdigestと、output frame、
deadband、moving-average window、low-pass time constant、rate limit、magnitude clamp、
stale sample gapを持つVirtualReactionForceConfigを束ねる。schemaは
virtual-reaction-force/v1、identityはvirtual_reaction_force/v1を使用する。
canonical UTF-8 JSONをSHA-256でdigest化し、source manifest digest、force source、unit、
sign convention、frame transform policy、filter order、initial state、no-contact、
stale、trial boundary policyをidentityへ含める。processing parameterまたはsource identityが
変わればsignal manifest digestも変わる。

encode_virtual_reaction_force_manifestはcanonical bytesを返す。strict decoderはUTF-8 JSON、
重複key、schema field、canonical representationと、呼出側のraw contact manifest digestを
照合する。

## Frameと変換

input forceはMuJoCo world frameである。output frameはmanifestで次から選ぶ。

| Frame | 契約 |
|---|---|
| mujoco_world | 入力world vectorをそのまま出力する。caller transformは受け付けず、identity rotationを記録する。 |
| tool | 各active sampleへcaller提供のworld-to-tool rotationを適用する。 |
| device_neutral | 各active sampleへcaller提供のworld-to-device-neutral rotationを適用する。device commandや軸割当は定義しない。 |

tool / device-neutral rotationは3×3 row-majorのproper orthonormal matrixである。
各sampleで直交性、単位長、右手系を検証し、force vectorへv_output = R × v_worldとして
適用する。translationや姿勢推定は行わない。active sampleにrotationがない場合、
またはrotationが不正な場合はinvalidを返す。no-contactではrotationを省略できる。
frame変換は座標表現だけを変え、device-specific command mappingを追加しない。

## Lifecycleと時計

| Signal status | 意味と出力 |
|---|---|
| active | source evidenceがmeasured。raw world force、output-frame raw force、派生forceを保持する。 |
| no_contact | 有効な測定上のtarget contactなし。status付きzeroを返し、filter stateを消去する。 |
| measurement_unavailable | sample欠落またはsource測定不能。forceはnullであり、zeroへ置換しない。 |
| invalid | source invalid、manifest / identity不一致、frame変換または数値計算の不正。forceはnull。 |
| stale | source clock停滞、またはsample / simulation gapがmanifest上限を超過。forceはnull。 |

sample timeとsimulation timeはsignalに併記する。どちらも非負かつ有限でなければならない。
同一trial内のsample time重複・逆行、simulation time逆行、存在するframe_indexの重複・逆行は
invalidとする。simulation timeが進まないsample、またはsample / simulation gapが
max_inter_sample_gap_sを超える場合はstaleとし、いずれもfilter stateを消去する。

ContactTrialIdentityが変わるとprocessorはclockと全filter stateをresetする。reset()でも同じ境界を
明示できる。missing、unavailable、invalid、stale、no-contactの後はfilter stateを引き継がない。
reset後の最初の有効sampleはfilter stateをseedし、rate limitの前回outputがないため初期sampleには
limitを適用しない。

## Filter orderとparameter

全parameterはmanifestに保存し、次の順序で適用する。

1. deadband: raw output-frame forceのmagnitudeがdeadband_n以下ならfilter inputをzeroにする。
2. moving average: 直近smoothing_window_samples件の有効なmeasured filter inputを単純平均する。
3. one-pole low-pass: tau > 0の場合、sample time差dtからalpha = 1 - exp(-dt / tau)を求め、
   y = previous + alpha × (input - previous)とする。tau = 0は無効化を表す。
4. vector rate limit: 前回outputからのvector変化量を、sample time差とrate_limit_n_per_sの積以下に
   抑える。nullは無効化を表す。
5. magnitude clamp: magnitude_clamp_nを超えたoutput vectorを上限へ比例縮小する。nullは無効化を表す。

smoothing_window_samplesは1以上、deadbandとlow-pass time constantは0以上である。
rate limit、magnitude clamp、stale gapは正値（または無効化するnull）でなければならない。
非有限値は受け付けない。出力signalはfiltered、deadbanded、rate_limited、saturatedを個別に記録する。
saturatedはmagnitude clampが実際に適用されたことを表す。raw_force_world_nとoutput.raw_force_nは
filter/clamp前の値を保持し、clamp後のoutput.force_nで上書きしない。

## Signal recordと再現性

VirtualReactionForceSignalはderived manifest digest、source contact manifest digest、trial identity、
source status、signal status、frame、sample / simulation time、optional frame indexを持つ。
force sourceはraw aggregate field、identity、unit、sign conventionとともに記録する。active signalは
raw world force、変換後raw force、derived output、実際に適用したrotationを併記する。
失敗statusでは有効なderived forceを持たない。

同じmanifest、trial境界、measured input stream、per-sample transformから同じcanonical signal bytesを
再生成できる。reference inputと期待値はtests/runtime/test_virtual_reaction_force.pyの
deterministic fixtureに置く。

## 境界

このAPIはsoftware-onlyである。force device選定、device-specific command mapping、OSC、network、
serial、Arduino、robot output、actuator control、participant study、physical stabilityの主張を含まない。
software validationは実機の力覚・安全・安定性を検証しない。

## 現行dynamic sceneの手先・対象別signal

`runtime/scene/force_signal.py`は`scene-dynamics-observation/v1`から
`scene-force-signal/v1`を導出する。旧単一cubeのContactTaskManifestを作らず、
model/scene/settings digestと明示した手先×Task対象物集合を独立manifestに固定する。
`trial-condition/v1.force_signal`へ設定・集約規則・観測位相を保存し、source revisionとtrial/epochは
既存trial記録で追跡する。任意の物体を対象へ追加せず、非対象の固定台・支持面を荷重へ混ぜない。

rawの`force_on_geom2_world_n`に対して、geom1がtoolなら負、geom2がtoolなら正の符号を適用し、
対象物が手先へ加えるworld力に統一する。同じ手先・対象のベクトル和と、各接触における
圧縮法線成分の和`normal_load_n`を別量として保持する。異なる法線や左右の荷重が相殺しても、
法線荷重が0になるとは限らない。矢印の原点は接触点の算術平均であり、合力の物理的な作用点や
等価wrenchを保証しない。contact indicesは同じraw観測の配列を参照する。

各pairは`active`、`no_contact`、`measurement_unavailable`、`partial_missing`を区別する。
部分欠測では観測済み成分だけを完全な合力とせず、ベクトル和・法線荷重・派生値を全てnullにする。
全体の欠測は`missing`、kinematicは`not_evaluated_kinematic`、不正identity/値は`invalid`、
時計の停滞・過大gapは`stale`。有効な無接触のみstatus付き0 Nである。

ModelExecutionは同じprovider sampleを使い、control commitごとに一度だけ導出する。
観測位相は最後のphysics substep後であり、substep間のpeakや全区間最大値ではない。
表示再送は元のcontrol tick、source frame、simulation time、host観測時刻を維持し、鮮度を更新しない。
STOP、欠測、stale、不正値、trial/epoch resetでfilterを破棄し、残留cueを失効させる。
初期設定は加工なしのworld力で、filterの時計はsimulation time。
window上限256、手先・物体各32、raw contact上限4096、保持sampleは最新1件である。

旧R7-Hと新sceneは`runtime/contact/force_filter.py`の同じベクトルfilterを使用する。
旧API、manifest bytes、clock/quality判定は維持し、raw evidenceやTask判定を派生signalで上書きしない。
連続evidenceの永続記録、正式Task/Evaluation、人体・実機の効果検証はこのsignalの完了条件に含めず、
既存#583/#584の責務を維持する。
