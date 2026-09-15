"""Wall-time + peak-RSS measurement for benchmarks (#79 F5).

`measure()` runs a callable on the main thread while a daemon thread samples the
process RSS, so the reported peak reflects the high-water mark *during* the run
(what F5's "max RSS during resolve" asks for) rather than a before/after delta.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

import psutil

T = TypeVar("T")

_MB = 1024 * 1024


@dataclass
class Measurement:
    wall_s: float
    peak_rss_mb: float
    rss_growth_mb: float  # peak RSS minus the baseline RSS at entry


def measure(fn: Callable[[], T], *, sample_interval_s: float = 0.01) -> tuple[T, Measurement]:
    """Run ``fn`` while sampling RSS; return ``(result, Measurement)``."""
    proc = psutil.Process()
    baseline = proc.memory_info().rss
    peak = baseline
    stop = threading.Event()

    def _sampler() -> None:
        nonlocal peak
        while not stop.is_set():
            try:
                rss = proc.memory_info().rss
            except psutil.Error:
                break
            if rss > peak:
                peak = rss
            stop.wait(sample_interval_s)

    sampler = threading.Thread(target=_sampler, daemon=True)
    sampler.start()
    t0 = time.perf_counter()
    try:
        result = fn()
    finally:
        wall = time.perf_counter() - t0
        stop.set()
        sampler.join(timeout=1.0)

    return result, Measurement(
        wall_s=wall,
        peak_rss_mb=peak / _MB,
        rss_growth_mb=(peak - baseline) / _MB,
    )
