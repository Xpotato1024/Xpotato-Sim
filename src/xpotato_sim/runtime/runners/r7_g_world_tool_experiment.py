"""R7-G software-only world/tool experiment runnerのthin module entry point。"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from xpotato_sim.runtime.evaluation.manifest import SoftwareExecutionIdentity
from xpotato_sim.runtime.experiment.world_tool_runner import (
    ExperimentRunnerError,
    run_r7_g_world_tool_experiment,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m xpotato_sim.runtime.runners.r7_g_world_tool_experiment"
    )
    parser.add_argument(
        "--manifest-software-revision",
        required=True,
        help="manifestへ固定したgit-sha1:<40 hex>などのstable identity",
    )
    parser.add_argument(
        "--execution-software-revision",
        required=True,
        help="startup側が独立に取得したactual execution revision identity",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """artifactを生成せず、2 conditionのTask-owned summaryをstdoutへ出す。"""

    args = _parser().parse_args(argv)
    try:
        result = run_r7_g_world_tool_experiment(
            manifest_software_revision_identity=args.manifest_software_revision,
            execution_identity=SoftwareExecutionIdentity(
                repository_identity="Xpotato1024/Xpotato-Sim",
                software_revision_identity=args.execution_software_revision,
            ),
        )
    except (ExperimentRunnerError, TypeError, ValueError) as exc:
        print(f"world/tool experiment runner: error: {exc}", file=sys.stderr)
        return 1
    for condition in (result.world, result.tool):
        print(
            f"{condition.condition_id}: "
            f"classification={condition.classification.value} "
            f"steps={condition.step_count} "
            f"elapsed_time_s={condition.final_elapsed_time_s:.6f} "
            f"stop={condition.stop_reason.value}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
