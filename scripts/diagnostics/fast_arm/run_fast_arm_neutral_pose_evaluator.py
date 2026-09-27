from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.neutral_initial_pose import (
    evaluate_fast_arm_neutral_initial_pose_candidates,
    format_neutral_pose_ranking,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate deterministic fast_arm neutral startup-pose candidates."
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--limit", type=int, default=10, help="human ranking row limit")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="optional JSON output path; no repository file is written by default",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    evaluation = evaluate_fast_arm_neutral_initial_pose_candidates()
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(evaluation.to_json() + "\n", encoding="utf-8", newline="\n")
    print(evaluation.to_json() if args.json else format_neutral_pose_ranking(evaluation, limit=args.limit))
    return 0 if evaluation.selected_candidate_id is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
