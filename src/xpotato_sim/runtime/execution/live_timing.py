"""Runtime execution timing and pacing."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from time import monotonic

MonotonicClock = Callable[[], float]
AsyncSleep = Callable[[float], Awaitable[None]]
DEADLINE_MISS_TOLERANCE_S = 1e-6


@dataclass(frozen=True, slots=True)
class LiveRuntimeTimingSummary:
    completed_frame_count: int
    published_frame_count: int
    simulation_time_s: float
    wall_elapsed_s: float
    realtime_factor: float
    compute_time_s: float
    simulation_step_time_s: float
    annotation_time_s: float
    publish_wait_or_enqueue_time_s: float
    pacing_sleep_time_s: float
    deadline_lag_max_s: float
    deadline_miss_count: int

    def to_dict(self) -> dict[str, int | float]:
        return asdict(self)


class LiveRuntimeTimingMetrics:
    """Bounded aggregate timing for a live runtime loop."""

    def __init__(self, *, clock: MonotonicClock = monotonic) -> None:
        self.clock = clock
        self.started_at_s: float | None = None
        self.completed_frame_count = 0
        self.published_frame_count = 0
        self.compute_time_s = 0.0
        self.simulation_step_time_s = 0.0
        self.annotation_time_s = 0.0
        self.publish_wait_or_enqueue_time_s = 0.0
        self.pacing_sleep_time_s = 0.0
        self.deadline_lag_max_s = 0.0
        self.deadline_miss_count = 0

    def start(self) -> float:
        if self.started_at_s is None:
            self.started_at_s = self.clock()
        return self.started_at_s

    def record_frame(
        self,
        *,
        compute_time_s: float,
        simulation_step_time_s: float,
        annotation_time_s: float,
        publish_wait_or_enqueue_time_s: float,
    ) -> None:
        self.completed_frame_count += 1
        self.published_frame_count += 1
        self.compute_time_s += max(0.0, compute_time_s)
        self.simulation_step_time_s += max(0.0, simulation_step_time_s)
        self.annotation_time_s += max(0.0, annotation_time_s)
        self.publish_wait_or_enqueue_time_s += max(0.0, publish_wait_or_enqueue_time_s)

    def record_pacing(self, *, sleep_time_s: float, deadline_lag_s: float, missed: bool) -> None:
        self.pacing_sleep_time_s += max(0.0, sleep_time_s)
        self.deadline_lag_max_s = max(self.deadline_lag_max_s, max(0.0, deadline_lag_s))
        if missed:
            self.deadline_miss_count += 1

    def summary(self, *, dt_s: float) -> LiveRuntimeTimingSummary:
        started_at_s = self.start()
        wall_elapsed_s = max(0.0, self.clock() - started_at_s)
        simulation_time_s = self.completed_frame_count * dt_s
        realtime_factor = simulation_time_s / wall_elapsed_s if wall_elapsed_s > 0.0 else 0.0
        return LiveRuntimeTimingSummary(
            completed_frame_count=self.completed_frame_count,
            published_frame_count=self.published_frame_count,
            simulation_time_s=simulation_time_s,
            wall_elapsed_s=wall_elapsed_s,
            realtime_factor=realtime_factor,
            compute_time_s=self.compute_time_s,
            simulation_step_time_s=self.simulation_step_time_s,
            annotation_time_s=self.annotation_time_s,
            publish_wait_or_enqueue_time_s=self.publish_wait_or_enqueue_time_s,
            pacing_sleep_time_s=self.pacing_sleep_time_s,
            deadline_lag_max_s=self.deadline_lag_max_s,
            deadline_miss_count=self.deadline_miss_count,
        )


class AbsoluteDeadlinePacer:
    """Pace one completed simulation step per monotonic wall-clock period."""

    def __init__(
        self,
        period_s: float,
        *,
        clock: MonotonicClock = monotonic,
        sleep: AsyncSleep = asyncio.sleep,
        metrics: LiveRuntimeTimingMetrics | None = None,
        deadline_miss_tolerance_s: float = DEADLINE_MISS_TOLERANCE_S,
    ) -> None:
        if period_s <= 0.0:
            raise ValueError("period_s must be positive")
        self.period_s = period_s
        self._clock = clock
        self._sleep = sleep
        self._metrics = metrics
        if deadline_miss_tolerance_s < 0.0:
            raise ValueError("deadline_miss_tolerance_s must be non-negative")
        self._deadline_miss_tolerance_s = deadline_miss_tolerance_s
        self._next_deadline_s: float | None = None

    def start(self) -> None:
        started_at_s = self._metrics.start() if self._metrics is not None else self._clock()
        self._next_deadline_s = started_at_s + self.period_s

    async def pace(self) -> None:
        if self._next_deadline_s is None:
            self.start()
        assert self._next_deadline_s is not None

        before_sleep_s = self._clock()
        remaining_s = self._next_deadline_s - before_sleep_s
        overran_before_sleep = remaining_s <= 0.0
        if remaining_s > 0.0:
            await self._sleep(remaining_s)

        after_sleep_s = self._clock()
        sleep_time_s = max(0.0, after_sleep_s - before_sleep_s)
        deadline_lag_s = max(0.0, after_sleep_s - self._next_deadline_s)
        missed = deadline_lag_s > self._deadline_miss_tolerance_s
        if self._metrics is not None:
            self._metrics.record_pacing(
                sleep_time_s=sleep_time_s,
                deadline_lag_s=deadline_lag_s,
                missed=missed,
            )

        # Do not run an unlimited catch-up loop after an overrun. One missed
        # deadline starts a fresh absolute period from the observed wall time.
        if overran_before_sleep:
            self._next_deadline_s = after_sleep_s + self.period_s
        else:
            self._next_deadline_s += self.period_s


__all__ = [
    "AbsoluteDeadlinePacer",
    "DEADLINE_MISS_TOLERANCE_S",
    "LiveRuntimeTimingMetrics",
    "LiveRuntimeTimingSummary",
]
