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
from Agents.state_machines import (
    ConfidenceLevel,
    CooldownState,
    DeploymentStatus,
    RiskLevel,
    ScalingAction,
    VM_SIZE_CATALOG,
    VM_SIZE_ORDER,
    VMTierStateMachine,
)

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
# Decision Thresholds
# ---------------------------------------------------------------------------
DOWNSIZE_UPPER_THRESHOLD = 20.0
UPSIZE_LOWER_THRESHOLD = 70.0
FORECAST_WINDOW_HOURS = 4
COOLDOWN_MINUTES = 60
MIN_CONFIDENCE = ConfidenceLevel.HIGH.value


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
        self._tier_sm = VMTierStateMachine(current_vm_size)
        self.db_path = db_path or DB_PATH
        self.dry_run = dry_run
        
        self._init_db()
        logger.info(
            "OptimizeAgent initialised (current_vm_size='%s', dry_run=%s)",
            self.current_vm_size, self.dry_run
        )

    @property
    def current_vm_size(self) -> str:
        return self._tier_sm.current_size

    @current_vm_size.setter
    def current_vm_size(self, value: str) -> None:
        self._tier_sm.set_size(value)

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

    def get_cooldown_state(self) -> tuple[CooldownState, datetime | None]:
        """Return ``(CooldownState, last_resize_time)``.

        Only executed/pending resize actions in the same ``dry_run`` mode
        trigger cooldown; rejected, failed, or skipped decisions do not.
        """
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """
                SELECT timestamp FROM optimization_decisions
                WHERE action != ?
                  AND status NOT IN (?, ?, ?)
                  AND dry_run = ?
                ORDER BY timestamp DESC LIMIT 1
                """,
                (
                    ScalingAction.HOLD.value,
                    DeploymentStatus.REJECTED.value,
                    DeploymentStatus.FAILED.value,
                    DeploymentStatus.SKIPPED.value,
                    int(bool(self.dry_run)),
                ),
            )
            row = cur.fetchone()
            if not row:
                return CooldownState.READY, None

            last_action_time = datetime.fromisoformat(row[0])
            now = datetime.now(timezone.utc)
            if last_action_time.tzinfo is None:
                last_action_time = last_action_time.replace(tzinfo=timezone.utc)
            if (now - last_action_time) < timedelta(minutes=COOLDOWN_MINUTES):
                return CooldownState.ACTIVE, last_action_time
            return CooldownState.READY, last_action_time

    def _check_cooldown(self) -> bool:
        """Return True if a resize was done recently (cooldown active)."""
        state, _ = self.get_cooldown_state()
        return state == CooldownState.ACTIVE

    def _select_target_size(self, direction: str) -> str | None:
        """Given 'upsize' or 'downsize', return the next VM size in order."""
        return self._tier_sm.preview(direction)

    def _assess_risk(self, max_upper: float, min_lower: float, action: str) -> str:
        """Evaluate the risk level of a proposed action based on forecast uncertainty."""
        band_width = max_upper - min_lower

        if band_width < 15.0:
            return RiskLevel.LOW.value
        elif band_width >= 15.0 and action == ScalingAction.HOLD.value:
            return RiskLevel.LOW.value
        elif band_width >= 30.0 and action != ScalingAction.HOLD.value:
            return RiskLevel.HIGH.value
        else:
            return RiskLevel.MEDIUM.value

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
        history_df = result.get("history", pd.DataFrame())

        now_utc = datetime.now(timezone.utc)
        anchor = (
            history_df["ds"].max()
            if not history_df.empty and "ds" in history_df.columns
            else pd.Timestamp(now_utc.replace(tzinfo=None))
        )

        base_decision = {
            "timestamp": now_utc.isoformat(),
            "resource_id": getattr(self.monitor, "resource_id", "local-synthetic-vm"),
            "current_size": self.current_vm_size,
            "status": DeploymentStatus.PENDING.value,
            "dry_run": self.dry_run,
            "estimated_monthly_savings": 0.0,
        }

        if forecast_df.empty:
            logger.warning("Empty forecast received. Defaulting to 'hold'.")
            decision = {
                **base_decision,
                "action": ScalingAction.HOLD.value,
                "reason": "Insufficient forecast data",
                "confidence": ConfidenceLevel.LOW.value,
                "risk_level": RiskLevel.LOW.value,
                "cooldown_state": CooldownState.READY.value,
                "forecast_summary": {},
            }
            self._log_decision(decision)
            return decision

        # 2. Filter to future-only predictions beyond the latest training sample
        future = forecast_df[forecast_df["ds"] > anchor]
        if future.empty:
            logger.warning("No future predictions in forecast. Defaulting to 'hold'.")
            decision = {
                **base_decision,
                "action": ScalingAction.HOLD.value,
                "reason": "No future forecast data points",
                "confidence": ConfidenceLevel.LOW.value,
                "risk_level": RiskLevel.LOW.value,
                "cooldown_state": CooldownState.READY.value,
                "forecast_summary": {},
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
            "min_lower": round(min_lower, 2),
        }

        # 4. Check cooldown state machine
        cooldown_state, _ = self.get_cooldown_state()
        if cooldown_state == CooldownState.ACTIVE:
            logger.info("Cooldown active. Action overriden to 'hold'.")
            decision = {
                **base_decision,
                "action": ScalingAction.HOLD.value,
                "reason": "Cooldown period active from previous resize",
                "confidence": ConfidenceLevel.HIGH.value,
                "risk_level": RiskLevel.LOW.value,
                "cooldown_state": cooldown_state.value,
                "forecast_summary": forecast_summary,
            }
            self._log_decision(decision)
            return decision

        # 5. Apply the rules
        action = ScalingAction.HOLD.value
        reason = "Metrics within normal operating bounds"
        confidence = ConfidenceLevel.MEDIUM.value

        if max_upper < DOWNSIZE_UPPER_THRESHOLD:
            action = ScalingAction.DOWNSIZE.value
            reason = f"Max predicted {metric} (upper bound) is {max_upper:.1f}% for next {horizon_hours}h — safe to downsize"
            confidence = ConfidenceLevel.HIGH.value
        elif min_lower > UPSIZE_LOWER_THRESHOLD:
            action = ScalingAction.UPSIZE.value
            reason = f"Min predicted {metric} (lower bound) is {min_lower:.1f}% for next {horizon_hours}h — risk of saturation"
            confidence = ConfidenceLevel.HIGH.value

        # 6. Select target VM size via VMTierStateMachine
        recommended_size = None
        if action != ScalingAction.HOLD.value:
            intended_action = action
            recommended_size = self._select_target_size(intended_action)
            if not recommended_size:
                logger.info("Cannot %s further. Already at limit. Overriding to 'hold'.", intended_action)
                action = ScalingAction.HOLD.value
                reason = f"Required {intended_action} but already at size limit ({self.current_vm_size})"
                confidence = ConfidenceLevel.HIGH.value

        # 7. Calculate estimated savings
        monthly_savings = 0.0
        if action == ScalingAction.DOWNSIZE.value and recommended_size:
            current_cost = VM_SIZE_CATALOG[self.current_vm_size]["cost_per_hour"]
            new_cost = VM_SIZE_CATALOG[recommended_size]["cost_per_hour"]
            monthly_savings = (current_cost - new_cost) * 24 * 30

        # 8. Assess risk
        risk_level = self._assess_risk(max_upper, min_lower, action)

        # 9. Package and log
        decision = {
            **base_decision,
            "action": action,
            "recommended_size": recommended_size,
            "reason": reason,
            "confidence": confidence,
            "risk_level": risk_level,
            "cooldown_state": cooldown_state.value,
            "estimated_monthly_savings": round(monthly_savings, 2),
            "forecast_summary": forecast_summary,
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
