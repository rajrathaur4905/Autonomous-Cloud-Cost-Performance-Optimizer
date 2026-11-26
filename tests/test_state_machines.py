"""
Tests for all explicit state machines across OptiCloud.ai.
Run with:
    .\venv\Scripts\python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from Agents.state_machines import (
    AuthMode,
    ConfidenceLevel,
    CooldownState,
    DataSourceMode,
    DeploymentStateMachine,
    DeploymentStatus,
    ForecastChartMode,
    ForecastStatus,
    InvalidStateTransitionError,
    LLMTransportMode,
    PipelineStage,
    RiskLevel,
    ScalingAction,
    VMTierStateMachine,
    is_azure_configured,
    is_placeholder_value,
)
from Agents.agent_deploy import DeployAgent
from Agents.agent_explain import ExplainAgent
from Agents.agent_forecast import ForecastAgent
from Agents.agent_monitor import LocalMonitor, build_azure_credential
from Agents.agent_optimize import OptimizeAgent
from config.app import get_monitor, run_pipeline


class TestStateMachineCore(unittest.TestCase):
    def test_deployment_state_machine_valid_and_invalid_transitions(self):
        sm = DeploymentStateMachine()
        self.assertEqual(sm.state, DeploymentStatus.PENDING)
        sm.transition(DeploymentStatus.APPROVED, "ok")
        sm.transition(DeploymentStatus.EXECUTING, "run")
        sm.transition(DeploymentStatus.COMPLETED, "done")
        sm.transition(DeploymentStatus.ROLLED_BACK, "revert")
        self.assertEqual(len(sm.history), 4)

        with self.assertRaises(InvalidStateTransitionError):
            sm.transition(DeploymentStatus.COMPLETED, "cannot un-rollback")

        sm_hold = DeploymentStateMachine()
        sm_hold.transition(DeploymentStatus.SKIPPED, "hold")
        with self.assertRaises(InvalidStateTransitionError):
            sm_hold.transition(DeploymentStatus.EXECUTING)

    def test_vm_tier_state_machine_boundaries(self):
        vm = VMTierStateMachine("Standard_B1s")
        self.assertIsNone(vm.preview(ScalingAction.DOWNSIZE))
        self.assertFalse(vm.can_transition(ScalingAction.DOWNSIZE))
        with self.assertRaises(InvalidStateTransitionError):
            vm.step(ScalingAction.DOWNSIZE)

        self.assertEqual(vm.step(ScalingAction.UPSIZE), "Standard_B1ms")
        self.assertEqual(vm.step(ScalingAction.HOLD), "Standard_B1ms")

        vm_top = VMTierStateMachine("Standard_B4ms")
        self.assertIsNone(vm_top.preview(ScalingAction.UPSIZE))
        with self.assertRaises(InvalidStateTransitionError):
            vm_top.step(ScalingAction.UPSIZE)

    def test_placeholder_and_azure_config_guards(self):
        self.assertTrue(is_placeholder_value(None))
        self.assertTrue(is_placeholder_value("your-subscription-id-here"))
        self.assertTrue(is_placeholder_value("paste-your-key"))
        self.assertFalse(
            is_azure_configured(
                "your-subscription-id-here",
                "/subscriptions/1234/resourceGroups/dummy/providers/Microsoft.Compute/virtualMachines/dummy",
            )
        )
        self.assertTrue(
            is_azure_configured(
                "11111111-2222-3333-4444-555555555555",
                "/subscriptions/11111111/resourceGroups/prod-rg/providers/Microsoft.Compute/virtualMachines/app-vm",
            )
        )


class TestAgentStateMachines(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test_metrics.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _seed_metrics(self, cpu_value: float = 10.0, points: int = 36):
        now = datetime.now(timezone.utc)
        rows = [
            (
                (now - timedelta(minutes=5 * (points - i))).isoformat(),
                cpu_value,
                800_000_000.0,
                10.0,
                5.0,
                1000.0,
                1000.0,
            )
            for i in range(points)
        ]
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vm_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    cpu_percent REAL,
                    available_memory REAL,
                    disk_read_ops REAL,
                    disk_write_ops REAL,
                    network_in_bytes REAL,
                    network_out_bytes REAL,
                    collected_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                """
            )
            conn.executemany(
                """
                INSERT INTO vm_metrics (
                    timestamp, cpu_percent, available_memory,
                    disk_read_ops, disk_write_ops,
                    network_in_bytes, network_out_bytes
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    def test_deploy_hold_transitions_to_skipped_and_syncs_decision(self):
        monitor = LocalMonitor(db_path=self.db_path)
        forecaster = MagicMock()
        forecaster.get_forecast.return_value = {
            "history": pd.DataFrame({"ds": [pd.Timestamp("2026-01-01 00:00:00")]}),
            "forecast": pd.DataFrame({
                "ds": [pd.Timestamp("2026-01-01 01:00:00")],
                "yhat": [45.0],
                "yhat_lower": [40.0],
                "yhat_upper": [50.0],
            }),
        }
        opt = OptimizeAgent(monitor, forecaster, current_vm_size="Standard_B2s", db_path=self.db_path, dry_run=True)
        decision = opt.evaluate()
        with sqlite3.connect(self.db_path) as conn:
            decision["id"] = conn.execute("SELECT id FROM optimization_decisions ORDER BY id DESC LIMIT 1").fetchone()[0]

        deployer = DeployAgent(current_vm_size="Standard_B2s", db_path=self.db_path, dry_run=True)
        dep = deployer.execute_decision(decision)

        self.assertEqual(dep["status"], DeploymentStatus.SKIPPED.value)
        with sqlite3.connect(self.db_path) as conn:
            opt_status = conn.execute(
                "SELECT status FROM optimization_decisions WHERE id = ?", (decision["id"],)
            ).fetchone()[0]
        self.assertEqual(opt_status, DeploymentStatus.SKIPPED.value)

    def test_cooldown_ignores_rejected_and_failed_decisions(self):
        monitor = LocalMonitor(db_path=self.db_path)
        forecaster = MagicMock()
        opt = OptimizeAgent(monitor, forecaster, current_vm_size="Standard_B2s", db_path=self.db_path, dry_run=True)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO optimization_decisions (
                    timestamp, resource_id, action, current_size, recommended_size,
                    reason, confidence, risk_level, estimated_monthly_savings,
                    forecast_summary, status, dry_run
                ) VALUES (?, ?, 'downsize', 'Standard_B2s', 'Standard_B1ms', 'test', 'high', 'HIGH', 864.0, '{}', 'rejected', 1)
                """,
                (datetime.now(timezone.utc).isoformat(), "vm-1"),
            )
            conn.commit()

        state, _ = opt.get_cooldown_state()
        self.assertEqual(state, CooldownState.READY)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO optimization_decisions (
                    timestamp, resource_id, action, current_size, recommended_size,
                    reason, confidence, risk_level, estimated_monthly_savings,
                    forecast_summary, status, dry_run
                ) VALUES (?, ?, 'downsize', 'Standard_B2s', 'Standard_B1ms', 'test', 'high', 'LOW', 864.0, '{}', 'dry_run', 1)
                """,
                (datetime.now(timezone.utc).isoformat(), "vm-1"),
            )
            conn.commit()

        state, _ = opt.get_cooldown_state()
        self.assertEqual(state, CooldownState.ACTIVE)

    def test_optimize_boundary_reason_text_and_rollback(self):
        monitor = LocalMonitor(db_path=self.db_path)
        forecaster = MagicMock()
        forecaster.get_forecast.return_value = {
            "history": pd.DataFrame({"ds": [pd.Timestamp("2026-01-01 00:00:00")]}),
            "forecast": pd.DataFrame({
                "ds": [pd.Timestamp("2026-01-01 01:00:00")],
                "yhat": [8.0],
                "yhat_lower": [5.0],
                "yhat_upper": [12.0],
            }),
        }
        opt_min = OptimizeAgent(monitor, forecaster, current_vm_size="Standard_B1s", db_path=self.db_path, dry_run=True)
        dec_min = opt_min.evaluate()
        self.assertEqual(dec_min["action"], ScalingAction.HOLD.value)
        self.assertIn("Required downsize", dec_min["reason"])

        opt = OptimizeAgent(monitor, forecaster, current_vm_size="Standard_B2s", db_path=self.db_path, dry_run=True)
        dec = opt.evaluate()
        with sqlite3.connect(self.db_path) as conn:
            dec["id"] = conn.execute("SELECT id FROM optimization_decisions ORDER BY id DESC LIMIT 1").fetchone()[0]

        deployer = DeployAgent(current_vm_size="Standard_B2s", db_path=self.db_path, dry_run=True)
        dep = deployer.execute_decision(dec)
        self.assertEqual(dep["status"], DeploymentStatus.DRY_RUN.value)
        self.assertEqual(deployer.current_vm_size, "Standard_B1ms")
        self.assertEqual(DeployAgent.get_effective_vm_size(self.db_path), "Standard_B1ms")

        rb = deployer.rollback_deployment(dep["id"])
        self.assertEqual(rb["status"], DeploymentStatus.ROLLED_BACK.value)
        self.assertEqual(deployer.current_vm_size, "Standard_B2s")
        self.assertEqual(DeployAgent.get_effective_vm_size(self.db_path), "Standard_B2s")

    def test_live_resize_retries_max_retries_on_transient_failure(self):
        with patch("Agents.agent_deploy.ComputeManagementClient") as mock_client_cls, \
             patch("Agents.agent_deploy.build_azure_credential", return_value=(MagicMock(), AuthMode.DEFAULT_CREDENTIAL)), \
             patch("Agents.agent_deploy.time.sleep"):
            mock_vm = MagicMock()
            mock_poller = MagicMock()
            mock_client = mock_client_cls.return_value
            mock_client.virtual_machines.get.return_value = mock_vm
            mock_client.virtual_machines.begin_create_or_update.side_effect = [
                RuntimeError("transient 1"),
                RuntimeError("transient 2"),
                mock_poller,
            ]

            deployer = DeployAgent(
                current_vm_size="Standard_B2s",
                db_path=self.db_path,
                dry_run=False,
                subscription_id="11111111-2222-3333-4444-555555555555",
            )
            res = deployer.execute_decision({
                "action": "downsize",
                "confidence": "high",
                "risk_level": "LOW",
                "recommended_size": "Standard_B1ms",
                "resource_id": "/subscriptions/1111/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm",
            })
            self.assertEqual(res["status"], DeploymentStatus.COMPLETED.value)
            self.assertEqual(mock_client.virtual_machines.begin_create_or_update.call_count, 3)

    def test_explain_transport_modes_and_model_fallback(self):
        exp_offline = ExplainAgent(db_path=self.db_path, api_key="paste-your-key")
        self.assertEqual(exp_offline.transport_mode, LLMTransportMode.OFFLINE)
        out = exp_offline.explain({"action": "hold", "current_size": "Standard_B2s"}, {})
        self.assertEqual(out["llm_status"], "offline")
        self.assertEqual(out["llm_model_used"], "offline")

        with patch("Agents.agent_explain.genai", None), \
             patch("Agents.agent_explain.requests.post") as mock_post:
            resp_404 = MagicMock(status_code=404, text="not found")
            resp_200 = MagicMock(status_code=200)
            resp_200.json.return_value = {
                "candidates": [{"content": {"parts": [{"text": "Mock explanation."}]}}],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
            }
            mock_post.side_effect = [resp_404, resp_200]
            exp_rest = ExplainAgent(db_path=self.db_path, api_key="real-key-123", model="custom-model")
            self.assertEqual(exp_rest.transport_mode, LLMTransportMode.REST_API)
            res = exp_rest.explain({"action": "downsize", "current_size": "Standard_B2s", "recommended_size": "Standard_B1ms"}, {})
            self.assertEqual(res["llm_status"], "ok")
            self.assertEqual(res["llm_model_used"], "gemini-flash-lite-latest")
            self.assertEqual(exp_rest.model, "custom-model")

    def test_end_to_end_pipeline_and_effective_vm_size_persistence(self):
        self._seed_metrics(cpu_value=8.0, points=36)
        res1 = run_pipeline(dry_run=True, db_path=self.db_path)
        self.assertTrue(res1["ok"])
        self.assertEqual(res1["data_source_mode"], DataSourceMode.LOCAL_SQLITE.value)
        self.assertIn(res1["deployment"]["status"], {DeploymentStatus.DRY_RUN.value, DeploymentStatus.SKIPPED.value, DeploymentStatus.REJECTED.value})

        # Verify ForecastAgent status field on insufficient data
        empty_db = os.path.join(self.tmp.name, "empty.db")
        fc = ForecastAgent(LocalMonitor(db_path=empty_db))
        breach = fc.check_threshold_breach("cpu_percent")
        self.assertEqual(breach["status"], ForecastStatus.INSUFFICIENT_DATA.value)

    def test_auth_mode_selection(self):
        with patch.dict(
            os.environ,
            {"AZURE_TENANT_ID": "", "AZURE_CLIENT_ID": "", "AZURE_CLIENT_SECRET": ""},
            clear=False,
        ):
            _, mode = build_azure_credential()
            self.assertEqual(mode, AuthMode.DEFAULT_CREDENTIAL)
        with patch.dict(
            os.environ,
            {
                "AZURE_TENANT_ID": "11111111-2222-3333-4444-555555555555",
                "AZURE_CLIENT_ID": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "AZURE_CLIENT_SECRET": "secret-123",
            },
            clear=False,
        ):
            _, mode = build_azure_credential()
            self.assertEqual(mode, AuthMode.SERVICE_PRINCIPAL)

    def test_dashboard_db_summary_classification(self):
        import dashboard.db as dash_db

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE vm_metrics (id INTEGER PRIMARY KEY, timestamp TEXT, cpu_percent REAL)")
            conn.execute("INSERT INTO vm_metrics (timestamp, cpu_percent) VALUES ('2026-11-26T00:00:00+00:00', 22.0)")
            conn.execute(
                "CREATE TABLE optimization_decisions (id INTEGER PRIMARY KEY, timestamp TEXT, action TEXT, estimated_monthly_savings REAL, status TEXT, dry_run BOOLEAN)"
            )
            conn.execute("INSERT INTO optimization_decisions VALUES (1, '2026-11-26T00:00:00+00:00', 'hold', 0, 'skipped', 1)")
            conn.execute("INSERT INTO optimization_decisions VALUES (2, '2026-11-26T00:05:00+00:00', 'downsize', 864, 'dry_run', 1)")
            conn.execute("CREATE TABLE deployment_logs (id INTEGER PRIMARY KEY, status TEXT, dry_run BOOLEAN)")
            conn.execute("INSERT INTO deployment_logs VALUES (1, 'skipped', 1)")
            conn.execute("INSERT INTO deployment_logs VALUES (2, 'dry_run', 1)")
            conn.execute("INSERT INTO deployment_logs VALUES (3, 'failed', 1)")
            conn.commit()

        orig_db = dash_db._DB
        try:
            dash_db._DB = self.db_path
            s = dash_db.summary()
            self.assertEqual(s["ok"], 1)
            self.assertEqual(s["fail"], 1)
            self.assertEqual(s["skipped"], 1)
        finally:
            dash_db._DB = orig_db


if __name__ == "__main__":
    unittest.main()
