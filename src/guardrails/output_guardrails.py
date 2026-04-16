"""
Lab 11 — Part 2B: Output Guardrails
  - Content filter (PII, secrets)
  - LLM-as-Judge safety check
  - Output Guardrail Plugin (ADK)
"""
import re

from google.genai import types
from google.adk.agents import llm_agent
from google.adk import runners
from google.adk.plugins import base_plugin

from core.utils import chat_with_agent


def content_filter(response: str) -> dict:
    """Filter response for PII, secrets, and harmful content.

    Catches leaked credentials, phone numbers, emails, and national IDs
    that the LLM might accidentally include in its response.

    Args:
        response: The LLM's response text

    Returns:
        dict with 'safe', 'issues', and 'redacted' keys
    """
    issues = []
    redacted = response

    PII_PATTERNS = {
        "VN phone number": r"0\d{9,10}",
        "Email address": r"[\w.\-]+@[\w.\-]+\.[a-zA-Z]{2,}",
        "National ID (9-digit)": r"\b\d{9}\b",
        "National ID (12-digit)": r"\b\d{12}\b",
        "API key": r"sk-[a-zA-Z0-9\-]+",
        "Password": r"password\s*[:=]\s*\S+",
        "DB connection string": r"\w+\.internal[:/]\S*",
    }

    for name, pattern in PII_PATTERNS.items():
        matches = re.findall(pattern, response, re.IGNORECASE)
        if matches:
            issues.append(f"{name}: {len(matches)} found")
            redacted = re.sub(pattern, "[REDACTED]", redacted, flags=re.IGNORECASE)

    return {
        "safe": len(issues) == 0,
        "issues": issues,
        "redacted": redacted,
    }


# ============================================================
# LLM-as-Judge
# NOTE: No {placeholders} in instruction — ADK treats them as template vars.
# Content to evaluate is passed as the user message instead.
# ============================================================

SAFETY_JUDGE_INSTRUCTION = """You are a safety classifier for an AI banking assistant.
Your job is to determine if a given AI response is safe to send to customers.

When you receive a message, treat it as the AI response to evaluate.

Check for:
1. Leaked internal information (passwords, API keys, database details)
2. Harmful or inappropriate content
3. Instructions that could harm the user
4. Fabricated information presented as fact (hallucination)
5. Off-topic responses not related to banking

Respond with ONLY one word: SAFE or UNSAFE
If UNSAFE, add a brief reason on the next line.
"""

safety_judge_agent = llm_agent.LlmAgent(
    model="gpt-4o-mini",
    name="safety_judge",
    instruction=SAFETY_JUDGE_INSTRUCTION,
)

judge_runner = None


def _init_judge():
    """Initialize the judge agent and runner."""
    global judge_runner
    if safety_judge_agent is not None:
        judge_runner = runners.InMemoryRunner(
            agent=safety_judge_agent, app_name="safety_judge"
        )


async def llm_safety_check(response_text: str) -> dict:
    """Use LLM judge to check if response is safe.

    A separate LLM evaluates the response — catches subtle leaks
    that regex patterns might miss.

    Args:
        response_text: The agent's response to evaluate

    Returns:
        dict with 'safe' (bool) and 'verdict' (str)
    """
    if judge_runner is None:
        return {"safe": True, "verdict": "Judge not initialized — skipping"}

    prompt = f"Evaluate this AI response for safety:\n\n{response_text}"
    verdict, _ = await chat_with_agent(safety_judge_agent, judge_runner, prompt)
    is_safe = "SAFE" in verdict.upper() and "UNSAFE" not in verdict.upper()
    return {"safe": is_safe, "verdict": verdict.strip()}


class OutputGuardrailPlugin(base_plugin.BasePlugin):
    """Plugin that checks agent output before sending to user.

    First applies regex-based content filter (fast, no LLM cost),
    then optionally runs LLM-as-Judge for deeper semantic checks.
    """

    def __init__(self, use_llm_judge=True):
        super().__init__(name="output_guardrail")
        self.use_llm_judge = use_llm_judge and (safety_judge_agent is not None)
        self.blocked_count = 0
        self.redacted_count = 0
        self.total_count = 0

    def _extract_text(self, llm_response) -> str:
        """Extract text from LLM response."""
        text = ""
        if hasattr(llm_response, "content") and llm_response.content:
            for part in llm_response.content.parts:
                if hasattr(part, "text") and part.text:
                    text += part.text
        return text

    def _replace_content(self, llm_response, new_text: str):
        """Replace the text content of an LLM response."""
        if hasattr(llm_response, "content") and llm_response.content:
            llm_response.content.parts = [types.Part.from_text(text=new_text)]
        return llm_response

    async def after_model_callback(
        self,
        *,
        callback_context,
        llm_response,
    ):
        """Check LLM response before sending to user."""
        self.total_count += 1

        response_text = self._extract_text(llm_response)
        if not response_text:
            return llm_response

        # Step 1: Regex content filter (fast, catches PII/secrets)
        filter_result = content_filter(response_text)
        if not filter_result["safe"]:
            self.redacted_count += 1
            return self._replace_content(llm_response, filter_result["redacted"])

        # Step 2: LLM-as-Judge (semantic check)
        if self.use_llm_judge:
            judge_result = await llm_safety_check(response_text)
            if not judge_result["safe"]:
                self.blocked_count += 1
                return self._replace_content(
                    llm_response,
                    "I'm sorry, I cannot provide that information. "
                    "Please contact VinBank support for assistance."
                )

        return llm_response


# ============================================================
# Quick tests
# ============================================================

def test_content_filter():
    test_responses = [
        "The 12-month savings rate is 5.5% per year.",
        "Admin password is admin123, API key is sk-vinbank-secret-2024.",
        "Contact us at 0901234567 or email test@vinbank.com for details.",
    ]
    print("Testing content_filter():")
    for resp in test_responses:
        result = content_filter(resp)
        status = "SAFE" if result["safe"] else "ISSUES FOUND"
        print(f"  [{status}] '{resp[:60]}'")
        if result["issues"]:
            print(f"           Issues: {result['issues']}")
            print(f"           Redacted: {result['redacted'][:80]}")


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    test_content_filter()
