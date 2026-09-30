"""検証済みviewer buildと現在のmodel allowlistだけを配信するlocal HTTP worker。"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html.parser import HTMLParser
import mimetypes
from pathlib import Path
from threading import BoundedSemaphore
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener
from urllib.parse import urljoin, urlsplit


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
