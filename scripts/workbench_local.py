"""root .envから既存Workbench CLIを組み立てるローカル起動の唯一のowner。"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Mapping

ROOT = Path(__file__).resolve().parents[1]
KEYS = {
    "XPOTATO_TEMP_ROOT", "XPOTATO_RESULT_ROOT", "XPOTATO_WEB_DIST",
    "XPOTATO_OPEN_BROWSER", "XPOTATO_WEB_PORT", "XPOTATO_BACKEND_PORT", "XPOTATO_PYTHON",
}
OWNER = ".xpotato-local-build.json"
CLI_ENTRY = "from xpotato_sim.cli import main; raise SystemExit(main())"


def tool(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise ValueError(f"必須tool {name} がありません。PATHを確認してください")
    return found


def execute(argv: list[str], *, env: dict[str, str], cwd: Path = ROOT) -> None:
    # 引数配列を使い、設定値をshell commandへ展開しない。toolの診断に秘密を載せない。
    result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True)
    if result.returncode:
        raise ValueError(f"{Path(argv[0]).name} の処理が失敗しました（exit {result.returncode}）")


def defaults(root: Path, env: Mapping[str, str], platform: str) -> dict[str, str]:
    identity = hashlib.sha256(str(root).encode()).hexdigest()[:12]
    temp = Path(tempfile.gettempdir()) / "xpotato-workbench" / identity
    if platform == "win32":
        data = Path(env.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    elif platform == "darwin":
        data = Path.home() / "Library/Application Support"
    else:
        data = Path(env.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    return {
        "XPOTATO_TEMP_ROOT": temp.as_posix(),
        "XPOTATO_RESULT_ROOT": (data / "Xpotato-Sim/results" / identity).as_posix(),
        "XPOTATO_WEB_DIST": (temp / "viewer-dist").as_posix(),
        "XPOTATO_OPEN_BROWSER": "true", "XPOTATO_WEB_PORT": "5173",
        "XPOTATO_BACKEND_PORT": "8766", "XPOTATO_PYTHON": "3.12",
    }


def init(root: Path = ROOT) -> None:
    try:
        with (root / ".env").open("x", encoding="utf-8", newline="\n") as stream:
            stream.write("# ローカル運用設定（Git管理外）。revisionは起動時に自動取得。\n")
            for key, value in defaults(root, os.environ, sys.platform).items():
                stream.write(f"{key}={json.dumps(value, ensure_ascii=False)}\n")
    except FileExistsError:
        print(".env は既に存在します。上書きしません")
    else:
        print("root .env を作成しました。保存先とportを確認し、just setup を実行してください")


def overlap(a: Path, b: Path) -> bool:
    return a.is_relative_to(b) or b.is_relative_to(a)


@dataclass(frozen=True)
class Config:
    temp: Path
    results: Path
    dist: Path
    browser: bool
    web_port: int
    backend_port: int
    python: str

    @classmethod
    def read(cls, root: Path, env: Mapping[str, str]) -> Config:
        if any(key.startswith("XPOTATO_") and key not in KEYS for key in env):
            raise ValueError("不明なXPOTATO設定があります。キーの誤字を確認してください（値は表示しません）")
        if not KEYS <= env.keys() or any(not env[key].strip() for key in KEYS):
            raise ValueError("必要な.env設定が空または欠落しています。.env.exampleのキーを確認してください")

        def path(key: str) -> Path:
            raw = Path(env[key])
            if not raw.is_absolute() and raw.drive:
                raise ValueError(f"{key} のdrive相対pathは禁止です")
            return (root / raw).resolve()

        def port(key: str) -> int:
            if not re.fullmatch(r"[0-9]+", env[key]) or not 1 <= int(env[key]) <= 65535:
                raise ValueError(f"{key} は1..65535の整数にしてください")
            return int(env[key])

        boolean = env["XPOTATO_OPEN_BROWSER"].lower()
        if boolean not in {"true", "false"}:
            raise ValueError("XPOTATO_OPEN_BROWSER はtrue/falseにしてください")
        config = cls(path("XPOTATO_TEMP_ROOT"), path("XPOTATO_RESULT_ROOT"),
                     path("XPOTATO_WEB_DIST"), boolean == "true", port("XPOTATO_WEB_PORT"),
                     port("XPOTATO_BACKEND_PORT"), env["XPOTATO_PYTHON"])
        if config.web_port == config.backend_port:
            raise ValueError("Web/backend portを別にしてください")
        if not re.fullmatch(r"3\.12(?:\.[0-9]+)?", config.python) and not Path(config.python).is_absolute():
            raise ValueError("XPOTATO_PYTHON は3.12、3.12.xまたはPython 3.12の絶対実行fileにしてください")
        if (overlap(config.dist, root) or overlap(config.temp, root)
                or root.is_relative_to(config.results) or overlap(config.dist, config.results)
                or config.temp.is_relative_to(config.dist) or overlap(config.temp, config.results)):
            raise ValueError("build/temp/result/repositoryの危険な重なりです。buildはrepository外、resultと分離してください")
        for item in (config.temp, config.results, config.dist):
            if item == Path(item.anchor) or (item.exists() and not item.is_dir()):
                raise ValueError("運用pathにdrive rootまたは通常fileは指定できません")
        return config

    def environment(self, root: Path) -> dict[str, str]:
        # uvの暗黙設定で別環境へ同期しない。root .envは運用キーだけを使う。
        env = {key: value for key, value in os.environ.items() if not key.startswith("UV_")}
        if "UV_OFFLINE" in os.environ:
            env["UV_OFFLINE"] = os.environ["UV_OFFLINE"]
        env.update({"UV_PROJECT_ENVIRONMENT": str(root / ".venv"),
                    "UV_CACHE_DIR": str(self.temp / "uv-cache"),
                    "npm_config_cache": str(self.temp / "npm-cache"),
                    "TEMP": str(self.temp), "TMP": str(self.temp), "TMPDIR": str(self.temp),
                    "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": str(root / "src"),
                    "XPOTATO_VITE_CACHE": str(self.temp / "vite-cache")})
        return env


def venv_python(root: Path) -> Path:
    return root / (".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python")


def fingerprint(root: Path, config: Config) -> dict[str, str]:
    digest = hashlib.sha256()
    for path in (root / "pyproject.toml", root / "uv.lock",
                 root / "src/xpotato_sim/plugins/robots/fast_arm/core/pyproject.toml"):
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return {"dependencies": digest.hexdigest(), "python_selection": config.python}


def python_version(python: Path, env: dict[str, str]) -> str:
    try:
        result = subprocess.run([str(python), "-I", "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
                                env=env, capture_output=True, check=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("準備済みPythonがありません。just setup を実行してください") from exc
    return result.stdout.strip()


def prepared(root: Path, config: Config) -> None:
    env = config.environment(root)
    version = python_version(venv_python(root), env)
    if not version.startswith("3.12."):
        raise ValueError("専用venvはPython 3.12ではありません。just setup で明示準備してください")
    if re.fullmatch(r"3\.12\.[0-9]+", config.python) and version != config.python:
        raise ValueError("設定とvenvのPython patch版が一致しません。just setup を実行してください")
    if Path(config.python).is_absolute() and python_version(Path(config.python), env) != version:
        raise ValueError("設定とvenvのPythonが一致しません。just setup を実行してください")
    try:
        marker = json.loads((root / ".venv/workbench-local.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("ローカル環境が未準備です。just setup を実行してください") from exc
    if marker != fingerprint(root, config):
        raise ValueError("設定/lockと準備済み環境が一致しません。just setup を実行してください")
    if not config.temp.is_dir() or not (root / ".venv/.lock").is_file():
        raise ValueError("準備済み運用directoryがありません。just setup を実行してください")
    try:
        # --checkもuvの一時coordination lockを作るため、検査の一時物だけを隔離・除去する。
        with tempfile.TemporaryDirectory(prefix="environment-check-", dir=config.temp) as directory:
            env.update({"TEMP": directory, "TMP": directory, "TMPDIR": directory})
            execute([tool("uv"), "sync", "--check", "--locked", "--offline", "--no-cache", "--no-python-downloads",
                     "--python", str(venv_python(root)), "--group", "dev", "--project", str(root)], env=env, cwd=root)
    except ValueError as exc:
        raise ValueError("準備済み依存が一致しません。just setup を実行してください") from exc


def verify_build(root: Path, dist: Path) -> None:
    spec = importlib.util.spec_from_file_location("local_build_check", root / "src/xpotato_sim/runtime/runners/workbench_web.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        module.verify_build_identity(dist, root)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError("固定buildが欠落・古い・不正です。just build を実行してください") from exc


def owner(root: Path) -> dict[str, str]:
    return {"schema": "xpotato-local-build/v1", "workspace": str(root)}


def check_owner(root: Path, dist: Path) -> None:
    if not dist.exists():
        return
    try:
        valid = json.loads((dist / OWNER).read_text(encoding="utf-8")) == owner(root)
    except (OSError, ValueError):
        valid = False
    if not valid or any(p.is_symlink() or p.is_junction() for p in [dist, *dist.rglob("*")]):
        raise ValueError("build出力先はこのランチャー所有ではありません。未作成の新しい出力先を設定してください")


def web_fingerprint(root: Path) -> dict[str, str]:
    app = root / "apps/mujoco-viewer"
    value = {"schema": "workbench-local-web/v1", "workspace": str(root.resolve())}
    for name in ("package.json", "package-lock.json", "node_modules/.package-lock.json"):
        value[name] = hashlib.sha256((app / name).read_bytes()).hexdigest()
    return value


def verify_web_dependencies(root: Path) -> None:
    try:
        path = root / "apps/mujoco-viewer/node_modules/.workbench-local-dependencies.json"
        valid = json.loads(path.read_text(encoding="utf-8")) == web_fingerprint(root)
    except (OSError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Web依存と設定/lockが一致しません。just setup を実行してください")


def build(root: Path, config: Config) -> None:
    check_owner(root, config.dist)
    verify_web_dependencies(root)
    node = tool("node")
    vite = root / "apps/mujoco-viewer/node_modules/vite/bin/vite.js"
    if not vite.is_file():
        raise ValueError("Web依存がありません。just setup を実行してください")
    config.temp.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="build-", dir=config.temp) as directory:
        stage = Path(directory) / "dist"
        execute([node, str(vite), "build", "--config", str(root / "apps/mujoco-viewer/vite.config.ts"),
                 "--configLoader", "runner", "--outDir", str(stage)], env=config.environment(root), cwd=root)
        verify_build(root, stage)
        (stage / OWNER).write_text(json.dumps(owner(root)), encoding="utf-8")
        config.dist.parent.mkdir(parents=True, exist_ok=True)
        backup = Path(directory) / "previous"
        check_owner(root, config.dist)
        existed = config.dist.exists()
        if existed:
            config.dist.rename(backup)
        try:
            stage.rename(config.dist)
        except OSError:
            if existed:
                backup.rename(config.dist)
            raise ValueError("buildの置換に失敗しました。tempとbuildは同じvolumeに置いてください") from None
    print("固定production buildを検証・配置しました")


def setup(root: Path, config: Config) -> None:
    check_owner(root, config.dist)
    uv, npm = tool("uv"), tool("npm.cmd" if sys.platform == "win32" else "npm")
    tool("node")
    config.temp.mkdir(parents=True, exist_ok=True)
    config.results.mkdir(parents=True, exist_ok=True)
    env = config.environment(root)
    if Path(config.python).is_absolute() and not python_version(Path(config.python), env).startswith("3.12."):
        raise ValueError("XPOTATO_PYTHON にPython 3.12を指定してください")
    execute([uv, "sync", "--locked", "--group", "dev", "--no-python-downloads", "--python", config.python,
             "--project", str(root)], env=env, cwd=root)
    version = python_version(venv_python(root), env)
    if not version.startswith("3.12."):
        raise ValueError("Python 3.12を指定してください")
    (root / ".venv/workbench-local.json").write_text(json.dumps(fingerprint(root, config)), encoding="utf-8")
    execute([npm, "--prefix", str(root / "apps/mujoco-viewer"), "ci", "--no-audit", "--no-fund"], env=env, cwd=root)
    marker = root / "apps/mujoco-viewer/node_modules/.workbench-local-dependencies.json"
    marker.write_text(json.dumps(web_fingerprint(root)), encoding="utf-8")
    build(root, config)
    print("準備完了。通常起動は just run です")


def revision(root: Path) -> str:
    git = tool("git")
    def read(*args: str) -> bytes:
        return subprocess.run([git, "-C", str(root), *args], capture_output=True, check=True).stdout
    head = read("rev-parse", "HEAD").decode().strip()
    diff = read("diff", "HEAD", "--binary", "--no-ext-diff")
    untracked = read("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
    digest = hashlib.sha256(diff)
    dirty = bool(diff)
    for name in sorted(n for n in untracked if n):
        path = root / os.fsdecode(name)
        if path.is_file():
            dirty = True
            digest.update(name + b"\0" + path.read_bytes() + b"\0")
    return head + ("-dirty-" + digest.hexdigest() if dirty else "")


def ports(config: Config) -> None:
    for port in (config.web_port, config.backend_port):
        with socket.socket() as sock:
            if sys.platform == "win32":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError as exc:
                # HTTP終了直後のTIME_WAITだけで通常再起動を止めない。listenerには接続だけで検出する。
                if exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == 10048:
                    with socket.socket() as probe:
                        probe.settimeout(0.2)
                        refused = probe.connect_ex(("127.0.0.1", port))
                    if refused in {errno.ECONNREFUSED, 10061}:
                        with socket.socket() as reusable:
                            reusable.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                            try:
                                reusable.bind(("127.0.0.1", port))
                            except OSError:
                                pass
                            else:
                                continue
                raise ValueError(f"loopback port {port} が利用できません。他のアプリまたは.envを確認してください") from exc


def launch(root: Path, config: Config, *, dev: bool = False) -> int:
    prepared(root, config)
    if not config.temp.is_dir() or not config.results.is_dir():
        raise ValueError("運用directoryがありません。just setup を実行してください")
    if dev:
        verify_web_dependencies(root)
        tool("node")
        if not (root / "apps/mujoco-viewer/node_modules/vite/bin/vite.js").is_file():
            raise ValueError("開発用Web依存がありません。just setup を実行してください")
    else:
        verify_build(root, config.dist)
    ports(config)
    identity = revision(root)
    if "-dirty-" in identity:
        print("未commit差分を含むdirty source identityで起動します")
    argv = [str(venv_python(root)), "-c", CLI_ENTRY, "workbench",
            "--temporary-root", str(config.temp), "--result-root", str(config.results),
            "--software-revision", identity, "--web-port", str(config.web_port),
            "--backend-port", str(config.backend_port)]
    argv += ["--dev-server"] if dev else ["--web-dist", str(config.dist)]
    if config.browser:
        argv.append("--open-browser")
    with subprocess.Popen(argv, cwd=root, env=config.environment(root)) as process:
        try:
            return process.wait()
        except KeyboardInterrupt:
            # 同じconsoleの正式CLIに届いたCtrl+Cのcleanupを、wrapperから早期killしない。
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)
            return 130


def doctor(root: Path, config: Config) -> int:
    checks = [("必須tool", lambda: [tool(n) for n in ("uv", "git", "node", "npm.cmd" if sys.platform == "win32" else "npm")]),
              ("Python/設定/lock/依存", lambda: prepared(root, config)),
              ("Web依存/lock", lambda: verify_web_dependencies(root)),
              ("固定build/source", lambda: verify_build(root, config.dist)),
              ("source identity", lambda: print(revision(root))),
              ("port", lambda: ports(config))]
    failed = False
    for label, check in checks:
        try:
            check()
            print(f"正常: {label}")
        except (ValueError, OSError, subprocess.CalledProcessError) as exc:
            failed = True
            print(f"問題: {label}: {exc if isinstance(exc, ValueError) else '検査できません'}")
    for label, path in (("temporary root", config.temp), ("result root", config.results)):
        if not path.is_dir():
            failed = True
            print(f"問題: {label} がありません。just setup を実行してください")
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description="ローカルWorkbench: init/setup/build/run/dev/doctor")
    parser.add_argument("command", choices=("init", "setup", "build", "run", "dev", "doctor"))
    args = parser.parse_args()
    try:
        if args.command == "init":
            init()
            return 0
        if not (ROOT / ".env").is_file():
            raise ValueError("root .env がありません。just init を実行してください")
        config = Config.read(ROOT, load_env(ROOT))
        if args.command == "doctor":
            return doctor(ROOT, config)
        if args.command == "setup":
            setup(ROOT, config)
        elif args.command == "build":
            build(ROOT, config)
        else:
            return launch(ROOT, config, dev=args.command == "dev")
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"問題: {exc if isinstance(exc, ValueError) else '処理を完了できません。toolとpathの権限を確認してください'}")
        return 1


def load_env(root: Path) -> dict[str, str]:
    # dotenv構文のownerはuv。上位directoryを探索せず、このfileだけを一度読む。
    # cwdを絶対rootに固定し、uv 0.10のpath-list解釈に空白やbackslashを渡さない。
    # uvのparse errorはfile内容を含み得るので表示しない。返すのは運用キーのみ。
    env = {key: value for key, value in os.environ.items() if not key.startswith("UV_")}
    code = "import os,json; print(json.dumps({k:v for k,v in os.environ.items() if k.startswith('XPOTATO_')}))"
    result = subprocess.run([tool("uv"), "run", "--no-project", "--no-config", "--no-cache", "--offline",
                             "--no-python-downloads", "--python", sys.executable,
                             "--env-file", ".env", "python", "-I", "-c", code],
                            cwd=root, env=env, capture_output=True)
    if result.returncode:
        raise ValueError(".env読み込みに失敗しました。dotenv構文を確認してください（内容は表示しません）")
    return json.loads(result.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
