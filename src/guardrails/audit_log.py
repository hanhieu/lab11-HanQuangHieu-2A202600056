"""
Assignment 11 — Audit Log & Monitoring Plugin

Records every interaction (input, output, which layer blocked, latency).
Fires alerts when block rate, rate-limit hits, or judge fail rate
exceed configured thresholds.
"""
import json
import time
from datetime import datetime, timezone

from google.genai import types
from google.adk.plugins import base_plugin
from google.adk.agents.invocation_context import InvocationContext


class AuditLogPlugin(base_plugin.BasePlugin):
    """Records every request/response pair with metadata.

    Provides full traceability: who sent what, what the agent replied,
    which layer (if any) blocked the request, and how long it took.
    Essential for compliance, debugging, and post-incident analysis.
    """

    def __init__(self):
        super().__init__(name="audit_log")
        self.logs: list[dict] = []
        self._pending: dict[str, dict] = {}  # session_id -> pending entry

    def _get_session_id(self, context) -> str:
        if context and hasattr(context, "session") and context.session:
            return str(context.session.id)
        return f"session_{len(self.logs)}"

    def _extract_text(self, content) -> str:
        text = ""
        if content and hasattr(content, "parts") and content.parts:
            for part in content.parts:
                if hasattr(part, "text") and part.text:
                    text += part.text
        return text

    async def on_user_message_callback(
        self,
        *,
        invocation_context: InvocationContext,
        user_message: types.Content,
    ) -> types.Content | None:
        """Record incoming message and start timing. Never blocks."""
        session_id = self._get_session_id(invocation_context)
        user_text = self._extract_text(user_message)

        self._pending[session_id] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "user_input": user_text,
            "agent_output": None,
            "blocked_by": None,
            "latency_ms": None,
            "_start_time": time.time(),
        }
        return None  # Never block

    async def after_model_callback(self, *, callback_context, llm_response):
        """Record agent output and finalize the log entry."""
        session_id = self._get_session_id(callback_context)
        response_text = self._extract_text(
            llm_response.content if hasattr(llm_response, "content") else None
        )

        entry = self._pending.pop(session_id, {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "user_input": "",
            "blocked_by": None,
            "_start_time": time.time(),
        })

        entry["agent_output"] = response_text
        entry["latency_ms"] = round((time.time() - entry.pop("_start_time", time.time())) * 1000, 1)
        self.logs.append(entry)

        return llm_response  # Never modify

    def log_blocked(self, session_id: str, blocked_by: str, user_input: str, message: str):
        """Log a request that was blocked before reaching the LLM."""
        self.logs.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "user_input": user_input,
            "agent_output": message,
            "blocked_by": blocked_by,
            "latency_ms": 0,
        })

    def export_json(self, filepath: str = "audit_log.json"):
        """Export all logs to a JSON file."""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.logs, f, indent=2, default=str, ensure_ascii=False)
        print(f"Audit log exported: {filepath} ({len(self.logs)} entries)")

    def get_summary(self) -> dict:
        """Return aggregate statistics from the audit log."""
        total = len(self.logs)
        blocked = sum(1 for e in self.logs if e.get("blocked_by"))
        latencies = [e["latency_ms"] for e in self.logs if e.get("latency_ms")]
        avg_latency = round(sum(latencies) / len(latencies), 1) if latencies else 0

        blocked_by_layer: dict[str, int] = {}
        for e in self.logs:
            layer = e.get("blocked_by")
            if layer:
                blocked_by_layer[layer] = blocked_by_layer.get(layer, 0) + 1

        return {
            "total_requests": total,
            "blocked_requests": blocked,
            "block_rate": round(blocked / total, 3) if total else 0.0,
            "avg_latency_ms": avg_latency,
            "blocked_by_layer": blocked_by_layer,
        }


class MonitoringAlert:
    """Monitors pipeline metrics and fires alerts when thresholds are exceeded.

    Tracks block rate, rate-limit hits, and judge fail rate.
    In production this would send to PagerDuty/Slack/CloudWatch.
    Here it prints alerts to stdout and stores them in memory.
    """

    def __init__(
        self,
        block_rate_threshold: float = 0.3,
        rate_limit_threshold: int = 5,
        judge_fail_threshold: float = 0.2,
    ):
        self.block_rate_threshold = block_rate_threshold
        self.rate_limit_threshold = rate_limit_threshold
        self.judge_fail_threshold = judge_fail_threshold
        self.alerts: list[dict] = []

    def _fire_alert(self, level: str, message: str, metric: str, value):
        alert = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "message": message,
            "metric": metric,
            "value": value,
        }
        self.alerts.append(alert)
        print(f"[ALERT {level}] {message} (metric={metric}, value={value})")

    def check_metrics(
        self,
        audit_plugin: AuditLogPlugin = None,
        rate_limit_plugin=None,
        judge_plugin=None,
    ) -> list[dict]:
        """Check all metrics and fire alerts if thresholds are exceeded.

        Args:
            audit_plugin: AuditLogPlugin instance
            rate_limit_plugin: RateLimitPlugin instance
            judge_plugin: LlmJudgePlugin instance

        Returns:
            List of fired alerts
        """
        fired = []

        if audit_plugin:
            summary = audit_plugin.get_summary()
            if summary["block_rate"] > self.block_rate_threshold:
                self._fire_alert(
                    "WARNING",
                    f"High block rate: {summary['block_rate']:.0%} of requests blocked",
                    "block_rate",
                    summary["block_rate"],
                )
                fired.append(self.alerts[-1])

        if rate_limit_plugin:
            stats = rate_limit_plugin.get_stats()
            if stats["blocked_requests"] >= self.rate_limit_threshold:
                self._fire_alert(
                    "WARNING",
                    f"Rate limit triggered {stats['blocked_requests']} times",
                    "rate_limit_hits",
                    stats["blocked_requests"],
                )
                fired.append(self.alerts[-1])

        if judge_plugin:
            stats = judge_plugin.get_stats()
            if stats["fail_rate"] > self.judge_fail_threshold:
                self._fire_alert(
                    "CRITICAL",
                    f"LLM judge fail rate: {stats['fail_rate']:.0%}",
                    "judge_fail_rate",
                    stats["fail_rate"],
                )
                fired.append(self.alerts[-1])

        if not fired:
            print("[MONITORING] All metrics within normal thresholds.")

        return fired
