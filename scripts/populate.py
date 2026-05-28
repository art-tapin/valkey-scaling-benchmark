"""Data population for benchmark clusters."""

import random
import string
import time
from typing import Any

import redis
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    TimeRemainingColumn,
    MofNCompleteColumn,
)

console = Console()


def generate_key(key_type: str, index: int) -> str:
    """Generate a benchmark key name."""
    return f"benchmark:{key_type}:{index}"


def generate_value(size_bytes: int) -> bytes:
    """Generate random value of specified size."""
    return "".join(
        random.choices(string.ascii_letters + string.digits, k=size_bytes)
    ).encode()


def populate_cluster(
    host: str,
    port: int,
    password: str,
    num_keys: int,
    value_size: int,
    list_ratio: float = 0.7,
    ssl: bool = True,
    show_progress: bool = True,
    flush_first: bool = False,
) -> dict[str, Any]:
    """Populate a Valkey cluster with test data.

    Args:
        host: Redis host
        port: Redis port
        password: Redis password
        num_keys: Total number of keys to create
        value_size: Size of each value in bytes
        list_ratio: Ratio of LIST keys to total keys (0.0 to 1.0)
        ssl: Use SSL connection
        show_progress: Show progress bar (default: True)
        flush_first: Flush all existing data before populating (default: False)

    Returns:
        Dictionary with keys_created, list_keys, string_keys, memory_used_mb
    """
    client = redis.Redis(
        host=host,
        port=port,
        password=password,
        ssl=ssl,
        decode_responses=False,
    )

    if flush_first:
        if show_progress:
            console.print("[yellow]Flushing existing data...[/yellow]")
        client.flushdb()

    list_count = int(num_keys * list_ratio)
    string_count = num_keys - list_count
    batch_size = 1000

    if show_progress:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TextColumn("[cyan]{task.fields[speed]:.1f} keys/s"),
            TextColumn("[cyan]{task.fields[memory]}"),
            TimeRemainingColumn(),
        ) as progress:
            # Create LIST keys (simulating job queues)
            list_task = progress.add_task(
                "[green]Creating LIST keys",
                total=list_count,
                speed=0.0,
                memory="~0 MB",
            )
            _populate_with_pipeline(
                client, "queue", list_count, value_size, batch_size,
                progress, list_task, is_list=True
            )

            # Create STRING keys (simulating cache)
            string_task = progress.add_task(
                "[blue]Creating STRING keys",
                total=string_count,
                speed=0.0,
                memory="~0 MB",
            )
            _populate_with_pipeline(
                client, "cache", string_count, value_size, batch_size,
                progress, string_task, is_list=False
            )
    else:
        # No progress bar - just populate
        _populate_with_pipeline(
            client, "queue", list_count, value_size, batch_size,
            None, None, is_list=True
        )
        _populate_with_pipeline(
            client, "cache", string_count, value_size, batch_size,
            None, None, is_list=False
        )

    # Get memory usage
    info = client.info("memory")
    used_memory_mb = info.get("used_memory", 0) // (1024 * 1024)

    client.close()

    return {
        "keys_created": num_keys,
        "list_keys": list_count,
        "string_keys": string_count,
        "memory_used_mb": used_memory_mb,
    }


def _populate_with_pipeline(
    client: redis.Redis,
    key_prefix: str,
    count: int,
    value_size: int,
    batch_size: int,
    progress: Progress | None,
    task_id: Any | None,
    is_list: bool,
) -> None:
    """Populate keys using Redis pipelining for efficiency.

    Args:
        client: Redis client
        key_prefix: Prefix for key generation ("queue" or "cache")
        count: Number of keys to create
        value_size: Size of each value in bytes
        batch_size: Number of operations per pipeline batch
        progress: Rich Progress instance (optional)
        task_id: Rich Progress task ID (optional)
        is_list: True for LPUSH operations, False for SET operations
    """
    start_time = time.time()
    keys_processed = 0

    for i in range(0, count, batch_size):
        batch_start = time.time()
        pipe = client.pipeline(transaction=False)

        # Add operations to pipeline
        batch_end = min(i + batch_size, count)
        for j in range(i, batch_end):
            key = generate_key(key_prefix, j)
            value = generate_value(value_size)
            if is_list:
                pipe.lpush(key, value)
            else:
                pipe.set(key, value)

        # Execute pipeline
        pipe.execute()

        keys_processed = batch_end

        # Update progress bar
        if progress and task_id is not None:
            elapsed = time.time() - start_time
            speed = keys_processed / elapsed if elapsed > 0 else 0

            # Calculate estimated memory (keys * value_size)
            memory_mb = (keys_processed * value_size) / (1024 * 1024)
            if memory_mb >= 1024:
                memory_str = f"~{memory_mb / 1024:.1f} GB"
            else:
                memory_str = f"~{memory_mb:.0f} MB"

            progress.update(
                task_id,
                completed=keys_processed,
                speed=speed,
                memory=memory_str,
            )
