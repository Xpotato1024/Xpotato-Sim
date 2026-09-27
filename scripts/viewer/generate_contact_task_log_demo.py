"""contact-task-log/v1 の合成デモを型付きの契約から再生成する。"""

from __future__ import annotations

import runpy
import argparse
import sys
from pathlib import Path


def main() -> int:
    """合成デモJSONLを固定のviewer fixture pathへ原子的に書き出す。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="出力先。省略時は固定のviewer fixture path")
    arguments = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[2]
    for import_root in (
        repository_root / "src",
        repository_root / "src" / "xpotato_sim" / "plugins" / "robots" / "fast_arm" / "core" / "src",
    ):
        sys.path.insert(0, str(import_root))
    test_module = runpy.run_path(
        str(repository_root / "tests" / "runtime" / "test_contact_task_log.py")
    )
    log = test_module["_build_log"]()
    writer = test_module["write_contact_task_log"]
    output = arguments.output or (
        repository_root
        / "apps"
        / "mujoco-viewer"
        / "tests"
        / "fixtures"
        / "contact-cube-v1-demo.jsonl"
    )
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    writer(output, log, overwrite=True)
    print(f"Wrote synthetic viewer fixture: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
