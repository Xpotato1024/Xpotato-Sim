from __future__ import annotations

import asyncio

import pytest

from xpotato_sim.runtime.execution.live_timing import AbsoluteDeadlinePacer, LiveRuntimeTimingMetrics


class FakeClock:
    def __init__(self) -> None:
        self.now_s = 0.0
        self.sleep_calls: list[float] = []

    def monotonic(self) -> float:
        return self.now_s

    def advance(self, duration_s: float) -> None:
        self.now_s += duration_s

    async def sleep(self, duration_s: float) -> None:
        self.sleep_calls.append(duration_s)
        self.advance(duration_s)


def test_absolute_deadline_pacing_subtracts_compute_time_from_sleep() -> None:
    clock = FakeClock()
    metrics = LiveRuntimeTimingMetrics(clock=clock.monotonic)
    pacer = AbsoluteDeadlinePacer(
        0.01,
        clock=clock.monotonic,
        sleep=clock.sleep,
        metrics=metrics,
    )
    pacer.start()
    clock.advance(0.004)

    asyncio.run(pacer.pace())

    assert clock.sleep_calls == pytest.approx([0.006])
    assert clock.now_s == pytest.approx(0.01)
    assert metrics.deadline_miss_count == 0


def test_deadline_miss_does_not_sleep_or_run_an_unlimited_catch_up_loop() -> None:
    clock = FakeClock()
    metrics = LiveRuntimeTimingMetrics(clock=clock.monotonic)
    pacer = AbsoluteDeadlinePacer(
        0.01,
        clock=clock.monotonic,
        sleep=clock.sleep,
        metrics=metrics,
    )
    pacer.start()
    clock.advance(0.025)

    asyncio.run(pacer.pace())
    assert clock.sleep_calls == []
    assert metrics.deadline_miss_count == 1
    assert metrics.deadline_lag_max_s == pytest.approx(0.015)

    clock.advance(0.003)
    asyncio.run(pacer.pace())
    assert clock.sleep_calls == pytest.approx([0.007])
    assert clock.now_s == pytest.approx(0.035)


def test_sleep_overshoot_counts_as_deadline_miss_and_records_actual_lag() -> None:
    clock = FakeClock()
    metrics = LiveRuntimeTimingMetrics(clock=clock.monotonic)

    async def overshooting_sleep(duration_s: float) -> None:
        clock.sleep_calls.append(duration_s)
        clock.advance(duration_s + 0.002)

    pacer = AbsoluteDeadlinePacer(
        0.01,
        clock=clock.monotonic,
        sleep=overshooting_sleep,
        metrics=metrics,
    )
    pacer.start()

    asyncio.run(pacer.pace())

    assert metrics.deadline_miss_count == 1
    assert metrics.deadline_lag_max_s == pytest.approx(0.002)

    asyncio.run(pacer.pace())
    assert clock.sleep_calls == pytest.approx([0.01, 0.008])


def test_exact_deadline_and_tolerance_noise_do_not_count_as_misses() -> None:
    exact_clock = FakeClock()
    exact_metrics = LiveRuntimeTimingMetrics(clock=exact_clock.monotonic)
    exact_pacer = AbsoluteDeadlinePacer(
        0.01,
        clock=exact_clock.monotonic,
        sleep=exact_clock.sleep,
        metrics=exact_metrics,
    )
    exact_pacer.start()
    asyncio.run(exact_pacer.pace())
    assert exact_metrics.deadline_miss_count == 0
    assert exact_metrics.deadline_lag_max_s == 0.0

    tolerance_clock = FakeClock()
    tolerance_metrics = LiveRuntimeTimingMetrics(clock=tolerance_clock.monotonic)

    async def noise_sleep(duration_s: float) -> None:
        tolerance_clock.advance(duration_s + 0.5e-6)

    tolerance_pacer = AbsoluteDeadlinePacer(
        0.01,
        clock=tolerance_clock.monotonic,
        sleep=noise_sleep,
        metrics=tolerance_metrics,
    )
    tolerance_pacer.start()
    asyncio.run(tolerance_pacer.pace())
    assert tolerance_metrics.deadline_miss_count == 0
    assert tolerance_metrics.deadline_lag_max_s == pytest.approx(0.5e-6)


def test_live_runtime_timing_summary_is_bounded_aggregate() -> None:
    clock = FakeClock()
    metrics = LiveRuntimeTimingMetrics(clock=clock.monotonic)
    metrics.start()
    metrics.record_frame(
        compute_time_s=0.002,
        simulation_step_time_s=0.003,
        annotation_time_s=0.001,
        publish_wait_or_enqueue_time_s=0.0005,
    )
    clock.advance(1.0 / 60.0)

    summary = metrics.summary(dt_s=1.0 / 60.0)

    assert summary.completed_frame_count == 1
    assert summary.simulation_time_s == pytest.approx(1.0 / 60.0)
    assert summary.wall_elapsed_s == pytest.approx(1.0 / 60.0)
    assert summary.realtime_factor == pytest.approx(1.0)
    assert summary.compute_time_s == pytest.approx(0.002)
    assert summary.simulation_step_time_s == pytest.approx(0.003)
    assert summary.annotation_time_s == pytest.approx(0.001)
    assert summary.publish_wait_or_enqueue_time_s == pytest.approx(0.0005)


def test_pacing_cancellation_propagates_without_retry_or_background_task() -> None:
    clock = FakeClock()
    sleep_calls = 0

    async def cancel_sleep(_duration_s: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        raise asyncio.CancelledError

    pacer = AbsoluteDeadlinePacer(0.01, clock=clock.monotonic, sleep=cancel_sleep)
    pacer.start()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(pacer.pace())

    assert sleep_calls == 1
