"""Output guard for reminder turns that fail to produce a forced tool call."""

from __future__ import annotations

from typing import Any

from loguru import logger

from chat_engine.data_models.runtime_data.data_bundle import DataBundle
from engine_utils.conversation_audit_logger import audit_event
from handlers.agent.tools.reminder.intent_policy import (
    MANAGE_REMINDER_TOOL,
    missing_forced_reminder_reply,
)


def emit_missing_reminder_tool_reply(
    *,
    context: Any,
    tool_choice: Any,
    has_tool_calls: bool,
    cancelled: bool,
    model_output: str,
    user_text: str,
    output_definition: Any,
    streamer: Any,
    stream_identity: Any,
    stream_key: Any,
    turn_id: Any,
) -> bool:
    """Replace a suppressed false-success response with a safe spoken failure."""
    if cancelled:
        return False
    reply = missing_forced_reminder_reply(
        tool_choice,
        has_tool_calls=has_tool_calls,
    )
    if not reply:
        return False

    logger.warning(
        "LLM was forced to call manage_reminder but returned no tool_calls: "
        f"user_text={user_text[:120]!r}"
    )
    audit_event(
        context,
        "llm_tool_call_missing",
        stream_identity=stream_identity,
        bind_stream_key=stream_key,
        turn_id=turn_id,
        llm_stage="main_response",
        model=context.model_name,
        expected_tool=MANAGE_REMINDER_TOOL,
        output_text=model_output,
        user_text=user_text,
    )
    context.output_texts = reply
    output = DataBundle(output_definition)
    output.set_main_data(reply)
    streamer.stream_data(output)
    return True
