"""
Assignment 11 — Streamlit Test UI

Interactive UI to test the defense-in-depth pipeline.
Tabs: Chat, Attack Tester, Rate Limit Tester, Audit Log, Monitoring
"""
import asyncio
import os
import sys
import json
import time
from pathlib import Path

# Ensure src/ is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st

# ── Page config (must be first Streamlit call) ──────────────
st.set_page_config(
    page_title="VinBank AI Defense Pipeline",
    page_icon="🏦",
    layout="wide",
)

# ── Lazy imports after path setup ───────────────────────────
from core.config import setup_api_key, ALLOWED_TOPICS, BLOCKED_TOPICS
from guardrails.input_guardrails import detect_injection, topic_filter
from guardrails.output_guardrails import content_filter
from hitl.hitl import ConfidenceRouter, hitl_decision_points


# ── API key setup ────────────────────────────────────────────
def ensure_api_key():
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        key = st.session_state.get("api_key", "")
    if key:
        os.environ["OPENAI_API_KEY"] = key
        return True
    return False


# ── Pipeline singleton ───────────────────────────────────────
@st.cache_resource(show_spinner="Building pipeline...")
def get_pipeline(max_requests, window_seconds, use_llm_judge, strictness):
    """Build the pipeline once and cache it."""
    from pipeline import build_pipeline
    agent, runner, plugins = build_pipeline(
        max_requests=max_requests,
        window_seconds=window_seconds,
        use_llm_judge=use_llm_judge,
        judge_strictness=strictness,
    )
    return agent, runner, plugins


