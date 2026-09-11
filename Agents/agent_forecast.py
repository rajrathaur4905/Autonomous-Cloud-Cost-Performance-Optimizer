"""
Agent 2 — Forecast Agent
=========================
Uses Facebook Prophet to perform time-series forecasting on historical
VM metrics collected by Agent 1 (MonitorAgent).

Capabilities
------------
- **Forecasting**    : Predict future metric values (CPU, memory, disk, network)
                       for a configurable horizon.
- **Anomaly detection**: Flag historical data points that fall outside Prophet's
                         uncertainty interval as anomalies.
- **Breach prediction**: Detect whether a metric is forecasted to cross a
                         critical threshold within the prediction window.
- **Visualisation**  : Generate and save forecast charts to ``data/`` for the
                       dashboard.

Downstream consumers
--------------------
- agent_optimize.py  → reads forecasts & breach alerts for proactive scaling
- agent_explain.py   → reads anomalies & forecasts for LLM narrative
- dashboard          → displays saved forecast plots

Usage
-----
    from Agents.agent_monitor import MonitorAgent
    from Agents.agent_forecast import ForecastAgent

    monitor = MonitorAgent()
    forecast = ForecastAgent(monitor)

    # Forecast CPU for the next 6 hours
    result = forecast.get_forecast("cpu_percent", horizon_hours=6)

    # Detect anomalies in the last 24 hours
    anomalies = forecast.detect_anomalies("cpu_percent", lookback_hours=24)

    # Check if CPU will breach 90% in the next 4 hours
    breach = forecast.check_threshold_breach("cpu_percent", threshold=90, horizon_hours=4)
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend for server/CI environments
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
from prophet import Prophet

# ---------------------------------------------------------------------------
# Resolve project root for imports
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config.settings import DB_PATH
from Agents.agent_monitor import MonitorAgent, METRIC_MAP

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("agent_forecast")
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
# Default thresholds for breach detection (can be overridden per call)
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLDS: dict[str, float] = {
    "cpu_percent":      85.0,    # CPU above 85% is concerning
    "available_memory": 500e6,   # Less than 500 MB available is critical
    "disk_read_ops":    500.0,   # High disk IOPS
    "disk_write_ops":   500.0,
    "network_in_bytes": 100e6,   # 100 MB/interval spikes
    "network_out_bytes": 100e6,
}

# For memory, "breach" means going *below* the threshold (running out).
# For everything else, "breach" means going *above*.
LOWER_IS_WORSE: set[str] = {"available_memory"}

# Directory for saving forecast plots
_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))


# ═══════════════════════════════════════════════════════════════════════════
#  ForecastAgent
# ═══════════════════════════════════════════════════════════════════════════
class ForecastAgent:
    """Time-series forecasting and anomaly detection using Prophet.

    Parameters
    ----------
    monitor : MonitorAgent
        An initialised MonitorAgent instance used to pull historical data.
    lookback_hours : int
        Default hours of history to use for model training. Default 24.
    interval_width : float
        Prophet uncertainty interval width (0–1). Default 0.95 (95%).
    """

    def __init__(
        self,
        monitor: MonitorAgent,
        lookback_hours: int = 24,
        interval_width: float = 0.95,
    ) -> None:
        self.monitor = monitor
        self.lookback_hours = lookback_hours
        self.interval_width = interval_width

        logger.info(
            "ForecastAgent initialised (lookback=%dh, interval_width=%.0f%%).",
            lookback_hours,
            interval_width * 100,
        )

    # ---- internal: build & fit a Prophet model ----------------------------

    def _build_model(self, df: pd.DataFrame) -> Prophet:
        """Create, configure, and fit a Prophet model on the given data.

        Parameters
        ----------
        df : pd.DataFrame
            Must have columns ``ds`` (datetime) and ``y`` (float).

        Returns
        -------
        Prophet
            A fitted Prophet model ready for ``.predict()``.
        """
        model = Prophet(
            interval_width=self.interval_width,
            daily_seasonality=True,
            weekly_seasonality=True,
            yearly_seasonality=False,   # VM metrics rarely have yearly cycles
            changepoint_prior_scale=0.1,  # slightly more flexible trend
        )
        # Suppress Prophet's verbose stdout logging
        model.fit(df)
        return model

    # ---- public: forecast -------------------------------------------------

    def get_forecast(
        self,
        metric: str,
        horizon_hours: int = 6,
        lookback_hours: int | None = None,
        frequency: str = "5min",
    ) -> dict[str, Any]:
        """Forecast a metric into the future.

        Parameters
        ----------
        metric : str
            Column name from ``METRIC_MAP`` (e.g. ``"cpu_percent"``).
        horizon_hours : int
            How far ahead to predict. Default 6 hours.
        lookback_hours : int | None
            Hours of history for training. Falls back to instance default.
        frequency : str
            Pandas frequency string for future dataframe. Must match the
            metric collection interval (default ``"5min"`` = ``PT5M``).

        Returns
        -------
        dict
            Keys:
            - ``metric``       : str — the metric name
            - ``horizon_hours``: int
            - ``history``      : pd.DataFrame — training data (ds, y)
            - ``forecast``     : pd.DataFrame — Prophet forecast with
              ``ds``, ``yhat``, ``yhat_lower``, ``yhat_upper``
            - ``model``        : Prophet — the fitted model (for plots)
        """
        lookback = lookback_hours or self.lookback_hours
        self._validate_metric(metric)

        logger.info(
            "Forecasting '%s' — %dh history → %dh ahead …",
            metric, lookback, horizon_hours,
        )

        # 1. Pull historical data from MonitorAgent
        history = self.monitor.get_metric_history(metric, hours=lookback)
        if history.empty or len(history) < 2:
            logger.warning(
                "Insufficient data for '%s' (%d rows). "
                "Need at least 2 data points.",
                metric, len(history),
            )
            return {
                "metric": metric,
                "horizon_hours": horizon_hours,
                "history": history,
                "forecast": pd.DataFrame(),
                "model": None,
                "error": "Insufficient historical data",
            }

        # 2. Fit Prophet
        model = self._build_model(history)

        # 3. Create future dataframe
        periods = int((horizon_hours * 60) / 5)  # 5-min intervals
        future = model.make_future_dataframe(
            periods=periods,
            freq=frequency,
            include_history=True,
        )

        # 4. Predict
        forecast = model.predict(future)
        forecast_cols = forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]].copy()

        logger.info(
            "Forecast complete for '%s' — %d historical + %d future points.",
            metric, len(history), periods,
        )

        return {
            "metric": metric,
            "horizon_hours": horizon_hours,
            "history": history,
            "forecast": forecast_cols,
            "model": model,
        }

    # ---- public: anomaly detection ----------------------------------------

    def detect_anomalies(
        self,
        metric: str,
        lookback_hours: int | None = None,
    ) -> dict[str, Any]:
        """Detect anomalies in historical data using Prophet's uncertainty.

        An anomaly is a data point where the *actual* value falls outside
        the model's ``[yhat_lower, yhat_upper]`` confidence band.

        Returns
        -------
        dict
            Keys:
            - ``metric``          : str
            - ``total_points``    : int — total historical data points
            - ``anomaly_count``   : int — number of anomalies found
            - ``anomaly_ratio``   : float — anomaly_count / total_points
            - ``anomalies``       : pd.DataFrame — rows with
              ``ds``, ``y``, ``yhat``, ``yhat_lower``, ``yhat_upper``
            - ``all_predictions`` : pd.DataFrame — full prediction set
        """
        lookback = lookback_hours or self.lookback_hours
        self._validate_metric(metric)

        logger.info(
            "Detecting anomalies in '%s' over the last %d hours …",
            metric, lookback,
        )

        # Pull history
        history = self.monitor.get_metric_history(metric, hours=lookback)
        if history.empty or len(history) < 2:
            logger.warning("Insufficient data for anomaly detection.")
            return {
                "metric": metric,
                "total_points": len(history),
                "anomaly_count": 0,
                "anomaly_ratio": 0.0,
                "anomalies": pd.DataFrame(),
                "all_predictions": pd.DataFrame(),
                "error": "Insufficient historical data",
            }

        # Fit and predict on the same historical range (in-sample)
        model = self._build_model(history)
        predictions = model.predict(
            history[["ds"]].copy()
        )

        # Merge actuals with predictions
        merged = history.merge(
            predictions[["ds", "yhat", "yhat_lower", "yhat_upper"]],
            on="ds",
            how="inner",
        )

        # Flag anomalies: actual outside the confidence band
        merged["is_anomaly"] = (
            (merged["y"] < merged["yhat_lower"]) |
            (merged["y"] > merged["yhat_upper"])
        )

        anomalies = merged[merged["is_anomaly"]].copy()
        total = len(merged)
        count = len(anomalies)

        logger.info(
            "Anomaly detection for '%s': %d / %d points flagged (%.1f%%).",
            metric, count, total,
            (count / total * 100) if total > 0 else 0,
        )

        return {
            "metric": metric,
            "total_points": total,
            "anomaly_count": count,
            "anomaly_ratio": round(count / total, 4) if total > 0 else 0.0,
            "anomalies": anomalies,
            "all_predictions": merged,
        }

    # ---- public: threshold breach prediction ------------------------------

    def check_threshold_breach(
        self,
        metric: str,
        threshold: float | None = None,
        horizon_hours: int = 4,
        lookback_hours: int | None = None,
    ) -> dict[str, Any]:
        """Predict whether a metric will breach a critical threshold.

        Parameters
        ----------
        metric : str
            Metric column name.
        threshold : float | None
            The critical threshold value. Falls back to ``DEFAULT_THRESHOLDS``.
        horizon_hours : int
            How far ahead to check. Default 4 hours.
        lookback_hours : int | None
            Training data window.

        Returns
        -------
        dict
            Keys:
            - ``metric``          : str
            - ``threshold``       : float
            - ``will_breach``     : bool — True if any forecasted point crosses
            - ``breach_direction``: str — ``"above"`` or ``"below"``
            - ``first_breach_at`` : str | None — ISO timestamp of first breach
            - ``time_to_breach``  : str | None — human-readable time until breach
            - ``max_forecasted``  : float — peak predicted value
            - ``min_forecasted``  : float — lowest predicted value
            - ``breach_points``   : pd.DataFrame — all breaching rows
        """
        self._validate_metric(metric)

        if threshold is None:
            threshold = DEFAULT_THRESHOLDS.get(metric)
            if threshold is None:
                raise ValueError(
                    f"No default threshold for '{metric}'. "
                    f"Please provide one explicitly."
                )

        # Get the forecast
        result = self.get_forecast(
            metric,
            horizon_hours=horizon_hours,
            lookback_hours=lookback_hours,
        )

        if result.get("error") or result["forecast"].empty:
            return {
                "metric": metric,
                "threshold": threshold,
                "will_breach": False,
                "breach_direction": None,
                "first_breach_at": None,
                "time_to_breach": None,
                "max_forecasted": None,
                "min_forecasted": None,
                "breach_points": pd.DataFrame(),
                "error": result.get("error", "Empty forecast"),
            }

        forecast = result["forecast"]

        # Only look at *future* points (after the last historical timestamp)
        now = datetime.now(timezone.utc)
        future_forecast = forecast[forecast["ds"] > now].copy()

        if future_forecast.empty:
            return {
                "metric": metric,
                "threshold": threshold,
                "will_breach": False,
                "breach_direction": None,
                "first_breach_at": None,
                "time_to_breach": None,
                "max_forecasted": None,
                "min_forecasted": None,
                "breach_points": pd.DataFrame(),
            }

        # Determine breach direction
        if metric in LOWER_IS_WORSE:
            # E.g. available_memory — breach when it drops BELOW threshold
            breach_mask = future_forecast["yhat"] < threshold
            direction = "below"
        else:
            # E.g. cpu_percent — breach when it goes ABOVE threshold
            breach_mask = future_forecast["yhat"] > threshold
            direction = "above"

        breach_points = future_forecast[breach_mask]
        will_breach = not breach_points.empty

        first_breach_at = None
        time_to_breach = None
        if will_breach:
            first_breach_time = breach_points["ds"].iloc[0]
            first_breach_at = first_breach_time.isoformat()
            delta = first_breach_time - now
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes = remainder // 60
            time_to_breach = f"{hours}h {minutes}m"

        result_dict = {
            "metric": metric,
            "threshold": threshold,
            "will_breach": will_breach,
            "breach_direction": direction if will_breach else None,
            "first_breach_at": first_breach_at,
            "time_to_breach": time_to_breach,
            "max_forecasted": round(float(future_forecast["yhat"].max()), 4),
            "min_forecasted": round(float(future_forecast["yhat"].min()), 4),
            "breach_points": breach_points,
        }

        if will_breach:
            logger.warning(
                "⚠ BREACH ALERT: '%s' predicted to go %s %.2f "
                "in ~%s (first at %s).",
                metric, direction, threshold, time_to_breach, first_breach_at,
            )
        else:
            logger.info(
                "✓ '%s' stays within threshold (%.2f) over the next %dh.",
                metric, threshold, horizon_hours,
            )

        return result_dict

    # ---- public: forecast all metrics at once -----------------------------

    def forecast_all_metrics(
        self,
        horizon_hours: int = 6,
        lookback_hours: int | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Run forecasts and breach checks for every metric in METRIC_MAP.

        Returns
        -------
        dict[str, dict]
            Keyed by metric name. Each value contains:
            - ``forecast`` : the forecast result dict
            - ``breach``   : the breach check result dict
            - ``anomalies``: the anomaly detection result dict
        """
        lookback = lookback_hours or self.lookback_hours
        results: dict[str, dict[str, Any]] = {}

        for metric_name in METRIC_MAP:
            logger.info("── Processing metric: %s ──", metric_name)
            try:
                fc = self.get_forecast(
                    metric_name,
                    horizon_hours=horizon_hours,
                    lookback_hours=lookback,
                )
                breach = self.check_threshold_breach(
                    metric_name,
                    horizon_hours=horizon_hours,
                    lookback_hours=lookback,
                )
                anomalies = self.detect_anomalies(
                    metric_name,
                    lookback_hours=lookback,
                )
                results[metric_name] = {
                    "forecast": fc,
                    "breach": breach,
                    "anomalies": anomalies,
                }
            except Exception as exc:
                logger.error(
                    "Failed to process '%s': %s", metric_name, exc,
                    exc_info=True,
                )
                results[metric_name] = {"error": str(exc)}

        # Print summary
        breaches = [
            name for name, r in results.items()
            if isinstance(r, dict)
            and r.get("breach", {}).get("will_breach", False)
        ]
        if breaches:
            logger.warning(
                "⚠ %d metric(s) predicted to breach: %s",
                len(breaches), ", ".join(breaches),
            )
        else:
            logger.info("✓ All metrics within safe thresholds.")

        return results

    # ---- public: generate forecast chart ----------------------------------

    def generate_forecast_plot(
        self,
        metric: str,
        horizon_hours: int = 6,
        lookback_hours: int | None = None,
        save_path: str | None = None,
    ) -> str:
        """Generate and save a forecast visualisation chart.

        Parameters
        ----------
        metric : str
            Metric to plot.
        horizon_hours : int
            Forecast horizon.
        lookback_hours : int | None
            Training data window.
        save_path : str | None
            Custom file path. Default: ``data/forecast_{metric}.png``.

        Returns
        -------
        str
            Absolute path to the saved chart image.
        """
        result = self.get_forecast(
            metric,
            horizon_hours=horizon_hours,
            lookback_hours=lookback_hours,
        )

        if result.get("error") or result["forecast"].empty:
            logger.warning(
                "Cannot generate plot for '%s': %s",
                metric, result.get("error", "empty forecast"),
            )
            return ""

        history = result["history"]
        forecast = result["forecast"]
        now = datetime.now(timezone.utc)

        # ---- Build the chart ----
        fig, ax = plt.subplots(figsize=(14, 5))

        # Historical actuals
        ax.plot(
            history["ds"], history["y"],
            color="#2196F3", linewidth=1.5, label="Actual",
            marker=".", markersize=3, alpha=0.8,
        )

        # Forecast line
        future_mask = forecast["ds"] > history["ds"].max()
        future_fc = forecast[future_mask]
        ax.plot(
            future_fc["ds"], future_fc["yhat"],
            color="#FF5722", linewidth=2, linestyle="--",
            label="Forecast",
        )

        # Confidence band
        ax.fill_between(
            future_fc["ds"],
            future_fc["yhat_lower"],
            future_fc["yhat_upper"],
            color="#FF5722", alpha=0.15,
            label=f"{self.interval_width*100:.0f}% confidence",
        )

        # Threshold line (if available)
        threshold = DEFAULT_THRESHOLDS.get(metric)
        if threshold is not None:
            ax.axhline(
                y=threshold, color="#F44336", linestyle=":",
                linewidth=1.5, alpha=0.7, label=f"Threshold ({threshold:,.0f})",
            )

        # "Now" vertical line
        ax.axvline(
            x=now, color="#9E9E9E", linestyle="-.",
            linewidth=1, alpha=0.6, label="Now",
        )

        # Formatting
        ax.set_title(
            f"Forecast: {metric}  |  {horizon_hours}h ahead",
            fontsize=13, fontweight="bold", pad=12,
        )
        ax.set_xlabel("Time (UTC)", fontsize=10)
        ax.set_ylabel(metric.replace("_", " ").title(), fontsize=10)
        ax.legend(loc="upper left", fontsize=9, framealpha=0.9)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
        fig.autofmt_xdate(rotation=30)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()

        # Save
        if save_path is None:
            os.makedirs(_DATA_DIR, exist_ok=True)
            save_path = os.path.join(_DATA_DIR, f"forecast_{metric}.png")

        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        plt.close(fig)

        logger.info("Forecast chart saved → %s", save_path)
        return os.path.abspath(save_path)

    # ---- public: structured report for agent_explain ----------------------

    def get_forecast_summary(
        self,
        horizon_hours: int = 6,
        lookback_hours: int | None = None,
    ) -> dict[str, Any]:
        """Generate a human-readable summary dict of all forecasts.

        Designed for **agent_explain** to pass into an LLM prompt.

        Returns
        -------
        dict
            High-level summary with breaches, anomalies, and key stats.
        """
        all_results = self.forecast_all_metrics(
            horizon_hours=horizon_hours,
            lookback_hours=lookback_hours,
        )

        summary: dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "horizon_hours": horizon_hours,
            "metrics": {},
            "alerts": [],
        }

        for metric_name, data in all_results.items():
            if "error" in data and isinstance(data["error"], str):
                summary["metrics"][metric_name] = {"error": data["error"]}
                continue

            breach_info = data.get("breach", {})
            anomaly_info = data.get("anomalies", {})
            forecast_info = data.get("forecast", {})

            metric_summary = {
                "will_breach": breach_info.get("will_breach", False),
                "threshold": breach_info.get("threshold"),
                "time_to_breach": breach_info.get("time_to_breach"),
                "max_forecasted": breach_info.get("max_forecasted"),
                "min_forecasted": breach_info.get("min_forecasted"),
                "anomaly_count": anomaly_info.get("anomaly_count", 0),
                "anomaly_ratio": anomaly_info.get("anomaly_ratio", 0.0),
                "data_points": anomaly_info.get("total_points", 0),
            }
            summary["metrics"][metric_name] = metric_summary

            # Collect alerts for easy consumption
            if breach_info.get("will_breach"):
                summary["alerts"].append({
                    "type": "BREACH_PREDICTED",
                    "metric": metric_name,
                    "direction": breach_info.get("breach_direction"),
                    "threshold": breach_info.get("threshold"),
                    "time_to_breach": breach_info.get("time_to_breach"),
                    "first_breach_at": breach_info.get("first_breach_at"),
                    "severity": "HIGH",
                })

            if anomaly_info.get("anomaly_ratio", 0) > 0.1:
                summary["alerts"].append({
                    "type": "HIGH_ANOMALY_RATE",
                    "metric": metric_name,
                    "anomaly_ratio": anomaly_info["anomaly_ratio"],
                    "anomaly_count": anomaly_info["anomaly_count"],
                    "severity": "MEDIUM",
                })

        summary["total_alerts"] = len(summary["alerts"])
        return summary

    # ---- private helpers --------------------------------------------------

    @staticmethod
    def _validate_metric(metric: str) -> None:
        """Raise ValueError if the metric name is not in METRIC_MAP."""
        if metric not in METRIC_MAP:
            raise ValueError(
                f"Unknown metric '{metric}'. "
                f"Choose from: {list(METRIC_MAP.keys())}"
            )

    # ---- dunder -----------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"ForecastAgent(lookback={self.lookback_hours}h, "
            f"interval_width={self.interval_width})"
        )


