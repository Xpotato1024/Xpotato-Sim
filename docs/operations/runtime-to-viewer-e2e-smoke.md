---
status: canonical
owner: operations
last_verified: 2026-07-16
canonical_for:
  - runtime-to-viewer E2E smoke
  - browser viewer troubleshooting
related:
  - README.md
  - apps/mujoco-viewer/README.md
  - docs/operations/backend-viewer-startup.md
  - docs/operations/websocket-host-port-contract.md
  - docs/operations/live-viewer-smoke.md
  - docs/operations/browser-visual-smoke.md
  - docs/operations/japanese-doc-writing-guardrails.md
---

# Runtime-to-Viewer E2E Smoke

## 目的

backend / dry-run 起動から WebSocket publisher、Web viewer、browser 接続までを
1 本の smoke として固定する。ここでは新しい viewer visual feature は追加せず、
current visual elementsを起動導線込みで観測できることだけを確認する。

## 前提

- `docs/operations/websocket-host-port-contract.md` にある bind host / browser-visible host /
  viewer page URL / WebSocket endpoint URL の contract を前提にする。
- CLI と host / port の正本は `docs/operations/unified-cli.md` と
  `docs/operations/websocket-host-port-contract.md` にある。
- viewer は rendering-only のままにする。
- browser-side MuJoCo model loading、viewer-side FK / IK、viewer-side qpos pose recompute は追加しない。
- production deployment、auth / TLS / reverse proxy、hardware / serial / OSC は扱わない。

## Smoke target

```text
backend / dry-run 起動
  -> payload v0 が出る
  -> WebSocket publisher が payload v0 を配信する
  -> viewer が WebSocket 接続する
  -> browser で target / tip / error / skeleton / mesh / DoF ring が観測できる
```

## 最短 loopback 手順

```bash
uv run xpotato-sim replay --robot fast_arm --steps 3 --preset sweep_x
uv run xpotato-sim viewer --robot fast_arm --host 127.0.0.1 --port 8766 --steps 3
cd apps/mujoco-viewer
npm ci
npm run browser:build
```

browser URL:

```text
http://127.0.0.1:5173/index.html?websocketUrl=ws://127.0.0.1:8766
```

旧`?ws=`は退役エラーとして起動を停止する。`?websocketUrl=ws://127.0.0.1:8766`を使用する。
viewer page URL と WebSocket endpoint URL は別である。host / port の詳細は
`docs/operations/websocket-host-port-contract.md` を参照する。

## WebSocket publisher 起動

- `uv run xpotato-sim replay --robot fast_arm --steps 3 --preset sweep_x` で payload / backend path を確認する。
- `uv run xpotato-sim viewer --robot fast_arm --host 127.0.0.1 --port 8766 --steps 3` で
  loopback の WebSocket endpoint を開く。
- `127.0.0.1:8766` は local smoke 用の既定値である。
- `0.0.0.0` は bind address であり browser URL の host ではない。

## Web viewer build

```bash
cd apps/mujoco-viewer
npm ci
npm run browser:build
```

- `npm ci` は viewer 側の依存を揃える。
- `npm run browser:build` は `index.html` が参照する `dist/browser/main.js` を生成する。
- `npm run typecheck` と `npm run build` は必要に応じて viewer の TypeScript 健全性を確認する。

## browser 接続

- `http://127.0.0.1:5173/index.html?websocketUrl=ws://127.0.0.1:8766` を browser で開く。
- `ws://` を使い、`http://` と混同しない。
- viewer page URL と WebSocket endpoint URL を分けて考える。
- `localhost` と `127.0.0.1` は same machine の loopback 用であり、`0.0.0.0` ではない。

## 観測項目

- WebSocket status が `open` になる。
- payload version が `v0` として観測できる。
- target marker が表示される。
- tip marker が表示される。
- target-tip error vector が表示される。
- arm skeleton が payload に追従する。
- fast_arm mesh が表示される。
- DoF ring descriptor / present / absent count が観測できる。

## root attributes / status text

- `data-websocket-status`
- `data-websocket-url`
- `data-payload-version`
- `data-marker-body-count`
- `data-marker-site-count`
- `data-marker-object-count`
- `data-arm-skeleton-status`
- `data-fast-arm-mesh-status`
- `data-dof-ring-status`
- `data-dof-ring-descriptor-count`
- `data-dof-ring-present-count`
- `data-dof-ring-absent-count`
- `data-dof-ring-count`

status text は `WebSocket: open` を含み、`connecting` / `closed` / `error` も
判別できるようにする。`data-dof-ring-count` は descriptor count の互換 alias であり、
present / absent の内訳は `data-dof-ring-present-count` と `data-dof-ring-absent-count` で読む。

## current visual elements

- target / tip / error vector / arm skeleton / fast_arm mesh / DoF ring を smoke の観測対象にする。
- これはcurrent visual elementsの観測であり、新規 visual feature ではない。
- browser pixel-level smoke の本格実装はしない。
- scene の見た目の polish は扱わない。

## Troubleshooting

### port が埋まっている

- 8766 が使用中か確認する。
- 別 port を使う場合は `websocketUrl` も同じ port に合わせる。

### viewer に payload が来ない

- publisher が起動しているか確認する。
- browser の `websocketUrl` が publisher endpoint を指しているか確認する。
- `ws://` と `http://` を混同していないか確認する。
- host / port / URL contract に沿っているか確認する。

### browser で開けない

- `npm run browser:build` が済んでいるか確認する。
- `index.html` の path が正しいか確認する。
- browser console に module / script error がないか確認する。
- launcher を使う場合は `--public-host` を明示して browser-visible host を固定する。

### `localhost` と `0.0.0.0` を混同している

- `0.0.0.0` は bind address である。
- browser URL には browser-visible host を使う。
- same machine なら `127.0.0.1` か `localhost` を使う。

### LAN / Tailscale で接続できない

- publisher が `--host 0.0.0.0` で bind されているか確認する。
- browser から見える host を使っているか確認する。
- `127.0.0.1` のままにしていないか確認する。
- firewall / OS network permission を確認する。

### browser console に WebSocket connection error が出る

- endpoint URL が `ws://...` になっているか確認する。
- `websocketUrl` と `ws` のどちらを使っているかを確認する。
- viewer page URL と WebSocket endpoint URL を取り違えていないか確認する。

### npm install / build / browser build が失敗する

- `cd apps/mujoco-viewer && npm ci` をやり直す。
- `npm run typecheck` と `npm run build` で TypeScript の失敗箇所を確認する。
- `npm run browser:build` で browser bundle の生成可否を確認する。

### browser-visible host が loopback のままになっている

- LAN / Tailscale / public host から開くときは loopback の `127.0.0.1` を使わない。
- browser-visible host は接続元から到達できる host を使う。
- 詳細は `docs/operations/websocket-host-port-contract.md` を参照する。

## Non-Goals

- 新規 viewer visual feature
- final UI polish
- browser pixel-level smoke の本格実装
- production deployment
- auth / TLS / reverse proxy
- browser-side MuJoCo model loading
- viewer-side FK / IK
- viewer-side qpos pose recompute
- hardware / serial / OSC
- legacy import / execute
- package dependency change
