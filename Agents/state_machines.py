"""
Centralized State Machines & Domain Enums for OptiCloud.ai
=========================================================
Replaces implicit string/boolean/sentinel state tracking across agents,
orchestrator, and dashboard with explicit enums, transition tables, and
validated state-machine helpers.
"""

from __future__ import annotations

from enum import Enum


class InvalidStateTransitionError(ValueError):
    """Raised when an operation attempts an illegal state transition."""


# ---------------------------------------------------------------------------
# Azure VM Size Catalog & Tier Ladder
# ---------------------------------------------------------------------------
VM_SIZE_CATALOG: dict[str, dict[str, float | int]] = {
    "Standard_B1s":  {"vcpus": 1, "ram_gb": 1,  "cost_per_hour": 0.60},
    "Standard_B1ms": {"vcpus": 1, "ram_gb": 2,  "cost_per_hour": 1.20},
    "Standard_B2s":  {"vcpus": 2, "ram_gb": 4,  "cost_per_hour": 2.40},
    "Standard_B2ms": {"vcpus": 2, "ram_gb": 8,  "cost_per_hour": 4.80},
    "Standard_B4ms": {"vcpus": 4, "ram_gb": 16, "cost_per_hour": 9.60},
}

VM_SIZE_ORDER: list[str] = [
    "Standard_B1s",
    "Standard_B1ms",
    "Standard_B2s",
    "Standard_B2ms",
    "Standard_B4ms",
]


class ScalingAction(str, Enum):
    UPSIZE = "upsize"
    DOWNSIZE = "downsize"
    HOLD = "hold"


class VMTierStateMachine:
    """Ordered state machine over ``VM_SIZE_ORDER``."""

    def __init__(self, current_size: str = "Standard_B2s") -> None:
        if current_size not in VM_SIZE_CATALOG:
            raise ValueError(
                f"Unknown VM size '{current_size}'. Must be one of {VM_SIZE_ORDER}"
            )
        self.current_size = current_size

    def preview(self, action: ScalingAction | str) -> str | None:
        """Return the target VM size for *action*, or None if hold / at boundary."""
        act = ScalingAction(action)
        if act == ScalingAction.HOLD:
            return None
        idx = VM_SIZE_ORDER.index(self.current_size)
        if act == ScalingAction.DOWNSIZE and idx > 0:
            return VM_SIZE_ORDER[idx - 1]
        if act == ScalingAction.UPSIZE and idx < len(VM_SIZE_ORDER) - 1:
            return VM_SIZE_ORDER[idx + 1]
        return None

    def can_transition(self, action: ScalingAction | str) -> bool:
        """Return True if *action* moves to a distinct VM tier."""
        return self.preview(action) is not None

    def step(self, action: ScalingAction | str) -> str:
        """Advance to the next VM tier for *action* and return the new size."""
        act = ScalingAction(action)
        if act == ScalingAction.HOLD:
            return self.current_size
        target = self.preview(act)
        if target is None:
            raise InvalidStateTransitionError(
                f"Cannot {act.value} from boundary VM tier '{self.current_size}'."
            )
        self.current_size = target
        return self.current_size

    def set_size(self, target_size: str) -> str:
        """Directly transition to a validated VM tier (e.g. rollback)."""
        if target_size not in VM_SIZE_CATALOG:
            raise InvalidStateTransitionError(
                f"Unknown target VM size '{target_size}'."
            )
        self.current_size = target_size
        return self.current_size


# ---------------------------------------------------------------------------
# Deployment & Decision Lifecycle State Machine
# ---------------------------------------------------------------------------
class DeploymentStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    REJECTED = "rejected"
    DRY_RUN = "dry_run"
    SKIPPED = "skipped"


DEPLOYMENT_TRANSITIONS: dict[DeploymentStatus, set[DeploymentStatus]] = {
    DeploymentStatus.PENDING: {
        DeploymentStatus.APPROVED,
        DeploymentStatus.REJECTED,
        DeploymentStatus.SKIPPED,
    },
    DeploymentStatus.APPROVED: {
        DeploymentStatus.EXECUTING,
        DeploymentStatus.REJECTED,
        DeploymentStatus.DRY_RUN,
        DeploymentStatus.SKIPPED,
    },
    DeploymentStatus.EXECUTING: {
        DeploymentStatus.COMPLETED,
        DeploymentStatus.DRY_RUN,
        DeploymentStatus.FAILED,
        DeploymentStatus.ROLLED_BACK,
    },
    DeploymentStatus.COMPLETED: {
        DeploymentStatus.ROLLED_BACK,
    },
    DeploymentStatus.FAILED: {
        DeploymentStatus.EXECUTING,
        DeploymentStatus.ROLLED_BACK,
    },
    DeploymentStatus.ROLLED_BACK: set(),
    DeploymentStatus.REJECTED: set(),
    DeploymentStatus.DRY_RUN: {
        DeploymentStatus.ROLLED_BACK,
    },
    DeploymentStatus.SKIPPED: set(),
}

