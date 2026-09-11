"""
Agent 1 — Monitor Agent
========================
Collects real-time and historical performance metrics from Azure Monitor
for a target Virtual Machine and persists them into a local SQLite database.

Metrics collected
-----------------
- CPU utilisation (%)          → Percentage CPU
- Available memory (bytes)     → Available Memory Bytes
- Disk read operations/sec     → Disk Read Operations/Sec
- Disk write operations/sec    → Disk Write Operations/Sec
- Network in total (bytes)     → Network In Total
- Network out total (bytes)    → Network Out Total

Downstream consumers
--------------------
- agent_forecast.py  → reads historical metric rows for Prophet time-series
- agent_optimize.py  → reads latest snapshots for threshold-based rules
- agent_explain.py   → reads recent data for LLM-powered explanations
- agent_deploy.py    → indirectly via optimize recommendations

Usage
-----
    from Agents.agent_monitor import MonitorAgent

    agent = MonitorAgent()          # authenticates & initialises DB
    agent.collect_metrics()         # one-shot fetch for the last hour
    df = agent.get_latest_metrics() # pandas DataFrame for other agents
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.mgmt.monitor import MonitorManagementClient
from azure.mgmt.monitor.models import ResultType

# ---------------------------------------------------------------------------
# Resolve project root so `config.settings` is always importable regardless
# of how the script is invoked (directly, as a module, from tests, etc.).
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config.settings import (
    AZURE_SUBSCRIPTION_ID,
    AZURE_VM_RESOURCE_ID,
    DB_PATH,
)

# ---------------------------------------------------------------------------
# Logging setup — one logger per module, not the root logger
# ---------------------------------------------------------------------------
logger = logging.getLogger("agent_monitor")
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
# Azure Monitor metric definitions
# ---------------------------------------------------------------------------
# Each entry maps a human-readable column name to its Azure metric ID.
METRIC_MAP: dict[str, str] = {
    "cpu_percent":          "Percentage CPU",
    "available_memory":     "Available Memory Bytes",
    "disk_read_ops":        "Disk Read Operations/Sec",
    "disk_write_ops":       "Disk Write Operations/Sec",
    "network_in_bytes":     "Network In Total",
    "network_out_bytes":    "Network Out Total",
}


# ═══════════════════════════════════════════════════════════════════════════
#  MonitorAgent
# ═══════════════════════════════════════════════════════════════════════════
class MonitorAgent:
    """Fetches Azure VM metrics and stores them in a local SQLite database.

    Parameters
    ----------
    subscription_id : str | None
        Azure subscription ID. Falls back to ``config.settings``.
    resource_id : str | None
        Full Azure resource ID of the target VM.
    db_path : str | None
        Path to the SQLite database file.
    lookback_minutes : int
        How many minutes of history to fetch per ``collect_metrics()`` call.
        Defaults to 60 (one hour).
    aggregation : str
        Azure metric aggregation type — ``Average``, ``Total``, ``Maximum``,
        ``Minimum``, or ``Count``.  Default is ``Average``.
    interval : str
        ISO 8601 duration for the metric granularity.  Default ``PT5M``
        (5-minute buckets).
    """

    # ---- construction & authentication ------------------------------------

    def __init__(
        self,
        subscription_id: str | None = None,
        resource_id: str | None = None,
        db_path: str | None = None,
        lookback_minutes: int = 60,
        aggregation: str = "Average",
        interval: str = "PT5M",
    ) -> None:
        self.subscription_id = subscription_id or AZURE_SUBSCRIPTION_ID
        self.resource_id = resource_id or AZURE_VM_RESOURCE_ID
        self.db_path = db_path or DB_PATH
        self.lookback_minutes = lookback_minutes
        self.aggregation = aggregation
        self.interval = interval

        # Validate essential config
        if not self.subscription_id:
            raise ValueError(
                "AZURE_SUBSCRIPTION_ID is not set. "
                "Please configure it in .env or pass it explicitly."
            )
        if not self.resource_id or "..." in self.resource_id:
            raise ValueError(
                "AZURE_VM_RESOURCE_ID is not set or still contains placeholder "
                "values. Please update your .env file."
            )

        # Authenticate — prefer service-principal if env vars present,
        # else fall back to DefaultAzureCredential (managed identity / CLI).
        self._credential = self._build_credential()
        self._client = MonitorManagementClient(
            credential=self._credential,
            subscription_id=self.subscription_id,
        )
        logger.info("Azure Monitor client initialised successfully.")

        # Ensure the database & table exist
        self._init_db()

    # ---- Azure credential factory -----------------------------------------

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
        """Create the ``vm_metrics`` table if it does not exist."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vm_metrics (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       TEXT    NOT NULL,
                    cpu_percent     REAL,
                    available_memory REAL,
                    disk_read_ops   REAL,
                    disk_write_ops  REAL,
                    network_in_bytes REAL,
                    network_out_bytes REAL,
                    collected_at    TEXT    NOT NULL DEFAULT (datetime('now'))
                );
                """
            )
            # Index for fast time-range queries used by the Forecast agent
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_vm_metrics_timestamp
                ON vm_metrics (timestamp);
                """
            )
            conn.commit()
        logger.info("Database ready at %s", self.db_path)

    # ---- core: fetch metrics from Azure Monitor ---------------------------

    def _fetch_metric(
        self,
        metric_name: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        """Fetch a single metric's time-series from Azure Monitor.

        Returns a list of ``{"timestamp": <iso-str>, "value": <float>}`` dicts.
        """
        results: list[dict[str, Any]] = []

        timespan = f"{start_time.isoformat()}/{end_time.isoformat()}"

        response = self._client.metrics.list(
            resource_uri=self.resource_id,
            timespan=timespan,
            interval=self.interval,
            metricnames=metric_name,
            aggregation=self.aggregation,
            result_type=ResultType.DATA,
        )

        for metric in response.value:
            for ts in metric.timeseries:
                for dp in ts.data:
                    value = getattr(dp, self.aggregation.lower(), None)
                    if value is not None:
                        results.append(
                            {
                                "timestamp": dp.time_stamp.isoformat(),
                                "value": value,
                            }
                        )
        return results

    # ---- public: collect & persist ----------------------------------------

    def collect_metrics(self) -> pd.DataFrame:
        """Fetch all configured metrics for the look-back window, persist
        them to SQLite, and return the collected rows as a DataFrame.

        Returns
        -------
        pd.DataFrame
            Columns: ``timestamp``, plus one column per metric in
            ``METRIC_MAP``.
        """
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=self.lookback_minutes)

        logger.info(
            "Collecting metrics from %s to %s …",
            start_time.strftime("%Y-%m-%d %H:%M UTC"),
            end_time.strftime("%Y-%m-%d %H:%M UTC"),
        )

        # Fetch every metric independently and merge on timestamp
        metric_frames: dict[str, pd.DataFrame] = {}
        for col_name, azure_name in METRIC_MAP.items():
            try:
                raw = self._fetch_metric(azure_name, start_time, end_time)
                if raw:
                    df = pd.DataFrame(raw)
                    df.rename(columns={"value": col_name}, inplace=True)
                    metric_frames[col_name] = df
                    logger.info(
                        "  ✓ %-22s → %d data points", col_name, len(df)
                    )
                else:
                    logger.warning("  ✗ %-22s → no data returned", col_name)
            except Exception as exc:
                logger.error(
                    "  ✗ %-22s → error: %s", col_name, exc, exc_info=True
                )

        if not metric_frames:
            logger.warning("No metric data was collected — nothing to store.")
            return pd.DataFrame()

        # Merge all metric DataFrames on timestamp (outer join keeps all rows)
        merged = None
        for col_name, df in metric_frames.items():
            if merged is None:
                merged = df
            else:
                merged = pd.merge(merged, df, on="timestamp", how="outer")

        merged.sort_values("timestamp", inplace=True)
        merged.reset_index(drop=True, inplace=True)

        # Persist to SQLite
        self._store_metrics(merged)

        logger.info(
            "Collection complete — %d rows across %d metrics.",
            len(merged),
            len(metric_frames),
        )
        return merged

    def _store_metrics(self, df: pd.DataFrame) -> None:
        """Insert collected metric rows into the ``vm_metrics`` table.

        Duplicate timestamps are silently skipped (idempotent inserts).
        """
        cols = ["timestamp"] + list(METRIC_MAP.keys())
        # Ensure all expected columns exist (fill missing with None)
        for c in cols:
            if c not in df.columns:
                df[c] = None

        rows = df[cols].values.tolist()

        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO vm_metrics
                    (timestamp, cpu_percent, available_memory,
                     disk_read_ops, disk_write_ops,
                     network_in_bytes, network_out_bytes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

        logger.info("Stored %d rows in vm_metrics.", len(rows))

    # ---- public helpers for downstream agents -----------------------------

    def get_latest_metrics(self, limit: int = 100) -> pd.DataFrame:
        """Return the most recent *limit* rows from the database.

        Used by **agent_optimize** for threshold checks and
        **agent_explain** for LLM context.
        """
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(
                """
                SELECT timestamp, cpu_percent, available_memory,
                       disk_read_ops, disk_write_ops,
                       network_in_bytes, network_out_bytes
                FROM   vm_metrics
                ORDER  BY timestamp DESC
                LIMIT  ?
                """,
                conn,
                params=(limit,),
            )
        # Reverse so oldest-first (natural time order)
        return df.iloc[::-1].reset_index(drop=True)

    def get_metric_history(
        self,
        metric: str,
        hours: int = 24,
    ) -> pd.DataFrame:
        """Return a single metric's history for the given number of hours.

        Used by **agent_forecast** to feed Prophet.

        Parameters
        ----------
        metric : str
            Column name from ``METRIC_MAP`` (e.g. ``"cpu_percent"``).
        hours : int
            How many hours of data to retrieve.

        Returns
        -------
        pd.DataFrame
            Two columns — ``ds`` (datetime) and ``y`` (value) — ready for
            Prophet's expected input format.
        """
        if metric not in METRIC_MAP:
            raise ValueError(
                f"Unknown metric '{metric}'. "
                f"Choose from: {list(METRIC_MAP.keys())}"
            )

        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).isoformat()

        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(
                f"""
                SELECT timestamp AS ds, {metric} AS y
                FROM   vm_metrics
                WHERE  timestamp >= ?
                  AND  {metric} IS NOT NULL
                ORDER  BY timestamp ASC
                """,
                conn,
                params=(cutoff,),
            )

        if not df.empty:
            df["ds"] = pd.to_datetime(df["ds"])
        return df

    def get_summary_stats(self) -> dict[str, Any]:
        """Return quick summary statistics for the entire metrics table.

        Useful for the dashboard and **agent_explain** context.
        """
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("SELECT COUNT(*) FROM vm_metrics")
            total_rows = cur.fetchone()[0]

            cur = conn.execute(
                "SELECT MIN(timestamp), MAX(timestamp) FROM vm_metrics"
            )
            row = cur.fetchone()
            earliest, latest = row[0], row[1]

            stats: dict[str, Any] = {}
            for col in METRIC_MAP:
                cur = conn.execute(
                    f"""
                    SELECT AVG({col}), MIN({col}), MAX({col})
                    FROM vm_metrics
                    WHERE {col} IS NOT NULL
                    """
                )
                avg, mn, mx = cur.fetchone()
                stats[col] = {"avg": avg, "min": mn, "max": mx}

        return {
            "total_rows": total_rows,
            "earliest_timestamp": earliest,
            "latest_timestamp": latest,
            "metrics": stats,
        }

    # ---- dunder -----------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"MonitorAgent(subscription={self.subscription_id!r}, "
            f"resource={self.resource_id!r}, "
            f"db={self.db_path!r})"
        )


# ═══════════════════════════════════════════════════════════════════════════
#  CLI entry-point — run a one-shot collection
# ═══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Monitor Agent — collect Azure VM metrics",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=60,
        help="Minutes of history to collect (default: 60)",
    )
    parser.add_argument(
        "--interval",
        type=str,
        default="PT5M",
        help="Metric granularity in ISO 8601 duration (default: PT5M)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print summary statistics after collection",
    )
    args = parser.parse_args()

    agent = MonitorAgent(
        lookback_minutes=args.lookback,
        interval=args.interval,
    )

    collected = agent.collect_metrics()

    if not collected.empty:
        print("\n── Collected Metrics ──")
        print(collected.to_string(index=False))

    if args.summary:
        import json
        stats = agent.get_summary_stats()
        print("\n── Summary Statistics ──")
        print(json.dumps(stats, indent=2, default=str))
