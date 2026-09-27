# Xpotato-Sim

ロボット操作・実験のためのシミュレーション中心の基盤。Selfrionetteは対応する入力装置の一つです。

`Xpotato-Sim` の docs 正本は `docs/README.md` です。
このルート README は、current architecture、plugin、backend / viewerの最初の入口だけをまとめます。

## まず読むもの

- [docs/README.md](docs/README.md)
- [docs/architecture/dependency-boundaries.md](docs/architecture/dependency-boundaries.md)
- [docs/architecture/runtime-composition.md](docs/architecture/runtime-composition.md)
- [docs/contracts/experiment-plugin-composition.md](docs/contracts/experiment-plugin-composition.md)
- [src/xpotato_sim/plugins/README.md](src/xpotato_sim/plugins/README.md)
- [docs/operations/backend-viewer-startup.md](docs/operations/backend-viewer-startup.md)
- [docs/operations/websocket-host-port-contract.md](docs/operations/websocket-host-port-contract.md)
- [docs/operations/runtime-to-viewer-e2e-smoke.md](docs/operations/runtime-to-viewer-e2e-smoke.md)
- [apps/mujoco-viewer/README.md](apps/mujoco-viewer/README.md)

## current architecture

MuJoCoがphysical stateのsource of truthであり、Three.js / browser viewerはrenderingと
read-only diagnosticsを担当します。複数層のcompositionは`src/xpotato_sim/runtime/`だけが所有します。
pluginはRobot、Environment、Mapping、Task、Evaluation、Input Sourceの6軸で独立選択しますが、
現在のproduction診断・運用pathはRobot、Input Source、Mappingが中心です。
Environment、Task、Evaluationにもfree-space/contactのproduction pluginと専用runnerがあります。
全6軸の選択をgeneric CLI / Web UIが一律に提供するわけではありません。

## directory map

- [`src/xpotato_sim/`](src/xpotato_sim/README.md): Python packageと各layerの入口
- [`src/xpotato_sim/plugins/`](src/xpotato_sim/plugins/README.md): plugin hierarchyと追加方法
- [`apps/mujoco-viewer/`](apps/mujoco-viewer/README.md): rendering-only browser viewer
- [`scripts/`](scripts/README.md): repository / diagnostics / viewer / hardware script
- [`tests/`](tests/README.md): test ownershipとvalidation入口
- [`firmware/`](firmware/README.md): hardware firmwareとlegacy境界
- [`docs/`](docs/README.md): canonical Source of Truth Map
- [`research/`](research/README.md): research logの記録条件

## セットアップ

- Python 側は `uv run ...` を使います。
- root projectはuv workspaceで独立distribution `fast_arm_core`を通常dependencyとして解決します。
  `uv sync --frozen --group dev`はrootとcoreをeditableに同期し、配布確認ではcore wheelとroot wheelを別々にbuild/installします。
- viewer 側は `apps/mujoco-viewer` 配下で `npm ci` を実行します。
- browser viewer 用の build は `npm run browser:build` です。
- `npm run typecheck`はTypeScript静的検証、`npm run build`はViteによるブラウザbundle生成です。
- `npm test` は viewer runtime / WebSocket skeleton のテストを実行します。

独立wheelの確認:

```bash
uv build --wheel src/xpotato_sim/plugins/robots/fast_arm/core --out-dir dist
uv build --wheel --out-dir dist
```

root sdist/wheelは`fast_arm_core` sourceを内包せず、install時にcore wheelを通常dependencyとして要求します。
rootのpackage dataはadapter resourceだけを明示収集し、物理mount pointは`MANIFEST.in`でもpruneします。

## 起動導線

通常はリポジトリrootから次の一つを実行します。初回だけ`uv sync --frozen --group dev`と
`npm --prefix apps/mujoco-viewer ci`で依存を揃えてください。

```powershell
uv run xpotato-sim app --profile sim-gamepad
```

`sim-keyboard` / `replay-sweep`へprofileを切り替えられます。Webとbackendの両方を起動し、
ブラウザを一度だけ開きます。終了はCtrl+Cまたはprofileの有限実行完了です。
`uv run xpotato-sim app --profile sim-gamepad --check`は検査のみを行います。
設定仕様は`docs/contracts/launch-profile.md`、操作の正本は`docs/operations/backend-viewer-startup.md`です。
以下は低位の個別開発・診断用の入口です。

### backend / dry-run

```bash
uv run xpotato-sim replay --robot fast_arm --steps 1
uv run xpotato-sim replay --robot fast_arm --steps 3 --preset sweep_x
```

dry-run は NDJSON payload / backend path の確認用です。WebSocket server は起動せず、browser viewer にも直接接続しません。

### WebSocket publisher

```bash
uv run xpotato-sim viewer --robot fast_arm --host 127.0.0.1 --port 8766 --steps 3
```

browser viewer に payload v0 を流す local/dev publisher です。標準的な loopback は `127.0.0.1:8766` です。

### Web viewer

```bash
cd apps/mujoco-viewer
npm ci
npm run browser:build
```

browser で開く URL 例:

```text
apps/mujoco-viewer/index.html?websocketUrl=ws://127.0.0.1:8766
```

互換 alias:

```text
apps/mujoco-viewer/index.html?ws=ws://127.0.0.1:8766
```

browser page URL と WebSocket URL は別です。viewer は `websocketUrl` を優先し、`ws` は互換 alias です。query がない場合は自動接続しません。

### live viewer smoke

```bash
uv run python scripts/viewer/run_live_viewer_smoke.py --host 127.0.0.1 --port 8766 --steps 3 --grace-period-s 5
```

browser / viewer smoke の補助導線です。CLI は browser URL と WebSocket endpoint を区別して出力します。

## URL と host の注意

- `127.0.0.1` / `localhost` は同じ machine 上の browser 向け loopback です。
- `0.0.0.0` は server 側の bind address です。browser URL の host としては通常使いません。
- LAN / Tailscale / public host から開くときは、browser から見える host を URL に使います。
- bind host と browser から見える host は別です。
- viewer page URL と WebSocket endpoint URL は別です。
- 詳細な host / port / URL contract は [docs/operations/websocket-host-port-contract.md](docs/operations/websocket-host-port-contract.md) を参照してください。

## 参照

- [docs/operations/runtime-dry-run.md](docs/operations/runtime-dry-run.md)
- [docs/operations/unified-cli.md](docs/operations/unified-cli.md)
- [docs/operations/websocket-publisher-runner.md](docs/operations/websocket-publisher-runner.md)
- [docs/operations/live-viewer-smoke.md](docs/operations/live-viewer-smoke.md)
- [docs/operations/runtime-to-viewer-e2e-smoke.md](docs/operations/runtime-to-viewer-e2e-smoke.md)
- [docs/operations/browser-visual-smoke.md](docs/operations/browser-visual-smoke.md)
- [docs/reports/audits/r6-g-p3-startup-script-gap-audit.md](docs/reports/audits/r6-g-p3-startup-script-gap-audit.md)
- [docs/reports/audits/r6-f-completion-audit.md](docs/reports/audits/r6-f-completion-audit.md)

名称移行と起動方法: [Xpotato-Simへの名称移行](docs/operations/xpotato-sim-migration.md)。
