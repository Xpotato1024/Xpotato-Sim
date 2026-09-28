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

新しい`sim-gamepad-left-xyz`／`sim-gamepad-right-xyz`と、XY/XZ切替・中立復帰・取得session・表示の規約は
[Gamepad平面操作契約](gamepad-plane-control.md)を参照する。現行の片腕へ選択した片側を適用する段階であり、双腕モデル完成ではない。

## 双腕Viewerの明示profile（#574）

`profiles/coordinated/fast-arm-bimanual-gamepad.json`は別schemaの
`fast-arm-coordinated-viewer-profile/v1`である。単腕の`LaunchProfile/v1`やRobot Catalogへ
8関節を混ぜず、`app`／`profile`だけがschemaを見て明示的に振り分ける。

必須fieldは`schema_version`、`name`、`workspace`、2件の`assembly`、`side_to_arm`、
`mapping_parameters`、`epoch`、`execution`、`web`。各assembly要素は`arm_id`、`mirror_y`、
`position_m`、`quaternion_wxyz`を持ち、left/rightのside bindingは両armを重複なく覆う。
`execution`は単腕と同じ有限実行値に`max_input_age_s`を加える。Mappingは既存の
`gamepad_plane_control`を必須とし、未知field・重複key・非finite値・非loopbackを拒否する。

`runtime/composition/coordinated_viewer_profile.py`が設定とRobot描画資源の接続を所有する。
decode／profile表示ではmodel実行・process・networkを開始しない。資源生成は明示した
`build_viewer_bundle()`からのみ行い、資源はlauncher所有の一時directoryへ置く。
既存単腕profileの設定・digest・操作意味は変えない。GUIでのprofile切替やTask/spawnは別Issue。
