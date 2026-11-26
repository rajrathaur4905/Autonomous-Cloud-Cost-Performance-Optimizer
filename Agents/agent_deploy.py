"""
Agent 4 — Deploy Agent
======================
Executes or simulates the optimization decisions made by Agent 3 (OptimizeAgent).
Includes safety checks, dry-run capabilities, and logs all deployment actions.

Capabilities
------------
- **Pre-flight Validation**: Ensures the decision is safe and valid before execution.
- **Dry-Run Mode**: Simulates actions without calling Azure APIs.
- **Azure SDK Integration**: Resizes VMs using ComputeManagementClient.
- **Action Auditing**: Records all deployment results in SQLite for ExplainAgent.

Downstream consumers
--------------------
- agent_explain.py   → reads the deployment_logs to generate natural language explanations

Usage
-----
    from Agents.agent_deploy import DeployAgent
    
    agent = DeployAgent(dry_run=True)
    decision = {...} # from OptimizeAgent
    result = agent.execute_decision(decision)
"""

import logging
import os
import sys
import sqlite3
import json
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

# ---------------------------------------------------------------------------
# Resolve project root so `config.settings` is always importable
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config.settings import DB_PATH, AZURE_SUBSCRIPTION_ID
from Agents.agent_monitor import build_azure_credential
from Agents.state_machines import (
    AuthMode,
    ConfidenceLevel,
    DeploymentStateMachine,
    DeploymentStatus,
    RiskLevel,
    ScalingAction,
    VM_SIZE_CATALOG,
    VMTierStateMachine,
    is_placeholder_value,
)

# Azure SDK imports
from azure.mgmt.compute import ComputeManagementClient

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("agent_deploy")
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
# Constants
# ---------------------------------------------------------------------------
DRY_RUN_DEFAULT = True
CONFIDENCE_THRESHOLD = ConfidenceLevel.HIGH.value
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 0.5
VALID_ACTIONS = {a.value for a in ScalingAction}
VALID_STATUSES = {s.value for s in DeploymentStatus}


