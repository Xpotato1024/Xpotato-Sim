---
status: canonical
owner: architecture
last_verified: 2026-09-28
canonical_for:
  - Xpotato-Sim naming and migration boundaries
related:
  - AGENTS.md
  - docs/operations/mujoco-viewer-dev-launcher.md
---

# Xpotato-Simへの名称移行

## 現在の名前

| 対象 | 名称 |
|---|---|
| 基盤・GitHub repository | `Xpotato1024/Xpotato-Sim` |
| Python配布名・CLI | `xpotato-sim` |
| Python名前空間・ソース | `xpotato_sim` / `src/xpotato_sim/` |
| 実験用CLI | `xpotato-sim-r7-g-e2e` |

Selfrionetteは対応する入力装置の名称として残す。FastArm、MuJoCo、通信上の装置ID、
`selfrionette/v1`、装置用firmware、過去の実験記録・報告・論文の固有名詞は改名しない。
研究室のrouterとPi側repositoryも別の所有者・責務であり、この改名の対象外である。

## 利用方法

```powershell
uv sync --frozen --group dev
uv run xpotato-sim --help
uv run xpotato-sim profile
uv run xpotato-sim app --profile sim-gamepad-left-xyz --check
uv run xpotato-sim app --profile sim-gamepad-left-xyz
```

Pythonのimportとモジュール指定には `xpotato_sim` を使用する。旧Python名前空間と旧CLIを
並行実装しない。変更後は環境を同期し、旧editable packageに依存していないことを確認する。
既存のスティック割当・平面切替・安全判定・実機出力許可は変更しない。

## 設定と記録の互換性

新規の起動設定は `xpotato-sim-launch-profile/v1` を使用する。
既存の `selfrionette-launch-profile/v1` も同じ厳密検証で読み込める。
読込み時に元のschema値や設定を自動書換えしないため、旧設定のcanonical JSONとdigestは維持する。
名称変更後に新規生成する成果物のrepository identityは新名とし、過去の成果物は書換えない。

起動用の内部環境変数は `XPOTATO_SIM_LAUNCHER` と `XPOTATO_SIM_JOB` に統一する。
旧作業ブランチと新しい起動処理を混在させず、作業ブランチを移行済みmainへ追従させて使う。
過去の報告に記載された旧パスは、当時のcommitを説明する記録として保持する。

## Gitと関連repository

既存GitHub repository自体を改名し、Issue・PR・commit履歴を維持する。
ローカルremoteは新URLへ変更する。旧名の別repositoryを作ってredirectを壊さない。
未マージPRは名称移行だけを取り込み、元の機能変更やレビューを勝手に完了扱いしない。
本変更でR5/R6、双腕Viewer、接触実験、実機安全検証が完了するわけではない。