DEPLOY_SUCCESS_STATUSES: frozenset[DeploymentStatus] = frozenset({
    DeploymentStatus.COMPLETED,
    DeploymentStatus.DRY_RUN,
})

DEPLOY_FAILURE_STATUSES: frozenset[DeploymentStatus] = frozenset({
    DeploymentStatus.FAILED,
    DeploymentStatus.REJECTED,
    DeploymentStatus.ROLLED_BACK,
})

DEPLOY_NEUTRAL_STATUSES: frozenset[DeploymentStatus] = frozenset({
    DeploymentStatus.PENDING,
    DeploymentStatus.APPROVED,
    DeploymentStatus.EXECUTING,
    DeploymentStatus.SKIPPED,
})


class DeploymentStateMachine:
    """Validated lifecycle state machine for a single deployment execution."""

    def __init__(
        self, initial_state: DeploymentStatus | str = DeploymentStatus.PENDING
    ) -> None:
        self.state = DeploymentStatus(initial_state)
        self.history: list[tuple[DeploymentStatus, DeploymentStatus, str]] = []

    def can_transition(self, target: DeploymentStatus | str) -> bool:
        target_status = DeploymentStatus(target)
        return target_status in DEPLOYMENT_TRANSITIONS[self.state]

    def transition(
        self, target: DeploymentStatus | str, reason: str = ""
    ) -> DeploymentStatus:
        target_status = DeploymentStatus(target)
        allowed = DEPLOYMENT_TRANSITIONS[self.state]
        if target_status not in allowed:
            raise InvalidStateTransitionError(
                f"Illegal deployment transition {self.state.value!r} -> "
                f"{target_status.value!r} (allowed: {[s.value for s in allowed]})"
            )
        self.history.append((self.state, target_status, reason))
        self.state = target_status
        return self.state


# ---------------------------------------------------------------------------
# Optimization Evaluation Enums
# ---------------------------------------------------------------------------
class CooldownState(str, Enum):
    READY = "ready"
    ACTIVE = "active"


class ConfidenceLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


# ---------------------------------------------------------------------------
# Monitor, Auth & Forecast States
# ---------------------------------------------------------------------------
class DataSourceMode(str, Enum):
    AZURE_LIVE = "azure_live"
    LOCAL_SQLITE = "local_sqlite"


class AuthMode(str, Enum):
    SERVICE_PRINCIPAL = "service_principal"
    DEFAULT_CREDENTIAL = "default_credential"


class ForecastStatus(str, Enum):
    OK = "ok"
    INSUFFICIENT_DATA = "insufficient_data"
    NO_FUTURE_POINTS = "no_future_points"
    BREACH_PREDICTED = "breach_predicted"
    ERROR = "error"


class AlertSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class LLMTransportMode(str, Enum):
    OFFLINE = "offline"
    GENAI_SDK = "genai_sdk"
    REST_API = "rest_api"


class PipelineStage(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    WARNING = "warning"
    ERROR = "error"


class ForecastChartMode(str, Enum):
    NO_DATA = "no_data"
    PROPHET_ML = "prophet_ml"
    ROLLING_AVG_FALLBACK = "rolling_avg_fallback"


# ---------------------------------------------------------------------------
# Shared Configuration Guard
# ---------------------------------------------------------------------------
_PLACEHOLDER_MARKERS = (
    "...",
    "your-subscription-id",
    "your-tenant-id",
    "your-client-id",
    "your-client-secret",
    "paste-your",
)


def is_placeholder_value(value: str | None) -> bool:
    """Return True if *value* is empty or looks like an unconfigured template value."""
    if not value:
        return True
    lowered = str(value).strip().lower()
    if not lowered:
        return True
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def is_azure_configured(
    subscription_id: str | None, resource_id: str | None
) -> bool:
    """Return True only when both Azure identifiers are populated and non-placeholder."""
    if is_placeholder_value(subscription_id) or is_placeholder_value(resource_id):
        return False
    res_lower = str(resource_id).strip().lower()
    if res_lower.endswith("/virtualmachines/dummy"):
        return False
    return True
