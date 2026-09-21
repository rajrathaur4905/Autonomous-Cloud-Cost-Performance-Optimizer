"""
Agent 3 — Optimize Agent
========================
Uses rule-based heuristics (V1) on top of forecast data to determine if
a cloud resource should be upsized, downsized, or held at its current size.

Capabilities
------------
- **Risk Evaluation**: Assesses the risk of scaling actions using forecast bounds.
- **Cost Calculation**: Computes expected monthly savings for downsize actions.
- **Safety Checks**: Implements cooldowns and confidence thresholds to prevent thrashing.

Downstream consumers
--------------------
- agent_deploy.py    → executes the logged decisions
- agent_explain.py   → explains the logged decisions

Usage
-----
    from Agents.agent_monitor import MonitorAgent
    from Agents.agent_forecast import ForecastAgent
    from Agents.agent_optimize import OptimizeAgent

    monitor = MonitorAgent()
    forecaster = ForecastAgent(monitor)
    optimizer = OptimizeAgent(monitor, forecaster, current_vm_size="Standard_B2s")

    decision = optimizer.evaluate(metric="cpu_percent", horizon_hours=4)
"""

import logging
import os
import sys
import sqlite3
import json
from datetime import datetime, timedelta, timezone

import pandas as pd

# ---------------------------------------------------------------------------
# Resolve project root so `config.settings` is always importable
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config.settings import DB_PATH
from Agents.agent_monitor import MonitorAgent
from Agents.agent_forecast import ForecastAgent

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("agent_optimize")
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

# ---------------------------------------------------------------------------
# Azure VM Size Catalog & Configuration Constants
# ---------------------------------------------------------------------------
VM_SIZE_CATALOG = {
    "Standard_B1s":  {"vcpus": 1, "ram_gb": 1,   "cost_per_hour": 0.60},
    "Standard_B1ms": {"vcpus": 1, "ram_gb": 2,   "cost_per_hour": 1.20},
    "Standard_B2s":  {"vcpus": 2, "ram_gb": 4,   "cost_per_hour": 2.40},
    "Standard_B2ms": {"vcpus": 2, "ram_gb": 8,   "cost_per_hour": 4.80},
    "Standard_B4ms": {"vcpus": 4, "ram_gb": 16,  "cost_per_hour": 9.60},
}

VM_SIZE_ORDER = [
    "Standard_B1s",
    "Standard_B1ms",
    "Standard_B2s",
    "Standard_B2ms",
    "Standard_B4ms"
]

# Decision Thresholds
DOWNSIZE_UPPER_THRESHOLD = 20.0
UPSIZE_LOWER_THRESHOLD = 70.0
FORECAST_WINDOW_HOURS = 4
COOLDOWN_MINUTES = 60
MIN_CONFIDENCE = "high"


