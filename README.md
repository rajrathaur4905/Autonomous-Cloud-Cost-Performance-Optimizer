# ☁️ Autonomous Cloud Cost & Performance Optimizer

> A multi-agent AI system that **monitors**, **forecasts**, **optimizes**, **deploys**, and **explains** cloud resource decisions — fully autonomously — on Microsoft Azure.

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Azure](https://img.shields.io/badge/Azure-Cloud-0078D4?logo=microsoftazure&logoColor=white)](https://azure.microsoft.com/)
[![Prophet](https://img.shields.io/badge/Prophet-Time--Series-1877F2?logo=meta&logoColor=white)](https://facebook.github.io/prophet/)
[![OpenAI](https://img.shields.io/badge/OpenAI-GPT--4o--mini-412991?logo=openai&logoColor=white)](https://openai.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 📋 Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [System Architecture](#system-architecture)
- [Agent Pipeline](#agent-pipeline)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Synthetic Data Generator](#synthetic-data-generator)
- [Tech Stack](#tech-stack)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

Cloud cost optimization remains one of the most challenging problems in modern infrastructure management. Resources are often over-provisioned "just in case", leading to significant waste, or under-provisioned, leading to performance degradation and SLA breaches.

**Autonomous Cloud Cost & Performance Optimizer** solves this by deploying a pipeline of five AI-powered agents that work in concert to:

1. **Continuously monitor** VM-level metrics from Azure Monitor
2. **Forecast** future resource utilization using time-series models
3. **Decide** optimal scaling actions via rule-based heuristics with safety guards
4. **Execute** (or simulate) infrastructure changes through Azure SDK
5. **Explain** every autonomous decision in plain English using an LLM

The result is a closed-loop system that balances **cost savings** with **performance guarantees** — and keeps humans in the loop through explainable AI.

---

## Key Features

| Feature                        | Description                                                                                             |
| ------------------------------ | ------------------------------------------------------------------------------------------------------- |
| 🔍 **Real-Time Monitoring**     | Collects CPU, memory, disk I/O, and network metrics from Azure Monitor at configurable intervals        |
| 📈 **Time-Series Forecasting**  | Uses Facebook Prophet to predict metric trends, detect anomalies, and forecast threshold breaches       |
| ⚙️ **Rule-Based Optimization**  | Evaluates upsize/downsize/hold decisions with risk scoring, cost estimation, and cooldown safety guards |
| 🚀 **Automated Deployment**     | Executes VM resizing via Azure Compute SDK with pre-flight validation and dry-run support               |
| 💬 **LLM-Powered Explanations** | Generates natural-language narratives for every decision using OpenAI GPT-4o-mini                       |
| 🧪 **Synthetic Data Generator** | Produces realistic VM metric data with daily cycles, weekend patterns, and random spikes for testing    |
| 🛡️ **Safety-First Design**      | Cooldowns, confidence thresholds, and dry-run modes prevent reckless infrastructure changes             |

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
                               │                      │
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
                   │  (Execute/Dry-Run)    │
                   └───────────┬───────────┘
                               │
                   ┌───────────▼───────────┐
                   │  Agent 5: EXPLAIN     │
                   │  (LLM Narratives)     │
                   └───────────┬───────────┘
                               │
                   ┌───────────▼───────────┐
                   │     SQLite Database    │
                   │  (metrics.db — single  │
                   │   source of truth)     │
                   └───────────────────────┘
```

---

## Agent Pipeline

### Agent 1 — Monitor Agent (`agent_monitor.py`)

Collects real-time and historical performance metrics from **Azure Monitor** for a target Virtual Machine and persists them into a local SQLite database.

**Metrics collected:**
| Metric                   | Azure Name                  |
| ------------------------ | --------------------------- |
| CPU Utilisation (%)      | `Percentage CPU`            |
| Available Memory (bytes) | `Available Memory Bytes`    |
| Disk Read Ops/sec        | `Disk Read Operations/Sec`  |
| Disk Write Ops/sec       | `Disk Write Operations/Sec` |
| Network In (bytes)       | `Network In Total`          |
| Network Out (bytes)      | `Network Out Total`         |

```python
from Agents.agent_monitor import MonitorAgent

agent = MonitorAgent()
agent.collect_metrics()            # Fetch metrics for the last hour
df = agent.get_latest_metrics()    # Pandas DataFrame for downstream agents
```

---

### Agent 2 — Forecast Agent (`agent_forecast.py`)

Uses **Facebook Prophet** to perform time-series forecasting on historical VM metrics. Capable of forecasting future values, detecting anomalies, and predicting threshold breaches.

**Capabilities:**
- 📊 Forecast future metric values for a configurable horizon
- 🚨 Anomaly detection via Prophet's uncertainty intervals
- ⚠️ Threshold breach prediction within the forecast window
- 📉 Visualization — generates and saves forecast charts to `data/`

```python
from Agents.agent_forecast import ForecastAgent

forecast = ForecastAgent(monitor)
result     = forecast.get_forecast("cpu_percent", horizon_hours=6)
anomalies  = forecast.detect_anomalies("cpu_percent", lookback_hours=24)
breach     = forecast.check_threshold_breach("cpu_percent", threshold=90, horizon_hours=4)
```

---

### Agent 3 — Optimize Agent (`agent_optimize.py`)

Applies **rule-based heuristics** on top of forecast data to determine whether a cloud resource should be **upsized**, **downsized**, or **held** at its current size.

**Key features:**
- Risk evaluation using forecast upper/lower bounds
- Monthly cost savings estimation for downsize recommendations
- Cooldown timers and confidence thresholds to prevent scaling thrash

```python
from Agents.agent_optimize import OptimizeAgent

optimizer = OptimizeAgent(monitor, forecaster, current_vm_size="Standard_B2s")
decision  = optimizer.evaluate(metric="cpu_percent", horizon_hours=4)
```

---

### Agent 4 — Deploy Agent (`agent_deploy.py`)

Executes or simulates the optimization decisions made by Agent 3. Includes **pre-flight validation**, **dry-run** capabilities, and full **audit logging**.

**Safety features:**
- Pre-flight checks ensure decision validity before execution
- Dry-run mode simulates actions without calling Azure APIs
- All deployment results are recorded in SQLite for auditing

```python
from Agents.agent_deploy import DeployAgent

agent  = DeployAgent(dry_run=True)
result = agent.execute_decision(decision)
```

---

### Agent 5 — Explain Agent (`agent_explain.py`)

Generates human-readable, **LLM-powered narratives** for every autonomous decision. Aggregates context from optimization decisions and deployment logs to provide clear, data-driven explanations.

**How it works:**
1. Assembles context — decision reasons, forecast bounds, deployment results
2. Structures a prompt for accurate LLM generation
3. Calls **OpenAI GPT-4o-mini** (or falls back to offline mode)
4. Logs explanations and token costs in SQLite

```python
from Agents.agent_explain import ExplainAgent

agent  = ExplainAgent()
result = agent.explain_latest_decision()
```

---

## Project Structure

```
Autonomous-Cloud-Cost-Performance-Optimizer/
│
├── Agents/                         # Core agent modules
│   ├── __init__.py
│   ├── agent_monitor.py            # Agent 1 — Metric collection
│   ├── agent_forecast.py           # Agent 2 — Time-series forecasting
│   ├── agent_optimize.py           # Agent 3 — Decision engine
│   ├── agent_deploy.py             # Agent 4 — Deployment executor
│   └── agent_explain.py            # Agent 5 — LLM explainability
│
├── config/
│   ├── settings.py                 # Central configuration (env loader)
│   └── app.py                      # Application orchestrator
│
├── dashboard/                      # Streamlit dashboard (Phase 5)
│
├── data/
│   ├── generate_synthetic_data.py  # Synthetic metric data generator
│   ├── metrics.db                  # SQLite database (auto-generated)
│   └── forecast_*.png              # Forecast visualizations
│
├── tests/                          # Unit & integration tests
│
├── .env                            # Environment variables (not committed)
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Prerequisites

- **Python** 3.10 or higher
- **Azure Subscription** with a deployed Virtual Machine (or use synthetic data for testing)
- **Azure Service Principal** with `Monitoring Reader` + `Virtual Machine Contributor` roles
- **OpenAI API Key** (for Agent 5 — optional, falls back to offline mode)

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/<your-username>/Autonomous-Cloud-Cost-Performance-Optimizer.git
cd Autonomous-Cloud-Cost-Performance-Optimizer
```

### 2. Create and activate a virtual environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

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

### Environment Variables

Create a `.env` file in the project root (a template is already provided):

```env
# Azure Credentials
AZURE_SUBSCRIPTION_ID=your-subscription-id
AZURE_TENANT_ID=your-tenant-id
AZURE_CLIENT_ID=your-client-id
AZURE_CLIENT_SECRET=your-client-secret

# Azure VM Resource ID
AZURE_VM_RESOURCE_ID=/subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.Compute/virtualMachines/<vm-name>

# OpenAI API Key (optional — Agent 5 works offline without it)
OPENAI_API_KEY=sk-...

# LLM Model (default: gpt-4o-mini)
LLM_MODEL=gpt-4o-mini
```

> ⚠️ **Important**: The `.env` file is listed in `.gitignore` and must **never** be committed to version control.

---

## Usage

### Quick Start with Synthetic Data (No Azure Required)

Generate realistic test data to run the full pipeline locally:

```bash
python data/generate_synthetic_data.py
```

### Running Individual Agents

```python
from Agents.agent_monitor  import MonitorAgent
from Agents.agent_forecast import ForecastAgent
from Agents.agent_optimize import OptimizeAgent
from Agents.agent_deploy   import DeployAgent
from Agents.agent_explain  import ExplainAgent

# 1. Monitor — collect metrics
monitor = MonitorAgent()
monitor.collect_metrics()

# 2. Forecast — predict future trends
forecaster = ForecastAgent(monitor)
forecast   = forecaster.get_forecast("cpu_percent", horizon_hours=6)

# 3. Optimize — decide scaling action
optimizer = OptimizeAgent(monitor, forecaster, current_vm_size="Standard_B2s")
decision  = optimizer.evaluate(metric="cpu_percent", horizon_hours=4)

# 4. Deploy — execute (or dry-run) the decision
deployer = DeployAgent(dry_run=True)
result   = deployer.execute_decision(decision)

# 5. Explain — generate human-readable narrative
explainer   = ExplainAgent()
explanation = explainer.explain_latest_decision()
```

---

## Synthetic Data Generator

The generator creates realistic VM metric patterns for offline testing:

| Pattern      | Description                                                                                                     |
| ------------ | --------------------------------------------------------------------------------------------------------------- |
| **CPU**      | Daily sine-wave cycle peaking at 10 AM & 3 PM IST. Weekdays are ~30% busier. Random spikes simulate batch jobs. |
| **Memory**   | Inversely correlated with CPU — high load consumes more RAM. Base: 1 GB (B1s instance).                         |
| **Disk I/O** | Bursty read/write operations loosely following CPU patterns with added noise.                                   |
| **Network**  | Correlated with CPU; weekday mornings see upload spikes simulating backup jobs.                                 |

```bash
# Default: 5 days of data at 5-minute intervals
python data/generate_synthetic_data.py

# Custom: 7 days at 1-minute intervals, replacing existing data
python data/generate_synthetic_data.py --days 7 --interval 1 --replace

# Quick check: 1 day with summary output
python data/generate_synthetic_data.py --days 1 --summary
```

---

## Tech Stack

| Layer                | Technology                                        |
| -------------------- | ------------------------------------------------- |
| **Language**         | Python 3.10+                                      |
| **Cloud Provider**   | Microsoft Azure (Monitor, Compute, Identity SDKs) |
| **ML / Forecasting** | Facebook Prophet, Pandas, NumPy, Matplotlib       |
| **LLM**              | OpenAI GPT-4o-mini (via `openai` SDK)             |
| **Database**         | SQLite (zero-config, embedded)                    |
| **Config**           | python-dotenv, environment variables              |
| **Dashboard**        | Streamlit *(planned — Phase 5)*                   |

---

## Contributing

Contributions are welcome! Please follow these steps:

1. **Fork** the repository
2. **Create** a feature branch (`git checkout -b feature/your-feature`)
3. **Commit** your changes (`git commit -m "Add your feature"`)
4. **Push** to the branch (`git push origin feature/your-feature`)
5. **Open** a Pull Request

---

## License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

---
