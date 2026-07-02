"""
Interrupt Handler

A dedicated handler for processing INTERRUPT signals and performing stream cancellation.
Decouples the "when to interrupt" decision (made by SemanticTurnDetector, client, etc.)
from the "how to cancel streams" execution (performed here).

Responsibilities:
- Listen for INTERRUPT signals from any source (CLIENT, HANDLER, etc.)
- Cancel the appropriate stream chains via StreamManager
- Record interrupt events in session history
"""
import time
from typing import Optional, Dict, cast

from loguru import logger

from chat_engine.common.handler_base import HandlerBase, HandlerDataInfo, HandlerDetail, HandlerBaseInfo
from chat_engine.data_models.chat_data_type import ChatDataType
from chat_engine.data_models.chat_signal import ChatSignal, SignalFilterRule
from chat_engine.data_models.chat_signal_type import ChatSignalType, ChatSignalSourceType
from chat_engine.contexts.handler_context import HandlerContext
from chat_engine.contexts.session_context import SessionContext
from chat_engine.data_models.chat_data.chat_data_model import ChatData
from chat_engine.data_models.chat_engine_config_data import ChatEngineConfigModel, HandlerBaseConfigModel


class InterruptHandlerConfig(HandlerBaseConfigModel):
    """Configuration for Interrupt Handler"""
    pass


class InterruptHandler(HandlerBase):
    """
    Interrupt Handler - centralized stream cancellation on INTERRUPT signals.

    Listens for INTERRUPT signals from any source and cancels the relevant
    stream chains. This decouples interrupt decision-making (in other handlers)
    from stream lifecycle management.

    Signal semantics:
    - related_stream set → targeted cancel via cancel_stream_chain
    - related_stream absent → cancel active playback, or pending avatar audio/text
    """

    def get_handler_info(self) -> HandlerBaseInfo:
        return HandlerBaseInfo(
            name="InterruptHandler",
            config_model=InterruptHandlerConfig
        )

    def load(self, engine_config: ChatEngineConfigModel, handler_config: Optional[HandlerBaseConfigModel] = None):
        pass

    def create_context(self, session_context: SessionContext,
                       handler_config: Optional[HandlerBaseConfigModel] = None) -> HandlerContext:
        return HandlerContext(session_context.session_info.session_id)

    def start_context(self, session_context: SessionContext, handler_context: HandlerContext):
        pass

    def get_handler_detail(self, session_context: SessionContext,
                           context: HandlerContext) -> HandlerDetail:
        signal_filters = [
            # Listen for INTERRUPT from any source (HANDLER, CLIENT, etc.)
            SignalFilterRule(ChatSignalType.INTERRUPT, None, None),
        ]

        return HandlerDetail(
            inputs=[],
            outputs=[],
            signal_filters=signal_filters
        )

    def handle(self, context: HandlerContext, inputs: ChatData,
               output_definitions: Dict[ChatDataType, HandlerDataInfo]):
        pass

    def on_signal(self, context: HandlerContext, signal: ChatSignal):
        """Handle INTERRUPT signals by cancelling the appropriate streams."""
        if signal.type != ChatSignalType.INTERRUPT:
            return

        logger.info(
            f"InterruptHandler: Received INTERRUPT signal, "
            f"source_type={signal.source_type}, source_name={signal.source_name}, "
            f"related_stream={signal.related_stream}"
        )
        received_mono = time.monotonic()
        logger.info(
            f"INTERRUPT_TRACE interrupt_handler_received "
            f"session={context.session_id} mono={received_mono:.6f} "
            f"source_type={signal.source_type} source_name={signal.source_name} "
            f"related_stream={signal.related_stream}"
        )

        target_stream = signal.related_stream
        cancel_data_type = None

        # If no related_stream specified, cancel by avatar response priority.
        if target_stream is None and context.stream_manager:
            active_streams = context.stream_manager.get_active_streams()
            for data_type in (
                ChatDataType.CLIENT_PLAYBACK,
                ChatDataType.AVATAR_AUDIO,
                ChatDataType.AVATAR_TEXT,
            ):
                candidates = [
                    s for s in active_streams
                    if s.identity.data_type == data_type
                ]
                if len(candidates) == 1:
                    target_stream = candidates[0].identity
                    break
                if len(candidates) > 1:
                    cancel_data_type = data_type
                    break

            if target_stream is None and cancel_data_type is None:
                logger.debug("InterruptHandler: No active avatar response streams to cancel")
                logger.info(
                    f"INTERRUPT_TRACE interrupt_handler_no_active_avatar_response "
                    f"session={context.session_id} "
                    f"since_received_ms={(time.monotonic() - received_mono) * 1000:.1f}"
                )
                return

        # Cancel streams via StreamManager
        cancelled = []
        if context.stream_manager:
            if target_stream:
                cancelled = context.stream_manager.cancel_stream_chain(target_stream)
                logger.info(
                    f"InterruptHandler: cancel_stream_chain({target_stream.stream_key_str}) "
                    f"cancelled {len(cancelled)} streams"
                )
                logger.info(
                    f"INTERRUPT_TRACE interrupt_handler_cancel_done "
                    f"session={context.session_id} mode=chain target_stream={target_stream.stream_key_str} "
                    f"cancelled_count={len(cancelled)} "
                    f"since_received_ms={(time.monotonic() - received_mono) * 1000:.1f}"
                )
            else:
                cancel_data_type = cancel_data_type or ChatDataType.CLIENT_PLAYBACK
                cancelled = context.stream_manager.cancel_streams_by_type(cancel_data_type)
                logger.info(
                    f"InterruptHandler: cancel_streams_by_type({cancel_data_type}) "
                    f"cancelled {len(cancelled)} streams"
                )
                logger.info(
                    f"INTERRUPT_TRACE interrupt_handler_cancel_done "
                    f"session={context.session_id} mode=by_type target_type={cancel_data_type} "
                    f"cancelled_count={len(cancelled)} "
                    f"since_received_ms={(time.monotonic() - received_mono) * 1000:.1f}"
                )

        # Record interrupt event in history
        if context.session_history is not None:
            signal_data = signal.signal_data or {}
            context.session_history.create_and_add_event(
                signal_type=ChatSignalType.INTERRUPT,
                data={
                    "reason": signal_data.get("reason", "interrupt"),
                    "trigger_text": signal_data.get("trigger_text", ""),
                    "cancelled_count": len(cancelled),
                    "source_type": signal.source_type.value if signal.source_type else None,
                    "source_name": signal.source_name,
                },
                owner=context.owner,
            )

    def destroy_context(self, context: HandlerContext):
        pass


# Export the handler class
handler_class = InterruptHandler
