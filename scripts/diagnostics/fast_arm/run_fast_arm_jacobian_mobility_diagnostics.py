from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.jacobian_mobility import run_fast_arm_jacobian_mobility_diagnostics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic offline fast_arm Jacobian mobility diagnostics.")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--output", type=Path, default=None, help="explicitly write JSON to this path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_fast_arm_jacobian_mobility_diagnostics()
        payload = result.to_json()
        if args.output is not None:
            args.output.write_text(payload + "\n", encoding="utf-8", newline="\n")
        if args.json or args.output is None:
            print(payload)
        else:
            print(f"wrote {args.output}")
        if not args.json:
            for pose in result.poses:
                print(f"{pose.label}: numeric_rank={pose.finite_difference.numeric_rank} effective_rank={pose.finite_difference.effective_rank} singular_values={pose.finite_difference.singular_values} row_norms={pose.finite_difference.row_norms}")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"diagnostic failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
