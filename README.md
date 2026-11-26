# ☁️ Autonomous Cloud Cost & Performance Optimizer (`OptiCloud.ai`)

> A multi-agent AI system that **monitors**, **forecasts**, **optimizes**, **deploys**, and **explains** cloud resource decisions — autonomously — on Microsoft Azure, with full offline synthetic-data simulation and an interactive Dash dashboard.

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Azure](https://img.shields.io/badge/Azure-Cloud-0078D4?logo=microsoftazure&logoColor=white)](https://azure.microsoft.com/)
[![Prophet](https://img.shields.io/badge/Prophet-Time--Series-1877F2?logo=meta&logoColor=white)](https://facebook.github.io/prophet/)
[![Gemini](https://img.shields.io/badge/Google%20Gemini-Explainability-4285F4?logo=googlegemini&logoColor=white)](https://ai.google.dev/)
[![Dash](https://img.shields.io/badge/Plotly%20Dash-Dashboard-008DE4?logo=plotly&logoColor=white)](https://dash.plotly.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 📋 Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [System Architecture](#system-architecture)
- [Agent Pipeline & State Machines](#agent-pipeline--state-machines)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Dashboard](#dashboard)
- [Synthetic Data Generator](#synthetic-data-generator)
- [Testing](#testing)
- [Tech Stack](#tech-stack)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

Cloud cost optimization remains one of the most challenging problems in modern infrastructure management. Resources are often over-provisioned "just in case", leading to significant waste, or under-provisioned, leading to performance degradation and SLA breaches.

**Autonomous Cloud Cost & Performance Optimizer (`OptiCloud.ai`)** solves this with a closed-loop pipeline of five specialized agents coordinated by `config/app.py` and backed by explicit state machines in `Agents/state_machines.py`:

1. **Monitor** VM-level metrics from Azure Monitor (or fall back to local SQLite via `LocalMonitor`)
2. **Forecast** future resource utilization and threshold breaches using Facebook Prophet
3. **Optimize** VM sizing via rule-based heuristics, risk scoring, tier bounds, and a cooldown state machine
4. **Deploy** (or dry-run simulate) VM resizes via Azure Compute SDK with validated lifecycle transitions, retries, and rollback support
5. **Explain** every decision in plain English using Google Gemini (with automatic REST/SDK fallback and offline mode)

---

## Key Features

| Feature                        | Description                                                                                                       |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------- |
| 🔍 **Hybrid Monitoring**       | Collects CPU, memory, disk I/O, and network metrics from Azure Monitor or reads local `metrics.db` offline        |
| 📈 **Time-Series Forecasting** | Uses Facebook Prophet to predict metric trends, detect anomalies, and flag predicted threshold breaches           |
| ⚙️ **Rule-Based Optimization** | Evaluates `upsize` / `downsize` / `hold` decisions across Azure B-series tiers with risk scoring and cost savings |
| 🔄 **Explicit State Machines** | Centralizes validated transitions (`DeploymentStateMachine`, `VMTierStateMachine`, `CooldownState`, etc.)        |
| 🚀 **Safe Deployment & Rollback** | Executes VM resizing via Azure Compute SDK with pre-flight checks, retries, dry-run mode, and rollback support |
| 💬 **Gemini Explanations**     | Generates FinOps audit narratives via Google Gemini (SDK or REST API with model fallback) or offline templates    |
| 📊 **Plotly Dash Dashboard**   | Multi-page dark glassmorphism web UI (`/overview`, `/metrics`, `/forecasts`, `/decisions`) with live pipeline run  |
| 🧪 **Synthetic Data Generator**| Produces realistic VM metric data with daily cycles, weekend patterns, and random spikes for offline testing      |

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Azure Cloud Platform                         │
│  ┌─────────────┐    ┌──────────────┐    ┌──────────────────────┐   │
│  │  Azure VM    │    │ Azure Monitor │    │ Compute Mgmt Client  │   │
│  │  (Target)    │───▶│    (Metrics)  │    │   (Resize Actions)   │   │
│  └─────────────┘    └──────┬───────┘    └──────────▲───────────┘   │
└──────────────────────────────┼──────────────────────┼───────────────┘
                               │ (or LocalMonitor offline fallback)
                   ┌───────────▼───────────┐          │
                   │  Agent 1: MONITOR     │          │
                   │  (Collect & Store)    │          │
                   └───────────┬───────────┘          │
                               │                      │
                   ┌───────────▼───────────┐          │
                   │  Agent 2: FORECAST    │          │
                   │  (Prophet ML Model)   │          │
                   └───────────┬───────────┘          │
                               │                      │
                   ┌───────────▼───────────┐          │
                   │  Agent 3: OPTIMIZE    │          │
                   │  (Decision Engine)    │          │
                   └───────────┬───────────┘          │
                               │                      │
                   ┌───────────▼───────────┐          │
                   │  Agent 4: DEPLOY      │──────────┘
                   │  (Execute/Dry-Run/RB) │
                   └───────────┬───────────┘
                               │
                   ┌───────────▼───────────┐
                   │  Agent 5: EXPLAIN     │
                   │  (Gemini Narratives)  │
                   └───────────┬───────────┘
                               │
             ┌─────────────────▼─────────────────┐
             │  SQLite Database (data/metrics.db) │
             │  vm_metrics · optimization_decisions│
             │  deployment_logs · explanations    │
             └─────────────────┬─────────────────┘
                               │
                   ┌───────────▼───────────┐
                   │  Plotly Dash UI       │
                   │  (http://localhost:8050)│
                   └───────────────────────┘
```

---

## Agent Pipeline & State Machines

All agent, orchestrator, and UI states are defined in [`Agents/state_machines.py`](Agents/state_machines.py):

### Agent 1 — Monitor Agent (`Agents/agent_monitor.py`)

Collects real-time performance metrics from **Azure Monitor** into `vm_metrics`, or switches to `LocalMonitor` (`DataSourceMode.LOCAL_SQLITE`) when Azure credentials are absent or contain template placeholders. Shares `build_azure_credential()` (`AuthMode.SERVICE_PRINCIPAL` vs `AuthMode.DEFAULT_CREDENTIAL`).

**Metrics collected:**
| Metric                   | Azure Name                  |
| ------------------------ | --------------------------- |
| CPU Utilisation (%)      | `Percentage CPU`            |
| Available Memory (bytes) | `Available Memory Bytes`    |
| Disk Read Ops/sec        | `Disk Read Operations/Sec`  |
| Disk Write Ops/sec       | `Disk Write Operations/Sec` |
| Network In (bytes)       | `Network In Total`          |
| Network Out (bytes)      | `Network Out Total`         |

### Agent 2 — Forecast Agent (`Agents/agent_forecast.py`)

Uses **Facebook Prophet** to forecast metric curves, detect anomalies outside the 95% uncertainty interval, and check threshold breaches (`ForecastStatus`: `ok`, `insufficient_data`, `no_future_points`, `breach_predicted`, `error`).

### Agent 3 — Optimize Agent (`Agents/agent_optimize.py`)

Evaluates scaling actions (`ScalingAction`: `upsize`, `downsize`, `hold`) over `VMTierStateMachine` (`Standard_B1s` → `Standard_B1ms` → `Standard_B2s` → `Standard_B2ms` → `Standard_B4ms`):
- **Cooldown guard (`CooldownState`):** 60-minute latch ignoring `rejected`, `failed`, and `skipped` entries and scoped per `dry_run` mode
- **Risk assessment (`RiskLevel`):** `LOW`, `MEDIUM`, or `HIGH` based on forecast uncertainty band width
- **Savings estimator:** Monthly INR (₹) cost delta from `VM_SIZE_CATALOG`

### Agent 4 — Deploy Agent (`Agents/agent_deploy.py`)

Executes or dry-runs decisions using `DeploymentStateMachine` and synchronizes `optimization_decisions.status` in SQLite:
- `hold` actions transition `pending -> skipped` (recorded neutrally, not as failures)
- Pre-flight validation failures transition `pending -> rejected`
- Valid resizes transition `pending -> approved -> executing -> dry_run | completed | failed` (with `MAX_RETRIES = 3` retry backoff for live Azure Compute SDK calls)
- `rollback_deployment(deployment_id)` transitions `completed | dry_run | failed -> rolled_back` using `previous_size`
- `DeployAgent.get_effective_vm_size()` recovers the active VM tier across pipeline runs

### Agent 5 — Explain Agent (`Agents/agent_explain.py`)

Assembles decision, forecast, and deployment context and selects `LLMTransportMode`:
- `offline`: Generates structured fallback output when `GEMINI_API_KEY` is missing or a placeholder
- `genai_sdk` / `rest_api`: Calls **Google Gemini** (`LLM_MODEL`, falling back across `gemini-flash-lite-latest` and `gemini-3-flash-preview`) and logs token usage and USD cost to `explanations`

---

## Project Structure

```
Autonomous-Cloud-Cost-Performance-Optimizer/
│
├── Agents/                         # Core agents & state machines
│   ├── __init__.py
│   ├── state_machines.py           # Centralized enums & validated state machines
│   ├── agent_monitor.py            # Agent 1 — Azure Monitor & LocalMonitor
│   ├── agent_forecast.py           # Agent 2 — Prophet forecasting & anomaly detection
│   ├── agent_optimize.py           # Agent 3 — Optimization engine & cooldown latch
│   ├── agent_deploy.py             # Agent 4 — Execution, retries, dry-run & rollback
│   └── agent_explain.py            # Agent 5 — Google Gemini audit explanations
│
├── config/
│   ├── settings.py                 # Central configuration (.env loader)
│   └── app.py                      # 5-agent pipeline orchestrator (run_pipeline)
│
├── dashboard/                      # Plotly Dash web application
│   ├── app.py                      # Dash server, routing & callbacks (port 8050)
│   ├── pages.py                    # Overview, Metrics, Forecasts & Decisions layouts
│   ├── db.py                       # SQLite query & KPI aggregation helpers
│   ├── assets/                     # Glassmorphism CSS theme & SVG logo
│   └── README.md                   # Dashboard documentation
│
├── data/
│   ├── generate_synthetic_data.py  # Synthetic metric generator
│   ├── metrics.db                  # SQLite database (auto-generated)
│   └── forecast_*.png              # Saved forecast plots
│
├── tests/
│   └── test_state_machines.py      # Unit & integration tests for state machines
│
├── app.py                          # CLI end-to-end pipeline walkthrough
├── demo_presentation.py            # Presentation demo script
├── .env                            # Environment variables (not committed)
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Prerequisites

- **Python** 3.10 or higher
- **Azure Subscription** with a target VM *(optional — runs locally with synthetic data)*
- **Azure Service Principal** with `Monitoring Reader` + `Virtual Machine Contributor` roles *(optional)*
- **Google Gemini API Key** *(optional — Agent 5 runs in offline fallback mode without it)*

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/<your-username>/Autonomous-Cloud-Cost-Performance-Optimizer.git
cd Autonomous-Cloud-Cost-Performance-Optimizer
```

### 2. Create and activate a virtual environment

```bash
# Windows (PowerShell)
python -m venv venv
.\venv\Scripts\Activate.ps1

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

---

## Configuration

Create a `.env` file in `Autonomous-Cloud-Cost-Performance-Optimizer/`:

```env
# Azure Credentials (leave as placeholders or omit for local offline mode)
AZURE_SUBSCRIPTION_ID=your-subscription-id-here
AZURE_TENANT_ID=your-tenant-id-here
AZURE_CLIENT_ID=your-client-id-here
AZURE_CLIENT_SECRET=your-client-secret-here

# Azure VM Resource ID
AZURE_VM_RESOURCE_ID=/subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.Compute/virtualMachines/<vm-name>

# Google Gemini API Key (optional — Agent 5 falls back to offline mode if unset)
GEMINI_API_KEY=your-gemini-api-key-here

# Gemini Model (default: gemini-flash-lite-latest)
LLM_MODEL=gemini-flash-lite-latest
```

> ⚠️ **Important**: `.env` is ignored by `.gitignore` and must never be committed with real credentials.

---

## Usage

### 1. Generate Synthetic Data (Offline Quick Start)

```bash
python data/generate_synthetic_data.py --days 5 --replace --summary
```

### 2. Run the Full 5-Agent Pipeline

Run the orchestrator (returns structured JSON, automatically choosing Azure Live or `LocalMonitor` and persisting VM tier state):

```bash
python config/app.py
```

Or run the formatted CLI walkthrough:

```bash
python app.py
```

### 3. Programmatic Usage

```python
from config.app import run_pipeline

result = run_pipeline(
    metric="cpu_percent",
    horizon_hours=4,
    lookback_hours=24,
    dry_run=True,
)
print(result["decision"]["action"], result["deployment"]["status"])
```

---

## Dashboard

Launch the interactive **Plotly Dash** web UI:

```bash
python dashboard/app.py
```

Then open **http://localhost:8050** in your browser. See [`dashboard/README.md`](dashboard/README.md) for full details on pages, callbacks, and UI state indicators.

---

## Synthetic Data Generator

| Pattern      | Description                                                                                                     |
| ------------ | --------------------------------------------------------------------------------------------------------------- |
| **CPU**      | Daily sine-wave cycle peaking at 10 AM & 3 PM IST. Weekdays are ~30% busier. Random spikes simulate batch jobs. |
| **Memory**   | Inversely correlated with CPU — high load consumes more RAM. Base: 1 GB (B1s instance).                         |
| **Disk I/O** | Bursty read/write operations loosely following CPU patterns with added noise.                                   |
| **Network**  | Correlated with CPU; weekday mornings (6–8 AM IST) see upload spikes simulating backup jobs.                    |

```bash
python data/generate_synthetic_data.py                          # 5 days, 5-min intervals
python data/generate_synthetic_data.py --days 7 --interval 1 --replace
python data/generate_synthetic_data.py --summary-only
```

---

## Testing

Run the state-machine and pipeline test suite:

```bash
python -m unittest discover -s tests -v
```

---

## Tech Stack

| Layer                | Technology                                                     |
| -------------------- | -------------------------------------------------------------- |
| **Language**         | Python 3.10+                                                   |
| **Cloud Provider**   | Microsoft Azure (`azure-mgmt-monitor`, `azure-mgmt-compute`, `azure-identity`) |
| **ML / Forecasting** | Facebook Prophet (`prophet`), `pandas`, `numpy`, `matplotlib`  |
| **LLM**              | Google Gemini (`gemini-flash-lite-latest` via REST / `google-genai`) |
| **Database**         | SQLite (`data/metrics.db`)                                     |
| **Dashboard**        | Plotly Dash (`dash`, `plotly`, `Flask`)                        |
| **Config**           | `python-dotenv`                                                |

---

## Contributing

1. **Fork** the repository
2. **Create** a feature branch (`git checkout -b feature/your-feature`)
3. **Commit** your changes (`git commit -m "Add your feature"`)
4. **Push** to the branch (`git push origin feature/your-feature`)
5. **Open** a Pull Request

---

## License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.
