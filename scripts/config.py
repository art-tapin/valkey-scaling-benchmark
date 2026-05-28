"""Config loader for benchmark parameters."""

from pathlib import Path
from typing import Any

import yaml


def load_config(config_path: Path) -> dict[str, Any]:
    """Load benchmark configuration from YAML file."""
    with open(config_path) as f:
        return yaml.safe_load(f)
