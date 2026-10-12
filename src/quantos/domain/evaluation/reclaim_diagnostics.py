"""Bounded operational observations for QOS06, separate from Alpha decisions."""
from collections import Counter
from decimal import Decimal


class ReclaimDiagnostics:
    def __init__(self):
        self.range_bins = Counter()
        self.queue_bins = Counter()
        self.feed_bins = Counter()
        self.range_valid = 0
        self.range_threshold = 0
        self.maximum_range_bps = Decimal(0)
        self.maximum_queue_delay_ms = Decimal(0)
        self.maximum_feed_latency_ms = Decimal(0)
        self.invalid_queue_clock = 0
        self.valid_windows = []
        self.current_start = self.current_end = None

    @staticmethod
    def bucket(value, bounds):
        for bound in bounds:
            if value < bound:
                return f"below_{bound}"
        return f"at_least_{bounds[-1]}"

    def close_window(self):
        if self.current_start is not None:
            self.valid_windows.append((self.current_start, self.current_end))
            self.current_start = self.current_end = None

    def observe_feature(self, now_ns, range_bps):
        if self.current_end is not None and now_ns - self.current_end > 1_500_000_000:
            self.close_window()
        if self.current_start is None:
            self.current_start = now_ns
        self.current_end = now_ns
        if range_bps is None:
            return
        self.range_valid += 1
        self.range_threshold += int(range_bps >= 60)
        self.maximum_range_bps = max(self.maximum_range_bps, range_bps)
        self.range_bins[self.bucket(range_bps, (10, 20, 40, 60, 80, 100, 200))] += 1

    def observe_timing(self, queue_ms, feed_ms):
        if queue_ms < 0:
            self.invalid_queue_clock += 1
        else:
            self.maximum_queue_delay_ms = max(self.maximum_queue_delay_ms, queue_ms)
            self.queue_bins[self.bucket(queue_ms, (10, 50, 100, 500, 1500))] += 1
        self.maximum_feed_latency_ms = max(self.maximum_feed_latency_ms, feed_ms)
        self.feed_bins[self.bucket(feed_ms, (10, 50, 100, 500, 1500))] += 1

    def report(self):
        windows = self.valid_windows + ([(self.current_start, self.current_end)] if self.current_start is not None else [])
        durations = [(end - start) / 1_000_000_000 for start, end in windows]
        duration_bins = Counter(self.bucket(value, (5, 30, 60, 300, 600, 1800)) for value in durations)
        return {"valid_window_count": len(windows), "valid_window_duration_bins_seconds": dict(duration_bins),
            "maximum_valid_window_seconds": max(durations, default=0),
            "usable_coverage_seconds": sum(durations), "range_valid_evaluations": self.range_valid,
            "range_at_least_60bps": self.range_threshold, "maximum_range_bps": self.maximum_range_bps,
            "range_bins_bps": dict(self.range_bins), "queue_delay_bins_ms": dict(self.queue_bins),
            "feed_latency_bins_ms": dict(self.feed_bins), "maximum_queue_delay_ms": self.maximum_queue_delay_ms,
            "maximum_feed_latency_ms": self.maximum_feed_latency_ms,
            "invalid_queue_clock": self.invalid_queue_clock}