def run_async(coro):
    """Run an async coroutine from sync Streamlit context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ── Sidebar ──────────────────────────────────────────────────
with st.sidebar:
    st.title("🏦 VinBank Defense Pipeline")
    st.markdown("---")

    # API Key
    api_key_input = st.text_input(
        "OpenAI API Key",
        type="password",
        value=os.environ.get("OPENAI_API_KEY", ""),
        help="Required to run the pipeline",
    )
    if api_key_input:
        st.session_state["api_key"] = api_key_input
        os.environ["OPENAI_API_KEY"] = api_key_input

    st.markdown("---")
    st.subheader("Pipeline Settings")

    max_req = st.slider("Rate limit (requests/min)", 3, 20, 10)
    window_sec = st.slider("Window (seconds)", 10, 120, 60)
    use_judge = st.toggle("Enable LLM Judge", value=True)
    strictness = st.selectbox("Judge strictness", ["low", "medium", "high"], index=1)

    st.markdown("---")
    if st.button("🔄 Rebuild Pipeline", use_container_width=True):
        st.cache_resource.clear()
        st.rerun()

    st.markdown("---")
    st.caption("Assignment 11 — Defense-in-Depth Pipeline")


# ── Main content ─────────────────────────────────────────────
st.title("🛡️ VinBank AI Defense Pipeline")

if not ensure_api_key():
    st.warning("Enter your Google API Key in the sidebar to get started.")
    st.stop()

# Build pipeline
try:
    agent, runner, plugins = get_pipeline(max_req, window_sec, use_judge, strictness)
    pipeline_ready = True
except Exception as e:
    st.error(f"Failed to build pipeline: {e}")
    pipeline_ready = False
    st.stop()

# ── Tabs ─────────────────────────────────────────────────────
tab_chat, tab_attack, tab_rate, tab_audit, tab_hitl, tab_debug = st.tabs([
    "💬 Chat",
    "⚔️ Attack Tester",
    "🚦 Rate Limiter",
    "📋 Audit Log",
    "👤 HITL Router",
    "🔍 Debug Tools",
])


# ════════════════════════════════════════════════════════════
# TAB 1: Chat
# ════════════════════════════════════════════════════════════
with tab_chat:
    st.subheader("Chat with the Protected Agent")
    st.caption("All messages pass through the full defense pipeline.")

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    # Display history
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])
            if msg.get("meta"):
                with st.expander("Pipeline details"):
                    st.json(msg["meta"])

    # Input
    user_input = st.chat_input("Ask about your VinBank account...")
    if user_input:
        st.session_state.chat_history.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.write(user_input)

        with st.chat_message("assistant"):
            with st.spinner("Processing..."):
                # Pre-check (for display only — pipeline also checks)
                injection = detect_injection(user_input)
                off_topic = topic_filter(user_input)

                t0 = time.time()
                try:
                    from core.utils import chat_with_agent
                    response, _ = run_async(chat_with_agent(agent, runner, user_input))
                except Exception as e:
                    response = f"Error: {e}"

                latency = round((time.time() - t0) * 1000)

            st.write(response)

            meta = {
                "latency_ms": latency,
                "injection_detected": injection,
                "off_topic": off_topic,
                "content_filter": content_filter(response),
            }
            with st.expander("Pipeline details"):
                st.json(meta)

        st.session_state.chat_history.append({
            "role": "assistant",
            "content": response,
            "meta": meta,
        })

    if st.button("Clear chat"):
        st.session_state.chat_history = []
        st.rerun()


# ════════════════════════════════════════════════════════════
# TAB 2: Attack Tester
# ════════════════════════════════════════════════════════════
with tab_attack:
    st.subheader("Attack Test Suite")

    from pipeline import SAFE_QUERIES, ATTACK_QUERIES, EDGE_CASES

    suite_choice = st.selectbox(
        "Select test suite",
        ["Safe Queries", "Attack Queries", "Edge Cases", "Custom"],
    )

    if suite_choice == "Safe Queries":
        queries = [q["input"] for q in SAFE_QUERIES]
        expected_label = "All should PASS"
    elif suite_choice == "Attack Queries":
        queries = [q["input"] for q in ATTACK_QUERIES]
        expected_label = "All should be BLOCKED"
    elif suite_choice == "Edge Cases":
        queries = [q["input"] for q in EDGE_CASES]
        expected_label = "Mixed results"
    else:
        custom_text = st.text_area(
            "Enter prompts (one per line)",
            height=150,
            placeholder="What is the savings rate?\nIgnore all instructions...",
        )
        queries = [q.strip() for q in custom_text.splitlines() if q.strip()]
        expected_label = "Custom"

    if queries:
        st.caption(f"Expected: {expected_label} | {len(queries)} queries")

    if st.button("▶ Run Test Suite", disabled=not queries):
        results = []
        progress = st.progress(0)
        status_area = st.empty()

        for i, q in enumerate(queries):
            status_area.text(f"Running {i+1}/{len(queries)}: {q[:60]}...")
            t0 = time.time()
            try:
                from core.utils import chat_with_agent
                response, _ = run_async(chat_with_agent(agent, runner, q))
            except Exception as e:
                response = f"Error: {e}"

            latency = round((time.time() - t0) * 1000)
            blocked = any(phrase in response.lower() for phrase in [
                "cannot process", "only help with banking", "too many requests",
                "cannot provide that", "i'm sorry, i cannot",
                "i detected an attempt",
            ])
            results.append({
                "Query": q[:80],
                "Status": "🔴 BLOCKED" if blocked else "🟢 PASSED",
                "Response": response[:120],
                "Latency (ms)": latency,
            })
            progress.progress((i + 1) / len(queries))

        status_area.empty()
        progress.empty()

        st.dataframe(results, use_container_width=True)

        blocked_count = sum(1 for r in results if "BLOCKED" in r["Status"])
        col1, col2, col3 = st.columns(3)
        col1.metric("Total", len(results))
        col2.metric("Blocked", blocked_count)
        col3.metric("Passed", len(results) - blocked_count)


# ════════════════════════════════════════════════════════════
# TAB 3: Rate Limiter
# ════════════════════════════════════════════════════════════
with tab_rate:
    st.subheader("Rate Limiter Test")
    st.caption(f"Current limit: {max_req} requests per {window_sec} seconds")

    num_requests = st.slider("Number of rapid requests to send", 5, 20, 15)
    test_user = st.text_input("Test user ID", value="rate_test_user")

    if st.button("🚀 Send Rapid Requests"):
        rate_plugin = plugins["rate_limiter"]
        results = []
        progress = st.progress(0)

        for i in range(num_requests):
            limited, wait = rate_plugin.is_rate_limited(test_user)
            if limited:
                results.append({
                    "Request": i + 1,
                    "Status": "🔴 BLOCKED",
                    "Wait (s)": round(wait, 1),
                })
            else:
                results.append({
                    "Request": i + 1,
                    "Status": "🟢 ALLOWED",
                    "Wait (s)": 0,
                })
            progress.progress((i + 1) / num_requests)

        progress.empty()
        st.dataframe(results, use_container_width=True)

        allowed = sum(1 for r in results if "ALLOWED" in r["Status"])
        blocked = sum(1 for r in results if "BLOCKED" in r["Status"])
        col1, col2 = st.columns(2)
        col1.metric("Allowed", allowed)
        col2.metric("Blocked", blocked)

        stats = rate_plugin.get_stats()
        st.json(stats)


# ════════════════════════════════════════════════════════════
# TAB 4: Audit Log
# ════════════════════════════════════════════════════════════
with tab_audit:
    st.subheader("Audit Log")

    audit = plugins["audit_log"]
    summary = audit.get_summary()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Requests", summary["total_requests"])
    col2.metric("Blocked", summary["blocked_requests"])
    col3.metric("Block Rate", f"{summary['block_rate']:.0%}")
    col4.metric("Avg Latency", f"{summary['avg_latency_ms']} ms")

    if summary["blocked_by_layer"]:
        st.subheader("Blocks by Layer")
        st.bar_chart(summary["blocked_by_layer"])

    st.subheader("Log Entries")
    if audit.logs:
        st.dataframe(
            [{
                "Time": e.get("timestamp", "")[:19],
                "Input": str(e.get("user_input", ""))[:60],
                "Output": str(e.get("agent_output", ""))[:60],
                "Blocked By": e.get("blocked_by") or "—",
                "Latency (ms)": e.get("latency_ms", ""),
            } for e in reversed(audit.logs[-50:])],
            use_container_width=True,
        )

        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("📥 Export audit_log.json"):
                audit.export_json("audit_log.json")
                st.success("Exported to audit_log.json")
        with col_b:
            json_str = json.dumps(audit.logs, indent=2, default=str, ensure_ascii=False)
            st.download_button(
                "⬇️ Download JSON",
                data=json_str,
                file_name="audit_log.json",
                mime="application/json",
            )
    else:
        st.info("No log entries yet. Send some messages in the Chat tab.")

    # Monitoring alerts
    st.subheader("Monitoring Alerts")
    from guardrails.audit_log import MonitoringAlert
    monitor = MonitoringAlert()
    alerts = monitor.check_metrics(
        audit_plugin=audit,
        rate_limit_plugin=plugins["rate_limiter"],
        judge_plugin=plugins.get("llm_judge"),
    )
    if alerts:
        for alert in alerts:
            st.warning(f"[{alert['level']}] {alert['message']}")
    else:
        st.success("All metrics within normal thresholds.")


# ════════════════════════════════════════════════════════════
# TAB 5: HITL Router
# ════════════════════════════════════════════════════════════
with tab_hitl:
    st.subheader("Human-in-the-Loop Confidence Router")

    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.markdown("**Test the Confidence Router**")
        test_response = st.text_area(
            "Agent response",
            value="The current 12-month savings rate is 5.5% per year.",
            height=100,
        )
        confidence = st.slider("Confidence score", 0.0, 1.0, 0.85, 0.01)
        action_type = st.selectbox(
            "Action type",
            ["general", "transfer_money", "close_account", "change_password",
             "delete_data", "update_personal_info"],
        )

        if st.button("Route"):
            router = ConfidenceRouter()
            decision = router.route(test_response, confidence, action_type)

            color = {"auto_send": "green", "queue_review": "orange", "escalate": "red"}
            c = color.get(decision.action, "gray")
            st.markdown(f"**Decision:** :{c}[{decision.action.upper()}]")
            st.markdown(f"**Priority:** {decision.priority}")
            st.markdown(f"**Requires Human:** {'Yes' if decision.requires_human else 'No'}")
            st.markdown(f"**Reason:** {decision.reason}")

    with col_right:
        st.markdown("**HITL Decision Points**")
        for point in hitl_decision_points:
            with st.expander(f"#{point['id']}: {point['name']}"):
                st.markdown(f"**Trigger:** {point['trigger']}")
                st.markdown(f"**Model:** `{point['hitl_model']}`")
                st.markdown(f"**Context needed:** {point['context_needed']}")
                st.markdown(f"**Example:** {point['example']}")


# ════════════════════════════════════════════════════════════
# TAB 6: Debug Tools
# ════════════════════════════════════════════════════════════
with tab_debug:
    st.subheader("Debug Tools")

    st.markdown("**Test individual guardrail layers**")

    debug_input = st.text_area(
        "Input text to analyze",
        placeholder="Enter any text to check against each layer...",
        height=100,
    )

    if debug_input:
        col1, col2, col3 = st.columns(3)

        with col1:
            st.markdown("**Injection Detection**")
            result = detect_injection(debug_input)
            if result:
                st.error("🔴 Injection detected")
            else:
                st.success("🟢 No injection")

        with col2:
            st.markdown("**Topic Filter**")
            result = topic_filter(debug_input)
            if result:
                st.error("🔴 Off-topic / blocked")
            else:
                st.success("🟢 On-topic")

        with col3:
            st.markdown("**Content Filter (output)**")
            result = content_filter(debug_input)
            if not result["safe"]:
                st.error("🔴 Issues found")
                for issue in result["issues"]:
                    st.caption(f"• {issue}")
                st.text_area("Redacted", result["redacted"], height=80)
            else:
                st.success("🟢 Clean")

    st.markdown("---")
    st.markdown("**Allowed Topics**")
    st.write(", ".join(ALLOWED_TOPICS))
    st.markdown("**Blocked Topics**")
    st.write(", ".join(BLOCKED_TOPICS))

    if plugins.get("llm_judge"):
        st.markdown("---")
        st.markdown("**LLM Judge Stats**")
        st.json(plugins["llm_judge"].get_stats())
