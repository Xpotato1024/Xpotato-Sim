from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from xpotato_sim.runtime.runners.selfrionette_serial_dry_run import (
    main as run_selfrionette_serial_dry_run_main,
)


def main(argv: Sequence[str] | None = None) -> int:
    return run_selfrionette_serial_dry_run_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
