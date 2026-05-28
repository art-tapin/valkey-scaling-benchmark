"""Progress display for benchmark Phase 2-4 using rich."""

from dataclasses import dataclass
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.table import Table


@dataclass
class ClusterStatus:
    """Status of a single cluster during benchmark."""
    name: str
    phase: str  # baseline, upgrade, post
    state: str  # RUNNING, REBALANCING, etc.
    elapsed_sec: float
    p50_ms: float
    p99_ms: float
    ops_per_sec: int
    errors: int


class BenchmarkDisplay:
    """Rich live display for benchmark progress."""

    def __init__(self):
        self._console = Console()
        self._live: Optional[Live] = None
        self._clusters: dict[str, ClusterStatus] = {}

    def _format_time(self, seconds: float) -> str:
        """Format seconds as MM:SS."""
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins:02d}:{secs:02d}"

    def _phase_color(self, phase: str) -> str:
        """Get color for phase."""
        colors = {"baseline": "cyan", "upgrade": "yellow", "post": "green"}
        return colors.get(phase, "white")

    def _state_color(self, state: str) -> str:
        """Get color for service state."""
        if state == "RUNNING":
            return "green"
        elif state == "REBALANCING":
            return "yellow"
        return "red"

    def _build_table(self) -> Table:
        """Build the status table."""
        table = Table(title="Phase 2-4: Benchmark Progress", expand=True)

        table.add_column("Cluster", style="bold", width=8)
        table.add_column("Phase", width=10)
        table.add_column("State", width=12)
        table.add_column("Time", width=7)
        table.add_column("P50", width=10, justify="right")
        table.add_column("P99", width=10, justify="right")
        table.add_column("Ops/s", width=8, justify="right")
        table.add_column("Errors", width=7, justify="right")

        for name in ["light", "heavy"]:
            status = self._clusters.get(name)
            if status:
                phase_style = self._phase_color(status.phase)
                state_style = self._state_color(status.state)
                error_style = "red" if status.errors > 0 else "green"

                table.add_row(
                    f"[bold]{status.name}[/bold]",
                    f"[{phase_style}]{status.phase}[/{phase_style}]",
                    f"[{state_style}]{status.state}[/{state_style}]",
                    self._format_time(status.elapsed_sec),
                    f"{status.p50_ms:.1f} ms",
                    f"{status.p99_ms:.1f} ms",
                    f"{status.ops_per_sec:,}",
                    f"[{error_style}]{status.errors}[/{error_style}]",
                )
            else:
                table.add_row(name, "waiting", "-", "-", "-", "-", "-", "-")

        return table

    def update_cluster(self, name: str, status: ClusterStatus) -> None:
        """Update the status of a cluster."""
        self._clusters[name] = status
        if self._live:
            self._live.update(self._build_table())

    def start(self) -> None:
        """Start the live display."""
        self._live = Live(
            self._build_table(),
            console=self._console,
            refresh_per_second=4,
        )
        self._live.start()

    def stop(self) -> None:
        """Stop the live display."""
        if self._live:
            self._live.stop()
            self._live = None
