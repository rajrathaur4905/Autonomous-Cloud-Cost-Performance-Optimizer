"""
OptiCloud.ai — Pipeline Orchestrator
====================================
Coordinates all 5 agents end-to-end:
  1. MonitorAgent   — Collects Azure VM metrics (or reads local SQLite offline)
  2. ForecastAgent  — Runs Prophet time-series forecasting & breach checks
  3. OptimizeAgent  — Evaluates resize/hold decisions with safety cooldowns
  4. DeployAgent    — Executes or dry-runs the decision and logs it
  5. ExplainAgent   — Generates LLM (or offline) audit narrative

Run directly:
    python config/app.py
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config.settings import AZURE_SUBSCRIPTION_ID, AZURE_VM_RESOURCE_ID, DB_PATH
from Agents.agent_monitor import LocalMonitor, METRIC_MAP, MonitorAgent
from Agents.agent_forecast import ForecastAgent
from Agents.agent_optimize import OptimizeAgent
from Agents.agent_deploy import DeployAgent
from Agents.agent_explain import ExplainAgent
from Agents.state_machines import DataSourceMode, is_azure_configured

logger = logging.getLogger("pipeline_orchestrator")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter(
            "[%(asctime)s] %(name)s | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(_handler)


def get_monitor(db_path: str = DB_PATH) -> tuple[MonitorAgent | LocalMonitor, bool]:
    """Return (MonitorAgent, True) if Azure is configured, else (LocalMonitor, False)."""
    if is_azure_configured(AZURE_SUBSCRIPTION_ID, AZURE_VM_RESOURCE_ID):
        try:
            return MonitorAgent(db_path=db_path), True
        except Exception as exc:
            logger.warning("Azure Monitor init failed (%s); falling back to LocalMonitor.", exc)
    return LocalMonitor(db_path=db_path), False


def _latest_row_id(db_path: str, table: str) -> int | None:
    if not os.path.exists(db_path):
        return None
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(f"SELECT id FROM {table} ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        return row[0] if row else None


def run_pipeline(
    metric: str = "cpu_percent",
    horizon_hours: int = 4,
    lookback_hours: int = 24,
    current_vm_size: str | None = None,
    dry_run: bool = True,
    db_path: str = DB_PATH,
) -> dict[str, Any]:
    """Execute the full 5-agent optimization loop and return a structured result."""
    monitor, is_live_azure = get_monitor(db_path=db_path)
    data_source_mode = (
        DataSourceMode.AZURE_LIVE if is_live_azure else DataSourceMode.LOCAL_SQLITE
    )
    collected_rows = 0

    if is_live_azure and isinstance(monitor, MonitorAgent):
        df = monitor.collect_metrics()
        collected_rows = len(df)
    else:
        hist = monitor.get_metric_history(metric, hours=lookback_hours)
        if hist.empty:
            return {
                "ok": False,
                "mode": "local",
                "data_source_mode": data_source_mode.value,
                "error": "No VM metrics found in data/metrics.db. "
                         "Run `python data/generate_synthetic_data.py` or configure Azure credentials.",
            }

    resolved_vm_size = current_vm_size or DeployAgent.get_effective_vm_size(db_path=db_path)

    forecaster = ForecastAgent(monitor, lookback_hours=lookback_hours)
    breach = forecaster.check_threshold_breach(
        metric=metric, horizon_hours=horizon_hours, lookback_hours=lookback_hours
    )

    optimizer = OptimizeAgent(
        monitor, forecaster, current_vm_size=resolved_vm_size,
        db_path=db_path, dry_run=dry_run,
    )
    decision = optimizer.evaluate(metric=metric, horizon_hours=horizon_hours)
    decision["id"] = _latest_row_id(db_path, "optimization_decisions")

    deployer = DeployAgent(
        current_vm_size=resolved_vm_size, db_path=db_path, dry_run=dry_run
    )
    deployment = deployer.execute_decision(decision)
    if deployment.get("id") is None:
        deployment["id"] = _latest_row_id(db_path, "deployment_logs")

    # Keep optimizer VM tier synchronized with deployer's resulting tier
    optimizer.current_vm_size = deployer.current_vm_size

    explainer = ExplainAgent(db_path=db_path)
    explanation = explainer.explain(decision, deployment)

    return {
        "ok": True,
        "mode": "azure" if is_live_azure else "local",
        "data_source_mode": data_source_mode.value,
        "dry_run": dry_run,
        "effective_vm_size": deployer.current_vm_size,
        "collected_rows": collected_rows,
        "breach": {
            "will_breach": breach.get("will_breach", False),
            "time_to_breach": breach.get("time_to_breach"),
            "status": breach.get("status"),
        },
        "decision": decision,
        "deployment": deployment,
        "explanation": explanation,
    }


if __name__ == "__main__":
    import json

    result = run_pipeline(dry_run=True)
    print(json.dumps(result, indent=2, default=str))
