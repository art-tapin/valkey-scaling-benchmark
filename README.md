# Aiven for Valkey - Scaling Benchmark

Benchmark tool to measure Aiven for Valkey plan scaling performance with zero-downtime validation.

## What it measures

- **Scaling duration** — How long it takes to scale between plans
- **Availability** — Whether the service remains accessible during scaling
- **Latency impact** — P50/P99 latency changes during the scaling window
- **Error rate** — Any failed operations during scaling

## Requirements

- Python 3.11+
- Two Aiven for Valkey services (light and heavy workloads)
- Aiven API token (set as `AIVEN_TOKEN` environment variable)

## Quick Start

```bash
# Clone and setup
git clone https://github.com/aiven-labs/valkey-scaling-benchmark.git
cd valkey-scaling-benchmark
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure
cp config.example.yaml config.yaml
# Edit config.yaml with your Aiven service details

# Set API token
export AIVEN_TOKEN="your-aiven-api-token"

# Run benchmark
python -m scripts.main --mode both
```

## Configuration

See `config.example.yaml` for all options:

```yaml
# Plan scaling
source_plan: business-28
target_plan: business-56

# Cluster sizes
clusters:
  light:
    data_size_gb: 2
  heavy:
    data_size_gb: 18

# Workload mix
load:
  ratio_write: 70  # % writes (LPUSH/RPUSH)
  ratio_read: 30   # % reads (LPOP/LRANGE)
```

## Modes

```bash
# Scale up only (source → target)
python -m scripts.main --mode up

# Scale down only (target → source)
python -m scripts.main --mode down

# Both directions
python -m scripts.main --mode both

# Skip data population (use existing data)
python -m scripts.main --mode both --skip-populate
```

## Output

Results are saved to `results/`:
- `report.html` — Interactive report with charts
- `report.md` — Markdown summary
- `upgrade/` and `downgrade/` — Raw metrics CSV files

Generate PDF:
```bash
python -m scripts.pdf --scale 0.85
```

## How it works

1. **Populate** — Creates test data (Redis LIST keys) in both clusters
2. **Baseline** — Measures latency/throughput before scaling (5 min default)
3. **Scale** — Triggers plan change via Aiven API, monitors throughout
4. **Report** — Generates HTML/Markdown reports with charts

The workload uses Redis LIST operations (LPUSH, RPUSH, LPOP, LRANGE) to simulate queue workloads like Laravel Horizon or Sidekiq.

## License

Apache 2.0
