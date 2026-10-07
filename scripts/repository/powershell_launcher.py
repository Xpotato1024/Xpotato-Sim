"""PowerShell 5.1のargv/文字コード境界だけを扱う。option/defaultは対象Pythonが所有する。"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[2]


def main(arguments: list[str] | None = None) -> int:
    script, encoded = sys.argv[1:] if arguments is None else arguments
    target = (ROOT / script).resolve()
    target.relative_to(ROOT)
    forwarded = json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
    if not isinstance(forwarded, list) or not all(isinstance(item, str) for item in forwarded):
        raise ValueError("PowerShell argv must be a list of strings")
    previous = sys.argv
    try:
        sys.argv = [str(target), *forwarded]
        runpy.run_path(str(target), run_name="__main__")
        return 0
    finally:
        sys.argv = previous


if __name__ == "__main__":
    raise SystemExit(main())
