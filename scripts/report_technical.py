#!/usr/bin/env python3
"""Generate technical report for SAs/AEs."""

import argparse
from pathlib import Path

from scripts.config import load_config
from scripts.report import load_metrics, generate_technical_report


def main():
    parser = argparse.ArgumentParser(description="Generate technical report (no customer-specific content)")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).parent.parent / "config.yaml",
        help="Path to config.yaml",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path(__file__).parent.parent / "results",
        help="Results directory",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    results_dir = args.results_dir

    # Load metrics for both directions
    all_results = {}

    upgrade_dir = results_dir / "upgrade"
    if upgrade_dir.exists():
        all_results["upgrade"] = {
            "light_metrics": load_metrics(upgrade_dir / "light" / "metrics.csv"),
            "heavy_metrics": load_metrics(upgrade_dir / "heavy" / "metrics.csv"),
            "from_plan": config.get("source_plan", "business-28"),
            "to_plan": config.get("target_plan", "business-56"),
        }

    downgrade_dir = results_dir / "downgrade"
    if downgrade_dir.exists():
        all_results["downgrade"] = {
            "light_metrics": load_metrics(downgrade_dir / "light" / "metrics.csv"),
            "heavy_metrics": load_metrics(downgrade_dir / "heavy" / "metrics.csv"),
            "from_plan": config.get("target_plan", "business-56"),
            "to_plan": config.get("source_plan", "business-28"),
        }

    mode = "both" if len(all_results) == 2 else ("up" if "upgrade" in all_results else "down")

    generate_technical_report(all_results, config, results_dir, mode)


if __name__ == "__main__":
    main()
