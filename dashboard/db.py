"""
OptiCloud.ai — Database Helpers

Read-only queries against the pipeline's SQLite database.
Every function handles missing-db / empty-table gracefully.
"""

import os
import sqlite3
import sys
import pandas as pd

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from Agents.state_machines import (
    DEPLOY_FAILURE_STATUSES,
    DEPLOY_SUCCESS_STATUSES,
    DeploymentStatus,
)

_DB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'metrics.db'
)

_SUCCESS_VALUES = {s.value for s in DEPLOY_SUCCESS_STATUSES}
_FAILURE_VALUES = {s.value for s in DEPLOY_FAILURE_STATUSES}


def db_exists() -> bool:
    """Return True if the SQLite database file exists on disk."""
    return os.path.exists(_DB)


def _conn():
    """Return a connection, or None when the database file is absent."""
    return sqlite3.connect(_DB) if db_exists() else None


# ── Metrics ────────────────────────────────────────────────────────────────

def metrics(hours: int = 24) -> pd.DataFrame:
    """VM metrics for the last *hours* (pass 0 for everything)."""
    if not db_exists():
        return pd.DataFrame()
    try:
        base = ("SELECT timestamp, cpu_percent, available_memory, "
                "disk_read_ops, disk_write_ops, "
                "network_in_bytes, network_out_bytes FROM vm_metrics ")
        with sqlite3.connect(_DB) as conn:
            if hours:
                q = base + "ORDER BY timestamp DESC LIMIT ?"
                df = pd.read_sql_query(q, conn, params=(hours * 12,))
            else:
                df = pd.read_sql_query(base + "ORDER BY timestamp", conn)
        if not df.empty:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.sort_values('timestamp', inplace=True)
        return df
    except Exception:
        return pd.DataFrame()


# ── Decisions ──────────────────────────────────────────────────────────────

def decisions(limit: int = 100) -> pd.DataFrame:
    """Recent optimisation decisions."""
    if not db_exists():
        return pd.DataFrame()
    try:
        with sqlite3.connect(_DB) as conn:
            df = pd.read_sql_query(
                "SELECT id, timestamp, action, current_size, recommended_size, "
                "reason, confidence, risk_level, estimated_monthly_savings, "
                "status, dry_run "
                "FROM optimization_decisions ORDER BY timestamp DESC LIMIT ?",
                conn, params=(limit,),
            )
        if not df.empty:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
        return df
    except Exception:
        return pd.DataFrame()


# ── Explanation ────────────────────────────────────────────────────────────

def explanation(decision_id) -> dict | None:
    """Return {'text', 'model', 'mode', 'cost_usd'} for a decision, or None."""
    if not db_exists():
        return None
    try:
        with sqlite3.connect(_DB) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT narrative, llm_model_used, summary_one_liner, llm_cost_usd "
                "FROM explanations WHERE decision_id = ? "
                "ORDER BY timestamp DESC LIMIT 1",
                (int(decision_id),),
            )
            row = cur.fetchone()
        if not row:
            return None
        model = row[1] or 'offline'
        mode = 'offline' if model == 'offline' else 'live'
        return {
            'text': row[0],
            'model': model,
            'mode': mode,
            'summary': row[2],
            'cost_usd': row[3] or 0.0,
        }
    except Exception:
        return None


# ── Summary KPIs ───────────────────────────────────────────────────────────

def summary() -> dict:
    """Aggregate statistics for the overview page."""
    s = dict(
        metrics=0, decisions=0, deploys=0, savings=0.0,
        avg_cpu=0.0, down=0, up=0, hold=0, ok=0, fail=0, skipped=0,
        dry_run=True,
    )
    if not db_exists():
        return s

    with sqlite3.connect(_DB) as conn:
        c = conn.cursor()

        def q(sql, default=0):
            try:
                c.execute(sql)
                res = c.fetchone()
                return res[0] if res and res[0] is not None else default
            except Exception:
                return default

        s['metrics']   = q("SELECT COUNT(*) FROM vm_metrics")
        s['decisions'] = q("SELECT COUNT(*) FROM optimization_decisions")
        s['deploys']   = q("SELECT COUNT(*) FROM deployment_logs")
        s['savings']   = round(
            q("SELECT COALESCE(SUM(estimated_monthly_savings),0) "
              "FROM optimization_decisions WHERE action='downsize' AND status != 'rejected'", 0.0), 2
        )
        s['avg_cpu'] = round(
            q("SELECT COALESCE(AVG(cpu_percent),0) FROM "
              "(SELECT cpu_percent FROM vm_metrics "
              "ORDER BY timestamp DESC LIMIT 72)", 0.0), 1
        )

        latest_dry = q(
            "SELECT dry_run FROM deployment_logs ORDER BY id DESC LIMIT 1",
            None,
        )
        if latest_dry is None:
            latest_dry = q(
                "SELECT dry_run FROM optimization_decisions ORDER BY id DESC LIMIT 1",
                None,
            )
        if latest_dry is not None:
            s['dry_run'] = bool(latest_dry)

        try:
            for a, n in c.execute(
                "SELECT action, COUNT(*) FROM optimization_decisions GROUP BY action"
            ):
                if a == 'downsize':
                    s['down'] = n
                elif a == 'upsize':
                    s['up'] = n
                elif a == 'hold':
                    s['hold'] = n
        except Exception:
            pass

        try:
            for st, n in c.execute(
                "SELECT status, COUNT(*) FROM deployment_logs GROUP BY status"
            ):
                st_l = (st or '').lower()
                if st_l in _SUCCESS_VALUES:
                    s['ok'] += n
                elif st_l in _FAILURE_VALUES:
                    s['fail'] += n
                elif st_l == DeploymentStatus.SKIPPED.value:
                    s['skipped'] += n
        except Exception:
            pass

    return s
