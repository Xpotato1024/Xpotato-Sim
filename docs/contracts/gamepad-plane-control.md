---
status: canonical
owner: mapping
last_verified: 2026-09-28
canonical_for:
  - independent gamepad stick plane control
related:
  - docs/contracts/continuous-endpoint-velocity-input.md
  - docs/contracts/launch-profile.md
  - docs/contracts/runtime-input-source-registry.md
---

# 1スティックによるXYZ速度操作

## 目的と実装範囲

左右のスティックを独立した操作セットとして扱い、通常XY／同側肩ボタン押下中XZへ切り替える。
これは2連続軸を切り替えて3方向へアクセスする方式であり、1腕XYZの同時独立入力ではない。
Issue #567は双腕モデルより先に入力経路を成立させる変更。現行片腕モデルに選択した片側だけを
適用し、両側の要求速度・modeを診断へ残す。双腕同時操作は末尾の#574専用profileで選択する。
二台Selfrionetteは別の未実装範囲である。

## 起動と操作

新profileを明示的に選ぶ。既存sim-gamepad／sim-gamepad-world-xyの設定は変更しない。

以下は改名後の `xpotato-sim` コマンドである。入力装置の割当と各profileの操作条件は変更しない。

```powershell
uv run xpotato-sim profile sim-gamepad-left-xyz
uv run xpotato-sim app --profile sim-gamepad-left-xyz
uv run xpotato-sim app --profile sim-gamepad-right-xyz
```

left/rightは入力セットの選択であり、現行fast_armの物理的な左右の区別ではない。
標準配置を確認したGamepadでは以下を使う。機種の自動認識・自動校正ではない。

| 操作セット | 横方向 | 通常の縦方向 | 肩ボタン押下中の縦方向 |
|---|---|---|---|
| 左stick + button 4（L1/LB） | +X/-X | +Y/-Y | +Z/-Z |
| 右stick + button 5（R1/RB） | +X/-X | +Y/-Y | +Z/-Z |

上へ倒すと正方向、下へ倒すと負方向。world基準は画面の右・上と同義ではなく、カメラを
回転しても操作基準を変えない。XYZは各profileの要求座標内の並進速度である。

## 設定の正本

`viewer_keyboard_gamepad_mapping/v1`のoptionalな`gamepad_plane_control`が方式を明示する。

```json
{
  "gamepad_plane_control": {
    "schema": "gamepad-plane-control/v1",
    "output_side": "left",
    "neutral_threshold": 0.1,
    "left": {"axes": [0, 1], "signs": [1, -1], "mode_button": 4},
    "right": {"axes": [2, 3], "signs": [1, -1], "mode_button": 5}
  }
}
```

すべてのfieldを必須とし、未知field、boolのindex、重複軸、同一切替button、非finite閾値、
不正なsideを拒否する。neutral_thresholdはraw stick値の0〜0.1で、速度単位ではない。
設定はdeep copy/freezeし、staticなgamepad_axis_mapとの同時指定を拒否する。
同一session内での設定・速度・要求frame変更は拒否する。変更には新しいruntime sessionが必要。

raw値を既存の固定0.1投影で正規化してから、各側の2軸をX/YまたはX/Zへ割り当てる。
共通builderのdeadzone、腕ごとのnorm制限、speed_m_sを適用する。旧二段deadzoneは維持する。
左右6成分を一括正規化しない。新方式ではface button 0/1のZ加算を適用しない。

## 中立と復帰の状態機械

起動直後は両側ともwaiting_neutral。肩buttonの押下・離上のどちらでも、対象側だけを
waiting_neutralにして速度要求をゼロへ落とす。raw 2軸が閾値内に入ってからrequested_planeを
有効にしarmedへ移る。切替中の傾きを別軸へ直ちに転用しない。
一方の切替は反対側のmode・要求速度を変更しない。

欠落・stale・切断・invalidでは両側を未armingへ戻す。復帰後の非zero入力だけで再開しない。
raw_axes、設定した軸と肩button、sequenceが必要で、欠落を中立の新規測定として補完しない。
同sequence・同内容の再評価はidempotent。逆順・同sequenceの内容違いは拒否する。

## 実行sessionと取得session