# ═══════════════════════════════════════════════════════════════════════════
#  DeployAgent
# ═══════════════════════════════════════════════════════════════════════════
class DeployAgent:
    def __init__(
        self,
        current_vm_size: str = "Standard_B2s",
        db_path: str | None = None,
        dry_run: bool = DRY_RUN_DEFAULT,
        subscription_id: str | None = None,
    ):
        self._tier_sm = VMTierStateMachine(current_vm_size)
        self.db_path = db_path or DB_PATH
        self.dry_run = dry_run
        self.subscription_id = subscription_id or AZURE_SUBSCRIPTION_ID
        self.auth_mode: AuthMode | None = None

        self._init_db()

        if not self.dry_run:
            if is_placeholder_value(self.subscription_id):
                raise ValueError(
                    "AZURE_SUBSCRIPTION_ID is required (and non-placeholder) when dry_run=False"
                )
            logger.info("Initializing Azure Compute Client (Live Mode)...")
            self._credential, self.auth_mode = build_azure_credential()
            self._compute_client = ComputeManagementClient(
                credential=self._credential,
                subscription_id=self.subscription_id,
            )
        else:
            self._compute_client = None

        logger.info(
            "DeployAgent initialised (current_vm_size='%s', dry_run=%s)",
            self.current_vm_size, self.dry_run
        )

    @property
    def current_vm_size(self) -> str:
        return self._tier_sm.current_size

    @current_vm_size.setter
    def current_vm_size(self, value: str) -> None:
        self._tier_sm.set_size(value)

    # ---- Azure credential factory -----------------------------------------
    @staticmethod
    def _build_credential():
        """Return the most appropriate Azure credential object."""
        credential, _ = build_azure_credential()
        return credential

    # ---- SQLite initialisation --------------------------------------------
    def _init_db(self) -> None:
        """Create the ``deployment_logs`` table if it does not exist."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS deployment_logs (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp           TEXT NOT NULL,
                    decision_id         INTEGER,
                    resource_id         TEXT NOT NULL,
                    action              TEXT NOT NULL,
                    previous_size       TEXT,
                    target_size         TEXT,
                    status              TEXT NOT NULL,
                    dry_run             BOOLEAN,
                    duration_seconds    REAL,
                    cost_before_monthly REAL,
                    cost_after_monthly  REAL,
                    actual_savings      REAL,
                    error_message       TEXT,
                    execution_details   TEXT
                );
                """
            )
            conn.commit()
        logger.info("Deployment log database ready at %s", self.db_path)

    def _validate_decision(self, decision: dict) -> tuple[bool, str]:
        """Validate a decision before execution. Returns (is_valid, reason)."""
        action = decision.get("action")

        if action not in VALID_ACTIONS:
            return False, f"Unknown action: {action}"

        if action == ScalingAction.HOLD.value:
            return False, "Action is 'hold' — nothing to execute"

        if decision.get("confidence") != CONFIDENCE_THRESHOLD:
            return False, f"Confidence '{decision.get('confidence')}' below threshold '{CONFIDENCE_THRESHOLD}'"

        if decision.get("risk_level") == RiskLevel.HIGH.value:
            return False, "Risk level is HIGH — requires manual approval"

        target = decision.get("recommended_size")
        if not target or target not in VM_SIZE_CATALOG:
            return False, f"Invalid or missing target VM size: {target}"

        return True, "All pre-flight checks passed"

    def _parse_resource_id(self, resource_id: str) -> tuple[str, str]:
        """Extract resource group and VM name from Azure resource ID."""
        # /subscriptions/.../resourceGroups/RG_NAME/providers/Microsoft.Compute/virtualMachines/VM_NAME
        parts = resource_id.strip("/").split("/")
        try:
            rg_index = parts.index("resourceGroups") + 1
            vm_index = parts.index("virtualMachines") + 1
            return parts[rg_index], parts[vm_index]
        except (ValueError, IndexError) as e:
            raise ValueError(f"Invalid Azure Resource ID format: {resource_id}") from e

    def _simulate_deployment(self, decision: dict) -> dict:
        """Simulate a deployment without touching Azure."""
        logger.info(
            "DRY RUN: Would %s VM from %s → %s",
            decision["action"],
            self.current_vm_size,
            decision["recommended_size"],
        )
        return {
            "status": "dry_run",
            "error_message": None,
            "duration_seconds": 0.05,
            "execution_details": f"DRY RUN: Would {decision['action']} "
                                 f"{self.current_vm_size} → {decision['recommended_size']}"
        }

    def _execute_azure_resize(self, resource_id: str, target_size: str) -> dict:
        """Live path: resize the VM using the Azure SDK with retry backoff."""
        start_time = time.time()
        last_error: Exception | None = None

        try:
            resource_group, vm_name = self._parse_resource_id(resource_id)
        except Exception as e:
            duration = time.time() - start_time
            logger.error("Invalid resource ID for resize: %s", e)
            return {
                "status": DeploymentStatus.FAILED.value,
                "error_message": str(e),
                "duration_seconds": duration,
                "execution_details": "Invalid Azure Resource ID before resize.",
            }

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info(
                    "LIVE DEPLOYMENT (attempt %d/%d): Resizing %s in %s to %s",
                    attempt, MAX_RETRIES, vm_name, resource_group, target_size,
                )
                vm = self._compute_client.virtual_machines.get(resource_group, vm_name)
                vm.hardware_profile.vm_size = target_size
                poller = self._compute_client.virtual_machines.begin_create_or_update(
                    resource_group, vm_name, vm
                )
                poller.result()

                duration = time.time() - start_time
                logger.info("Successfully resized VM to %s in %.2fs", target_size, duration)
                return {
                    "status": DeploymentStatus.COMPLETED.value,
                    "error_message": None,
                    "duration_seconds": duration,
                    "execution_details": f"Successfully resized {vm_name} to {target_size} (attempt {attempt}).",
                }
            except Exception as e:
                last_error = e
                logger.warning("Resize attempt %d/%d failed: %s", attempt, MAX_RETRIES, e)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)

        duration = time.time() - start_time
        logger.error("Failed to resize VM after %d attempts: %s", MAX_RETRIES, last_error)
        return {
            "status": DeploymentStatus.FAILED.value,
            "error_message": str(last_error),
            "duration_seconds": duration,
            "execution_details": f"Azure SDK error after {MAX_RETRIES} attempts during VM resize.",
        }

    def _update_decision_status(
        self, decision_id: int | None, status: DeploymentStatus
    ) -> None:
        """Synchronize ``optimization_decisions.status`` with deployment lifecycle."""
        if decision_id is None:
            return
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE optimization_decisions SET status = ? WHERE id = ?",
                    (status.value, int(decision_id)),
                )
                conn.commit()
        except sqlite3.Error as exc:
            logger.warning("Could not update decision %s status: %s", decision_id, exc)

    def _log_deployment(self, log_entry: dict) -> int | None:
        """Insert the deployment result into deployment_logs table and return row ID."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO deployment_logs (
                    timestamp, decision_id, resource_id, action, previous_size, 
                    target_size, status, dry_run, duration_seconds, 
                    cost_before_monthly, cost_after_monthly, actual_savings, 
                    error_message, execution_details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log_entry["timestamp"],
                    log_entry.get("decision_id"),
                    log_entry["resource_id"],
                    log_entry["action"],
                    log_entry.get("previous_size"),
                    log_entry.get("target_size"),
                    log_entry["status"],
                    log_entry["dry_run"],
                    log_entry.get("duration_seconds"),
                    log_entry.get("cost_before_monthly"),
                    log_entry.get("cost_after_monthly"),
                    log_entry.get("actual_savings"),
                    log_entry.get("error_message"),
                    log_entry.get("execution_details"),
                )
            )
            conn.commit()
            return cur.lastrowid

    def execute_decision(self, decision: dict) -> dict:
        """Execute (or simulate) an optimization decision through explicit state transitions."""
        sm = DeploymentStateMachine(DeploymentStatus.PENDING)
        decision_id = decision.get("id")
        action = decision.get("action", ScalingAction.HOLD.value)

        # 0. Explicit no-op path for 'hold' decisions (do NOT mark as 'rejected')
        if action == ScalingAction.HOLD.value:
            sm.transition(DeploymentStatus.SKIPPED, "Hold action requires no resize")
            self._update_decision_status(decision_id, sm.state)
            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "decision_id": decision_id,
                "resource_id": decision.get("resource_id", "unknown"),
                "action": action,
                "previous_size": self.current_vm_size,
                "target_size": None,
                "status": sm.state.value,
                "dry_run": self.dry_run,
                "duration_seconds": 0.0,
                "cost_before_monthly": VM_SIZE_CATALOG[self.current_vm_size]["cost_per_hour"] * 24 * 30,
                "cost_after_monthly": VM_SIZE_CATALOG[self.current_vm_size]["cost_per_hour"] * 24 * 30,
                "actual_savings": 0.0,
                "error_message": None,
                "execution_details": "Skipped deployment: optimizer recommended 'hold'.",
            }
            log_entry["id"] = self._log_deployment(log_entry)
            return log_entry

        # 1. Pre-flight validation
        is_valid, reason = self._validate_decision(decision)
        if not is_valid:
            logger.warning("Decision rejected during pre-flight: %s", reason)
            sm.transition(DeploymentStatus.REJECTED, reason)
            self._update_decision_status(decision_id, sm.state)
            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "decision_id": decision_id,
                "resource_id": decision.get("resource_id", "unknown"),
                "action": action,
                "previous_size": self.current_vm_size,
                "target_size": decision.get("recommended_size"),
                "status": sm.state.value,
                "dry_run": self.dry_run,
                "error_message": reason,
                "execution_details": "Rejected by pre-flight validation.",
            }
            log_entry["id"] = self._log_deployment(log_entry)
            return log_entry

        # 2. Approve & begin execution
        sm.transition(DeploymentStatus.APPROVED, reason)
        sm.transition(DeploymentStatus.EXECUTING, "Execution started")
        self._update_decision_status(decision_id, sm.state)

        previous_size = self.current_vm_size
        target_size = decision["recommended_size"]
        resource_id = decision.get("resource_id", "unknown")

        # 3. Execute
        if self.dry_run:
            result = self._simulate_deployment(decision)
        else:
            result = self._execute_azure_resize(resource_id, target_size)

        # 4. Transition to terminal state
        terminal_status = DeploymentStatus(result["status"])
        sm.transition(terminal_status, result.get("execution_details", ""))
        self._update_decision_status(decision_id, sm.state)

        # 5. Calculate costs
        cost_before = VM_SIZE_CATALOG[previous_size]["cost_per_hour"] * 24 * 30
        cost_after = VM_SIZE_CATALOG[target_size]["cost_per_hour"] * 24 * 30
        actual_savings = cost_before - cost_after

        # 6. Update internal VM tier state on success
        if sm.state in (DeploymentStatus.COMPLETED, DeploymentStatus.DRY_RUN):
            self.current_vm_size = target_size

        # 7. Log and return
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "decision_id": decision_id,
            "resource_id": resource_id,
            "action": action,
            "previous_size": previous_size,
            "target_size": target_size,
            "status": sm.state.value,
            "dry_run": self.dry_run,
            "duration_seconds": result.get("duration_seconds"),
            "cost_before_monthly": cost_before,
            "cost_after_monthly": cost_after,
            "actual_savings": actual_savings,
            "error_message": result.get("error_message"),
            "execution_details": result.get("execution_details"),
        }
        log_entry["id"] = self._log_deployment(log_entry)
        return log_entry

    def rollback_deployment(self, deployment_id: int) -> dict:
        """Roll back a previously executed deployment to its ``previous_size``."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM deployment_logs WHERE id = ?", (int(deployment_id),)
            ).fetchone()

        if not row:
            raise ValueError(f"Deployment log {deployment_id} not found.")

        record = dict(row)
        sm = DeploymentStateMachine(record["status"])
        if not sm.can_transition(DeploymentStatus.ROLLED_BACK):
            raise ValueError(
                f"Deployment {deployment_id} in state '{record['status']}' cannot be rolled back."
            )

        previous_size = record.get("previous_size")
        if not previous_size or previous_size not in VM_SIZE_CATALOG:
            raise ValueError(
                f"Cannot roll back deployment {deployment_id}: invalid previous_size '{previous_size}'."
            )

        if self.dry_run:
            exec_details = (
                f"DRY RUN ROLLBACK: Reverted {record.get('target_size')} -> {previous_size}"
            )
            duration = 0.05
            error_msg = None
        else:
            res = self._execute_azure_resize(record["resource_id"], previous_size)
            if res["status"] != DeploymentStatus.COMPLETED.value:
                return res
            exec_details = f"LIVE ROLLBACK: Reverted {record.get('target_size')} -> {previous_size}"
            duration = res["duration_seconds"]
            error_msg = None

        sm.transition(DeploymentStatus.ROLLED_BACK, exec_details)
        self.current_vm_size = previous_size

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE deployment_logs SET status = ?, execution_details = ? WHERE id = ?",
                (sm.state.value, exec_details, int(deployment_id)),
            )
            conn.commit()
        self._update_decision_status(record.get("decision_id"), sm.state)

        return {
            "id": int(deployment_id),
            "decision_id": record.get("decision_id"),
            "status": sm.state.value,
            "previous_size": record.get("target_size"),
            "target_size": previous_size,
            "duration_seconds": duration,
            "error_message": error_msg,
            "execution_details": exec_details,
        }

    @staticmethod
    def get_effective_vm_size(
        db_path: str = DB_PATH, default: str = "Standard_B2s"
    ) -> str:
        """Recover the latest active VM tier from ``deployment_logs``."""
        if not os.path.exists(db_path):
            return default
        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    """
                    SELECT status, previous_size, target_size
                    FROM deployment_logs
                    WHERE status IN (?, ?, ?)
                    ORDER BY id DESC LIMIT 1
                    """,
                    (
                        DeploymentStatus.COMPLETED.value,
                        DeploymentStatus.DRY_RUN.value,
                        DeploymentStatus.ROLLED_BACK.value,
                    ),
                ).fetchone()
            if not row:
                return default
            status, prev_sz, target_sz = row
            candidate = prev_sz if status == DeploymentStatus.ROLLED_BACK.value else target_sz
            return candidate if candidate in VM_SIZE_CATALOG else default
        except sqlite3.Error:
            return default

    def get_deployment_history(self, limit: int = 20) -> pd.DataFrame:
        """Return the most recent deployment logs."""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(
                '''
                SELECT * FROM deployment_logs 
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
        description="Deploy Agent — execute VM resizing decisions",
    )
    parser.add_argument(
        "--decision-json",
        type=str,
        default=None,
        help="JSON string representing a decision to execute",
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
        help="Disable dry run mode (WARNING: WILL MODIFY AZURE RESOURCES)",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Print the deployment history instead of executing",
    )
    args = parser.parse_args()

    agent = DeployAgent(
        current_vm_size=args.vm_size,
        dry_run=not args.no_dry_run
    )

    if args.history:
        history_df = agent.get_deployment_history()
        print("\n── Recent Deployments ──")
        if history_df.empty:
            print("No deployments recorded yet.")
        else:
            print(history_df.to_string(index=False))
    elif args.decision_json:
        try:
            decision = json.loads(args.decision_json)
            print(f"\n── Executing Decision ──")
            result = agent.execute_decision(decision)
            print(json.dumps(result, indent=2))
        except json.JSONDecodeError as e:
            print(f"Error parsing --decision-json: {e}")
    else:
        print("\nPlease provide either --history or --decision-json")
