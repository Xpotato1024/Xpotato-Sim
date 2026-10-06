"""GUIと同じ制御protocolを使う明示的なheadless実行client。"""
from __future__ import annotations

import json

async def run_headless_client(config, capability):
    """CLIの明示Start。GUIと同じprotocol/service/runnerを通り、Viewer ACKだけを要求しない。"""
    from websockets.asyncio.client import connect
    async with connect(f"ws://127.0.0.1:{config['port']}/control", proxy=None, max_size=2**20) as ws:
        await ws.send(json.dumps({"op": "claim", "capability": capability}))
        await ws.send(json.dumps({"op": "status"}))
        selected = started = False
        async for raw in ws:
            event = json.loads(raw)
            if event.get("type") == "rejected" or (event.get("type") == "completed" and event.get("error")):
                raise RuntimeError(event["error"])
            if event.get("type") != "status" or event.get("busy"):
                continue
            if event["phase"] in {"faulted", "recording_failed"}:
                print(json.dumps(event.get("result") or {"error": event.get("error")}, ensure_ascii=False))
                return 1
            if event["phase"] == "terminal":
                result = event["result"]
                print(json.dumps(result, ensure_ascii=False))
                return int(result["recording"] != "complete" or result["runner_stop_reason"] not in {"simulation_budget", "task_success"})
            if event["phase"] == "unselected" and not selected:
                selected = True
                op, extra = "prepare", {"profile_id": config["profile"]}
                if config.get("condition"):
                    extra["condition"] = config["condition"]
            elif event["phase"] == "ready" and not started:
                started = True
                op, extra = "start", {}
            else:
                continue
            await ws.send(json.dumps({"op": op, "capability": capability, "id": "headless-" + op,
                "revision": event["revision"], "ticket": event["ticket"], **extra}))
    raise RuntimeError("headless制御接続が終了しました")

