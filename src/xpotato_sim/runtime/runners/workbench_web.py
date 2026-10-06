"""検証済みviewer buildと現在のmodel allowlistだけを配信するlocal HTTP worker。"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html.parser import HTMLParser
import mimetypes
from pathlib import Path
from threading import BoundedSemaphore
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener
from urllib.parse import urljoin, urlsplit
import hashlib
import json


def verify_build_identity(root: Path, workspace: Path):
    """現sourceとlockの完全byte一致を検査し、古いbuildを拒否する。"""
    app = workspace / "apps/mujoco-viewer"
    files = []
    for name in ("src", "tooling", "index.html", "package.json", "package-lock.json", "vite.config.ts"):
        path = app / name
        files.extend(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else files.append(path)
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda p: p.relative_to(app).as_posix()):
        digest.update(path.relative_to(app).as_posix().encode() + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    try:
        identity = json.loads((root / "workbench-build.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("固定buildがありません。事前にnpm ciとVite build --outDir <絶対build root>を実行し、--web-distへ指定してください。開発は--dev-serverを明示してください") from exc
    if (set(identity) != {"schema_version", "source_sha256", "assets"}
            or identity["schema_version"] != "workbench-build/v1" or identity["source_sha256"] != digest.hexdigest()):
        raise ValueError("固定buildと現在source/lockが一致しません。試行前に再buildしてください")
    paths = build_asset_allowlist(root)
    assets = identity["assets"]
    if not isinstance(assets, dict) or not assets:
        raise ValueError("固定buildのasset identityがありません")
    for name, expected in assets.items():
        path = paths.get("/" + name)
        if path is None or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("固定buildのasset bytesが一致しません")


def build_asset_allowlist(root: Path):
    root = root.resolve(strict=True)
    paths = {}
    for path in root.rglob("*"):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("build資産のsymlink/root外参照は禁止です")
        if path.is_file():
            paths["/" + path.relative_to(root).as_posix()] = path
    if "/apps/mujoco-viewer/index.html" not in paths:
        raise ValueError("viewer buildのindex.htmlがありません")
    class References(HTMLParser):
        def __init__(self):
            super().__init__()
            self.sources = []
            self.modules = 0

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "script" and attrs.get("src"):
                self.sources.append(attrs["src"])
                self.modules += int(attrs.get("type") == "module")
            if tag == "link" and attrs.get("rel") in {"stylesheet", "modulepreload"}:
                self.sources.append(attrs.get("href", ""))

    references = References()
    references.feed(paths["/apps/mujoco-viewer/index.html"].read_text(encoding="utf-8"))
    if not references.modules:
        raise ValueError("buildのmodule scriptがありません")
    for source in references.sources:
        parsed = urlsplit(urljoin("/apps/mujoco-viewer/index.html", source))
        if parsed.scheme or parsed.netloc or parsed.path not in paths or paths[parsed.path].stat().st_size == 0:
            raise ValueError("build参照資産が欠落または外部URLです")
    wasm_paths = [path for path in paths.values() if path.suffix == ".wasm"]
    def valid_wasm(path):
        with path.open("rb") as stream:
            return stream.read(8) == b"\x00asm\x01\x00\x00\x00"
    if not wasm_paths or not all(valid_wasm(path) for path in wasm_paths):
        raise ValueError("buildのWASM資産が欠落または不正です")
    return paths


def run_static_viewer(root: Path, port: int, backend_port: int):
    assets = build_asset_allowlist(root)
    origin = f"http://127.0.0.1:{port}"
    opener = build_opener(ProxyHandler({}))

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        request_queue_size = 8
        slots = BoundedSemaphore(16)

        def process_request(self, request, client_address):
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            request.settimeout(2)
            try:
                super().process_request(request, client_address)
            except BaseException:
                self.slots.release()
                raise

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self.slots.release()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            # URL/fragment由来の制御資格をlogへ残す経路を持たない。
            pass

        def do_GET(self):
            if self.headers.get("Host") != f"127.0.0.1:{port}" or self.headers.get("Origin", origin) != origin:
                self.send_error(403)
                return
            path = self.path.split("?", 1)[0]
            if "%" in path or "\\" in path or any(p in (".", "..") for p in path.split("/")):
                self.send_error(404)
                return
            if path == "/apps/mujoco-viewer/":
                path += "index.html"
            if path.startswith("/mujoco/fast_arm_assembly/"):
                try:
                    with opener.open(f"http://127.0.0.1:{backend_port}{path}", timeout=2) as response:
                        body = response.read(32 * 1024 * 1024 + 1)
                    if len(body) > 32 * 1024 * 1024:
                        raise ValueError("model資産が上限を超えました")
                except (HTTPError, URLError, OSError, ValueError):
                    self.send_error(404)
                    return
            elif path in assets:
                body = assets[path].read_bytes()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

    with Server(("127.0.0.1", port), Handler) as server:
        server.serve_forever(poll_interval=0.1)
