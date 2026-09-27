from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from xpotato_sim.runtime.runners.live_viewer_smoke import (
    build_live_viewer_smoke_parser,
    build_live_viewer_smoke_report_lines,
    run_live_viewer_smoke,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_live_viewer_smoke_parser()
    args = parser.parse_args(argv)

    for line in build_live_viewer_smoke_report_lines(args.host, args.port):
        print(line)
    run_live_viewer_smoke(
        host=args.host,
        port=args.port,
        steps=args.steps,
        dt_s=args.dt_s,
        interval_s=args.interval_s,
        grace_period_s=args.grace_period_s,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