# ═══════════════════════════════════════════════════════════════════════════
#  OptimizeAgent
# ═══════════════════════════════════════════════════════════════════════════
class OptimizeAgent:
    def __init__(
        self,
        monitor: MonitorAgent,
        forecaster: ForecastAgent,
        current_vm_size: str = "Standard_B2s",
        db_path: str | None = None,
        dry_run: bool = True,
    ):
        self.monitor = monitor
        self.forecaster = forecaster
        
        if current_vm_size not in VM_SIZE_CATALOG:
            raise ValueError(f"Unknown VM size '{current_vm_size}'. Must be one of {VM_SIZE_ORDER}")
            
        self.current_vm_size = current_vm_size
        self.db_path = db_path or DB_PATH
        self.dry_run = dry_run
        
        self._init_db()
        logger.info(
            "OptimizeAgent initialised (current_vm_size='%s', dry_run=%s)",
            self.current_vm_size, self.dry_run
        )

    def _init_db(self) -> None:
        """Create the ``optimization_decisions`` table if it does not exist."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS optimization_decisions (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       TEXT NOT NULL,
                    resource_id     TEXT NOT NULL,
                    action          TEXT NOT NULL,
                    current_size    TEXT,
                    recommended_size TEXT,
                    reason          TEXT,
                    confidence      TEXT,
                    risk_level      TEXT,
                    estimated_monthly_savings REAL,
                    forecast_summary TEXT,
                    status          TEXT DEFAULT 'pending',
                    dry_run         BOOLEAN
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_optimization_timestamp
                ON optimization_decisions (timestamp);
                """
            )
            conn.commit()
        logger.info("Decision database ready at %s", self.db_path)

    def _check_cooldown(self) -> bool:
        """Return True if a resize was done recently (cooldown active)."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """
                SELECT timestamp FROM optimization_decisions 
                WHERE action != 'hold' 
                ORDER BY timestamp DESC LIMIT 1
                """
            )
            row = cur.fetchone()
            if not row:
                return False
                
            last_action_time = datetime.fromisoformat(row[0])
            now = datetime.now(timezone.utc)
            return (now - last_action_time) < timedelta(minutes=COOLDOWN_MINUTES)

    def _select_target_size(self, direction: str) -> str | None:
        """Given 'upsize' or 'downsize', return the next VM size in order."""
        current_index = VM_SIZE_ORDER.index(self.current_vm_size)
        if direction == "downsize" and current_index > 0:
            return VM_SIZE_ORDER[current_index - 1]
        elif direction == "upsize" and current_index < len(VM_SIZE_ORDER) - 1:
            return VM_SIZE_ORDER[current_index + 1]
        return None

    def _assess_risk(self, max_upper: float, min_lower: float, action: str) -> str:
        """Evaluate the risk level of a proposed action based on forecast uncertainty."""
        band_width = max_upper - min_lower
        
        if band_width < 15.0:
            return "LOW"
        elif band_width >= 15.0 and action == "hold":
            return "LOW"
        elif band_width >= 30.0 and action != "hold":
            return "HIGH"
        else:
            return "MEDIUM"

    def _log_decision(self, decision: dict) -> None:
        """Insert the decision into optimization_decisions table."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO optimization_decisions (
                    timestamp, resource_id, action, current_size, 
                    recommended_size, reason, confidence, risk_level, 
                    estimated_monthly_savings, forecast_summary, status, dry_run
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision["timestamp"],
                    decision["resource_id"],
                    decision["action"],
                    decision.get("current_size"),
                    decision.get("recommended_size"),
                    decision.get("reason"),
                    decision.get("confidence"),
                    decision.get("risk_level"),
                    decision.get("estimated_monthly_savings"),
                    json.dumps(decision.get("forecast_summary", {})),
                    decision.get("status", "pending"),
                    decision.get("dry_run", True)
                )
            )
            conn.commit()

    def evaluate(self, metric: str = "cpu_percent", horizon_hours: int = 4) -> dict:
        """Core logic to decide upsize, downsize, or hold."""
        logger.info("Evaluating optimization decision based on '%s' (horizon: %dh)", metric, horizon_hours)
        
        # 1. Get the forecast
        result = self.forecaster.get_forecast(metric, horizon_hours=horizon_hours)
        forecast_df = result.get("forecast", pd.DataFrame())
        
        now = datetime.now(timezone.utc)
        
        base_decision = {
            "timestamp": now.isoformat(),
            "resource_id": self.monitor.resource_id,
            "current_size": self.current_vm_size,
            "status": "pending",
            "dry_run": self.dry_run,
            "estimated_monthly_savings": 0.0
        }

        if forecast_df.empty:
            logger.warning("Empty forecast received. Defaulting to 'hold'.")
            decision = {
                **base_decision,
                "action": "hold",
                "reason": "Insufficient forecast data",
                "confidence": "low",
                "risk_level": "LOW",
                "forecast_summary": {}
            }
            self._log_decision(decision)
            return decision

        # 2. Filter to future-only predictions
        future = forecast_df[forecast_df["ds"] > now]
        if future.empty:
            logger.warning("No future predictions in forecast. Defaulting to 'hold'.")
            decision = {
                **base_decision,
                "action": "hold",
                "reason": "No future forecast data points",
                "confidence": "low",
                "risk_level": "LOW",
                "forecast_summary": {}
            }
            self._log_decision(decision)
            return decision

        # 3. Compute decision statistics
        avg_predicted = float(future["yhat"].mean())
        max_upper = float(future["yhat_upper"].max())
        min_lower = float(future["yhat_lower"].min())
        
        forecast_summary = {
            "avg_predicted": round(avg_predicted, 2),
            "max_upper": round(max_upper, 2),
            "min_lower": round(min_lower, 2)
        }

        # 4. Check cooldown
        if self._check_cooldown():
            logger.info("Cooldown active. Action overriden to 'hold'.")
            decision = {
                **base_decision,
                "action": "hold",
                "reason": "Cooldown period active from previous resize",
                "confidence": "high",
                "risk_level": "LOW",
                "forecast_summary": forecast_summary
            }
            self._log_decision(decision)
            return decision

        # 5. Apply the rules
        action = "hold"
        reason = "Metrics within normal operating bounds"
        confidence = "medium"

        if max_upper < DOWNSIZE_UPPER_THRESHOLD:
            action = "downsize"
            reason = f"Max predicted {metric} (upper bound) is {max_upper:.1f}% for next {horizon_hours}h — safe to downsize"
            confidence = "high"
        elif min_lower > UPSIZE_LOWER_THRESHOLD:
            action = "upsize"
            reason = f"Min predicted {metric} (lower bound) is {min_lower:.1f}% for next {horizon_hours}h — risk of saturation"
            confidence = "high"

        # 6. Select target VM size
        recommended_size = None
        if action != "hold":
            recommended_size = self._select_target_size(action)
            if not recommended_size:
                logger.info("Cannot %s further. Already at limit. Overriding to 'hold'.", action)
                action = "hold"
                reason = f"Required {action} but already at size limit ({self.current_vm_size})"
                confidence = "high"
            
        # 7. Calculate estimated savings
        monthly_savings = 0.0
        if action == "downsize" and recommended_size:
            current_cost = VM_SIZE_CATALOG[self.current_vm_size]["cost_per_hour"]
            new_cost = VM_SIZE_CATALOG[recommended_size]["cost_per_hour"]
            monthly_savings = (current_cost - new_cost) * 24 * 30

        # 8. Assess risk
        risk_level = self._assess_risk(max_upper, min_lower, action)

        # 9. Package and log
        decision = {
            **base_decision,
            "action": "hold" if action == "hold" else action,
            "recommended_size": recommended_size,
            "reason": reason,
            "confidence": confidence,
            "risk_level": risk_level,
            "estimated_monthly_savings": round(monthly_savings, 2),
            "forecast_summary": forecast_summary
        }
        
        self._log_decision(decision)
        logger.info("Decision: %s | Reason: %s", decision["action"].upper(), decision["reason"])
        return decision

    def get_decision_history(self, limit: int = 20) -> pd.DataFrame:
        """Return the most recent optimization decisions."""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(
                '''
                SELECT * FROM optimization_decisions 
                ORDER BY timestamp DESC LIMIT ?
                ''',
                conn,
                params=(limit,)
            )
        return df


