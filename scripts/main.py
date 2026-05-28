#!/usr/bin/env python3
"""Main orchestrator for Valkey benchmark."""

import argparse
import asyncio
import redis
import signal
import sys
import time
from pathlib import Path
from typing import Optional

from scripts.config import load_config
from scripts.display import BenchmarkDisplay, ClusterStatus
from scripts.monitor import LatencyTracker, MetricsSample, MetricsWriter, StateTransitionTracker
from scripts.populate import populate_cluster
from scripts.load_generator import QueueWorkload, run_memtier
from scripts.upgrade import AivenClient, wait_for_running
from scripts.report import load_metrics, generate_report


class BenchmarkRunner:
    """Orchestrates the full benchmark flow."""

    def __init__(self, config_path: Path):
        self.config = load_config(config_path)
        self.results_dir = Path(self.config["results_dir"])
        self._shutdown = False

    def _get_connection_info(self, cluster_key: str) -> dict:
        """Get connection info for a cluster from config."""
        cluster = self.config["clusters"][cluster_key]
        return {
            "host": cluster["host"],
            "port": cluster["port"],
            "password": cluster["password"],
        }

    async def run_phase_populate(self, flush: bool = True) -> None:
        """Phase 1: Populate both clusters with test data (in parallel)."""
        print("\n=== Phase 1: Populating clusters (parallel) ===")
        if not flush:
            print("(--no-flush: keeping existing data, overwriting keys)")

        async def populate_one(cluster_key: str) -> dict:
            """Populate a single cluster."""
            cluster = self.config["clusters"][cluster_key]
            print(f"Populating {cluster_key} cluster ({cluster['data_size_gb']} GB target)...")

            conn = self._get_connection_info(cluster_key)

            # Run synchronous populate_cluster in a thread to avoid blocking
            result = await asyncio.to_thread(
                populate_cluster,
                host=conn["host"],
                port=conn["port"],
                password=conn["password"],
                num_keys=cluster["num_keys"],
                value_size=cluster["value_size_bytes"],
                list_ratio=0.7,
                flush_first=flush,
            )

            print(f"  [{cluster_key}] Created {result['keys_created']} keys, {result['memory_used_mb']} MB used")
            return result

        # Run both populations in parallel
        try:
            results = await asyncio.gather(
                populate_one("light"),
                populate_one("heavy"),
            )
        except Exception as e:
            print(f"Error during population: {e}")
            raise

    def _run_workload_sync(self, workload: "QueueWorkload", duration_sec: float) -> None:
        """Run workload synchronously (for use with asyncio.to_thread)."""
        import time
        import random
        start = time.time()
        while workload._running and (time.time() - start) < duration_sec:
            try:
                op_start = time.perf_counter()
                if random.random() < workload._write_ratio:
                    key = workload._random_key()
                    value = workload._random_value()
                    if random.random() < 0.5:
                        workload._client.lpush(key, value)
                    else:
                        workload._client.rpush(key, value)
                else:
                    key = workload._random_key()
                    if random.random() < 0.5:
                        workload._client.lpop(key)
                    else:
                        workload._client.lrange(key, 0, 10)
                latency_ms = (time.perf_counter() - op_start) * 1000
                workload._tracker.record(latency_ms)
                workload._ops_count += 1
            except redis.RedisError:
                workload._error_count += 1
                time.sleep(0.1)

    async def run_cluster_benchmark(
        self,
        cluster_key: str,
        writer: MetricsWriter,
        upgrade_event: asyncio.Event,
        display: Optional[BenchmarkDisplay] = None,
        target_plan: Optional[str] = None,
    ) -> None:
        """Run benchmark for a single cluster."""
        cluster = self.config["clusters"][cluster_key]
        conn = self._get_connection_info(cluster_key)

        write_ratio = self.config["load"].get("ratio_write", 70) / 100.0
        workload = QueueWorkload(
            host=conn["host"],
            port=conn["port"],
            password=conn["password"],
            num_keys=cluster["num_keys"],
            write_ratio=write_ratio,
        )

        memtier_output = self.results_dir / cluster_key / "memtier_output.txt"
        memtier_output.parent.mkdir(parents=True, exist_ok=True)

        memtier_proc = run_memtier(
            host=conn["host"],
            port=conn["port"],
            password=conn["password"],
            output_path=memtier_output,
            clients=self.config["load"]["clients"],
            threads=self.config["load"]["threads"],
            duration_sec=self.config["baseline_duration_sec"] + self.config["upgrade_timeout_sec"],
        )

        aiven = AivenClient()
        state_tracker = StateTransitionTracker()
        project = self.config["project"]
        service_name = cluster["service_name"]

        # Start workload as background task
        workload._running = True
        workload_duration = self.config["baseline_duration_sec"] + self.config["upgrade_timeout_sec"] + 120
        workload_task = asyncio.create_task(
            asyncio.to_thread(self._run_workload_sync, workload, workload_duration)
        )

        phase = "baseline"
        start_time = time.time()
        baseline_end = start_time + self.config["baseline_duration_sec"]

        try:
            while not self._shutdown:
                state = await aiven.get_service(project, service_name)
                transition = state_tracker.check_transition(
                    time.time(), cluster_key, state.state, state.plan
                )
                if transition:
                    print(f"  [{cluster_key}] State change: {transition.from_state}({transition.from_plan}) -> {transition.to_state}({transition.to_plan})")

                tracker = workload.get_tracker()
                stats = tracker.get_stats()
                tracker.reset()

                current_time = time.time()
                if phase == "baseline" and current_time >= baseline_end:
                    upgrade_event.set()
                    phase = "upgrade"
                    print(f"  [{cluster_key}] Starting upgrade phase")

                    plan_to_use = target_plan or self.config["target_plan"]

                    # Get current plan before upgrade
                    pre_upgrade_plan = state.plan

                    await aiven.upgrade_plan(
                        project, service_name, plan_to_use
                    )

                    print(f"  [{cluster_key}] Upgrade API called: {pre_upgrade_plan} -> {plan_to_use}")

                # Re-fetch state after potential upgrade to get current plan
                if phase == "upgrade":
                    state = await aiven.get_service(project, service_name)

                plan_to_use = target_plan or self.config["target_plan"]
                if phase == "upgrade" and state.is_ready and state.plan == plan_to_use:
                    phase = "post"
                    print(f"  [{cluster_key}] Upgrade complete, running post-upgrade validation")

                sample = MetricsSample(
                    timestamp=current_time,
                    cluster=cluster_key,
                    phase=phase,
                    state=state.state,
                    latency_p50_ms=stats["p50"],
                    latency_p99_ms=stats["p99"],
                    ops_per_sec=int(stats["count"] / (self.config["sample_interval_ms"] / 1000)),
                    memory_used_mb=0,
                    errors=0,
                )
                writer.write(sample)

                # Update display
                if display:
                    display.update_cluster(
                        cluster_key,
                        ClusterStatus(
                            name=cluster_key,
                            phase=phase,
                            state=state.state,
                            elapsed_sec=current_time - start_time,
                            p50_ms=stats["p50"],
                            p99_ms=stats["p99"],
                            ops_per_sec=int(stats["count"] / (self.config["sample_interval_ms"] / 1000)),
                            errors=workload._error_count,
                        )
                    )

                if phase == "post":
                    await asyncio.sleep(60)
                    break

                await asyncio.sleep(self.config["sample_interval_ms"] / 1000)

        finally:
            workload.stop()
            workload_task.cancel()
            try:
                await workload_task
            except asyncio.CancelledError:
                pass
            workload.close()
            memtier_proc.terminate()
            memtier_proc.wait()

    async def run_benchmark_direction(
        self,
        direction: str,
        from_plan: str,
        to_plan: str,
    ) -> dict:
        """Run benchmark for a specific scaling direction."""
        direction_dir = self.results_dir / direction

        light_writer = MetricsWriter(direction_dir / "light" / "metrics.csv")
        heavy_writer = MetricsWriter(direction_dir / "heavy" / "metrics.csv")

        upgrade_event = asyncio.Event()

        display = BenchmarkDisplay()
        display.start()

        try:
            await asyncio.gather(
                self.run_cluster_benchmark("light", light_writer, upgrade_event, display, to_plan),
                self.run_cluster_benchmark("heavy", heavy_writer, upgrade_event, display, to_plan),
            )
        finally:
            display.stop()
            light_writer.close()
            heavy_writer.close()

        return {
            "light_metrics": load_metrics(direction_dir / "light" / "metrics.csv"),
            "heavy_metrics": load_metrics(direction_dir / "heavy" / "metrics.csv"),
            "from_plan": from_plan,
            "to_plan": to_plan,
        }

    def _print_config_summary(self, mode: str, skip_populate: bool) -> None:
        """Print configuration summary for user review."""
        mode_desc = {
            "up": "Scale UP only (source → target)",
            "down": "Scale DOWN only (target → source)",
            "both": "Bidirectional (UP then DOWN)",
        }
        write_ratio = self.config["load"].get("ratio_write", 70)
        read_ratio = self.config["load"].get("ratio_read", 30)

        print("\n" + "=" * 60)
        print("  BENCHMARK CONFIGURATION")
        print("=" * 60)
        print(f"""
  Mode:           {mode_desc.get(mode, mode)}
  Project:        {self.config['project']}

  Plans:
    Source:       {self.config['source_plan']}
    Target:       {self.config['target_plan']}

  Clusters:
    Light:        {self.config['clusters']['light']['service_name']}
                  {self.config['clusters']['light']['data_size_gb']} GB ({self.config['clusters']['light']['num_keys']:,} keys)
    Heavy:        {self.config['clusters']['heavy']['service_name']}
                  {self.config['clusters']['heavy']['data_size_gb']} GB ({self.config['clusters']['heavy']['num_keys']:,} keys)

  Workload:
    Write/Read:   {write_ratio}% / {read_ratio}%
    Operations:   LPUSH, RPUSH, LPOP, LRANGE (Redis LIST)

  Timing:
    Baseline:     {self.config['baseline_duration_sec']} sec
    Timeout:      {self.config['upgrade_timeout_sec']} sec

  Options:
    Populate:     {'Skip (use existing data)' if skip_populate else 'Yes (create test data)'}
    Results:      {self.results_dir}
""")
        print("=" * 60)

    async def run(self, skip_populate: bool = False, flush: bool = True, mode: str = "up") -> None:
        """Run the full benchmark."""
        print("=" * 60)
        print("  Aiven for Valkey - Scaling Benchmark")
        print("=" * 60)

        # Show config summary
        self._print_config_summary(mode, skip_populate)

        # Ask for confirmation
        try:
            response = input("\nPress ENTER to start, or 'q' to abort: ").strip().lower()
            if response in ('q', 'quit', 'exit', 'abort'):
                print("Benchmark aborted.")
                return
        except EOFError:
            # Non-interactive mode, continue without confirmation
            pass

        def handle_shutdown(sig, frame):
            print("\nShutdown requested, finishing current samples...")
            self._shutdown = True

        signal.signal(signal.SIGINT, handle_shutdown)
        signal.signal(signal.SIGTERM, handle_shutdown)

        if skip_populate:
            print("\n=== Skipping Phase 1 (using existing data) ===")
        else:
            await self.run_phase_populate(flush=flush)

        directions = []
        if mode in ("up", "both"):
            directions.append(("upgrade", self.config["source_plan"], self.config["target_plan"]))
        if mode in ("down", "both"):
            directions.append(("downgrade", self.config["target_plan"], self.config["source_plan"]))

        all_results = {}

        for direction, from_plan, to_plan in directions:
            print(f"\n=== Running {direction} benchmark: {from_plan} → {to_plan} ===\n")

            results = await self.run_benchmark_direction(direction, from_plan, to_plan)
            all_results[direction] = results

            if direction == "upgrade" and mode == "both":
                print("\n=== Waiting 60s before downgrade benchmark ===")
                await asyncio.sleep(60)

        print("\n=== Phase 5: Generating report ===")
        self.generate_combined_report(all_results, mode)
        print("\nBenchmark complete!")

    def generate_combined_report(self, all_results: dict, mode: str) -> None:
        """Generate combined report for all benchmark directions."""
        from scripts.report import generate_bidirectional_report

        generate_bidirectional_report(
            all_results,
            self.config,
            self.results_dir,
            mode,
        )


def main():
    parser = argparse.ArgumentParser(description="Run Valkey plan upgrade benchmark")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).parent.parent / "config.yaml",
        help="Path to config.yaml",
    )
    parser.add_argument(
        "--skip-populate",
        action="store_true",
        help="Skip populate phase (use existing data)",
    )
    parser.add_argument(
        "--no-flush",
        action="store_true",
        help="Don't flush existing data before populating",
    )
    parser.add_argument(
        "--mode",
        choices=["up", "down", "both"],
        default="up",
        help="Scaling direction: up (upgrade), down (downgrade), or both (default: up)",
    )
    args = parser.parse_args()

    runner = BenchmarkRunner(args.config)
    asyncio.run(runner.run(skip_populate=args.skip_populate, flush=not args.no_flush, mode=args.mode))


if __name__ == "__main__":
    main()
