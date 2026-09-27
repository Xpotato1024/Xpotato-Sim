---
status: canonical
owner: operations
last_verified: 2026-07-18
canonical_for:
  - WebSocket / host / port / public host contract
  - backend publisher bind host vs browser-visible host
  - WebSocket host and URL contract
related:
  - README.md
  - apps/mujoco-viewer/README.md
  - docs/operations/backend-viewer-startup.md
  - docs/operations/browser-visual-smoke.md
  - docs/operations/live-viewer-smoke.md
  - docs/operations/websocket-publisher-runner.md
---

# WebSocket host / port contract

publisher の bind address と、browser から見える接続先を分けて扱う。

- `127.0.0.1` / `localhost`: 同じ machine 上の browser 用
- `0.0.0.0`: server の全interfaceへbindする値。browser URLへは使わない
- LAN / Tailscale: browser から到達できる machine のIPまたはhostnameをURLへ使う

## Loopback

```bash
uv run xpotato-sim viewer --robot fast_arm --host 127.0.0.1 --port 8766 --steps 3
```

browser 側 endpoint は `ws://127.0.0.1:8766` とする。

## LAN / Tailscale

publisher は必要な場合だけ `0.0.0.0` へbindする。

```bash
uv run xpotato-sim viewer --robot fast_arm --host 0.0.0.0 --port 8766 --steps 3
```

browser URL には `ws://<browser-visible-host>:8766` を指定し、`0.0.0.0` を含めない。
query parameter は canonical な `websocketUrl` を使用する。

```text
http://<viewer-host>/apps/mujoco-viewer/?websocketUrl=ws://<browser-visible-host>:8766
```

`ws` は既存 compatibility alias である。両方ある場合の既存 precedence は変更しない。

## Failure と非目標

port 使用中、invalid port、空hostは既存 command semantics でfailする。AutoPort、daemon、service、
TLS終端、reverse proxy、deploymentは提供しない。外部networkへ公開する場合のfirewall、TLS、認証は
このrepositoryのlocal/dev publisherの範囲外である。