# ═══════════════════════════════════════════════════════════════════════════
#  CLI entry-point
# ═══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Forecast Agent — predict Azure VM metric trends",
    )
    parser.add_argument(
        "--metric",
        type=str,
        default=None,
        help="Metric to forecast (e.g. cpu_percent). Omit to forecast all.",
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=6,
        help="Forecast horizon in hours (default: 6)",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=24,
        help="Hours of training history (default: 24)",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Save forecast chart(s) to data/",
    )
    parser.add_argument(
        "--anomalies",
        action="store_true",
        help="Run anomaly detection",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print full forecast summary (JSON)",
    )
    args = parser.parse_args()

    # Initialise agents
    monitor = MonitorAgent()
    forecaster = ForecastAgent(monitor, lookback_hours=args.lookback)

    if args.summary:
        summary = forecaster.get_forecast_summary(
            horizon_hours=args.horizon,
            lookback_hours=args.lookback,
        )
        print("\n── Forecast Summary ──")
        print(json.dumps(summary, indent=2, default=str))

    elif args.metric:
        # Single metric mode
        result = forecaster.get_forecast(
            args.metric,
            horizon_hours=args.horizon,
            lookback_hours=args.lookback,
        )
        fc = result["forecast"]
        if not fc.empty:
            print(f"\n── Forecast: {args.metric} ──")
            print(fc.tail(20).to_string(index=False))

        if args.anomalies:
            anom = forecaster.detect_anomalies(args.metric, args.lookback)
            print(f"\n── Anomalies: {anom['anomaly_count']} / "
                  f"{anom['total_points']} points ──")
            if not anom["anomalies"].empty:
                print(anom["anomalies"].to_string(index=False))

        breach = forecaster.check_threshold_breach(
            args.metric,
            horizon_hours=args.horizon,
            lookback_hours=args.lookback,
        )
        print(f"\n── Breach Check ──")
        print(f"  Will breach: {breach['will_breach']}")
        if breach["will_breach"]:
            print(f"  Direction  : {breach['breach_direction']}")
            print(f"  At         : {breach['first_breach_at']}")
            print(f"  In         : {breach['time_to_breach']}")

        if args.plot:
            path = forecaster.generate_forecast_plot(
                args.metric,
                horizon_hours=args.horizon,
                lookback_hours=args.lookback,
            )
            if path:
                print(f"\n  Chart saved → {path}")

    else:
        # All metrics mode
        results = forecaster.forecast_all_metrics(
            horizon_hours=args.horizon,
            lookback_hours=args.lookback,
        )
        for name, data in results.items():
            if "error" in data and isinstance(data["error"], str):
                print(f"\n  ✗ {name}: {data['error']}")
            else:
                breach = data.get("breach", {})
                status = "⚠ BREACH" if breach.get("will_breach") else "✓ OK"
                print(f"\n  {status} {name}")
                if breach.get("will_breach"):
                    print(f"       → {breach['breach_direction']} "
                          f"{breach['threshold']} in {breach['time_to_breach']}")

        if args.plot:
            print("\n── Generating charts ──")
            for name in METRIC_MAP:
                path = forecaster.generate_forecast_plot(
                    name,
                    horizon_hours=args.horizon,
                    lookback_hours=args.lookback,
                )
                if path:
                    print(f"  ✓ {name} → {path}")
