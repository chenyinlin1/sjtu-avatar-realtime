from dataclasses import dataclass
from typing import Dict, Optional

from chat_engine.contexts.session_clock import SessionClock
from chat_engine.contexts.session_history import SessionHistory, HistoryConfig
from chat_engine.data_models.session_info_data import SessionInfoData


@dataclass
class SharedStates:
    active: bool = False
    persona_runtime: Optional[Dict] = None
    device_info: Optional[Dict] = None
    client_endpoint: Optional[str] = None
    music_status: Optional[Dict] = None
    music_player_active: bool = False


class SessionContext(object):
    def __init__(self, session_info: SessionInfoData, history_config: Optional[HistoryConfig] = None):
        self.session_info = session_info
        self.session_clock: SessionClock = SessionClock(self.session_info.timestamp_base)
        self.shared_states = SharedStates()
        # Global session history for full-duplex conversation support
        self.session_history: SessionHistory = SessionHistory(history_config)

    def cleanup(self):
        pass

    def get_clock(self):
        return self.session_clock
    
    def get_history(self) -> SessionHistory:
        """Get the session history for event tracking."""
        return self.session_history