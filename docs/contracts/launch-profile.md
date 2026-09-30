---
status: canonical
owner: runtime
last_verified: 2026-09-28
canonical_for:
  - launch profile configuration
related:
  - docs/operations/unified-cli.md
  - docs/operations/backend-viewer-startup.md
  - docs/contracts/experiment-plugin-composition.md
---

# 起動プロファイル

## 役割

#591の有限trialは[有限試行契約](finite-trial-runtime.md)で別の意味条件digestを固定する。
本契約のv1〜v4保存byte、configuration digest、旧CLI/publisherの意味は維持する。
新trialは実効fieldを再検証し、元保存JSONを来歴として記録する。

`runtime/composition/launch_profile.py`は、`xpotato-sim-launch-profile/v1` JSONを
既存のRobot、Input Source、Mapping、command routeへ解決する。Robot Profileのモデル定義や
関節範囲、Experiment Manifestの実験条件を複製しない。process、Source開始、model step、
network、serial、physical permissionは所有しない。

## ファイルと選択

リポジトリの`profiles/`に`sim-keyboard.json`、`sim-gamepad.json`、`replay-sweep.json`を置く。
単純名は実行中Python packageのsource checkoutから解決する。cwdや親directoryにある同名fileを
暗黙に探索しない。外部JSONは明示pathで指定する。profile内の`workspace`は、そのJSONの所在
directoryを基準に解決する。profileの名前解決はsource checkout専用で、wheelだけの環境では
checkoutを含む明示JSON pathが必要である。

```powershell
uv run xpotato-sim profile
uv run xpotato-sim profile sim-gamepad
uv run xpotato-sim profile ./profiles/replay-sweep.json
```

このコマンドは設定検査・表示だけを行い、サーバーやブラウザを起動しない。

## JSON contract

次のfieldはすべて必須で、未知fieldと欠落fieldを拒否する。

| field | 内容 |
|---|---|
| `schema_version` | `xpotato-sim-launch-profile/v1` |
| `name` | lowercaseで始まる英数字・underscore・hyphenの1〜64文字 |
| `workspace` | source checkoutへの相対または絶対path |
| `mode` | `simulation`または`replay`。実機出力は対象外 |
| `robot` | `name`と正整数`version`。既存Robot Catalogで解決 |
| `input` | `plugin`（name/version）、`provider`、`preset` |
| `mapping` | `plugin`（name/version）と`parameters` JSON object |
| `execution` | `steps`、`dt_s`、`interval_s`、`grace_period_s` |
| `web` | numeric loopbackの`host`、`port`、`websocket_port`、`open_browser` |

`simulation`はviewer-bridge Sourceと`keyboard/v1`または`gamepad/v1`の一つを要求する。
`replay`はoffline/replay Sourceと`provider: null`を要求する。live serial sourceを追加しない。
`preset`は既存`sweep_x`またはnull。Mapping parametersは既存pluginのvalidator / normalizerを
通し、sample schemaとRobot command semanticsの互換性を既存resolverで確認する。

`steps`は1〜2147483647のintegerで、boolは不可。時間値はfiniteかつ正、合計durationもfiniteを
要求する。両portは1〜65535の異なるinteger。`open_browser`はboolだけを受理する。
profile fileの上限は256 KiB、UTF-8 without BOM。duplicate key、NaN、Infinity、1e999、未知versionを
拒否する。これはboundedなsoftware configuration policyで、実機の性能・安全範囲ではない。

配布profileのsimulationは18000 step、1/60 s刻み・配信間隔で約5分、replayは600 stepで約10秒。
実行時間は条件として表示し、無期限serviceや暗黙restartへ変換しない。

## 解決結果と上書き

`LaunchProfile`はcanonical JSON文字列とtyped identityを保持する。公開JSONはcopyを返す。
`configuration_sha256`はcanonicalな入力設定を識別し、local absolute pathをdigestへ追加しない。
resolved route、Mappingの展開済みdefault、simulation / scheduled duration、physical output disabledを
別の`resolved`欄へ表示する。展開済みMapping値は閲覧用projectionで、plugin固有DTOを逆生成する
設定schemaではない。再現性の記録では設定digestに加えてsoftware revisionも保持する。
このdigestを実験manifest freezeや実機安全性の証明と呼ばない。

上書きは明示されたWeb port、WebSocket port、ブラウザ表示の3項目に限定し、
**明示override > profile**の順とする。上書き後も全設定を再検証し、元profileを変更しない。
robot、写像、実験意味条件を環境変数やlocal storageから黙って上書きしない。
任意shell command、secret、hardware enable、accepted evidenceはprofileに書けない。

## 検証

`tests/runtime/test_launch_profile.py`が正常3 profile、strict decode、identity / parameter不整合、
cwdとprofile相対path、override、CLI表示、および取得・process・networkのno-I/O境界を検証する。
後続のアプリ起動はこの設定を入力とし、起動・終了の操作手順は
`docs/operations/backend-viewer-startup.md`を正本とする。

## Gamepadのworld-XY確認用profile

`sim-gamepad-world-xy`は、既存`sim-gamepad`の軸対応だけを明示変更した追加profile。
`gamepad_axis_map={axis_indices:[0,1,3], axis_signs:[1,-1,-1]}`をMappingへ渡す。
左stick右/上をworld +X/+Y、右stick上をworld +Zへ対応させる案で、
実機種のstandard配置確認と、固定したviewでの操作受入は別途必要。
旧`sim-gamepad`の設定・digest・挙動は変更しない。任意cameraのscreen方向へは追従しない。
`selfrionette profile sim-gamepad-world-xy`で解決後の設定を表示できる。

