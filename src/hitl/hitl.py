"""
Lab 11 — Part 4: Human-in-the-Loop Design
  - Confidence Router
  - 3 HITL decision points
"""
from dataclasses import dataclass


HIGH_RISK_ACTIONS = [
    "transfer_money",
    "close_account",
    "change_password",
    "delete_data",
    "update_personal_info",
]


@dataclass
class RoutingDecision:
    """Result of the confidence router."""
    action: str          # "auto_send", "queue_review", "escalate"
    confidence: float
    reason: str
    priority: str        # "low", "normal", "high"
    requires_human: bool


class ConfidenceRouter:
    """Route agent responses based on confidence and risk level.

    High-risk actions (money transfers, account changes) always escalate
    to a human regardless of confidence — the cost of a mistake is too high.
    Low-confidence responses queue for review before reaching the customer.

    Thresholds:
        HIGH:   confidence >= 0.9 -> auto-send
        MEDIUM: 0.7 <= confidence < 0.9 -> queue for review
        LOW:    confidence < 0.7 -> escalate to human
    """

    HIGH_THRESHOLD = 0.9
    MEDIUM_THRESHOLD = 0.7

    def route(self, response: str, confidence: float,
              action_type: str = "general") -> RoutingDecision:
        """Route a response based on confidence score and action type.

        Args:
            response: The agent's response text
            confidence: Confidence score between 0.0 and 1.0
            action_type: Type of action (e.g., "general", "transfer_money")

        Returns:
            RoutingDecision with routing action and metadata
        """
        # High-risk actions always require human approval
        if action_type in HIGH_RISK_ACTIONS:
            return RoutingDecision(
                action="escalate",
                confidence=confidence,
                reason=f"High-risk action: {action_type}",
                priority="high",
                requires_human=True,
            )

        # Route by confidence threshold
        if confidence >= self.HIGH_THRESHOLD:
            return RoutingDecision(
                action="auto_send",
                confidence=confidence,
                reason="High confidence",
                priority="low",
                requires_human=False,
            )
        elif confidence >= self.MEDIUM_THRESHOLD:
            return RoutingDecision(
                action="queue_review",
                confidence=confidence,
                reason="Medium confidence — needs review",
                priority="normal",
                requires_human=True,
            )
        else:
            return RoutingDecision(
                action="escalate",
                confidence=confidence,
                reason="Low confidence — escalating",
                priority="high",
                requires_human=True,
            )


# ============================================================
# HITL Decision Points
# ============================================================

hitl_decision_points = [
    {
        "id": 1,
        "name": "High-Value Transaction Approval",
        "trigger": "Customer requests a transfer above 50,000,000 VND or an international wire transfer",
        "hitl_model": "human-in-the-loop",
        "context_needed": (
            "Transaction amount, destination account, customer's transaction history "
            "for the past 30 days, fraud risk score, and the agent's proposed response"
        ),
        "example": (
            "Customer asks to transfer 200M VND to an overseas account. "
            "Agent flags it; a compliance officer reviews the request, "
            "verifies identity via OTP, and approves or rejects before the transfer executes."
        ),
    },
    {
        "id": 2,
        "name": "Ambiguous Complaint Escalation",
        "trigger": (
            "Agent confidence < 0.7 on a complaint or dispute, "
            "or customer uses escalation keywords ('manager', 'lawsuit', 'fraud')"
        ),
        "hitl_model": "human-on-the-loop",
        "context_needed": (
            "Full conversation history, customer account standing, "
            "previous complaint records, and the agent's draft response"
        ),
        "example": (
            "Customer claims an unauthorized transaction drained their account. "
            "Agent drafts a response but confidence is 0.6. "
            "A human agent reviews the draft within 5 minutes and either approves it "
            "or takes over the conversation."
        ),
    },
    {
        "id": 3,
        "name": "Policy Exception Request",
        "trigger": (
            "Customer requests a waiver, exception, or override of standard bank policy "
            "(e.g., fee waiver, overdraft limit increase, early CD withdrawal)"
        ),
        "hitl_model": "human-as-tiebreaker",
        "context_needed": (
            "Customer's account tenure, loyalty tier, reason for exception, "
            "policy rules, and two automated recommendations (approve/deny)"
        ),
        "example": (
            "Long-term customer requests a fee waiver for a late payment. "
            "Two automated systems disagree (one approves, one denies). "
            "A human banker casts the deciding vote based on customer relationship context."
        ),
    },
]


# ============================================================
# Quick tests
# ============================================================

def test_confidence_router():
    router = ConfidenceRouter()

    test_cases = [
        ("Balance inquiry", 0.95, "general"),
        ("Interest rate question", 0.82, "general"),
        ("Ambiguous request", 0.55, "general"),
        ("Transfer $50,000", 0.98, "transfer_money"),
        ("Close my account", 0.91, "close_account"),
    ]

    print("Testing ConfidenceRouter:")
    print("=" * 80)
    print(f"{'Scenario':<25} {'Conf':<6} {'Action Type':<18} {'Decision':<15} {'Priority':<10} {'Human?'}")
    print("-" * 80)

    for scenario, conf, action_type in test_cases:
        decision = router.route(scenario, conf, action_type)
        print(
            f"{scenario:<25} {conf:<6.2f} {action_type:<18} "
            f"{decision.action:<15} {decision.priority:<10} "
            f"{'Yes' if decision.requires_human else 'No'}"
        )

    print("=" * 80)


def test_hitl_points():
    print("\nHITL Decision Points:")
    print("=" * 60)
    for point in hitl_decision_points:
        print(f"\n  Decision Point #{point['id']}: {point['name']}")
        print(f"    Trigger:  {point['trigger']}")
        print(f"    Model:    {point['hitl_model']}")
        print(f"    Context:  {point['context_needed']}")
        print(f"    Example:  {point['example']}")
    print("\n" + "=" * 60)


if __name__ == "__main__":
    test_confidence_router()
    test_hitl_points()
