# 📊 OptiCloud.ai — Dashboard

Interactive **Plotly Dash** web application for monitoring VM telemetry, inspecting Prophet ML forecasts and anomalies, triggering the 5-agent optimization pipeline, and auditing deployment & Gemini explanation logs.

---

## Running the Dashboard

From the project root (`Autonomous-Cloud-Cost-Performance-Optimizer/`):

```bash
python dashboard/app.py
```

Or from inside `dashboard/`:

```bash
python app.py
```

Open **http://localhost:8050** in your browser.

> **Note:** Ensure `data/metrics.db` exists first (for example via `python data/generate_synthetic_data.py`), or run `python config/app.py` to populate metrics, decisions, deployments, and explanations. If the database is missing, the sidebar shows `No database` (`Waiting for metrics.db`).

---

## Pages & Routes

| Route | Title | What It Displays |
| --- | --- | --- |
| `/` or `/overview` | **Dashboard / Overview** | AI Hero banner with **Run AI Pipeline** trigger, 4 KPI cards (monthly savings, decisions, deployments, 6h avg CPU), 24h CPU area chart, action breakdown donut, recent decisions table, and 30-second auto-refresh (`ov-tick`) |
| `/metrics` | **Metrics** | Time-range selector (`6 hours`, `24 hours`, `7 days`, `30 days`, `All`) driving CPU %, Available Memory (GB), Disk Read/Write IOPS, and Network In/Out (MB) charts |
| `/forecasts` | **Forecasts** | Metric selector driving Prophet ML forecast + 95% confidence band + anomaly markers (`ForecastChartMode.PROPHET_ML`), with automatic fallback to a 1-hour rolling average (`ForecastChartMode.ROLLING_AVG_FALLBACK`) if Prophet fails or history is insufficient |
| `/decisions` | **Decisions** | Action filter (`All`, `Downsize`, `Upsize`, `Hold`), 4-stage pipeline diagram (`Detect -> Predict -> Decide -> Optimize`), interactive `DataTable` with color-coded `action`, `risk_level`, and `status` cells, and a row-click AI explanation drawer |

---

## Pipeline & State Integration

- **Live Pipeline Execution (`Run AI Pipeline`):** Calls `config.app.run_pipeline(dry_run=True, current_vm_size=None, db_path=db._DB)`, preserving the effective VM tier across runs and updating `pipeline-state` (`PipelineStage`: `idle`, `running`, `succeeded`, `warning`, `error`) and `pipeline-trigger` to refresh all KPIs and tables.
- **Deployment Status Classification (`db.py:summary`):** Uses canonical `DeploymentStatus` sets from `Agents/state_machines.py`:
  - **Success (`ok`):** `completed`, `dry_run`
  - **Failure (`fail`):** `failed`, `rejected`, `rolled_back`
  - **Neutral (`skipped`):** `hold` decisions (`skipped` is tracked separately and never counted as a deployment failure)
- **Global Mode Badge (`topbar`):** Reflects whether the latest recorded deployment/decision ran in `Dry-Run Mode` or `Live Azure Mode`.
- **Safe DB Access:** All queries in `db.py` use context-managed SQLite connections (`with sqlite3.connect(_DB) as conn:`) so connections close cleanly even on missing tables or query errors.

---

## Directory Structure

```text
dashboard/
├── app.py              # Dash app initialization, routing, and callbacks
├── pages.py            # Page layouts, DataTable styles, and conditional formatting
├── db.py               # Read-only SQLite queries and KPI aggregation
├── assets/
│   ├── style.css       # Glassmorphism dark theme, responsive layout, animations
│   └── logo.svg        # Brand icon
└── README.md           # This file
```