## 左右独立の1スティックXYZ操作

`sim-gamepad-left-xyz`／`sim-gamepad-right-xyz`と現行FastArm Gamepad profileはstick XY + analog trigger Zを使用する。
FastArmの胴体`+X=前、+Y=左、+Z=上`へ固定したoperator/TPS mappingを採用し、
stick上=`+X`、右=`-Y`とする。Gamepad viewerは同じ基準に合わせた背後`-X`側の`operator` camera presetで起動する。
cameraを手動回転してもMapping自体は追従させない。設定・符号ラッチ・表示の詳細は
[Gamepad trigger操作契約](gamepad-trigger-control.md)を参照する。
旧XY/XZ切替は[Gamepad平面操作契約](gamepad-plane-control.md)の互換経路として維持する。

## 同列モデル選択を持つv2（#574 / #580）

単腕と双腕で別のprofile型・フォルダー・launcherを作らない。全設定は`profiles/`直下へ置き、
`LaunchProfile`と`decode_launch_profile`で解決する。v2はv1のfieldに`model`と`coordination`を加える。

```json
{
  "model": {"name": "bimanual", "version": 1},
  "coordination": {
    "side_to_endpoint": {"left": "left", "right": "right"},
    "epoch": "trial-1",
    "max_input_age_s": 0.2
  }
}
```

`robot`は既存Robot Catalogを選び、`model`はその登録の`models`から同じ`RobotModelRegistration`型で
解決する。FastArmは`single_original`、`single_left`、`single_right`、`bimanual`を同列に持つ。
各定義はcoreが保持し、profileへ配置quaternionやモデル形状を複写しない。Robot登録のidentity bytesには
モデルidentity、手先/関節名、configuration digestも含む。

`coordination.side_to_endpoint`は入力側とモデルの手先IDの明示対応であり、選択した全手先を重複なく覆う。
単腕も1要素の対応として同じ制御loopを通る。バインドしないスティックは表示上も入力診断と区別する。
現行v2実行はsimulation・gamepad/v1・名前付き手先を扱える明示Gamepad control Mappingを要求する。他の組合せは代用せず拒否する。

モデル構築は登録factoryを通し、同じ生成modelからproviderとViewer resourceを作り、artifact digest、関節順、
次元を照合する。v2の入力開始は腕数に関係なくscene準備後で、physical outputはdisabledのまま。
旧v1は単腕keyboard/replay等の既存意味・digestを保持する互換境界として残す。互換分岐はversion/command意味に
よるもので、双腕専用schemaへの分岐ではない。未マージだった`fast-arm-coordinated-viewer-profile/v1`、
`CoordinatedViewerProfile`、`profiles/coordinated/`は退役し、誤った取付設定を黙って移行しない。

配布profileは`fast-arm-single-gamepad`、`fast-arm-left-gamepad`、`fast-arm-right-gamepad`、
`fast-arm-bimanual-gamepad`。GUI task/spawnとruntime中の切替は別Issueである。

## v3: 名前付きモデルと固定物体診断の合成（#585）

`xpotato-sim-launch-profile/v3`はv2へenvironment/taskを明示追加する。旧v1/v2の設定byte、意味、互換decoderは変更しない。
EnvironmentとTaskは既存catalogからidentity/versionで解決し、未知・不適合を開始前に拒否する。

```json
{
  "environment": {
    "plugin": {"name": "object_scene_environment", "version": 1},
    "parameters": {"preset": "two_cubes"},
    "robot_collision_profile": "tool_sphere_10mm/v1"
  },
  "task": {
    "plugin": {"name": "contact_observation_task", "version": 1},
    "parameters": {"target_object_ids": ["cube_left", "cube_right"], "duration_s": 60.0}
  }
}
```

sceneはpresetまたは完全な展開済みdocumentを受け、resolved出力へ物体定義・配置・全接触条件とdigestを含める。
scene/Robotをmodel factoryで一度結合し、旧Task用cube manifestへ変換しない。Task targetはscene object IDの部分集合。
v3から旧schemaへラベルだけ変える降格は拒否し、scene選択を黙って捨てない。
具体値契約は[固定物体scene・幾何接触診断](object-scene-contact-diagnostic.md)を正とする。

## v4: worldを明示した動力学実行（#582）

`xpotato-sim-launch-profile/v4`はv3へ`execution.dynamics`を必須追加し、object-scene/v2の明示worldと同時に解決する。
数値設定は`dynamics-settings/v1`、semanticsは`coordinated_actuator_servo_dynamic/v1`。
固定診断へdynamic物体を黙って変換したり、gravity/solverを未知値から補完したりしない。旧v1/v2/v3の設定byteは維持する。

配布profileは`dynamic-cube-drop`、`dynamic-fixed-contact`、`dynamic-cube-push`。共通Robotモデル選択と
`coordination.side_to_endpoint`で原型/左/右単腕/双腕へ接続し、腕数専用のrunnerを追加しない。
`resolved.scene`にworld/物体条件、`resolved.dynamics`に数値条件、各digestを出力する。
world条件と積分条件の変更を同じ意味に混同せず、試行の条件固定では両方を保存する。

既定Newton50 iteration/tolerance1e-10、physics_dt=1/600 s、control_dt=1/60 s、速度budget10 rad/s、追従誤差budget0.75 radは
診断用仮定値で、実機で安全・最適と測定した値ではない。gainとtorque limitはRobot sourceのまま。
