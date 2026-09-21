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
from Agents.agent_optimize import VM_SIZE_CATALOG

# Azure SDK imports
from azure.identity import DefaultAzureCredential, ClientSecretCredential
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
CONFIDENCE_THRESHOLD = "high"
MAX_RETRIES = 3
VALID_ACTIONS = {"upsize", "downsize", "hold"}
VALID_STATUSES = {
    "pending", "approved", "executing",
    "completed", "failed", "rolled_back", "rejected", "dry_run"
}


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
        if current_vm_size not in VM_SIZE_CATALOG:
            raise ValueError(f"Unknown VM size '{current_vm_size}'")
            
        self.current_vm_size = current_vm_size
        self.db_path = db_path or DB_PATH
        self.dry_run = dry_run
        self.subscription_id = subscription_id or AZURE_SUBSCRIPTION_ID
        
        self._init_db()

        if not self.dry_run:
            if not self.subscription_id:
                raise ValueError("AZURE_SUBSCRIPTION_ID is required when dry_run=False")
            logger.info("Initializing Azure Compute Client (Live Mode)...")
            self._credential = self._build_credential()
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

    # ---- Azure credential factory (copied from MonitorAgent pattern) ------
    @staticmethod
    def _build_credential():
        """Return the most appropriate Azure credential object."""
        tenant = os.getenv("AZURE_TENANT_ID")
        client_id = os.getenv("AZURE_CLIENT_ID")
        client_secret = os.getenv("AZURE_CLIENT_SECRET")

        if tenant and client_id and client_secret:
            logger.info("Using ClientSecretCredential (service principal).")
            return ClientSecretCredential(
                tenant_id=tenant,
                client_id=client_id,
                client_secret=client_secret,
            )
        logger.info("Using DefaultAzureCredential (CLI / managed identity).")
        return DefaultAzureCredential()

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
            
        if action == "hold":
            return False, "Action is 'hold' — nothing to execute"
            
        if decision.get("confidence") != CONFIDENCE_THRESHOLD:
            return False, f"Confidence '{decision.get('confidence')}' below threshold '{CONFIDENCE_THRESHOLD}'"
            
        if decision.get("risk_level") == "HIGH":
            return False, "Risk level is HIGH — requires manual approval"
            
        target = decision.get("recommended_size")
        if target and target not in VM_SIZE_CATALOG:
            return False, f"Unknown target VM size: {target}"
            
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
        """Live path: resize the VM using the Azure SDK."""
        start_time = time.time()
        try:
            resource_group, vm_name = self._parse_resource_id(resource_id)
            logger.info("LIVE DEPLOYMENT: Resizing %s in %s to %s", vm_name, resource_group, target_size)
            
            # 1. Get current VM object
            vm = self._compute_client.virtual_machines.get(resource_group, vm_name)
            
            # 2. Update hardware profile
            vm.hardware_profile.vm_size = target_size
            
            # 3. Begin update
            poller = self._compute_client.virtual_machines.begin_create_or_update(
                resource_group, vm_name, vm
            )
            
            # 4. Wait for completion
            poller.result()
            
            duration = time.time() - start_time
            logger.info("Successfully resized VM to %s in %.2fs", target_size, duration)
            return {
                "status": "completed",
                "error_message": None,
                "duration_seconds": duration,
                "execution_details": f"Successfully resized {vm_name} to {target_size}."
            }
        except Exception as e:
            duration = time.time() - start_time
            logger.error("Failed to resize VM: %s", e)
            return {
                "status": "failed",
                "error_message": str(e),
                "duration_seconds": duration,
                "execution_details": "Azure SDK error during VM resize operation."
            }

    def _log_deployment(self, log_entry: dict) -> None:
        """Insert the deployment result into deployment_logs table."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
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

    def execute_decision(self, decision: dict) -> dict:
        """Execute (or simulate) an optimization decision."""
        # 1. Validate
        is_valid, reason = self._validate_decision(decision)
        if not is_valid:
            logger.warning("Decision rejected during pre-flight: %s", reason)
            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "decision_id": decision.get("id"),
                "resource_id": decision.get("resource_id", "unknown"),
                "action": decision.get("action", "hold"),
                "previous_size": self.current_vm_size,
                "target_size": decision.get("recommended_size"),
                "status": "rejected",
                "dry_run": self.dry_run,
                "error_message": reason,
                "execution_details": "Rejected by pre-flight validation.",
            }
            self._log_deployment(log_entry)
            return log_entry

        # 2. Record pre-change state
        previous_size = self.current_vm_size
        target_size = decision["recommended_size"]
        resource_id = decision["resource_id"]
        
        # 3. Execute
        if self.dry_run:
            result = self._simulate_deployment(decision)
        else:
            result = self._execute_azure_resize(resource_id, target_size)
            
        # 4. Calculate costs
        cost_before = VM_SIZE_CATALOG[previous_size]["cost_per_hour"] * 24 * 30
        cost_after = VM_SIZE_CATALOG[target_size]["cost_per_hour"] * 24 * 30
        actual_savings = cost_before - cost_after

        # 5. Update internal state on success
        if result["status"] in ("completed", "dry_run"):
            self.current_vm_size = target_size

        # 6. Log and return
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "decision_id": decision.get("id"),
            "resource_id": resource_id,
            "action": decision["action"],
            "previous_size": previous_size,
            "target_size": target_size,
            "status": result["status"],
            "dry_run": self.dry_run,
            "duration_seconds": result.get("duration_seconds"),
            "cost_before_monthly": cost_before,
            "cost_after_monthly": cost_after,
            "actual_savings": actual_savings,
            "error_message": result.get("error_message"),
            "execution_details": result.get("execution_details"),
        }
        self._log_deployment(log_entry)
        return log_entry

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
