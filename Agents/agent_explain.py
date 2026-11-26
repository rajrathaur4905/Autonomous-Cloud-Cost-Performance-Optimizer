"""
Agent 5 — Explain Agent
=======================
Generates human-readable, LLM-powered narratives for autonomous decisions.
Consumes data from optimization decisions and deployment logs to provide a clear,
data-driven explanation of what the system did and why.

Capabilities
------------
- **Context Assembly**: Aggregates decision reasons, forecast bounds, and deployment results.
- **Prompt Engineering**: Structures context into a prompt for accurate LLM generation.
- **LLM Integration**: Calls Google Gemini API (or works offline if no key is provided).
- **Audit Logging**: Tracks explanations and LLM token costs in SQLite.

Downstream consumers
--------------------
- Streamlit Dashboard (Phase 5) reads from the explanations table.

Usage
-----
    from Agents.agent_explain import ExplainAgent
    
    agent = ExplainAgent()
    result = agent.explain_latest_decision()
"""

import logging
import os
import sys
import sqlite3
import json
from datetime import datetime, timezone

import pandas as pd
import requests

try:
    from google import genai
except ImportError:
    genai = None

# ---------------------------------------------------------------------------
# Resolve project root so `config.settings` is always importable
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config.settings import DB_PATH, GEMINI_API_KEY, LLM_MODEL
from Agents.state_machines import LLMTransportMode, VM_SIZE_CATALOG, is_placeholder_value

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("agent_explain")
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
# Constants & Configuration
# ---------------------------------------------------------------------------
LLM_TEMPERATURE = 0.3
LLM_MAX_TOKENS = 500
DEFAULT_SYSTEM_PROMPT = (
    "You are a FinOps AI auditor that explains cloud cost optimization decisions. "
    "You write clear, professional, data-driven explanations. "
    "Always include specific numbers from the context provided. "
    "Never invent or hallucinate data points that aren't in the context. "
    "Use a confident but measured tone appropriate for a technical audit log."
)


