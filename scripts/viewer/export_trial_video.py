"""保存済み有限試行を字幕・計器なしのMP4へ書き出す。物理再実行・実時間録画はしない。"""
from __future__ import annotations
import argparse
import base64
from hashlib import file_digest
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from uuid import uuid4
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
from xpotato_sim.runtime.experiment.video_bundle import CAMERAS, build_video_bundle, frame_schedule
from websockets.sync.client import connect


def browser_path(explicit=None):
    if explicit:
        result = Path(explicit).resolve()
        if not result.is_file():
            raise FileNotFoundError(result)
        return str(result)
    for name in ("chromium", "chromium-browser", "google-chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        for suffix in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"):
            path = Path(os.environ.get(env, "")) / suffix
            if path.is_file():
                return str(path.resolve())
    # WindowsのMCP環境ではProgramFiles系環境変数が公開されない場合がある。
    if os.name == "nt":
        for base in (Path("C:/Program Files"), Path("C:/Program Files (x86)")):
            for suffix in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"):
                path = base / suffix
                if path.is_file():
                    return str(path.resolve())
    raise RuntimeError("Chromium/Chrome/Edge not found; supply --browser. No browser is downloaded automatically.")


def _wait_file(path, process, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("owned process exited before startup; inspect video-export.log")
        if path.is_file() and path.stat().st_size:
            return path.read_text(encoding="utf-8")
        time.sleep(.05)
    raise TimeoutError("owned process startup timed out")


def owned_tab(process, profile):
    """専用profileが発行したCDPだけに接続し、ユーザーの既存browserへ接続しない。"""
    lines = _wait_file(profile / "DevToolsActivePort", process).splitlines()
    if len(lines) < 2:
        raise ValueError("incomplete owned Chromium endpoint")
    port = int(lines[0])
    if not 0 < port < 65536 or not lines[1].startswith("/devtools/browser/"):
        raise ValueError("invalid owned Chromium endpoint")
    origin = f"http://127.0.0.1:{port}"
    with urllib.request.urlopen(origin + "/json/version", timeout=5) as reply:
        version = json.load(reply)
    if version["webSocketDebuggerUrl"] != f"ws://127.0.0.1:{port}{lines[1]}":
        raise ValueError("owned Chromium identity mismatch")
    with urllib.request.urlopen(origin + "/json/list", timeout=5) as reply:
        tabs = json.load(reply)
    tab = next(item for item in tabs if item.get("type") == "page")
    url = urlsplit(tab["webSocketDebuggerUrl"])
    if url.scheme != "ws" or url.netloc != f"127.0.0.1:{port}" or not url.path.startswith("/devtools/page/"):
        raise ValueError("invalid owned page endpoint")
    return tab["webSocketDebuggerUrl"]


class CDP:
    def __init__(self, wire):
        self.wire, self.sequence, self.errors = wire, 0, []

    def call(self, method, params=None):
        self.sequence += 1
        rid = self.sequence
        self.wire.send(json.dumps({"id": rid, "method": method, "params": params or {}}))
        deadline = time.monotonic() + 60
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("CDP timeout: " + method)
            item = json.loads(self.wire.recv(timeout=remaining))
            if item.get("method") == "Runtime.exceptionThrown":
                self.errors.append(item["params"])
            if item.get("id") == rid:
                if "error" in item:
                    raise RuntimeError(item["error"])
                return item.get("result", {})

    def js(self, expression):
        result = self.call("Runtime.evaluate", {"expression": expression, "awaitPromise": True, "returnByValue": True})
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"])
        return result.get("result", {}).get("value")


def _stop(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def export(args):
    output = args.output.resolve()
    sidecar = output.with_suffix(".json")
    if output.suffix.lower() != ".mp4" or output.exists() or sidecar.exists():
        raise ValueError("new .mp4 output and sidecar paths required; existing files are never overwritten")
    if output.parent == args.trial.resolve() or args.trial.resolve() in output.parents:
        raise ValueError("output must be outside the immutable trial")
    from xpotato_sim.runtime.experiment.trial_analysis import load_validated_trial
    validated = load_validated_trial(args.trial)
    if validated.analysis()["condition"]["schema_version"] == "trial-condition/v2":
        from xpotato_sim.runtime.experiment.scene_video_export import export_scene_only_video
        return export_scene_only_video(args, trial=validated)
    if getattr(args, "gif", False) or getattr(args, "webm", False) or getattr(args, "speed", 1.0) != 1.0:
        raise ValueError("GIF/WebM and speed controls require a recorded passive Scene")
    if any(type(v) is not int or not 16 <= v <= 3840 or v % 2 for v in (args.width, args.height)):
        raise ValueError("even image dimensions in [16,3840] required")
    if type(args.fps) is not int or not 1 <= args.fps <= 120:
        raise ValueError("fps must be an integer in [1,120]")
    browser_executable = browser_path(args.browser)
    tools = {name: shutil.which(name) for name in ("node", "ffmpeg", "ffprobe")}
    if not all(tools.values()):
        raise RuntimeError("Node, FFmpeg and FFprobe must be installed")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="raw-video-", dir=output.parent) as temporary:
        temp = Path(temporary)
        frames, evidence = build_video_bundle(args.trial, temp / "bundle", camera=args.camera)
        schedule = frame_schedule([f["time_s"] for f in frames], args.fps)
        browser = server = encoder = wire = None
        cdp = None
        log_path = output.with_name(output.stem + "." + uuid4().hex[:8] + ".export.log")
        if log_path.exists():
            raise FileExistsError(log_path)
        with log_path.open("x", encoding="utf-8") as log:
            try:
                receipt = temp / "server.json"
                server = subprocess.Popen([tools["node"], str(ROOT / "scripts/viewer/video_server.mjs"),
                    str(temp / "bundle"), str(receipt)], cwd=ROOT, stdin=subprocess.PIPE, stdout=log, stderr=log)
                url = json.loads(_wait_file(receipt, server))["url"]
                parsed = urlsplit(url)
                if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
                    raise ValueError("export server is not loopback")
                profile = temp / "browser"
                profile.mkdir()
                browser = subprocess.Popen([browser_executable, "--headless=new", "--remote-debugging-port=0",
                    "--user-data-dir=" + str(profile), "--no-first-run", "--no-default-browser-check",
                    "--disable-background-networking", "--disable-extensions", "--hide-scrollbars",
                    "--disable-component-update", "--disable-sync", "about:blank"], stdout=log, stderr=log)
                wire = connect(owned_tab(browser, profile), max_size=32*1024*1024, open_timeout=10)
                cdp = CDP(wire)
                cdp.call("Page.enable")
                cdp.call("Runtime.enable")
                cdp.call("Emulation.setDeviceMetricsOverride", {"width": args.width, "height": args.height,
                    "deviceScaleFactor": 1, "mobile": False})
                cdp.call("Page.navigate", {"url": url})
                deadline = time.monotonic() + 60
                while not cdp.js("Boolean(window.rawVideo)"):
                    error = cdp.js("window.rawVideoError")
                    if error:
                        raise RuntimeError(error)
                    if time.monotonic() > deadline:
                        raise TimeoutError("raw Viewer did not become ready")
                    time.sleep(.05)
                if cdp.js("document.body.innerText.trim()") or cdp.js("document.querySelectorAll('canvas').length") != 1:
                    raise ValueError("raw video requires one canvas and no text UI")
                staged = temp / "video.mp4"
                encoder = subprocess.Popen([tools["ffmpeg"], "-hide_banner", "-loglevel", "error", "-f", "image2pipe",
                    "-vcodec", "png", "-framerate", str(args.fps), "-i", "pipe:0", "-an", "-c:v", "libx264",
                    "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(staged)],
                    stdin=subprocess.PIPE, stdout=log, stderr=log)
                for number, index in enumerate(schedule):
                    observed = cdp.js(f"window.rawVideo.show({index})")
                    if observed["rendered"] != frames[index]["tick"]:
                        raise ValueError("rendered frame does not match requested recorded state")
                    pixels = cdp.call("Page.captureScreenshot", {"format": "png", "fromSurface": True,
                        "captureBeyondViewport": False})["data"]
                    encoder.stdin.write(base64.b64decode(pixels, validate=True))
                    if number % args.fps == 0:
                        print(f"frames {number}/{len(schedule)}", flush=True)
                encoder.stdin.close()
                if encoder.wait(timeout=90):
                    raise RuntimeError("FFmpeg failed")
                evidence["renderer_counters"] = cdp.js("window.rawVideo.counters()")
                if evidence["renderer_counters"]["raf"] != 0 or evidence["renderer_counters"]["visibleDiagnosticGroups"] != 0:
                    raise ValueError("raw renderer must have no animation loop or diagnostic overlays")
                evidence["browser_errors"] = cdp.errors
                if cdp.errors:
                    raise RuntimeError("browser exceptions during export")
                probe = json.loads(subprocess.check_output([tools["ffprobe"], "-v", "error", "-show_entries",
                    "stream=codec_name,width,height,nb_frames,avg_frame_rate:format=duration", "-of", "json", str(staged)]))
                stream = probe["streams"][0]
                if (int(stream["nb_frames"]) != len(schedule) or stream["width"] != args.width or stream["height"] != args.height
                        or stream["codec_name"] != "h264" or stream["avg_frame_rate"] != f"{args.fps}/1"):
                    raise ValueError("encoded video metadata mismatch")
                subprocess.run([tools["ffmpeg"], "-v", "error", "-i", str(staged), "-f", "null", "-"],
                    check=True, timeout=90, stdout=log, stderr=log)
                revision = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
                dirty = bool(subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain"]))
                with staged.open("rb") as content:
                    digest=file_digest(content,"sha256").hexdigest()
                evidence.update(renderer_revision=revision, renderer_dirty=dirty, fps=args.fps, width=args.width,
                    height=args.height, output_frames=len(schedule), encoded_duration_s=len(schedule)/args.fps,
                    video_sha256=digest, full_decode="passed", export_log=log_path.name)
                # 既存出力を置き換えず、完成・検証済みartifactだけを公開する。
                staged_json = temp / "video.json"
                staged_json.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
                os.link(staged_json, sidecar)
                try:
                    os.link(staged, output)
                except Exception:
                    sidecar.unlink()
                    raise
                print(str(output), flush=True)
                return evidence
            finally:
                if cdp:
                    try:
                        cdp.js("window.rawVideo?.close()")
                        cdp.call("Browser.close")
                    except Exception:
                        pass
                if wire:
                    wire.close()
                _stop(encoder)
                _stop(browser)
                if server and server.poll() is None:
                    try:
                        server.stdin.write(b"close\\n")
                        server.stdin.flush()
                        server.stdin.close()
                        server.wait(timeout=10)
                    except (OSError, subprocess.TimeoutExpired):
                        _stop(server)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trial", type=Path, help="保存済み有限試行directory")
    parser.add_argument("output", type=Path, help="新規MP4のpath")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--camera", choices=CAMERAS, default="operator")
    parser.add_argument("--browser", help="Chrome/Edge/Chromium実行file（省略時は既存installを探索）")
    parser.add_argument("--gif", action="store_true", help="受動Scene試行で同じ記録からGIFも書き出す")
    parser.add_argument("--webm", action="store_true", help="受動Scene試行でWebMも書き出す")
    parser.add_argument("--speed", type=float, default=1.0, help="受動Scene動画の再生速度倍率（0.1〜4）")
    export(parser.parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