`ControlMappingPlugin.session_strategy_factory`はoptionalな後方互換のfactory契約。
factoryのない既存Mappingは従来のstateless strategyを使う。factory付きMappingはruntime pipelineが
実行ごとのstrategyを生成し、catalog singletonで状態を持たない。factoryの戻り型・semantic identityを検査する。

bounded step loopは開始時と終了時（例外を含む）にMapping sessionを破棄する。
`pipeline.run_once`を連続呼出しするcallerは状態を継続利用でき、新試行前に
`reset_mapping_session()`を呼ぶ。Mapping失敗時も状態を破棄する。
物理状態、source取得、Taskの状態をこのMappingの状態機械へ移さない。

viewerのGamepad providerは一時的な`viewer_provider_session_id`をmetadataに付ける。
mode表示を受け取る前の最初のsampleにもIDを付け、bootstrap中の制御停止を避ける。
SourceはASCII英数字・hyphen・underscoreの1〜128文字を検証しcanonical sampleのdiagnosticsへ渡す。
これは装置serialや人の識別子ではない。別provider sessionのsequence再開は中立待ちから受け付け、
同じruntime中の退役済みsessionは拒否する。128回を超えるsession切替では新runを要求し、無制限の履歴を持たない。
IDは認証情報ではなく、既存loopback運用内の更新系列を区別するもの。

既存providerは中立時にheartbeatを止めるため、新modeだけ接続中・非staleの中立sampleも
100 ms間隔で送信する。sourceのzero/inactiveをactiveへ変更しない。blur/hidden/切断では停止する。
UIはbackendのmode情報の存在からこの取得オプションだけを有効化し、入力写像を計算しない。
省略時のlegacy cadenceは維持する。再開時は同じ中立snapshotでも再送できるよう署名を破棄する。

## 表示と診断

backendは`metadata.gamepad_plane_control_v1`へ両側のplane、requested_plane、status、
switch_count、mode_button、要求velocity_m_s、output_side、single_endpoint scopeを出力する。
これは両腕の実測速度ではない。未選択側は入力診断のみで、現行Robotへ送信しない。
raw inputからこのfieldを偽装してもlegacy Mappingの表示へは通さない。

GUIは入力欄に左右の適用平面と中立待ち、新modeで単腕へ適用する側を表示する。
ボタンからmodeを再計算せず、未取得・不正値は非表示／未取得とする。配信停止時は前回値と示す。
表示待ちを実機の安全停止や双腕稼働の証拠にしない。

## 検証と残存事項

設定・状態機械・session隔離、Sourceからのcanonical入力、CLI profile、片腕MuJoCoの6方向比較、
GUI decoder・React markup、neutral heartbeatと復帰をsoftware testで検証する。
通常の回帰testは参加者実験ではない。実Gamepadを人が操作した受入と、任意cameraでの方向感は別途確認する。
実機、serial/OSC、双腕モデル、タスクGUI、物体spawn、ばねの力学はこの変更では実装しない。

## 双腕Viewerでの適用（#574）

`fast-arm-bimanual-gamepad`では`map_coordinated_input`のtyped要求を両armへ同じtickで渡す。
表示metadataの`output_scope`は`coordinated`、`output_side`はnullとし、既存のsingle_endpoint形式と区別する。
左右のmode・中立待ちはMappingが所有し、UIは結果の表示だけを行う。初回未取得・fault時の表示resetは
運動指令や新鮮な観測を生成しない。全体faultでは両腕停止を表示し、操作可能と表示し続けない。
二台Selfrionetteの取得や実Gamepad機種ごとの操作受入は含まない。


## 同列モデルの入力binding（#580修正）

共通LaunchProfile/v2では、単腕も双腕も`coordination.side_to_endpoint`で1〜2手先へ明示対応させる。
`coordinated`は複数の可能性を持つ名前付き更新方式を表し、必ず2腕であることは意味しない。
表示にはruntime所有の`endpoint_bindings`を付け、未割当側を「入力診断のみ」と表示する。
Robotモデルの姿勢とInput側のleft/right割当は独立し、配線・画面方向から推測しない。
旧single_endpoint presentationと旧v1の単腕操作は互換維持する。
