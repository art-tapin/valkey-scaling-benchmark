"""Monitoring utilities for benchmark metrics capture."""

import csv
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LatencyTracker:
    """Tracks latency samples and computes percentiles."""

    _samples: list[float] = field(default_factory=list)

    def record(self, latency_ms: float) -> None:
        """Record a latency sample in milliseconds."""
        self._samples.append(latency_ms)

    def get_stats(self) -> dict[str, float]:
        """Calculate and return latency statistics."""
        if not self._samples:
            return {
                "count": 0,
                "p50": 0.0,
                "p99": 0.0,
                "min": 0.0,
                "max": 0.0,
                "mean": 0.0,
            }

        sorted_samples = sorted(self._samples)
        count = len(sorted_samples)

        def percentile_value(p: float) -> float:
            """Calculate the value at a given percentile."""
            # Use nearest-rank method: index = ceil(p/100 * n) - 1
            index = max(0, int((p / 100.0) * count - 0.5))
            return sorted_samples[index]

        return {
            "count": count,
            "p50": percentile_value(50),
            "p99": percentile_value(99),
            "min": min(sorted_samples),
            "max": max(sorted_samples),
            "mean": statistics.mean(sorted_samples),
        }

    def reset(self) -> None:
        """Clear all recorded samples."""
        self._samples.clear()


@dataclass
class MetricsSample:
    """A single metrics sample."""

    timestamp: float
    cluster: str
    phase: str
    state: str
    latency_p50_ms: float
    latency_p99_ms: float
    ops_per_sec: int
    memory_used_mb: int
    errors: int


class MetricsWriter:
    """Writes metrics samples to CSV file."""

    FIELDNAMES = [
        "timestamp", "cluster", "phase", "state",
        "latency_p50_ms", "latency_p99_ms", "ops_per_sec",
        "memory_used_mb", "errors"
    ]

    def __init__(self, output_path: Path):
        """Initialize writer and create CSV with headers."""
        self._output_path = output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(output_path, "w", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=self.FIELDNAMES)
        self._writer.writeheader()

    def write(self, sample: MetricsSample) -> None:
        """Write a metrics sample to the CSV."""
        self._writer.writerow({
            "timestamp": sample.timestamp,
            "cluster": sample.cluster,
            "phase": sample.phase,
            "state": sample.state,
            "latency_p50_ms": sample.latency_p50_ms,
            "latency_p99_ms": sample.latency_p99_ms,
            "ops_per_sec": sample.ops_per_sec,
            "memory_used_mb": sample.memory_used_mb,
            "errors": sample.errors,
        })
        self._file.flush()

    def close(self) -> None:
        """Close the CSV file."""
        self._file.close()


@dataclass
class StateTransition:
    """A state transition event."""
    timestamp: float
    cluster: str
    from_state: str
    to_state: str
    from_plan: str
    to_plan: str


class StateTransitionTracker:
    """Tracks service state transitions for diagnostics."""

    def __init__(self):
        self._transitions: list[StateTransition] = []
        self._last_state: dict[str, tuple[str, str]] = {}  # cluster -> (state, plan)

    def check_transition(
        self,
        timestamp: float,
        cluster: str,
        state: str,
        plan: str,
    ) -> StateTransition | None:
        """Check for state/plan change and record if found."""
        last = self._last_state.get(cluster)

        if last is None:
            self._last_state[cluster] = (state, plan)
            return None

        last_state, last_plan = last
        if state != last_state or plan != last_plan:
            transition = StateTransition(
                timestamp=timestamp,
                cluster=cluster,
                from_state=last_state,
                to_state=state,
                from_plan=last_plan,
                to_plan=plan,
            )
            self._transitions.append(transition)
            self._last_state[cluster] = (state, plan)
            return transition

        return None

    def get_transitions(self) -> list[StateTransition]:
        """Get all recorded transitions."""
        return self._transitions.copy()
