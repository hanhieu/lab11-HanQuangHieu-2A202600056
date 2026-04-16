"""
Assignment 11 — Rate Limiter Plugin

Prevents abuse by limiting requests per user in a sliding time window.
Catches DoS attempts and automated attack scripts that other layers miss.
"""
import time
from collections import defaultdict, deque

from google.genai import types
from google.adk.plugins import base_plugin
from google.adk.agents.invocation_context import InvocationContext


class RateLimitPlugin(base_plugin.BasePlugin):
    """Sliding-window rate limiter per user.

    Tracks request timestamps per user_id. If a user exceeds
    max_requests within window_seconds, their request is blocked
    and they receive a wait-time message.

    This layer catches automated attack scripts and DoS attempts
    before they reach any LLM — saving cost and preventing abuse.
    """

    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        super().__init__(name="rate_limiter")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # Maps user_id -> deque of request timestamps
        self.user_windows: dict[str, deque] = defaultdict(deque)
        self.blocked_count = 0
        self.total_count = 0

    def _get_user_id(self, invocation_context) -> str:
        """Extract user ID from context, fallback to 'anonymous'."""
        if invocation_context and hasattr(invocation_context, "user_id"):
            return invocation_context.user_id or "anonymous"
        return "anonymous"

    def is_rate_limited(self, user_id: str) -> tuple[bool, float]:
        """Check if user has exceeded the rate limit.

        Args:
            user_id: The user identifier

        Returns:
            Tuple of (is_limited, wait_seconds)
        """
        now = time.time()
        window = self.user_windows[user_id]

        # Remove timestamps outside the sliding window
        while window and window[0] < now - self.window_seconds:
            window.popleft()

        if len(window) >= self.max_requests:
            # Calculate how long until the oldest request expires
            wait_time = self.window_seconds - (now - window[0])
            return True, max(0.0, wait_time)

        # Record this request
        window.append(now)
        return False, 0.0

    def get_stats(self, user_id: str = None) -> dict:
        """Get rate limit stats for a user or globally."""
        if user_id:
            window = self.user_windows.get(user_id, deque())
            now = time.time()
            active = sum(1 for t in window if t >= now - self.window_seconds)
            return {
                "user_id": user_id,
                "requests_in_window": active,
                "max_requests": self.max_requests,
                "window_seconds": self.window_seconds,
            }
        return {
            "total_requests": self.total_count,
            "blocked_requests": self.blocked_count,
            "active_users": len(self.user_windows),
        }

    async def on_user_message_callback(
        self,
        *,
        invocation_context: InvocationContext,
        user_message: types.Content,
    ) -> types.Content | None:
        """Block request if user has exceeded rate limit."""
        self.total_count += 1
        user_id = self._get_user_id(invocation_context)
        limited, wait_time = self.is_rate_limited(user_id)

        if limited:
            self.blocked_count += 1
            return types.Content(
                role="model",
                parts=[types.Part.from_text(
                    text=f"You have sent too many requests. "
                         f"Please wait {wait_time:.0f} seconds before trying again."
                )],
            )

        return None
