"""Shared LLM usage-accounting value type.

Leaf module (no first-party imports) so both the core LLM turn machinery that
produces a :class:`TurnUsage` and the telemetry service that persists it can
depend on the shape without a services→core layering violation.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class TurnUsage:
    """Usage statistics from a single LLM API turn."""

    message_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    stop_reason: str
    service_tier: str | None
    inference_geo: str | None
    started_at: datetime
    ended_at: datetime