# ═══════════════════════════════════════════════════════════════════════════
#  ExplainAgent
# ═══════════════════════════════════════════════════════════════════════════
class ExplainAgent:
    def __init__(
        self,
        db_path: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self.db_path = db_path or DB_PATH
        self.api_key = api_key if api_key is not None else GEMINI_API_KEY
        self.model = model or LLM_MODEL

        if is_placeholder_value(self.api_key):
            logger.warning(
                "GEMINI_API_KEY not set or contains placeholder. ExplainAgent will work in offline mode "
                "(context assembly only, no LLM calls)."
            )
            self.transport_mode = LLMTransportMode.OFFLINE
            self.client = None
        elif genai is not None:
            self.transport_mode = LLMTransportMode.GENAI_SDK
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.transport_mode = LLMTransportMode.REST_API
            self.client = None

        self._init_db()
        logger.info(
            "ExplainAgent initialised (model='%s', transport='%s')",
            self.model, self.transport_mode.value,
        )

    def _init_db(self) -> None:
        """Create the ``explanations`` table if it does not exist."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS explanations (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp           TEXT NOT NULL,
                    decision_id         INTEGER,
                    deployment_id       INTEGER,
                    narrative           TEXT NOT NULL,
                    summary_one_liner   TEXT,
                    llm_model_used      TEXT,
                    prompt_tokens       INTEGER,
                    completion_tokens   INTEGER,
                    llm_cost_usd        REAL,
                    context_json        TEXT
                );
                """
            )
            conn.commit()
        logger.info("Explanation log database ready at %s", self.db_path)

    def _get_specs(self, size_name: str | None) -> str:
        """Look up VM specs for richer explanations."""
        if not size_name or size_name not in VM_SIZE_CATALOG:
            return "Unknown specs"
        specs = VM_SIZE_CATALOG[size_name]
        return f"{specs['vcpus']} vCPUs, {specs['ram_gb']} GB RAM"

    def _assemble_context(
        self,
        decision: dict | None = None,
        deployment: dict | None = None,
    ) -> dict:
        """Gather all relevant data into a single dict for the LLM prompt."""
        if not decision:
            decision = {}
        if not deployment:
            deployment = {}

        # Safely parse JSON strings if they come from the DB directly
        if isinstance(decision, str):
            try: decision = json.loads(decision)
            except: decision = {}
        if isinstance(deployment, str):
            try: deployment = json.loads(deployment)
            except: deployment = {}

        # Handle forecast summary which might be a JSON string from DB
        forecast_summary = decision.get("forecast_summary", {})
        if isinstance(forecast_summary, str):
            try: forecast_summary = json.loads(forecast_summary)
            except: forecast_summary = {}

        current_size = decision.get("current_size") or deployment.get("previous_size") or "Unknown"
        recommended_size = decision.get("recommended_size") or deployment.get("target_size")

        # Use the most recent timestamp available
        ts = deployment.get("timestamp") or decision.get("timestamp") or datetime.now(timezone.utc).isoformat()
        try:
            # Try to parse and reformat for humans
            dt = datetime.fromisoformat(ts)
            formatted_ts = dt.strftime("%B %d, %Y at %I:%M %p UTC")
        except:
            formatted_ts = ts

        context = {
            "timestamp": formatted_ts,
            "resource_id": decision.get("resource_id") or deployment.get("resource_id", "Unknown"),
            "action": decision.get("action", "hold"),
            "current_size": current_size,
            "current_specs": self._get_specs(current_size),
            "recommended_size": recommended_size,
            "recommended_specs": self._get_specs(recommended_size),
            "reason": decision.get("reason", "No reason provided"),
            "confidence": decision.get("confidence", "Unknown"),
            "risk_level": decision.get("risk_level", "Unknown"),
            "estimated_monthly_savings": decision.get("estimated_monthly_savings") or deployment.get("actual_savings", 0.0),
            "forecast_avg_predicted": forecast_summary.get("avg_predicted"),
            "forecast_max_upper": forecast_summary.get("max_upper"),
            "forecast_min_lower": forecast_summary.get("min_lower"),
            "deployment_status": deployment.get("status", "pending/not run"),
            "deployment_duration": deployment.get("duration_seconds", 0.0),
            "decision_id": decision.get("id"),
            "deployment_id": deployment.get("id")
        }
        return context

    def _build_prompt(self, context: dict) -> str:
        """Construct the LLM user prompt from the assembled context."""
        return f"""Explain the following cloud infrastructure optimization action.

ACTION CONTEXT:
- Resource: {context['resource_id']}
- Action Taken: {context['action']}
- Current VM: {context['current_size']} ({context['current_specs']})
- Target VM: {context.get('recommended_size', 'N/A')} ({context.get('recommended_specs', 'N/A')})
- Executed At: {context['timestamp']}
- Deployment Status: {context.get('deployment_status', 'pending')}

FORECAST DATA:
- Average Predicted CPU (next 4h): {context.get('forecast_avg_predicted', 'N/A')}%
- Worst-Case CPU (upper bound): {context.get('forecast_max_upper', 'N/A')}%
- Best-Case CPU (lower bound): {context.get('forecast_min_lower', 'N/A')}%

DECISION:
- Reason: {context.get('reason', 'N/A')}
- Confidence: {context.get('confidence', 'N/A')}
- Risk Level: {context.get('risk_level', 'N/A')}
- Estimated Monthly Savings: ₹{context.get('estimated_monthly_savings', 0):.0f}

Write a clear, professional explanation in 3-4 sentences that:
1. States WHAT was done and WHEN
2. Explains WHY based on the forecast data (cite specific numbers)
3. Quantifies the expected cost savings in ₹
4. Assesses the risk level with brief justification"""

    def _log_explanation(self, result: dict) -> None:
        """Insert the generated explanation into the database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO explanations (
                    timestamp, decision_id, deployment_id, narrative, 
                    summary_one_liner, llm_model_used, prompt_tokens, 
                    completion_tokens, llm_cost_usd, context_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result["timestamp"],
                    result.get("decision_id"),
                    result.get("deployment_id"),
                    result["narrative"],
                    result["summary_one_liner"],
                    result["llm_model_used"],
                    result["prompt_tokens"],
                    result["completion_tokens"],
                    result["llm_cost_usd"],
                    json.dumps(result["context"])
                )
            )
            conn.commit()

    def _candidate_models(self) -> list[str]:
        """Return ordered Gemini model IDs to try without mutating ``self.model``."""
        models = [self.model]
        for fb in ("gemini-flash-lite-latest", "gemini-3-flash-preview"):
            if fb not in models:
                models.append(fb)
        return models

    def _call_rest_api(self, full_prompt: str) -> tuple[str, str, int, int, str]:
        """Call Gemini REST endpoint with model fallback."""
        last_status = None
        last_text = ""
        for model_name in self._candidate_models():
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
            headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
            payload = {
                "contents": [{"parts": [{"text": full_prompt}]}],
                "generationConfig": {
                    "temperature": LLM_TEMPERATURE,
                    "maxOutputTokens": LLM_MAX_TOKENS,
                },
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            last_status = resp.status_code
            last_text = resp.text
            if resp.status_code == 200:
                data = resp.json()
                narrative = ""
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        narrative = parts[0].get("text", "").strip()
                usage = data.get("usageMetadata", {})
                prompt_tokens = usage.get("promptTokenCount", 0)
                completion_tokens = usage.get("candidatesTokenCount", 0)
                logger.info("Generated explanation via Gemini REST using '%s'.", model_name)
                return narrative, model_name, prompt_tokens, completion_tokens, "ok"
            logger.warning("Gemini model '%s' returned status %d. Trying next...", model_name, resp.status_code)

        logger.error("All Gemini REST models failed. Last error: %s", last_text)
        return (
            f"[LLM ERROR] Gemini API returned HTTP {last_status}: {last_text}",
            self.model,
            0,
            0,
            "error",
        )

    def _call_genai_sdk(self, full_prompt: str) -> tuple[str, str, int, int, str]:
        """Call Gemini via google.genai SDK with unified model fallback."""
        last_exc: Exception | None = None
        for model_name in self._candidate_models():
            try:
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=full_prompt,
                    config=genai.types.GenerateContentConfig(
                        temperature=LLM_TEMPERATURE,
                        max_output_tokens=LLM_MAX_TOKENS,
                    ),
                )
                narrative = response.text.strip()
                prompt_tokens = 0
                completion_tokens = 0
                if response.usage_metadata:
                    prompt_tokens = response.usage_metadata.prompt_token_count or 0
                    completion_tokens = response.usage_metadata.candidates_token_count or 0
                logger.info("Generated explanation via Gemini SDK using '%s'.", model_name)
                return narrative, model_name, prompt_tokens, completion_tokens, "ok"
            except Exception as exc:
                last_exc = exc
                logger.warning("Gemini SDK model '%s' failed: %s. Trying next...", model_name, exc)

        logger.error("All Gemini SDK models failed: %s", last_exc)
        return (
            f"[LLM ERROR] Unable to generate explanation: {last_exc}",
            self.model,
            0,
            0,
            "error",
        )

    def explain(
        self,
        decision: dict | None = None,
        deployment: dict | None = None,
    ) -> dict:
        """Core logic to generate explanation from decision/deployment data."""
        logger.info("Assembling context for explanation...")

        # 1. Assemble context
        context = self._assemble_context(decision, deployment)

        # 2. Build prompt
        user_prompt = self._build_prompt(context)

        # 3. Dispatch based on explicit LLMTransportMode
        prompt_tokens = 0
        completion_tokens = 0
        model_used = LLMTransportMode.OFFLINE.value
        llm_status = "offline"

        if self.transport_mode == LLMTransportMode.OFFLINE:
            logger.info("Offline mode active. Generating fallback explanation.")
            narrative = f"[OFFLINE MODE — LLM not called]\n\nPrompt that would be sent:\n{user_prompt}"
        elif self.transport_mode == LLMTransportMode.REST_API:
            logger.info("Calling Google Gemini API (%s) via REST...", self.model)
            full_prompt = f"{DEFAULT_SYSTEM_PROMPT}\n\n{user_prompt}"
            try:
                narrative, model_used, prompt_tokens, completion_tokens, llm_status = self._call_rest_api(full_prompt)
            except Exception as e:
                logger.error("Failed to generate explanation from Gemini REST: %s", e)
                narrative = f"[LLM ERROR] Unable to generate explanation: {e}"
                model_used = self.model
                llm_status = "error"
        else:
            logger.info("Calling Google Gemini API (%s) via SDK...", self.model)
            full_prompt = f"{DEFAULT_SYSTEM_PROMPT}\n\n{user_prompt}"
            narrative, model_used, prompt_tokens, completion_tokens, llm_status = self._call_genai_sdk(full_prompt)

        # 4. Generate summary one-liner
        action_str = str(context.get('action', 'unknown')).upper()
        savings_str = f"₹{context.get('estimated_monthly_savings', 0):.0f}"
        target = context.get('recommended_size', 'N/A')
        summary = f"{action_str}: {context['current_size']} → {target} | Savings: {savings_str}/mo"

        # 5. Calculate cost (Gemini Flash pricing: ~$0.075/1M input, ~$0.30/1M output)
        input_cost = (prompt_tokens / 1_000_000) * 0.075
        output_cost = (completion_tokens / 1_000_000) * 0.30
        llm_cost = input_cost + output_cost

        # 6. Package and log
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "decision_id": context.get("decision_id"),
            "deployment_id": context.get("deployment_id"),
            "narrative": narrative,
            "summary_one_liner": summary,
            "llm_model_used": model_used,
            "llm_transport_mode": self.transport_mode.value,
            "llm_status": llm_status,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "llm_cost_usd": round(llm_cost, 6),
            "context": context,
        }

        self._log_explanation(result)
        return result

    def get_explanation_history(self, limit: int = 20) -> pd.DataFrame:
        """Return the most recent explanations."""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(
                '''
                SELECT * FROM explanations 
                ORDER BY timestamp DESC LIMIT ?
                ''',
                conn,
                params=(limit,)
            )
        return df
        
    def explain_latest_decision(self) -> dict | None:
        """Reads the latest decision and deployment logs and explains them."""
        logger.info("Fetching latest decision and deployment from DB...")
        with sqlite3.connect(self.db_path) as conn:
            # Use row_factory to get dicts instead of tuples
            conn.row_factory = sqlite3.Row
            
            # Get latest decision
            decision_cur = conn.execute(
                "SELECT * FROM optimization_decisions ORDER BY timestamp DESC LIMIT 1"
            )
            decision_row = decision_cur.fetchone()
            if not decision_row:
                logger.warning("No decisions found in database.")
                return None
                
            decision_dict = dict(decision_row)
            
            # Try to get associated deployment log (if any)
            deployment_cur = conn.execute(
                "SELECT * FROM deployment_logs WHERE decision_id = ? ORDER BY timestamp DESC LIMIT 1",
                (decision_dict["id"],)
            )
            deployment_row = deployment_cur.fetchone()
            deployment_dict = dict(deployment_row) if deployment_row else None
            
            return self.explain(decision_dict, deployment_dict)


# ═══════════════════════════════════════════════════════════════════════════
#  CLI entry-point
# ═══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Explain Agent — generate narratives for autonomous actions",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Explain the most recent decision in the database",
    )
    parser.add_argument(
        "--decision-json",
        type=str,
        default=None,
        help="Provide a JSON string representing a decision",
    )
    parser.add_argument(
        "--deployment-json",
        type=str,
        default=None,
        help="Provide a JSON string representing a deployment log (optional)",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Print the explanation history instead of generating a new one",
    )
    args = parser.parse_args()

    agent = ExplainAgent()

    if args.history:
        history_df = agent.get_explanation_history()
        print("\n── Recent Explanations ──")
        if history_df.empty:
            print("No explanations recorded yet.")
        else:
            print(history_df.to_string(index=False))
            
    elif args.latest:
        print("\n── Generating Explanation for Latest Decision ──")
        result = agent.explain_latest_decision()
        if result:
            print("\nNARRATIVE:\n" + "="*80)
            print(result["narrative"])
            print("="*80 + "\n")
            print(f"Summary: {result['summary_one_liner']}")
            print(f"Cost: ${result['llm_cost_usd']:.6f} ({result['prompt_tokens']} + {result['completion_tokens']} tokens)")
            
    elif args.decision_json:
        try:
            decision = json.loads(args.decision_json)
            deployment = json.loads(args.deployment_json) if args.deployment_json else None
            print(f"\n── Explaining Provided JSON ──")
            result = agent.explain(decision, deployment)
            print("\nNARRATIVE:\n" + "="*80)
            print(result["narrative"])
            print("="*80)
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON: {e}")
            
    else:
        print("\nPlease provide --latest, --decision-json, or --history")
