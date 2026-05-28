"""Load generation for benchmark using memtier and custom queue workload."""

import asyncio
import random
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import redis

from scripts.monitor import LatencyTracker


@dataclass
class LoadResult:
    """Result from a load generation run."""

    ops_completed: int
    errors: int
    duration_sec: float


class QueueWorkload:
    """Custom workload simulating job queue operations (LIST commands)."""

    def __init__(
        self,
        host: str,
        port: int,
        password: str,
        ssl: bool = True,
        num_keys: int = 1000,
        write_ratio: float = 0.7,
    ):
        """Initialize queue workload generator.

        Args:
            write_ratio: Fraction of operations that are writes (0.0-1.0).
                         Default 0.7 = 70% writes, 30% reads.
        """
        self._client = redis.Redis(
            host=host,
            port=port,
            password=password,
            ssl=ssl,
            decode_responses=False,
        )
        self._num_keys = num_keys
        self._write_ratio = write_ratio
        self._running = False
        self._tracker = LatencyTracker()
        self._ops_count = 0
        self._error_count = 0

    def _random_key(self) -> str:
        """Get a random queue key."""
        return f"benchmark:queue:{random.randint(0, self._num_keys - 1)}"

    def _random_value(self) -> bytes:
        """Generate a random job payload (1-10 KB)."""
        size = random.randint(1000, 10000)
        return b"x" * size

    async def run_ops(self, duration_sec: float) -> LoadResult:
        """Run queue operations for specified duration.

        Uses configured write_ratio for operation mix.
        Writes: LPUSH/RPUSH, Reads: LPOP/LRANGE
        """
        self._running = True
        self._ops_count = 0
        self._error_count = 0
        start_time = time.time()

        while self._running and (time.time() - start_time) < duration_sec:
            try:
                op_start = time.perf_counter()

                if random.random() < self._write_ratio:
                    # Write operation
                    key = self._random_key()
                    value = self._random_value()
                    if random.random() < 0.5:
                        self._client.lpush(key, value)
                    else:
                        self._client.rpush(key, value)
                else:
                    # Read operation
                    key = self._random_key()
                    if random.random() < 0.5:
                        self._client.lpop(key)
                    else:
                        self._client.lrange(key, 0, 10)

                latency_ms = (time.perf_counter() - op_start) * 1000
                self._tracker.record(latency_ms)
                self._ops_count += 1

            except redis.RedisError as e:
                self._error_count += 1
                await asyncio.sleep(0.1)

            if self._ops_count % 100 == 0:
                await asyncio.sleep(0)

        return LoadResult(
            ops_completed=self._ops_count,
            errors=self._error_count,
            duration_sec=time.time() - start_time,
        )

    def stop(self) -> None:
        """Signal the workload to stop."""
        self._running = False

    def get_tracker(self) -> LatencyTracker:
        """Get the latency tracker."""
        return self._tracker

    def close(self) -> None:
        """Close Redis connection."""
        self._client.close()


def run_memtier(
    host: str,
    port: int,
    password: str,
    output_path: Path,
    clients: int = 50,
    threads: int = 4,
    duration_sec: int = 300,
    ratio: str = "1:1",
    tls: bool = True,
) -> subprocess.Popen:
    """Start memtier_benchmark as a background process."""
    cmd = [
        "memtier_benchmark",
        "-s", host,
        "-p", str(port),
        "-a", password,
        "--clients", str(clients),
        "--threads", str(threads),
        "--test-time", str(duration_sec),
        "--ratio", ratio,
        "--data-size-range", "1000-10000",
        "--key-pattern", "R:R",
        "--hide-histogram",
        "--json-out-file", str(output_path.with_suffix(".json")),
    ]

    if tls:
        cmd.extend(["--tls", "--tls-skip-verify"])

    output_file = open(output_path, "w")

    return subprocess.Popen(
        cmd,
        stdout=output_file,
        stderr=subprocess.STDOUT,
    )