# ═══════════════════════════════════════════════════════════════════════════
#  CLI entry-point
# ═══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Optimize Agent — decide on VM resizing based on forecast",
    )
    parser.add_argument(
        "--metric",
        type=str,
        default="cpu_percent",
        help="Metric to evaluate (default: cpu_percent)",
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=4,
        help="Forecast horizon in hours (default: 4)",
    )
    parser.add_argument(
        "--vm-size",
        type=str,
        default="Standard_B2s",
        help="Current VM size (default: Standard_B2s)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Disable dry run mode (writes dry_run=False in DB)",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Print the decision history instead of evaluating",
    )
    args = parser.parse_args()

    monitor = MonitorAgent()
    forecaster = ForecastAgent(monitor)
    optimizer = OptimizeAgent(
        monitor, forecaster, 
        current_vm_size=args.vm_size, 
        dry_run=not args.no_dry_run
    )

    if args.history:
        history_df = optimizer.get_decision_history()
        print("\n── Recent Optimization Decisions ──")
        if history_df.empty:
            print("No decisions recorded yet.")
        else:
            print(history_df.to_string(index=False))
    else:
        print(f"\n── Evaluating Optimization based on {args.metric} ──")
        decision = optimizer.evaluate(metric=args.metric, horizon_hours=args.horizon)
        print(json.dumps(decision, indent=2))
